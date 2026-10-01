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
    window.render(UiState(view, None, "⚠ нет связи · обновлено 3 мин назад"))
    assert "обновлено 3 мин назад" in " ".join(texts(window))


def test_rerender_replaces_rows(qapp):
    window = OverlayWindow(icon_lookup=lambda name: None)
    view = ZoneView("Forest", 1, (Row(1, "Flue", "Fire", "Common", ()),))
    window.render(UiState(view, None, None))
    window.rerender()
    assert sum("Flue" in t for t in texts(window)) == 1


def test_clamp_keeps_panel_off_taskbar():
    from PySide6.QtCore import QPoint, QRect, QSize

    from miscrits_hud.overlay import clamp_position

    available = QRect(0, 0, 2560, 1392)  # экран 1440 минус панель задач 48
    # панель 320×650, поставлена низко — вылезла бы на панель задач
    assert clamp_position(QPoint(2231, 781), QSize(320, 650), available) == QPoint(2231, 742)
    # вылезает за правый и левый/верхний край
    assert clamp_position(QPoint(2500, -20), QSize(320, 300), available) == QPoint(2240, 0)
    # влезает — не трогаем
    assert clamp_position(QPoint(100, 100), QSize(320, 300), available) == QPoint(100, 100)
    # выше экрана — прижимаем к верху
    assert clamp_position(QPoint(100, 100), QSize(320, 2000), available) == QPoint(100, 0)


def test_panel_returns_to_anchor_when_it_shrinks(qapp):
    from PySide6.QtCore import QPoint

    window = OverlayWindow(icon_lookup=lambda name: None)
    bottom = window.screen().availableGeometry().bottom()
    window.place(QPoint(0, bottom - 200))
    tall = ZoneView("Forest", 1, tuple(Row(i, f"M{i}", "Fire", "Common", ()) for i in range(12)))
    window.render(UiState(tall, None, None))
    assert window.y() + window.height() - 1 <= bottom
    window.render(UiState(ZoneView("Forest", 1, (Row(1, "Flue", "Fire", "Common", ()),)), None, None))
    assert window.y() == bottom - 200
