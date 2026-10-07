"""Прозрачное окно поверх игры: заголовок зоны и строки мискритов."""

from ctypes import wintypes

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import hotkeys
from .model import format_days, format_ranks
from .rank import rank_tier

ICON = 40
WIDTH = 320
TIER_COLORS = {"low": "#ff8a7a", "mid": "#ffd166", "high": "#6be38a"}
ELEMENT_COLORS = {
    "Fire": "#e8553d", "Water": "#3d8be8", "Nature": "#4caf50", "Earth": "#a0703d",
    "Lightning": "#e8c22e", "Wind": "#7fc8e0",
}


ELEMENT_NAMES = {"Fire": "Огонь", "Water": "Вода", "Nature": "Природа", "Earth": "Земля",
                 "Lightning": "Молния", "Wind": "Ветер"}
RARITY = {  # редкость → (как подписать, цвет)
    "Legendary": ("Легенда", "#ffb547"), "Exotic": ("Экзотик", "#c77dff"), "Epic": ("Эпик", "#5aa9ff"),
    "Rare": ("Редкий", "#3ecf8e"), "Common": ("Обычный", "#aab2c5"),
}


def _element_color(element: str) -> str:
    # Гибридные стихии вроде "FireWind" красим по первой.
    return next((color for name, color in ELEMENT_COLORS.items() if element.startswith(name)), "#666666")


def split_element(element: str) -> list:
    """«NatureEarth» → [«Nature», «Earth»]."""
    parts, rest = [], element or ""
    while rest:
        name = next((n for n in ELEMENT_NAMES if rest.startswith(n)), None)
        if name is None:
            return parts or [element]
        parts.append(name)
        rest = rest[len(name):]
    return parts


def _kind_html(rarity: str, element: str, dim: bool) -> str:
    """«Экзотик · Природа / Земля» — редкость и стихии своими цветами (приглушённо у непойманных)."""
    label, color = RARITY.get(rarity, (rarity, "#9a9a9a"))
    tone = _muted if dim else (lambda c: c)
    elements = " / ".join(f"<span style='color:{tone(ELEMENT_COLORS.get(p, '#9a9a9a'))}'>{ELEMENT_NAMES.get(p, p)}</span>"
                          for p in split_element(element))
    return (f"<span style='font-size:11px'><span style='color:{tone(color)}'>{label}</span>"
            f"<span style='color:#7a7a7a'> · </span>{elements}</span>")


def _muted(color: str) -> str:
    """Цвет наполовину к серому — для непойманных и не сегодняшних (Qt в подписях не понимает opacity)."""
    c = QColor(color)
    grey = 110
    return QColor((c.red() + grey) // 2, (c.green() + grey) // 2, (c.blue() + grey) // 2).name()


def clamp_position(pos: QPoint, size, available) -> QPoint:
    """Держит панель внутри рабочей области экрана (без панели задач)."""
    x = max(available.left(), min(pos.x(), available.right() + 1 - size.width()))
    y = max(available.top(), min(pos.y(), available.bottom() + 1 - size.height()))
    return QPoint(x, y)


def _ranks_html(ranks) -> str:
    if not ranks:
        return '<span style="color:#9a9a9a">не пойман</span>'
    shown, _, extra = format_ranks(ranks).partition(" +")
    html = " · ".join(f'<span style="color:{TIER_COLORS[rank_tier(r)]}">{r}</span>' for r in shown.split(" · "))
    return f'{html} <span style="color:#9a9a9a">+{extra}</span>' if extra else html


def _fallback_pixmap(name: str, element: str) -> QPixmap:
    pixmap = QPixmap(ICON, ICON)
    pixmap.fill(QColor(_element_color(element)))
    painter = QPainter(pixmap)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Segoe UI", 16, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, name[:1].upper())
    painter.end()
    return pixmap


def _icon_pixmap(data, name: str, element: str, caught: bool) -> QPixmap:
    image = QImage()
    if not data or not image.loadFromData(data):
        image = _fallback_pixmap(name, element).toImage()
    image = image.scaled(ICON, ICON, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if caught:
        return QPixmap.fromImage(image)
    gray = image.convertToFormat(QImage.Format_Grayscale8).convertToFormat(QImage.Format_ARGB32)
    faded = QPixmap(gray.size())
    faded.fill(Qt.transparent)
    painter = QPainter(faded)
    painter.setOpacity(0.4)
    painter.drawImage(0, 0, gray)
    painter.end()
    return faded


class OverlayWindow(QWidget):
    hotkey_pressed = Signal(int)
    moved = Signal()

    def __init__(self, icon_lookup):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)  # «поверх» включает app только над игрой
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(WIDTH)
        self._icon_lookup = icon_lookup
        self._state = None
        self._move_mode = False
        self._drag_from = None
        self._anchor = None  # где пользователь поставил панель; при росте вниз сдвигаемся вверх

        self._panel = QWidget(self)
        self._panel.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._panel)
        self._layout = QVBoxLayout(self._panel)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(4)
        self._apply_style()

    def place(self, pos: QPoint):
        self._anchor = QPoint(pos)
        self._apply_position()

    def _apply_position(self):
        screen = self.screen()
        if self._anchor is None or screen is None:
            return
        self.move(clamp_position(self._anchor, self.size(), screen.availableGeometry()))

    @property
    def move_mode(self) -> bool:
        return self._move_mode

    def _apply_style(self):
        border = "#ffd166" if self._move_mode else "transparent"
        self._panel.setStyleSheet(
            f"#panel {{ background: rgba(18, 20, 28, 200); border-radius: 8px; border: 2px solid {border}; }}"
            "QLabel { color: #f0f0f0; font-family: 'Segoe UI'; font-size: 13px; background: transparent; }"
        )

    def render(self, state):
        self._state = state
        self.rerender()

    def rerender(self):
        while self._layout.count():
            widget = self._layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)  # сразу убрать из дерева, deleteLater удалит позже
                widget.deleteLater()
        state = self._state
        if state is None:
            return
        if state.view is not None and state.view.location_name:
            view = state.view
            self._layout.addWidget(QLabel(
                f"<b>{view.location_name} · зона {view.area_id}</b>"
                f"<span style='color:#9a9a9a'>&nbsp;&nbsp;{view.caught_count}/{view.total} сегодня</span>"
            ))
            for row in view.today_rows:
                self._layout.addWidget(self._row_widget(row))
            if view.other_rows:
                divider = QLabel("<span style='color:#9a9a9a'>В другие дни</span>")
                divider.setStyleSheet("border-top: 1px solid rgba(255,255,255,40); padding-top: 4px; margin-top: 2px;")
                self._layout.addWidget(divider)
                for row in view.other_rows:
                    self._layout.addWidget(self._row_widget(row))
        if state.message:
            self._layout.addWidget(QLabel(f"<span style='color:#c8c8c8'>{state.message}</span>"))
        if state.note:
            note = QLabel(f"<span style='color:#ffd166'>{state.note}</span>")
            note.setWordWrap(True)
            self._layout.addWidget(note)
        self._fit()

    def _fit(self):
        # Qt пересчитывает раскладку отложенно — без этого окно не уменьшается, когда строк стало меньше.
        self._layout.activate()
        self.layout().activate()
        self.adjustSize()
        self._apply_position()

    def showEvent(self, event):
        # Строки, добавленные пока окно было спрятано, Qt считал пустыми — пересчитываем размер при показе.
        super().showEvent(event)
        self._fit()

    def _row_widget(self, row) -> QWidget:
        widget = QWidget()
        line = QHBoxLayout(widget)
        line.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        # Не сегодняшние приглушены так же, как непойманные: ловить их сейчас нельзя.
        icon.setPixmap(_icon_pixmap(self._icon_lookup(row.name), row.name, row.element, row.caught and row.today))
        icon.setFixedSize(ICON, ICON)
        title = row.name if row.caught and row.today else f"<span style='color:#8a8a8a'>{row.name}</span>"
        title += "<br>" + _kind_html(row.rarity, row.element, not (row.caught and row.today))
        if not row.today:
            title += f"<br><span style='color:#7a7a7a; font-size:11px'>{format_days(row.days)}</span>"
        name = QLabel(title)
        ranks = QLabel(_ranks_html(row.ranks))
        ranks.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        line.addWidget(icon)
        line.addWidget(name, 1)
        line.addWidget(ranks)
        return widget

    # --- перемещение и клики насквозь ---

    def set_move_mode(self, on: bool):
        self._move_mode = on
        hotkeys.set_click_through(int(self.winId()), not on)
        self.setCursor(Qt.SizeAllCursor if on else Qt.ArrowCursor)
        self._apply_style()
        if not on:
            self.place(self.pos())
            self.moved.emit()

    def mousePressEvent(self, event):
        if self._move_mode and event.button() == Qt.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._move_mode and self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, event):
        self._drag_from = None

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == hotkeys.WM_HOTKEY:
            self.hotkey_pressed.emit(int(msg.wParam))
            return True, 0
        return super().nativeEvent(event_type, message)
