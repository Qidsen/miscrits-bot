"""Общая программа: оверлей HUD + окно бота + трей."""

import logging
import sys

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMessageBox

from miscrits_hud.app import acquire_single_instance, setup_logging, start_hud
from miscrits_hud.config import app_dir

from . import theme
from .gui import MainWindow
from .settings import bot_dir, load_settings
from .storage import load_teaching


def main() -> int:
    hud_home = app_dir()
    setup_logging(hud_home)
    home = bot_dir()
    bot_log = logging.FileHandler(home / "bot.log", encoding="utf-8")
    bot_log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("mbot").addHandler(bot_log)
    logging.getLogger("mbot").setLevel(logging.INFO)

    app = QApplication(sys.argv)
    app.setApplicationName("Miscrits")
    theme.apply(app)
    lock = acquire_single_instance(hud_home)
    if lock is None:
        QMessageBox.warning(None, "Miscrits", "Программа уже запущена (или открыт старый MiscritsHUD.exe — закройте его).")
        return 0
    hud = start_hud(app, hud_home)

    teaching_path = home / "teaching.json"
    settings_path = home / "settings.json"
    window = MainWindow(hud, load_teaching(teaching_path), teaching_path, load_settings(settings_path),
                        settings_path, home)
    window.show()
    window.register_hotkeys()

    def show_window():
        window.showNormal()
        window.raise_()
        window.activateWindow()

    open_action = QAction("Открыть бота", hud.menu)
    open_action.triggered.connect(show_window)
    first = hud.menu.actions()[0] if hud.menu.actions() else None
    hud.menu.insertAction(first, open_action)
    if first is not None:
        hud.menu.insertSeparator(first)
    app.setQuitOnLastWindowClosed(False)  # окно закрыто — HUD живёт в трее, выход через меню трея
    logging.getLogger("mbot").info("app started")
    try:
        return app.exec()
    finally:
        window.unregister_hotkeys()
        if window.bot and window.bot.running:
            window.bot.stop()
        hud.shutdown()


if __name__ == "__main__":
    sys.exit(main())
