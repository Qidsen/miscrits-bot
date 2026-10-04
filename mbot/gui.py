"""Главное окно: управление ботом, обучение элементов и маршрутов, настройки, журнал."""

import logging
import threading
import time
import urllib.request
from ctypes import wintypes
from dataclasses import fields

import cv2
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from miscrits_hud import hotkeys
from miscrits_hud.catalog import CatalogCache
from miscrits_hud.config import game_data_dir

from . import mouse
from .bot import Bot
from .collection import Collection
from .eyes import Eyes
from .hunt import LOCMAP_URL, hunt_rows
from .location_dialog import LocationDialog, spots_from_points
from .screen import Ocr, around, crop, grab, monitors_on_image, to_image, to_screen
from .settings import Settings, save_settings
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
    "spot_cooldown": "Кулдаун точки поиска (с момента клика), с",
    "heal_below": "Идти лечиться, если HP ниже, %",
    "plat_capture_limit": "Платиновых попыток за бой (Exotic/Legendary)",
    "capture_min_chance": "Жать Capture с шанса, %",
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
        self.bot = None
        self.capture_mode = None  # ("element", id) | ("spot",) | ("route", имя)
        self.region_corner = None

        tabs = QTabWidget()
        tabs.addTab(self._bot_tab(), "Бот")
        tabs.addTab(self._hunt_tab(), "Охота")
        tabs.addTab(self._teach_tab(), "Обучение")
        tabs.addTab(self._routes_tab(), "Точки и маршруты")
        tabs.addTab(self._settings_tab(), "Настройки")
        tabs.addTab(self._log_tab(), "Журнал")
        self.setCentralWidget(tabs)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(1000)
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

    def _bot_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.status = QLabel("Остановлен")
        self.status.setFont(QFont("Segoe UI", 16, QFont.Bold))
        v.addWidget(self.status)
        self.substatus = QLabel("")
        self.substatus.setWordWrap(True)
        v.addWidget(self.substatus)

        row = QHBoxLayout()
        self.start_btn = QPushButton("▶ Старт")
        self.pause_btn = QPushButton("⏸ Пауза (F6)")
        self.stop_btn = QPushButton("■ Стоп (F7)")
        for b in (self.start_btn, self.pause_btn, self.stop_btn):
            b.setMinimumHeight(40)
            row.addWidget(b)
        self.start_btn.clicked.connect(self._on_start)
        self.pause_btn.clicked.connect(self._on_pause)
        self.stop_btn.clicked.connect(self._on_stop)
        v.addLayout(row)

        box = QGroupBox("Сессия")
        grid = QGridLayout(box)
        self.stat_labels = {}
        for i, (key, title) in enumerate((("battles", "Боёв"), ("captures", "Поймано"),
                                          ("plat_captures", "Из них за платину"), ("trainings", "Тренировок"),
                                          ("heals", "Походов к лекарю"), ("time", "Время"))):
            grid.addWidget(QLabel(title), i // 3, (i % 3) * 2)
            label = QLabel("0")
            label.setFont(QFont("Segoe UI", 12, QFont.Bold))
            grid.addWidget(label, i // 3, (i % 3) * 2 + 1)
            self.stat_labels[key] = label
        v.addWidget(box)

        self.checks = QLabel("")
        self.checks.setWordWrap(True)
        v.addWidget(self.checks)

        v.addWidget(QLabel("События:"))
        self.events = QListWidget()
        v.addWidget(self.events, 1)
        v.addWidget(QLabel("Аварийный стоп: резко уведите мышь в левый верхний угол экрана."))
        return w

    def _readiness(self) -> list:
        problems = []
        missing = self.teaching.missing_required()
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
        eyes = Eyes(self.teaching, Ocr(self.settings.tesseract_cmd), self.settings.match_threshold)
        logs = self.home / "logs"
        logs.mkdir(exist_ok=True)
        self.bot = Bot(
            eyes, lambda rect: mouse.click(to_screen(rect)), self.catalogs.get, lambda: self.hud.controller.player, self.settings,
            self.home / "learn.json", logs, on_event=lambda kind, data: self.bridge.event.emit(kind, data),
            foreground=lambda: hotkeys.foreground_process()[0],
        )
        self.bot.start()
        self.status.setText("Работает — переключитесь в игру")

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
            self.events.insertItem(0, f"{time.strftime('%H:%M:%S')}  {data}")
            while self.events.count() > 300:
                self.events.takeItem(self.events.count() - 1)
        elif kind == "state":
            self.substatus.setText(data)
        elif kind == "paused":
            self.status.setText("Пауза")
            self.substatus.setText(data)
        elif kind == "resumed":
            self.status.setText("Работает")
        elif kind == "stopped":
            self.status.setText("Остановлен")
        elif kind == "teaching_changed":
            save_teaching(self.teaching_path, self.teaching)
            self._refresh_spot_list()
        elif kind == "locmap":
            self._show_locmap(*data)
        elif kind == "stats":
            self._show_stats(data)

    def _show_stats(self, stats):
        for key in ("battles", "captures", "plat_captures", "trainings", "heals"):
            self.stat_labels[key].setText(str(getattr(stats, key)))

    def _refresh_status(self):
        if self.bot and self.bot.running:
            minutes = int((time.time() - self.bot.stats.started) // 60)
            self.stat_labels["time"].setText(f"{minutes // 60}:{minutes % 60:02d}")
            if not self.bot._paused.is_set() and self.status.text() not in ("Работает",):
                self.status.setText("Работает")
        teaching_locked = bool(self.bot and self.bot.running)
        for b in self._teach_buttons + self._teach_buttons_routes:
            b.setEnabled(not teaching_locked)

    def _refresh_checks(self):
        problems = self._readiness()
        self.checks.setText("✅ Всё готово к запуску" if not problems else "⚠ " + "\n⚠ ".join(problems))

    # ---------- вкладка «Охота» ----------

    def _hunt_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        explain = QLabel(
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
        self.hunt_map = QLabel("Выберите вид — покажу, где он на карте (Miscrits Companion).")
        self.hunt_map.setMinimumSize(360, 240)
        self.hunt_map.setAlignment(Qt.AlignCenter)
        bottom.addWidget(self.hunt_map)
        self.hunt_summary = QLabel("")
        self.hunt_summary.setWordWrap(True)
        self.hunt_summary.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        bottom.addWidget(self.hunt_summary, 1)
        v.addLayout(bottom)
        self._hunt_rows = []
        self._hunt_filling = False
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
            if len(r.species.names) > 1:
                name += f"  ({' → '.join(r.species.names[1:])})"
            for col, value in enumerate((name, r.species.rarity, r.species.element, r.where,
                                         "да" if r.today else "", r.owned or "нет"), 1):
                self.hunt_table.setItem(i, col, QTableWidgetItem(value))
        self._hunt_filling = False
        self._hunt_summary()

    def _hunt_summary(self):
        targets = self.settings.hunt_targets
        if not targets:
            self.hunt_summary.setText("Целей нет — бот просто фармит опыт и ловит всё, что лучше имеющегося.")
            return
        lines = []
        for name in targets:
            spots = [str(i + 1) for i, s in enumerate(self.teaching.spots) if name in s.species_here()]
            lines.append(f"<b>{name}</b>: " + (f"точка {', '.join(spots)}" if spots else "точка пока неизвестна"))
        self.hunt_summary.setText("Цели:<br>" + "<br>".join(lines))

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

    def _show_locmap(self, species_id, data):
        self._locmap_cache[species_id] = data
        if self._wanted_map != species_id:
            return
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.hunt_map.setPixmap(pixmap)
        else:
            self.hunt_map.setText("Для этого вида карты нет.")

    # ---------- вкладка «Обучение» ----------

    def _teach_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        help_text = QLabel(
            "Как обучать: выберите строку, нажмите «Снять», переключитесь в игру, где этот элемент виден.\n"
            "• Кнопка — наведите мышь на её центр и нажмите F4.\n"
            "• Область (текст для чтения) — F4 в левом верхнем углу, затем F4 в правом нижнем.\n"
            "Элементы боя удобно снимать во время обычного боя вручную.")
        help_text.setWordWrap(True)
        v.addWidget(help_text)
        self.capture_hint = QLabel("")
        self.capture_hint.setStyleSheet("color:#d97706;font-weight:bold")
        v.addWidget(self.capture_hint)

        self.table = QTableWidget(len(ELEMENTS), 4)
        self.table.setHorizontalHeaderLabels(["Элемент", "Тип", "", "Снимок"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(48)
        for row, e in enumerate(ELEMENTS):
            item = QTableWidgetItem(e.title)
            item.setToolTip(e.hint)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem("кнопка" if e.kind == BUTTON else "область"))
            self.table.setItem(row, 2, QTableWidgetItem("обязательно" if e.required else ""))
        v.addWidget(self.table, 1)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        self.table.itemSelectionChanged.connect(
            lambda: self.hint.setText(ELEMENTS[self.table.currentRow()].hint if self.table.currentRow() >= 0 else ""))

        row = QHBoxLayout()
        snap_btn = QPushButton("Снять (F4)")
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
            name = self.capture_mode[1]
            rect = around(pos, self.settings.button_size, image.shape)
            self.teaching.routes.setdefault(name, []).append(Step(Snapshot(rect, crop(image, rect).copy())))
            # кликаем за пользователя — так запись идёт в один проход: F4 = «запомнить и нажать»
            mouse.click(to_screen(rect))
            self.route_hint.setText(f"Записано шагов: {len(self.teaching.routes[name])}. "
                                    "F4 на следующем элементе или «Закончить запись».")
        self._save_teaching()

    def _test_screen(self):
        image = grab()
        eyes = Eyes(self.teaching, Ocr(self.settings.tesseract_cmd), self.settings.match_threshold, lambda: image)
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
        v = QVBoxLayout(w)
        spots_box = QGroupBox("Точки поиска на текущей локации (бот обходит их по кругу)")
        sv = QVBoxLayout(spots_box)
        self.spot_list = QListWidget()
        self.spot_list.setViewMode(QListWidget.IconMode)
        self.spot_list.setIconSize(QPixmap(110, 110).size())
        self.spot_list.setMaximumHeight(170)
        sv.addWidget(self.spot_list)
        self.spot_hint = QLabel(
            "Лучше всего: «Снимок локации и разметка» — через 3 с программа снимет экран игры, и вы кликами отметите "
            "точки прямо на снимке. По снимку бот видит, куда уехала камера, и находит точки, даже когда их загородил "
            "персонаж. Перед снимком отведите персонажа от точек.")
        self.spot_hint.setWordWrap(True)
        sv.addWidget(self.spot_hint)
        row = QHBoxLayout()
        mark = QPushButton("Снимок локации и разметка (рекомендуется)")
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
        explain = QLabel(
            "Запись: выберите маршрут, нажмите «Записать», в игре наводите мышь на то, что нужно нажать, и жмите F4 — "
            "бот запомнит это место и сам нажмёт. Лечение: Return Home → здание Healing → Miscrit Healer → первая опция → "
            "Okay → красная стрелка → путь обратно на локацию. Тренировка: Train → кнопка тренировки → Okay/Continue → закрыть.\n"
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
        rec = QPushButton("Записать (F4)")
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
        v.addWidget(routes_box, 1)
        self._teach_buttons_routes = [mark, add, delete, rec, optional, remove, wipe]
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
        self.capture_mode = ("route", self._route_key())
        self.route_hint.setText("Запись идёт: F4 на каждом шаге в игре.")

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
        form = QFormLayout(w)
        self.setting_widgets = {}
        for f in fields(Settings):
            value = getattr(self.settings, f.name)
            if isinstance(value, list):
                continue  # цели охоты задаются на вкладке «Охота»
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
            self.setting_widgets[f.name] = widget
            form.addRow(SETTING_LABELS.get(f.name, f.name), widget)
        save = QPushButton("Сохранить")
        save.clicked.connect(self._save_settings)
        form.addRow(save)
        note = QLabel("Изменения применяются при следующем старте бота.")
        form.addRow(note)
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
