import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from miscrits_hud.controller import UiState
from miscrits_hud.model import Row, ZoneView
from miscrits_hud.overlay import OverlayWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def texts(window):
    return [label.text() for label in window.findChildren(QLabel)]


def test_renders_rows_and_header(qapp):
    window = OverlayWindow(icon_lookup=lambda name: None)
    view = ZoneView("Hidden Forest", 1, (
        Row(1, "Flue", "Fire", "Common", ("S+", "C")),
        Row(2, "Prawnja", "Water", "Common", ()),
    ))
    window.render(UiState(view, None, None))
    all_text = " ".join(texts(window))
    assert "Hidden Forest · зона 1" in all_text
    assert "1/2" in all_text
    assert "Flue" in all_text and "S+" in all_text
    assert "не пойман" in all_text


def test_renders_message_and_stale(qapp):
    window = OverlayWindow(icon_lookup=lambda name: None)
    window.render(UiState(None, "Ждём игру…", None))
    assert "Ждём игру…" in " ".join(texts(window))
    view = ZoneView("Forest", 2, (Row(1, "Flue", "Fire", "Common", ("A",)),))
    window.render(UiState(view, None, 3))
    assert "обновлено 3 мин назад" in " ".join(texts(window))


def test_rerender_replaces_rows(qapp):
    window = OverlayWindow(icon_lookup=lambda name: None)
    view = ZoneView("Forest", 1, (Row(1, "Flue", "Fire", "Common", ()),))
    window.render(UiState(view, None, None))
    window.rerender()
    assert sum("Flue" in t for t in texts(window)) == 1
