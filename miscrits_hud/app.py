"""Склейка: таймер → Controller.tick() → OverlayWindow.render()."""

import logging
import os
import queue
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QLockFile, QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import hotkeys
from .catalog import CatalogCache
from .config import app_dir, game_data_dir, load_cache, load_config, save_cache, save_config
from .controller import Controller
from .game_api import fetch_player
from .icons import IconStore
from .log_watcher import LogWatcher
from .overlay import OverlayWindow

HK_TOGGLE, HK_REFRESH, HK_MOVE = 1, 2, 3
GAME_EXE = "miscrits.exe"
GAME_CHECK_INTERVAL = 2.0
TICK_MS = 500


def acquire_single_instance(directory):
    """Вторая копия HUD дала бы второй оверлей без горячих клавиш — не запускаемся."""
    lock = QLockFile(str(directory / "hud.lock"))
    return lock if lock.tryLock(0) else None


def _tray_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#ffd166"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(pixmap.rect(), 6, 6)
    painter.setPen(QColor("#12141c"))
    painter.setFont(QFont("Segoe UI", 16, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "M")
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    home = app_dir()
    sys.excepthook = lambda *exc: logging.critical("unhandled exception", exc_info=exc)
    logging.basicConfig(
        filename=home / "hud.log", level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    game = game_data_dir()
    app = QApplication(sys.argv)
    instance_lock = acquire_single_instance(home)
    if instance_lock is None:
        logging.info("another HUD is already running; exiting")
        return 0
    pool = ThreadPoolExecutor(max_workers=2)

    icons = IconStore(game / "image_cache" / "miscrits", home / "icons")
    icons_ready = queue.Queue()
    requested = set()

    def icon_lookup(name):
        data = icons.get(name)
        if data is None and name not in requested:
            requested.add(name)
            pool.submit(icons.fetch, name).add_done_callback(lambda f: icons_ready.put(f.result()))
        return data

    cache_path = home / "collection.json"
    controller = Controller(
        LogWatcher(game / "logs" / "godot.log"),
        CatalogCache(game / "image_cache" / "miscrits.json").get,
        fetch_player,
        pool.submit,
        cached=load_cache(cache_path),
        on_update=lambda player, names, when: save_cache(cache_path, player, names, when),
    )
    window = OverlayWindow(icon_lookup)

    config_path = home / "config.json"
    cfg = load_config(config_path)
    screen = app.primaryScreen().availableGeometry()
    window.place(QPoint(cfg.x if cfg.x is not None else screen.right() - window.width() - 20,
                        cfg.y if cfg.y is not None else screen.top() + 20))

    def save_position():
        cfg.x, cfg.y = window.x(), window.y()
        save_config(config_path, cfg)

    def on_hotkey(hotkey_id):
        if hotkey_id == HK_TOGGLE:
            cfg.visible = not cfg.visible
            save_config(config_path, cfg)
            update_placement()
        elif hotkey_id == HK_REFRESH:
            controller.request_refresh()
        elif hotkey_id == HK_MOVE:
            window.set_move_mode(not window.move_mode)

    window.hotkey_pressed.connect(on_hotkey)

    tray = QSystemTrayIcon(_tray_icon())
    tray.setToolTip("Miscrits HUD")
    menu = QMenu()
    for title, hotkey_id in (("Показать/скрыть (F8)", HK_TOGGLE), ("Обновить (F9)", HK_REFRESH),
                             ("Переместить (Ctrl+F8)", HK_MOVE)):
        action = QAction(title, menu)
        action.triggered.connect(lambda _=False, h=hotkey_id: on_hotkey(h))
        menu.addAction(action)
    menu.addSeparator()
    quit_action = QAction("Выход", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.show()
    window.moved.connect(save_position)

    window.show()
    hwnd = int(window.winId())
    hotkeys.set_click_through(hwnd, True)
    for hotkey_id, mods, vk in ((HK_TOGGLE, 0, hotkeys.VK_F8), (HK_REFRESH, 0, hotkeys.VK_F9),
                                (HK_MOVE, hotkeys.MOD_CONTROL, hotkeys.VK_F8)):
        if not hotkeys.register(hwnd, hotkey_id, mods, vk):
            logging.warning("hotkey %s is taken by another program", hotkey_id)
    own_pid = os.getpid()
    game = {"running": False, "checked": float("-inf")}

    def update_placement():
        # HUD держится над игрой, пока активна игра (или сам HUD); в других окнах — прячется.
        now = time.monotonic()
        if now - game["checked"] >= GAME_CHECK_INTERVAL:
            game["running"] = GAME_EXE in hotkeys.running_process_names()
            game["checked"] = now
        name, pid = hotkeys.foreground_process()
        mode = hotkeys.overlay_mode(name, pid == own_pid, game["running"], GAME_EXE)
        show = cfg.visible and mode != "hidden"
        if show and not window.isVisible():
            window.show()
            hotkeys.set_click_through(hwnd, not window.move_mode)
        elif not show and window.isVisible():
            window.hide()
        if show:
            hotkeys.set_topmost(hwnd, mode == "top")

    def tick():
        try:
            _tick()
        except Exception:
            # exe без консоли: без этого ошибка пропадает молча, а окно остаётся пустым
            logging.exception("tick failed")

    def _tick():
        update_placement()
        state = controller.tick()
        if state is not None:
            window.render(state)
            return
        got_icon = False
        while not icons_ready.empty():
            got_icon = icons_ready.get_nowait() is not None or got_icon
        if got_icon:
            window.rerender()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(TICK_MS)
    tick()
    logging.info("HUD started")
    try:
        return app.exec()
    finally:
        for hotkey_id in (HK_TOGGLE, HK_REFRESH, HK_MOVE):
            hotkeys.unregister(hwnd, hotkey_id)
        pool.shutdown(wait=False, cancel_futures=True)
