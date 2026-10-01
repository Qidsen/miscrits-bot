import os

import pytest

from miscrits_hud.hotkeys import overlay_mode, process_name, running_process_names

GAME = "miscrits.exe"


@pytest.mark.parametrize(
    "foreground, ours, game_running, expected",
    [
        ("miscrits.exe", False, True, "top"),      # играем — панель над игрой
        ("chrome.exe", True, True, "top"),         # фокус на самом HUD (меню трея, перетаскивание)
        ("chrome.exe", False, True, "hidden"),     # ушли в другое окно — не мешаем
        ("explorer.exe", False, False, "normal"),  # игры нет — обычное окно с «Ждём игру…»
        ("", False, True, "hidden"),
    ],
)
def test_overlay_mode(foreground, ours, game_running, expected):
    assert overlay_mode(foreground, ours, game_running, GAME) == expected


def test_process_name_of_self():
    assert process_name(os.getpid()) in ("python.exe", "pythonw.exe")


def test_running_process_names_contains_self():
    assert process_name(os.getpid()) in running_process_names()
