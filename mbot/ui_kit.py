"""Элементы интерфейса: тумблер, боковое меню, строка настройки."""

from PySide6.QtCore import QPropertyAnimation, QRectF, QSize, Qt, Property, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QSpinBox, QVBoxLayout, QWidget,
)

from . import theme


class Toggle(QAbstractButton):
    """Переключатель-«тумблер» вместо галочки: плавно ездит и подсвечивается акцентом."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get(self):
        return self._pos

    def _set(self, value):
        self._pos = value
        self.update()

    knob = Property(float, _get, _set)

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off, on = QColor(theme.SURFACE_2), QColor(theme.ACCENT)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos))
        p.setPen(QColor(theme.BORDER) if self._pos < 0.5 else Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 13, 13)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1d1604") if self._pos > 0.5 else QColor(theme.MUTED))
        x = 3 + (self.width() - 26) * self._pos
        p.drawEllipse(QRectF(x, 3, 20, 20))
        p.end()


class Sidebar(QListWidget):
    """Боковое меню с разделами. pages — [(раздел, [(значок, название, ключ)])]; page_selected(ключ)."""

    page_selected = Signal(str)

    def __init__(self, sections, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(220)
        self.setFocusPolicy(Qt.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._keys = []
        for title, pages in sections:
            header = QListWidgetItem(title.upper())
            header.setFlags(Qt.NoItemFlags)
            header.setData(Qt.UserRole + 1, "header")
            self.addItem(header)
            self._keys.append(None)
            for icon, name, key in pages:
                item = QListWidgetItem(f"{icon}   {name}")
                item.setSizeHint(QSize(200, 40))
                self.addItem(item)
                self._keys.append(key)
        self.currentRowChanged.connect(self._changed)

    def _changed(self, row):
        if 0 <= row < len(self._keys) and self._keys[row] is not None:
            self.page_selected.emit(self._keys[row])

    def select(self, key):
        if key in self._keys:
            self.setCurrentRow(self._keys.index(key))


def setting_control(value, field, on_change):
    """Поле под тип значения: тумблер, число с единицами или строка. on_change(новое значение)."""
    if isinstance(value, bool):
        control = Toggle(value)
        control.toggled.connect(on_change)
        return control
    if isinstance(value, int):
        control = QSpinBox()
        control.setRange(int(field.low), int(field.high))
        control.setSingleStep(int(field.step) or 1)
        control.setValue(value)
        control.valueChanged.connect(on_change)
    elif isinstance(value, float):
        control = QDoubleSpinBox()
        control.setRange(field.low, field.high)
        control.setDecimals(2)
        control.setSingleStep(field.step)
        control.setValue(value)
        control.valueChanged.connect(on_change)
    else:
        control = QLineEdit(str(value))
        control.setMinimumWidth(320)
        control.editingFinished.connect(lambda: on_change(control.text()))
        return control
    control.setButtonSymbols(QSpinBox.UpDownArrows)
    control.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    control.setFixedWidth(130)
    if field.unit:
        control.setSuffix(f"  {field.unit}")
    return control


def setting_row(field, control) -> QWidget:
    """Строка настройки: название и пояснение слева, поле справа."""
    row = QFrame()
    row.setObjectName("settingRow")
    h = QHBoxLayout(row)
    h.setContentsMargins(18, 14, 18, 14)
    h.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(3)
    title = QLabel(field.title)
    title.setObjectName("settingTitle")
    text.addWidget(title)
    if field.hint:
        hint = QLabel(field.hint)
        hint.setObjectName("settingHint")
        hint.setWordWrap(True)
        text.addWidget(hint)
    h.addLayout(text, 1)
    h.addWidget(control, 0, Qt.AlignVCenter)
    return row
