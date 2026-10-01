"""Склейка: таймер → Controller.tick() → OverlayWindow.render()."""

import logging
import queue
import sys
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import hotkeys
from .catalog import CatalogCache
from .config import app_dir, game_data_dir, load_cache, load_config, save_cache, save_config
from .controller import Controller
from .game_api import fetch_player
from .icons import IconStore
from .log_watcher import LogWatcher
from .overlay import OverlayWindow

HK_TOGGLE, HK_REFRESH, HK_MOVE = 1, 2, 3
TICK_MS = 500


def main() -> int:
    home = app_dir()
    logging.basicConfig(
        filename=home / "hud.log", level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    game = game_data_dir()
    app = QApplication(sys.argv)
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
    window.move(cfg.x if cfg.x is not None else screen.right() - window.width() - 20,
                cfg.y if cfg.y is not None else screen.top() + 20)

    def save_position():
        cfg.x, cfg.y = window.x(), window.y()
        save_config(config_path, cfg)

    def on_hotkey(hotkey_id):
        if hotkey_id == HK_TOGGLE:
            cfg.visible = not window.isVisible()
            window.setVisible(cfg.visible)
            save_config(config_path, cfg)
        elif hotkey_id == HK_REFRESH:
            controller.request_refresh()
        elif hotkey_id == HK_MOVE:
            window.set_move_mode(not window.move_mode)

    window.hotkey_pressed.connect(on_hotkey)
    window.moved.connect(save_position)

    window.show()
    hwnd = int(window.winId())
    hotkeys.set_click_through(hwnd, True)
    for hotkey_id, mods, vk in ((HK_TOGGLE, 0, hotkeys.VK_F8), (HK_REFRESH, 0, hotkeys.VK_F9),
                                (HK_MOVE, hotkeys.MOD_CONTROL, hotkeys.VK_F8)):
        if not hotkeys.register(hwnd, hotkey_id, mods, vk):
            logging.warning("hotkey %s is taken by another program", hotkey_id)
    if not cfg.visible:
        window.hide()

    def tick():
        if window.isVisible():
            hotkeys.keep_on_top(hwnd)
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
