"""Главное окно: управление ботом, обучение элементов и маршрутов, настройки, журнал."""

import logging
import threading
import time
import urllib.request
from ctypes import wintypes
from dataclasses import fields

import cv2
import numpy as np
from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from miscrits_hud import hotkeys
from miscrits_hud.catalog import CatalogCache
from miscrits_hud.config import app_dir, game_data_dir
from miscrits_hud.icons import IconStore

from . import mouse, theme
from .bot import Bot
from .collection import Collection
from .eyes import Eyes
from .hunt import LOCMAP_URL, hunt_rows
from .location_dialog import LocationDialog, spots_from_points
from .screen import Ocr, around, crop, grab, monitors_on_image, to_image, to_screen
from .gamewindow import game_client_rect
from .settings import Settings, bot_dir, save_settings
from .ranks import RANKS, RankBook, unknown_samples
from .worldmap import Companion, Locator, view_rect
from .storage import BUTTON, ELEMENTS, REGION, ROUTES, Snapshot, Step, save_teaching

log = logging.getLogger(__name__)

HK_CAPTURE, HK_PAUSE, HK_STOP = 101, 102, 103
VK_F4, VK_F6, VK_F7 = 0x73, 0x75, 0x76

SETTING_LABELS = {
    "delay_min": "Пауза перед кликом от, с",
    "delay_max": "Пауза перед кликом до, с",
    "break_every_min": "Перерыв каждые от, мин",
    "break_every_max": "Перерыв каждые до, мин",
    "break_len_min": "Длина перерыва от, мин",
    "break_len_max": "Длина перерыва до, мин",
    "session_limit_min": "Лимит сессии, мин (0 — без лимита)",
    "kill_with_first": "Добивать первой способностью (обычно она лечит)",
    "explore_damage": "Изучать урон в обычных боях (пробовать разные атаки)",
    "explore_switch": "Пробовать других критов команды в обычных боях",
    "spot_cooldown": "Кулдаун точки поиска (с момента клика), с",
    "heal_below": "Идти лечиться, если HP ниже, %",
    "plat_capture_limit": "Платиновых попыток за бой (Exotic/Legendary)",
    "capture_min_chance": "Ловить сразу, если шанс поимки не ниже, %",
    "capture_hp_floor": "Перед поимкой подводить HP цели до",
    "train_every": "Тренировка каждые N боёв (если «Есть кого тренировать» не обучено; 0 — нет)",
    "match_threshold": "Точность совпадения картинок (0.5–0.99)",
    "button_size": "Размер снимка кнопки по F4, px",
    "tesseract_cmd": "Путь к tesseract.exe",
}


def to_pixmap(image, max_w=160, max_h=60) -> QPixmap:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    qimage = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimage).scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


SETTING_GROUPS = (
    ("Поведение и перерывы", ("delay_min", "delay_max", "break_every_min", "break_every_max",
                              "break_len_min", "break_len_max", "session_limit_min")),
    ("Охота и бой", ("spot_cooldown", "kill_with_first", "explore_damage", "explore_switch", "capture_hp_floor", "capture_min_chance", "plat_capture_limit",
                     "heal_below", "train_every")),
    ("Распознавание", ("match_threshold", "button_size", "tesseract_cmd")),
)


def game_rect_on_image():
    """Окно игры в координатах скриншота рабочего стола (при двух мониторах они сдвинуты)."""
    rect = game_client_rect()
    if rect is None:
        return None
    x, y = to_image(rect[:2])
    return x, y, rect[2], rect[3]


def _hint(text="") -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def _primary(text) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primary")
    return button


class Bridge(QObject):
    """События из потока бота и логгера → в поток Qt."""
    event = Signal(str, object)


class _QtLogHandler(logging.Handler):
    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self._bridge.event.emit("logline", self.format(record))


class MainWindow(QMainWindow):
    def __init__(self, hud, teaching, teaching_path, settings: Settings, settings_path, home):
        super().__init__()
        self.setWindowTitle("Miscrits Bot")
        theme.style_window(self)
        self.resize(980, 720)
        self.hud = hud
        self.teaching = teaching
        self.teaching_path = teaching_path
        self.settings = settings
        self.settings_path = settings_path
        self.home = home
        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        handler = _QtLogHandler(self.bridge)
        handler.setLevel(logging.INFO)
        logging.getLogger("mbot").addHandler(handler)
        self.catalogs = CatalogCache(game_data_dir() / "image_cache" / "miscrits.json")
        self.companion = Companion(bot_dir() / "companion")
        self.rank_book = RankBook(bot_dir() / "ranks_book")
        self.bot = None
        self.capture_mode = None  # ("element", id) | ("spot",) | ("route", имя)
        self.region_corner = None

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        for widget, title in ((self._bot_tab(), "▶  Бот"), (self._hunt_tab(), "🎯  Охота"),
                              (self._teach_tab(), "🎓  Обучение"), (self._ranks_tab(), "🏅  Ранги"), (self._routes_tab(), "📍  Точки и маршруты"),
                              (self._settings_tab(), "⚙  Настройки"), (self._log_tab(), "📜  Журнал")):
            tabs.addTab(widget, title)
        page = QWidget()
        page.setObjectName("page")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(tabs, 1)
        self.setCentralWidget(page)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(1000)
        self._set_state("stopped", "Остановлен")
        self._refresh_all()

    # ---------- горячие клавиши ----------

    def register_hotkeys(self):
        hwnd = int(self.winId())
        for hotkey_id, vk in ((HK_CAPTURE, VK_F4), (HK_PAUSE, VK_F6), (HK_STOP, VK_F7)):
            if not hotkeys.register(hwnd, hotkey_id, 0, vk):
                log.warning("hotkey %s is taken by another program", hotkey_id)

    def unregister_hotkeys(self):
        hwnd = int(self.winId())
        for hotkey_id in (HK_CAPTURE, HK_PAUSE, HK_STOP):
            hotkeys.unregister(hwnd, hotkey_id)

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == hotkeys.WM_HOTKEY:
            {HK_CAPTURE: self._on_f4, HK_PAUSE: self._on_pause, HK_STOP: self._on_stop}.get(int(msg.wParam), lambda: None)()
            return True, 0
        return super().nativeEvent(event_type, message)

    # ---------- вкладка «Бот» ----------

    def _header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(20, 14, 20, 14)
        logo = QLabel()
        logo.setPixmap(theme.app_icon().pixmap(40, 40))
        row.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Miscrits Bot")
        title.setObjectName("appTitle")
        subtitle = QLabel("охота · прокачка · HUD коллекции")
        subtitle.setObjectName("appSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        row.addLayout(titles)
        row.addStretch(1)
        hotkeys_hint = QLabel("F6 пауза · F7 стоп · F4 снять · мышь в угол — аварийный стоп")
        hotkeys_hint.setObjectName("appSubtitle")
        row.addWidget(hotkeys_hint)
        row.addSpacing(16)
        self.pill = QLabel()
        self.pill.setObjectName("statusPill")
        row.addWidget(self.pill)
        return header

    def _set_state(self, state: str, text: str):
        """state: running / paused / stopped — цвет «пилюли» в шапке и заголовок на вкладке «Бот»."""
        self._state = state
        self.pill.setText({"running": "● Работает", "paused": "❚❚ Пауза", "stopped": "■ Остановлен"}[state])
        self.pill.setStyleSheet(theme.pill_style(state))
        self.status.setText(text)

    @staticmethod
    def _card(caption: str) -> tuple:
        card = QFrame()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        value = QLabel("0")
        value.setObjectName("cardValue")
        label = QLabel(caption)
        label.setObjectName("cardCaption")
        v.addWidget(value)
        v.addWidget(label)
        return card, value

    def _bot_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("page")
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(14)

        top = QHBoxLayout()
        state_box = QVBoxLayout()
        self.status = QLabel("Остановлен")
        self.status.setObjectName("sectionTitle")
        self.substatus = QLabel("Нажмите «Старт» и переключитесь в игру")
        self.substatus.setObjectName("bigState")
        self.substatus.setWordWrap(True)
        state_box.addWidget(self.status)
        state_box.addWidget(self.substatus)
        top.addLayout(state_box, 1)
        self.start_btn = QPushButton("▶  Старт")
        self.start_btn.setObjectName("start")
        self.pause_btn = QPushButton("❚❚  Пауза")
        self.pause_btn.setObjectName("pause")
        self.stop_btn = QPushButton("■  Стоп")
        self.stop_btn.setObjectName("stop")
        for b in (self.start_btn, self.pause_btn, self.stop_btn):
            b.setMinimumSize(130, 46)
            b.setCursor(Qt.PointingHandCursor)
            top.addWidget(b)
        self.start_btn.clicked.connect(self._on_start)
        self.pause_btn.clicked.connect(self._on_pause)
        self.stop_btn.clicked.connect(self._on_stop)
        v.addLayout(top)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.stat_labels = {}
        for i, (key, title) in enumerate((("battles", "боёв"), ("captures", "поймано"),
                                          ("plat_captures", "за платину"), ("trainings", "тренировок"),
                                          ("heals", "походов к лекарю"), ("time", "в работе"))):
            card, value = self._card(title)
            cards.addWidget(card, 0, i)
            self.stat_labels[key] = value
        self.stat_labels["time"].setText("0:00")
        v.addLayout(cards)
        v.addWidget(self._battle_card())

        self.checks = QLabel("")
        self.checks.setWordWrap(True)
        v.addWidget(self.checks)

        events_title = QLabel("События")
        events_title.setObjectName("sectionTitle")
        v.addWidget(events_title)
        self.events = QListWidget()
        self.events.setAlternatingRowColors(True)
        v.addWidget(self.events, 1)
        return w

    def _battle_card(self) -> QWidget:
        """Что бот видит в бою и почему так ходит."""
        card = QFrame()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        self.battle_title = QLabel("⚔  Сейчас не в бою")
        self.battle_title.setObjectName("sectionTitle")
        v.addWidget(self.battle_title)
        self.battle_sides = QLabel("")
        self.battle_sides.setWordWrap(True)
        v.addWidget(self.battle_sides)
        self.battle_moves = QTableWidget(0, 7)
        self.battle_moves.setHorizontalHeaderLabels(["Атака", "Стихия", "× стихии", "Ожидаемо", "Худший случай",
                                                     "Ударов видел", "Вердикт"])
        self.battle_moves.verticalHeader().setVisible(False)
        self.battle_moves.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.battle_moves.setSelectionMode(QAbstractItemView.NoSelection)
        self.battle_moves.setShowGrid(False)
        self.battle_moves.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.battle_moves.setMaximumHeight(170)
        v.addWidget(self.battle_moves)
        self.battle_action = QLabel("")
        self.battle_action.setWordWrap(True)
        self.battle_action.setObjectName("bigState")
        v.addWidget(self.battle_action)
        return card

    def _show_battle(self, info):
        mode = "ЛОВИМ" if info["mode"] == "capture" else "убиваем"
        color = theme.ACCENT if info["mode"] == "capture" else theme.MUTED
        self.battle_title.setText(f"⚔  Бой — <span style='color:{color}'>{mode}</span>")
        rarity = info["rarity"]
        enemy = (f"<b>{info['enemy']}</b> <span style='color:{theme.RARITY_COLORS.get(rarity, theme.MUTED)}'>"
                 f"{rarity}</span> <b style='color:{theme.rank_color(info['rank'] or '')}'>{info['rank'] or '?'}</b>"
                 f" · ур. {info['level'] or '?'}")
        hp = info["hp"]
        if hp:
            share = hp[0] / hp[1] if hp[1] else 0
            bar_color = theme.GREEN if share > 0.5 else (theme.ACCENT if share > 0.2 else theme.RED)
            enemy += f" · HP <b style='color:{bar_color}'>{hp[0]}/{hp[1]}</b>"
            if info["mode"] == "capture":
                enemy += f" <span style='color:{theme.MUTED}'>(подводим до {info['floor']})</span>"
        mine = info["my_hp"]
        me = f"Мой: <b>{info['me']}</b>" + (f" · HP {mine[0]}/{mine[1]}" if mine else "")
        self.battle_sides.setText(f"{enemy}<br>{me}<br><span style='color:{theme.MUTED}'>{info['reason']}</span>")
        self.battle_moves.setRowCount(len(info["moves"]))
        for i, m in enumerate(info["moves"]):
            mult = m["mult"]
            cells = (m["name"], m["element"], f"×{mult:g}", f"{m['expected']:.0f}", f"{m['worst']:.0f}",
                     str(m["seen"]), m["verdict"])
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if col == 1:
                    item.setForeground(QColor(theme.element_color(m["element"])))
                elif col == 2:
                    item.setForeground(QColor(theme.GREEN if mult > 1 else (theme.RED if mult < 1 else theme.MUTED)))
                elif col == 6:
                    v = m["verdict"]
                    item.setForeground(QColor(theme.ACCENT if v.startswith("выбрана") else
                                              theme.GREEN if "безопасно" in v else
                                              theme.RED if v else theme.MUTED))
                self.battle_moves.setItem(i, col, item)
        self.battle_action.setText("→ " + info["action"])

    def _readiness(self) -> list:
        problems = []
        missing = self.teaching.missing_required()
        if self.settings.hunt_targets and "Точки поиска" in missing:
            missing.remove("Точки поиска")  # точки целей берутся с карты сайта
        if missing:
            problems.append("Не обучено: " + ", ".join(missing))
        if "heal" not in self.teaching.routes:
            problems.append("Маршрут лечения не записан — бот не сможет лечиться")
        if self.catalogs.get() is None:
            problems.append("Не найден каталог игры (miscrits.json) — запустите игру")
        if self.hud.controller.player is None:
            problems.append("Коллекция ещё не загружена (HUD) — бот будет считать, что у вас нет никого")
        if not Ocr(self.settings.tesseract_cmd).available():
            problems.append(f"Tesseract не найден: {self.settings.tesseract_cmd}")
        return problems

    def _on_start(self):
        if self.bot and self.bot.running:
            if self.bot._paused.is_set():
                self.bot.resume()
            return
        problems = self._readiness()
        blocking = [p for p in problems if p.startswith(("Не обучено", "Не найден", "Tesseract"))]
        if blocking:
            QMessageBox.warning(self, "Бот не готов", "\n".join(blocking))
            return
        eyes = Eyes(self.teaching, Ocr(self.settings.tesseract_cmd), self.settings.match_threshold, ranks=self.rank_book)
        logs = self.home / "logs"
        logs.mkdir(exist_ok=True)
        self.bot = Bot(
            eyes, lambda rect: mouse.click(to_screen(rect)), self.catalogs.get, lambda: self.hud.controller.player, self.settings,
            self.home / "learn.json", logs, on_event=lambda kind, data: self.bridge.event.emit(kind, data),
            foreground=lambda: hotkeys.foreground_process()[0],
            location_fn=lambda: self.hud.controller.location, companion=self.companion, game_rect_fn=game_rect_on_image,
        )
        self.bot.start()
        self._set_state("running", "Работает")
        self.substatus.setText("Переключитесь в игру — бот действует, только когда её окно активно")

    def _on_pause(self):
        if self.bot and self.bot.running:
            self.bot.toggle_pause()

    def _on_stop(self):
        if self.bot and self.bot.running:
            self.bot.stop()

    def _on_event(self, kind, data):
        if kind == "logline":
            self.log_view.appendPlainText(data)
            return
        if kind == "log":
            item = QListWidgetItem(f"{time.strftime('%H:%M:%S')}   {data}")
            if data.startswith("ПОЙМАН"):
                item.setForeground(QColor(theme.GREEN))
            elif "ЛОВИМ" in data:
                item.setForeground(QColor(theme.ACCENT))
            elif data.startswith(("не понимаю", "ошибка", "аварийный", "маршрут")):
                item.setForeground(QColor(theme.RED))
            self.events.insertItem(0, item)
            while self.events.count() > 300:
                self.events.takeItem(self.events.count() - 1)
        elif kind == "state":
            self.substatus.setText(data)
        elif kind == "paused":
            self._set_state("paused", "Пауза")
            self.substatus.setText(data)
        elif kind == "resumed":
            self._set_state("running", "Работает")
        elif kind == "stopped":
            self._set_state("stopped", "Остановлен")
        elif kind == "teaching_changed":
            save_teaching(self.teaching_path, self.teaching)
            self._refresh_spot_list()
        elif kind == "rank_unknown":
            self._refresh_ranks()
        elif kind == "position":
            self._show_position(*data)
        elif kind == "position_failed":
            self.hunt_map.setText(data)
        elif kind == "locmap":
            self._show_locmap(*data)
        elif kind == "battle":
            self._show_battle(data)
        elif kind == "battle_end":
            self.battle_title.setText("⚔  Сейчас не в бою")
        elif kind == "stats":
            self._show_stats(data)
            self._hunt_summary()

    def _show_stats(self, stats):
        for key in ("battles", "captures", "plat_captures", "trainings", "heals"):
            self.stat_labels[key].setText(str(getattr(stats, key)))

    def _refresh_status(self):
        if self.bot and self.bot.running:
            minutes = int((time.time() - self.bot.stats.started) // 60)
            self.stat_labels["time"].setText(f"{minutes // 60}:{minutes % 60:02d}")
            if not self.bot._paused.is_set() and self._state != "running":
                self._set_state("running", "Работает")
        teaching_locked = bool(self.bot and self.bot.running)
        for b in self._teach_buttons + self._teach_buttons_routes:
            b.setEnabled(not teaching_locked)

    def _refresh_checks(self):
        problems = self._readiness()
        self.checks.setObjectName("ok" if not problems else "warn")
        self.checks.setStyleSheet("")  # перечитать стиль после смены objectName
        self.checks.setText("✓  Всё готово к запуску" if not problems else "⚠  " + "\n⚠  ".join(problems))

    # ---------- вкладка «Охота» ----------

    def _hunt_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("page")
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        explain = _hint(
            "Отметьте галочкой цели. На локации бот первым делом жмёт точку, где водится цель (её он узнаёт сам по "
            "встречам или по вашей подписи на вкладке «Точки и маршруты»), остальные точки — пока она на кулдауне. "
            "Ловится по-прежнему всё, чего нет или что лучше имеющегося.")
        explain.setWordWrap(True)
        v.addWidget(explain)
        row = QHBoxLayout()
        self.hunt_search = QLineEdit()
        self.hunt_search.setPlaceholderText("Поиск по имени…")
        self.hunt_rarity = QComboBox()
        self.hunt_rarity.addItems(["Все", "Legendary", "Exotic", "Epic", "Rare", "Common"])
        self.hunt_today = QCheckBox("Только сегодня")
        self.hunt_missing = QCheckBox("Только кого нет")
        self.hunt_targets_only = QCheckBox("Только цели")
        for widget in (self.hunt_search, self.hunt_rarity, self.hunt_today, self.hunt_missing, self.hunt_targets_only):
            row.addWidget(widget)
        v.addLayout(row)
        self.hunt_table = QTableWidget(0, 7)
        self.hunt_table.setHorizontalHeaderLabels(["Цель", "Вид", "Редкость", "Стихия", "Где водится", "Сегодня", "У вас"])
        self.hunt_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.hunt_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.hunt_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.hunt_table.verticalHeader().setVisible(False)
        header = self.hunt_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        v.addWidget(self.hunt_table, 1)
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        map_card = QFrame()
        map_card.setObjectName("card")
        map_layout = QVBoxLayout(map_card)
        map_layout.setContentsMargins(10, 10, 10, 10)
        self.hunt_map = _hint("Выберите вид — покажу, где он на карте")
        self.hunt_map.setFixedSize(420, 260)
        self.hunt_map.setAlignment(Qt.AlignCenter)
        map_layout.addWidget(self.hunt_map)
        where_btn = QPushButton("📍  Где я на карте? (через 3 с)")
        where_btn.clicked.connect(lambda: QTimer.singleShot(3000, self._where_am_i))
        map_layout.addWidget(where_btn)
        bottom.addWidget(map_card)
        targets_card = QFrame()
        targets_card.setObjectName("card")
        targets_layout = QVBoxLayout(targets_card)
        targets_layout.setContentsMargins(16, 12, 16, 12)
        targets_title = QLabel("🎯  Цели охоты")
        targets_title.setObjectName("sectionTitle")
        targets_layout.addWidget(targets_title)
        self.hunt_summary = QLabel("")
        self.hunt_summary.setWordWrap(True)
        self.hunt_summary.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        targets_layout.addWidget(self.hunt_summary, 1)
        bottom.addWidget(targets_card, 1)
        v.addLayout(bottom)
        self._hunt_rows = []
        self._hunt_filling = False
        self._marker_names = None
        self._icons = {}
        self._icon_requested = set()
        self._icon_store = IconStore(game_data_dir() / "image_cache" / "miscrits", app_dir() / "icons")
        self.hunt_table.setIconSize(QSize(28, 28))
        self.hunt_table.verticalHeader().setDefaultSectionSize(36)
        self.hunt_table.setAlternatingRowColors(True)
        self.hunt_table.setShowGrid(False)
        self._locmap_cache = {}
        self._wanted_map = None
        for widget in (self.hunt_today, self.hunt_missing, self.hunt_targets_only):
            widget.toggled.connect(self._fill_hunt)
        self.hunt_search.textChanged.connect(self._fill_hunt)
        self.hunt_rarity.currentIndexChanged.connect(self._fill_hunt)
        self.hunt_table.itemChanged.connect(self._hunt_item_changed)
        self.hunt_table.itemSelectionChanged.connect(self._hunt_selected)
        QTimer.singleShot(500, self._fill_hunt)
        return w

    def _icon(self, name):
        """Иконка мискрита из кэша игры/HUD; недостающие докачиваются в фоне и появятся при следующем обновлении."""
        if name in self._icons:
            return self._icons[name]
        data = self._icon_store.get(name)
        icon = None
        if data:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                icon = QIcon(pixmap)
        elif name not in self._icon_requested:
            self._icon_requested.add(name)
            threading.Thread(target=lambda: self._icon_store.fetch(name), daemon=True).start()
        if icon is not None:
            self._icons[name] = icon
        return icon

    def _collection(self):
        player = self.hud.controller.player
        return Collection.from_player(player) if player is not None else Collection()

    def _fill_hunt(self):
        catalog = self.catalogs.get()
        if catalog is None:
            self.hunt_summary.setText("Каталог игры не найден — запустите игру.")
            return
        rows = hunt_rows(catalog, self._collection())
        text = self.hunt_search.text().strip().lower()
        rarity = self.hunt_rarity.currentText()
        targets = set(self.settings.hunt_targets)
        rows = [r for r in rows
                if (not text or any(text in n.lower() for n in r.species.names))
                and (rarity == "Все" or r.species.rarity == rarity)
                and (not self.hunt_today.isChecked() or r.today)
                and (not self.hunt_missing.isChecked() or not r.owned)
                and (not self.hunt_targets_only.isChecked() or r.species.names[0] in targets)]
        self._hunt_rows = rows
        self._hunt_filling = True
        self.hunt_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if r.species.names[0] in targets else Qt.Unchecked)
            self.hunt_table.setItem(i, 0, check)
            name = r.species.names[0]
            cells = (name, r.species.rarity, r.species.element, r.where, "● сегодня" if r.today else "",
                     r.owned or "нет")
            for col, value in enumerate(cells, 1):
                item = QTableWidgetItem(value)
                if col == 1:
                    item.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
                    item.setToolTip(" → ".join(r.species.names))
                    icon = self._icon(r.species.names[0])
                    if icon is not None:
                        item.setIcon(icon)
                elif col == 2:
                    item.setForeground(QColor(theme.RARITY_COLORS.get(r.species.rarity, theme.MUTED)))
                elif col == 3:
                    item.setForeground(QColor(theme.element_color(r.species.element)))
                elif col == 5:
                    item.setForeground(QColor(theme.GREEN))
                elif col == 6:
                    item.setForeground(QColor(theme.rank_color(r.owned) if r.owned not in ("", "есть") else
                                              (theme.MUTED if not r.owned else theme.TEXT)))
                self.hunt_table.setItem(i, col, item)
        self._hunt_filling = False
        self._hunt_summary()

    def _hunt_summary(self):
        targets = self.settings.hunt_targets
        if not targets:
            self.hunt_summary.setText("Целей нет — бот просто фармит опыт и ловит всё, что лучше имеющегося.")
            return
        lines = []
        on_map = self._markers_by_name()
        catalog = self.catalogs.get()
        by_name = {s.names[0]: s for s in catalog.species} if catalog else {}
        collection = self._collection()
        if self.bot is not None:
            collection = collection.merged(self.bot.stats.catches)
        for name in targets:
            spots = [str(i + 1) for i, s in enumerate(self.teaching.spots) if name in s.species_here()]
            species = by_name.get(name)
            if species is not None and collection.owns(species.id):
                best = collection.best(species.id)
                lines.append(f"<b>{name}</b>: <span style='color:{theme.GREEN}'>✓ поймана{f' ({best})' if best else ''}</span>"
                             f" <span style='color:{theme.MUTED}'>— бот её больше не ищет</span>")
                continue
            if name in on_map:
                where = f"<span style='color:{theme.GREEN}'>✓ точка на карте сайта</span> ({', '.join(sorted(on_map[name]))})"
            elif spots:
                where = f"точка {', '.join(spots)} (размечена у вас)"
            else:
                where = f"<span style='color:{theme.MUTED}'>точки нет на карте — бот узнает её сам при встрече</span>"
            lines.append(f"<b>{name}</b>: {where}")
        self.hunt_summary.setText("<br>".join(lines))

    def _markers_by_name(self) -> dict:
        """Имя вида -> локации, где он отмечен на карте сайта (данные кэшируются на диске)."""
        if self._marker_names is None:
            try:
                names = {}
                for location, markers in (self.companion.all_markers() or {}).items():
                    for m in markers:
                        names.setdefault(m["name"], set()).add(location)
                self._marker_names = names
            except Exception:
                log.exception("cannot read companion markers")
                return {}
        return self._marker_names

    def _hunt_item_changed(self, item):
        if self._hunt_filling or item.column() != 0:
            return
        name = self._hunt_rows[item.row()].species.names[0]
        targets = [t for t in self.settings.hunt_targets if t != name]
        if item.checkState() == Qt.Checked:
            targets.append(name)
        self.settings.hunt_targets = targets
        save_settings(self.settings_path, self.settings)
        self._hunt_summary()

    def _hunt_selected(self):
        row = self.hunt_table.currentRow()
        if not 0 <= row < len(self._hunt_rows):
            return
        species_id = self._hunt_rows[row].species.id
        self._wanted_map = species_id
        if species_id in self._locmap_cache:
            self._show_locmap(species_id, self._locmap_cache[species_id])
            return
        self.hunt_map.setText("Загружаю карту…")

        def fetch():
            data = None
            try:
                request = urllib.request.Request(LOCMAP_URL.format(id=species_id), headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    data = response.read()
            except Exception:
                pass
            self.bridge.event.emit("locmap", (species_id, data))

        threading.Thread(target=fetch, daemon=True).start()

    def _where_am_i(self):
        where = self.hud.controller.location
        if not where:
            self.hunt_map.setText("Локация неизвестна — HUD ещё не видел переход в игре")
            return
        self.hunt_map.setText("Ищу себя на карте…")

        def work():
            try:
                world = self.companion.map_image(where[0])
                game = game_rect_on_image()
                if world is None or game is None:
                    self.bridge.event.emit("position_failed", "нет карты локации или окна игры")
                    return
                image = grab()
                x, y, w, h = view_rect(game)
                place = Locator(world).locate(image[y:y + h, x:x + w])
                if place is None:
                    self.bridge.event.emit("position_failed", "не нашёл экран на карте — игра на локации, не в бою?")
                else:
                    self.bridge.event.emit("position", (where[0], place, (x, y, w, h)))
            except Exception as e:
                log.exception("where am i failed")
                self.bridge.event.emit("position_failed", repr(e))

        threading.Thread(target=work, daemon=True).start()

    def _show_position(self, location, place, rect):
        """Кусок карты вокруг экрана: рамка — что видно сейчас, кружки — маркеры (цели жёлтые)."""
        world = self.companion.map_image(location)
        if world is None:
            return
        _, _, w, h = rect
        x0, y0 = place.mx, place.my
        x1, y1 = x0 + w * place.scale, y0 + h * place.scale
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        half_w, half_h = (x1 - x0) * 1.6, (y1 - y0) * 1.6
        left, top = int(max(cx - half_w, 0)), int(max(cy - half_h, 0))
        right, bottom = int(min(cx + half_w, world.shape[1])), int(min(cy + half_h, world.shape[0]))
        crop = world[top:bottom, left:right]
        k = min(self.hunt_map.width() / crop.shape[1], self.hunt_map.height() / crop.shape[0])
        view = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        # рисуем уже на уменьшенной картинке, чтобы метки не превратились в точки
        cv2.rectangle(view, (int((x0 - left) * k), int((y0 - top) * k)), (int((x1 - left) * k), int((y1 - top) * k)),
                      (108, 93, 255), 2)
        targets = set(self.settings.hunt_targets)
        for m in self.companion.markers(location):
            px, py = int((m.x - left) * k), int((m.y - top) * k)
            if 0 <= px < view.shape[1] and 0 <= py < view.shape[0]:
                color = (102, 209, 255) if m.name in targets else (220, 220, 220)
                cv2.circle(view, (px, py), 7, (0, 0, 0), 4)
                cv2.circle(view, (px, py), 7, color, 2)
                cv2.putText(view, m.name, (px + 10, py + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(view, m.name, (px + 10, py + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
        self.hunt_map.setPixmap(to_pixmap(view, self.hunt_map.width(), self.hunt_map.height()))

    def _show_locmap(self, species_id, data):
        self._locmap_cache[species_id] = data
        if self._wanted_map != species_id:
            return
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.hunt_map.setPixmap(pixmap)
        else:
            self.hunt_map.setText("Для этого вида карты нет.")

    # ---------- вкладка «Ранги» ----------

    def _ranks_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("page")
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        v.addWidget(_hint(
            "Ранг дикого крита бот узнаёт по образцам значков: у каждой буквы свой цвет и форма. Если значок ему "
            "незнаком, он не угадывает, а сохраняет его сюда. Выберите значок, укажите ранг и нажмите «Запомнить» — "
            "дальше бот будет узнавать такой ранг сам. Хватает одного образца на ранг."))
        self.rank_counts = QLabel("")
        self.rank_counts.setWordWrap(True)
        v.addWidget(self.rank_counts)
        self.rank_list = QListWidget()
        self.rank_list.setViewMode(QListWidget.IconMode)
        self.rank_list.setIconSize(QSize(96, 93))
        self.rank_list.setGridSize(QSize(120, 130))
        self.rank_list.setResizeMode(QListWidget.Adjust)
        v.addWidget(self.rank_list, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Это ранг:"))
        self.rank_choice = QComboBox()
        self.rank_choice.addItems(list(RANKS))
        row.addWidget(self.rank_choice)
        remember = _primary("Запомнить выбранный")
        remember.clicked.connect(self._remember_rank)
        drop = QPushButton("Удалить выбранный")
        drop.clicked.connect(self._drop_rank_sample)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self._refresh_ranks)
        for b in (remember, drop, refresh):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        QTimer.singleShot(300, self._refresh_ranks)
        return w

    def _rank_samples_dir(self):
        return self.home / "logs" / "ranks"

    def _refresh_ranks(self):
        counts = self.rank_book.counts()
        known = ", ".join(f"<b style='color:{theme.rank_color(r)}'>{r}</b>×{counts[r]}" for r in RANKS if r in counts)
        missing = [r for r in RANKS if r not in counts]
        self.rank_counts.setText(f"Знаю: {known or '—'}<br><span style='color:{theme.MUTED}'>Ещё не видел: "
                                 f"{', '.join(missing) or 'все знаю'}</span>")
        self.rank_list.clear()
        self._rank_files = unknown_samples(self._rank_samples_dir())
        for path in self._rank_files:
            image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
            item = QListWidgetItem(time.strftime("%H:%M", time.localtime(path.stat().st_mtime)))
            if image is not None:
                item.setIcon(QIcon(to_pixmap(image, 96, 93)))
            self.rank_list.addItem(item)
        if not self._rank_files:
            self.rank_list.addItem(QListWidgetItem("Незнакомых значков нет 👍"))

    def _selected_rank_file(self):
        row = self.rank_list.currentRow()
        files = getattr(self, "_rank_files", [])
        return files[row] if 0 <= row < len(files) else None

    def _remember_rank(self):
        path = self._selected_rank_file()
        if path is None:
            QMessageBox.information(self, "Ранги", "Выберите значок в списке.")
            return
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            self.rank_book.learn(self.rank_choice.currentText(), image)
        path.unlink(missing_ok=True)
        self._refresh_ranks()

    def _drop_rank_sample(self):
        path = self._selected_rank_file()
        if path is not None:
            path.unlink(missing_ok=True)
            self._refresh_ranks()

    # ---------- вкладка «Обучение» ----------

    def _teach_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("page")
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        help_text = _hint(
            "Как обучать: выберите строку, нажмите «Снять», переключитесь в игру, где этот элемент виден.\n"
            "• Кнопка — наведите мышь на её центр и нажмите F4.\n"
            "• Область (текст для чтения) — F4 в левом верхнем углу, затем F4 в правом нижнем.\n"
            "Элементы боя удобно снимать во время обычного боя вручную.")
        help_text.setWordWrap(True)
        v.addWidget(help_text)
        self.capture_hint = QLabel("")
        self.capture_hint.setObjectName("warn")
        v.addWidget(self.capture_hint)

        self.table = QTableWidget(len(ELEMENTS), 4)
        self.table.setHorizontalHeaderLabels(["Элемент", "Тип", "Нужно", "Снимок"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        for row, e in enumerate(ELEMENTS):
            item = QTableWidgetItem(e.title)
            item.setToolTip(e.hint)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem("кнопка" if e.kind == BUTTON else "область"))
            required = QTableWidgetItem("обязательно" if e.required else "по желанию")
            required.setForeground(QColor(theme.ACCENT if e.required else theme.MUTED))
            self.table.setItem(row, 2, required)
        v.addWidget(self.table, 1)
        self.hint = _hint("")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        self.table.itemSelectionChanged.connect(
            lambda: self.hint.setText(ELEMENTS[self.table.currentRow()].hint if self.table.currentRow() >= 0 else ""))

        row = QHBoxLayout()
        snap_btn = _primary("Снять (F4)")
        snap_btn.clicked.connect(self._arm_element)
        clear_btn = QPushButton("Сбросить")
        clear_btn.clicked.connect(self._clear_element)
        test_btn = QPushButton("Проверить экран через 3 с")
        test_btn.clicked.connect(lambda: QTimer.singleShot(3000, self._test_screen))
        for b in (snap_btn, clear_btn, test_btn):
            row.addWidget(b)
        v.addLayout(row)
        self._teach_buttons = [snap_btn, clear_btn]
        self.test_output = QPlainTextEdit()
        self.test_output.setReadOnly(True)
        self.test_output.setMaximumHeight(150)
        v.addWidget(self.test_output)
        return w

    def _arm_element(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Обучение", "Сначала выберите элемент в таблице.")
            return
        e = ELEMENTS[row]
        self.capture_mode = ("element", e.id)
        self.region_corner = None
        what = "наведите мышь на кнопку и нажмите F4" if e.kind == BUTTON else "F4 в левом верхнем углу области"
        self.capture_hint.setText(f"Снимаю «{e.title}»: переключитесь в игру, {what}.")

    def _clear_element(self):
        row = self.table.currentRow()
        if row >= 0:
            self.teaching.elements.pop(ELEMENTS[row].id, None)
            self._save_teaching()

    def _on_f4(self):
        if self.capture_mode is None:
            return
        image = grab()
        pos = to_image(mouse.position())
        kind = self.capture_mode[0]
        if kind == "element":
            element = next(e for e in ELEMENTS if e.id == self.capture_mode[1])
            if element.kind == REGION:
                if self.region_corner is None:
                    self.region_corner = pos
                    self.capture_hint.setText(f"«{element.title}»: теперь F4 в правом нижнем углу области.")
                    return
                (x0, y0), (x1, y1) = self.region_corner, pos
                rect = (min(x0, x1), min(y0, y1), max(abs(x1 - x0), 4), max(abs(y1 - y0), 4))
            else:
                rect = around(pos, self.settings.button_size, image.shape)
            self.teaching.elements[element.id] = Snapshot(rect, crop(image, rect).copy())
            self.capture_mode = None
            self.capture_hint.setText(f"«{element.title}» снят ✔")
        elif kind == "spot":
            rect = around(pos, self.settings.button_size, image.shape)
            self.teaching.spots.append(Snapshot(rect, crop(image, rect).copy()))
            self.capture_hint.setText("")
            self.spot_hint.setText(f"Точек: {len(self.teaching.spots)}. F4 — ещё одна, «Готово» — закончить.")
        elif kind == "route":
            _, name, insert_at = self.capture_mode
            rect = around(pos, self.settings.button_size, image.shape)
            steps = self.teaching.routes.setdefault(name, [])
            step = Step(Snapshot(rect, crop(image, rect).copy()))
            if insert_at is None:
                steps.append(step)
                done = len(steps)
            else:
                steps.insert(insert_at, step)
                done = insert_at + 1
                self.capture_mode = ("route", name, insert_at + 1)  # следующие — за только что вставленным
            # кликаем за пользователя — так запись идёт в один проход: F4 = «запомнить и нажать»
            mouse.click(to_screen(rect))
            self.route_hint.setText(f"Записан шаг {done} (всего {len(steps)}). "
                                    "F4 на следующем элементе или «Закончить запись».")
        elif kind == "reshoot":
            _, name, row = self.capture_mode
            rect = around(pos, self.settings.button_size, image.shape)
            steps = self.teaching.routes.get(name, [])
            if 0 <= row < len(steps):
                steps[row].snap = Snapshot(rect, crop(image, rect).copy())
            mouse.click(to_screen(rect))
            self.capture_mode = None
            self.route_hint.setText(f"Шаг {row + 1} переснят ✔")
        self._save_teaching()

    def _test_screen(self):
        image = grab()
        eyes = Eyes(self.teaching, Ocr(self.settings.tesseract_cmd), self.settings.match_threshold, lambda: image,
                    ranks=self.rank_book)
        eyes.look()
        catalog = self.catalogs.get()
        all_names = [n for s in catalog.species for n in s.names] if catalog else []
        ability_names = sorted({a["name"] for s in catalog.species for a in s.abilities}) if catalog else []
        lines = []
        for e in ELEMENTS:
            if e.id not in self.teaching.elements:
                continue
            if e.kind == BUTTON:
                lines.append(f"{e.title}: {'ВИДНО' if eyes.sees(e.id) else '—'}")
            elif e.id in ("my_hp", "enemy_hp"):
                lines.append(f"{e.title}: {eyes.read_hp(e.id)}")
            elif e.id == "capture_chance":
                lines.append(f"{e.title}: {eyes.read_percent(e.id)}")
            elif e.id == "enemy_rank":
                lines.append(f"{e.title}: {eyes.read_rank(e.id)}")
            elif e.id.startswith("ability_"):
                lines.append(f"{e.title}: {eyes.read_name(e.id, ability_names)}")
            else:
                lines.append(f"{e.title}: {eyes.read_name(e.id, all_names)}")
        spots = sum(1 for s in self.teaching.spots if eyes.locate_spot(s))
        lines.append(f"Точек поиска видно: {spots}/{len(self.teaching.spots)}")
        self.test_output.setPlainText("\n".join(lines))

    # ---------- вкладка «Точки и маршруты» ----------

    def _routes_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("page")
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        spots_box = QGroupBox("Точки поиска на текущей локации (бот обходит их по кругу)")
        sv = QVBoxLayout(spots_box)
        self.spot_list = QListWidget()
        self.spot_list.setViewMode(QListWidget.IconMode)
        self.spot_list.setIconSize(QPixmap(110, 110).size())
        self.spot_list.setMaximumHeight(170)
        sv.addWidget(self.spot_list)
        self.spot_hint = _hint(
            "Лучше всего: «Снимок локации и разметка» — через 3 с программа снимет экран игры, и вы кликами отметите "
            "точки прямо на снимке. По снимку бот видит, куда уехала камера, и находит точки, даже когда их загородил "
            "персонаж. Перед снимком отведите персонажа от точек.")
        self.spot_hint.setWordWrap(True)
        sv.addWidget(self.spot_hint)
        row = QHBoxLayout()
        mark = _primary("Снимок локации и разметка (рекомендуется)")
        mark.clicked.connect(self._start_location_shot)
        sv.addWidget(mark)
        add = QPushButton("Добавить точки (F4)")
        add.clicked.connect(lambda: (setattr(self, "capture_mode", ("spot",)),
                                     self.spot_hint.setText("Жду F4 на точках поиска…")))
        done = QPushButton("Готово")
        done.clicked.connect(self._finish_capture)
        delete = QPushButton("Удалить выбранную")
        delete.clicked.connect(self._delete_spot)
        label_btn = QPushButton("Подписать точку…")
        label_btn.clicked.connect(self._label_spot)
        for b in (add, done, delete, label_btn):
            row.addWidget(b)
        sv.addLayout(row)
        v.addWidget(spots_box)

        routes_box = QGroupBox("Маршруты")
        rv = QVBoxLayout(routes_box)
        explain = _hint(
            "Запись: выберите маршрут, нажмите «Записать», в игре наводите мышь на то, что нужно нажать, и жмите F4 — "
            "бот запомнит это место и сам нажмёт. Лечение: Return Home → здание Healing → Miscrit Healer → первая опция → "
            "Okay → красная стрелка → путь обратно на локацию. Тренировка — строго по порядку: Train → строка готового крита (с меткой READY TO TRAIN) → TRAIN NOW → Continue → крестик закрытия. Бот сам найдёт всех готовых, а если готовых нет — просто закроет окно.\n"
            "Шаги, которых иногда нет (например, попап эволюции), отметьте как необязательные.")
        explain.setWordWrap(True)
        rv.addWidget(explain)
        self.route_combo = QComboBox()
        for key, title in ROUTES.items():
            self.route_combo.addItem(title, key)
        self.route_combo.currentIndexChanged.connect(self._refresh_route)
        rv.addWidget(self.route_combo)
        self.route_list = QListWidget()
        self.route_list.setIconSize(QPixmap(110, 60).size())
        rv.addWidget(self.route_list, 1)
        self.route_hint = QLabel("")
        rv.addWidget(self.route_hint)
        row = QHBoxLayout()
        rec = _primary("Записать (F4)")
        rec.clicked.connect(self._record_route)
        stop = QPushButton("Закончить запись")
        stop.clicked.connect(self._finish_capture)
        optional = QPushButton("Шаг обязателен / нет")
        optional.clicked.connect(self._toggle_optional)
        remove = QPushButton("Удалить шаг")
        remove.clicked.connect(self._delete_step)
        wipe = QPushButton("Очистить маршрут")
        wipe.clicked.connect(self._wipe_route)
        for b in (rec, stop, optional, remove, wipe):
            row.addWidget(b)
        rv.addLayout(row)
        edit_row = QHBoxLayout()
        up = QPushButton("▲  Выше")
        up.clicked.connect(lambda: self._move_step(-1))
        down = QPushButton("▼  Ниже")
        down.clicked.connect(lambda: self._move_step(1))
        reshoot = QPushButton("Переснять шаг (F4)")
        reshoot.clicked.connect(self._reshoot_step)
        for b in (up, down, reshoot):
            edit_row.addWidget(b)
        edit_row.addWidget(_hint("Выбран шаг — «Записать» вставит новые шаги сразу после него."), 1)
        rv.addLayout(edit_row)
        v.addWidget(routes_box, 1)
        self._teach_buttons_routes = [mark, add, delete, rec, optional, remove, wipe, up, down, reshoot]
        return w

    def _start_location_shot(self):
        self.spot_hint.setText("Снимок через 3 с — переключитесь в игру (мышь оставьте на мониторе с игрой)…")
        QTimer.singleShot(3000, self._location_shot)

    def _location_shot(self):
        image = grab()
        cursor = to_image(mouse.position())
        # снимаем монитор, на котором мышь, — там игра
        monitor = next((m for m in monitors_on_image() if m[0] <= cursor[0] < m[0] + m[2] and m[1] <= cursor[1] < m[1] + m[3]),
                       (0, 0, image.shape[1], image.shape[0]))
        shot = crop(image, monitor).copy()
        old = self.teaching.location
        points = []
        if old is not None and old.rect == monitor:
            points = [(s.rect[0] - monitor[0] + s.rect[2] // 2, s.rect[1] - monitor[1] + s.rect[3] // 2)
                      for s in self.teaching.spots]
        self.showNormal()
        self.raise_()
        self.activateWindow()
        dialog = LocationDialog(self, shot, points)
        if dialog.exec():
            self.teaching.location = Snapshot(monitor, shot)
            self.teaching.spots = spots_from_points(shot, monitor[:2], dialog.points, self.settings.button_size)
            save_teaching(self.teaching_path, self.teaching, with_location=True)
            self._refresh_all()
        self.spot_hint.setText(f"Точек: {len(self.teaching.spots)}.")

    def _route_key(self):
        return self.route_combo.currentData()

    def _record_route(self):
        # если в списке выбран шаг, новые шаги встают сразу после него, иначе — в конец
        row = self.route_list.currentRow()
        self.capture_mode = ("route", self._route_key(), row + 1 if row >= 0 else None)
        where = f"после шага {row + 1}" if row >= 0 else "в конец маршрута"
        self.route_hint.setText(f"Запись идёт ({where}): F4 на каждом шаге в игре.")

    def _reshoot_step(self):
        row = self.route_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Маршрут", "Выберите шаг, который нужно переснять.")
            return
        self.capture_mode = ("reshoot", self._route_key(), row)
        self.route_hint.setText(f"Пересъёмка шага {row + 1}: F4 на нужной кнопке в игре (она будет нажата).")

    def _move_step(self, delta):
        steps = self.teaching.routes.get(self._route_key(), [])
        row = self.route_list.currentRow()
        target = row + delta
        if 0 <= row < len(steps) and 0 <= target < len(steps):
            steps[row], steps[target] = steps[target], steps[row]
            self._save_teaching()
            self.route_list.setCurrentRow(target)

    def _finish_capture(self):
        self.capture_mode = None
        self.capture_hint.setText("")
        self.route_hint.setText("")
        self.spot_hint.setText(f"Точек: {len(self.teaching.spots)}.")

    @staticmethod
    def _spot_caption(i, spot) -> str:
        text = f"#{i}"
        if spot.label:
            text += f" {spot.label}"
        if spot.seen:
            top = sorted(spot.seen.items(), key=lambda kv: -kv[1])[:3]
            text += "\n" + ", ".join(f"{n}×{c}" for n, c in top)
        return text

    def _refresh_spot_list(self):
        for i, spot in enumerate(self.teaching.spots):
            item = self.spot_list.item(i)
            if item is not None:
                item.setText(self._spot_caption(i + 1, spot))

    def _label_spot(self):
        row = self.spot_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Точки", "Выберите точку в списке.")
            return
        catalog = self.catalogs.get()
        names = sorted({s.names[0] for s in catalog.species}) if catalog else []
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Кто водится на этой точке")
        dialog.setLabelText("Вид (пусто — убрать подпись):")
        dialog.setComboBoxEditable(True)
        dialog.setComboBoxItems([""] + names)
        dialog.setTextValue(self.teaching.spots[row].label)
        if dialog.exec():
            self.teaching.spots[row].label = dialog.textValue().strip()
            self._save_teaching()

    def _delete_spot(self):
        row = self.spot_list.currentRow()
        if row >= 0:
            del self.teaching.spots[row]
            self._save_teaching()

    def _toggle_optional(self):
        steps = self.teaching.routes.get(self._route_key(), [])
        row = self.route_list.currentRow()
        if 0 <= row < len(steps):
            steps[row].optional = not steps[row].optional
            self._save_teaching()
            self.route_list.setCurrentRow(row)

    def _delete_step(self):
        steps = self.teaching.routes.get(self._route_key(), [])
        row = self.route_list.currentRow()
        if 0 <= row < len(steps):
            del steps[row]
            self._save_teaching()

    def _wipe_route(self):
        if QMessageBox.question(self, "Маршрут", "Удалить все шаги маршрута?") == QMessageBox.Yes:
            self.teaching.routes.pop(self._route_key(), None)
            self._save_teaching()

    def _refresh_route(self):
        self.route_list.clear()
        for i, step in enumerate(self.teaching.routes.get(self._route_key(), []), 1):
            item = QListWidgetItem(f"Шаг {i}" + ("  (необязательный)" if step.optional else ""))
            if step.snap.image is not None:
                item.setIcon(to_pixmap(step.snap.image, 110, 60))
            self.route_list.addItem(item)

    # ---------- вкладка «Настройки» ----------

    def _settings_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        w = QWidget()
        w.setObjectName("page")
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        self.setting_widgets = {}
        values = {f.name: getattr(self.settings, f.name) for f in fields(Settings)}
        for title, names in SETTING_GROUPS:
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setHorizontalSpacing(24)
            form.setVerticalSpacing(10)
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            for name in names:
                value = values[name]
                if isinstance(value, bool):
                    widget = QCheckBox()
                    widget.setChecked(value)
                elif isinstance(value, int):
                    widget = QSpinBox()
                    widget.setRange(0, 100000)
                    widget.setValue(value)
                elif isinstance(value, float):
                    widget = QDoubleSpinBox()
                    widget.setRange(0, 1000)
                    widget.setDecimals(2)
                    widget.setSingleStep(0.05)
                    widget.setValue(value)
                else:
                    widget = QLineEdit(str(value))
                if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setButtonSymbols(QSpinBox.NoButtons)
                    widget.setFixedWidth(120)
                    widget.setAlignment(Qt.AlignRight)
                self.setting_widgets[name] = widget
                form.addRow(SETTING_LABELS.get(name, name), widget)
            v.addWidget(box)
        row = QHBoxLayout()
        row.addWidget(_hint("Изменения применяются при следующем старте бота."), 1)
        save = _primary("Сохранить")
        save.setMinimumWidth(180)
        save.clicked.connect(self._save_settings)
        row.addWidget(save)
        v.addLayout(row)
        v.addStretch(1)
        scroll.setWidget(w)
        return scroll

    def _save_settings(self):
        for name, widget in self.setting_widgets.items():
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                value = widget.value()
            else:
                value = widget.text()
            setattr(self.settings, name, value)
        if self.settings.delay_max < self.settings.delay_min:
            self.settings.delay_max = self.settings.delay_min
        save_settings(self.settings_path, self.settings)
        self._refresh_checks()
        QMessageBox.information(self, "Настройки", "Сохранено.")

    # ---------- вкладка «Журнал» ----------

    def _log_tab(self) -> QWidget:
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        return self.log_view

    # ---------- общее ----------

    def _save_teaching(self):
        save_teaching(self.teaching_path, self.teaching)
        self._refresh_all()

    def _refresh_all(self):
        for row, e in enumerate(ELEMENTS):
            snap = self.teaching.elements.get(e.id)
            label = QLabel()
            if snap is not None and snap.image is not None:
                label.setPixmap(to_pixmap(snap.image))
            elif snap is not None:
                label.setText("✔")
            else:
                label.setText("— не снято —")
                label.setObjectName("hint")
            label.setContentsMargins(8, 0, 0, 0)
            self.table.setCellWidget(row, 3, label)
        self.spot_list.clear()
        for i, spot in enumerate(self.teaching.spots, 1):
            item = QListWidgetItem(self._spot_caption(i, spot))
            if spot.image is not None:
                item.setIcon(to_pixmap(spot.image, 110, 110))
            self.spot_list.addItem(item)
        self._refresh_route()
        self._refresh_checks()

    def closeEvent(self, event):
        # Окно прячется в трей: бот и HUD продолжают работать, выход — через меню трея.
        event.ignore()
        self.hide()
