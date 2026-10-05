"""Оформление приложения: тёмная тема в цветах HUD (тёмно-синий фон, жёлтый акцент)."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap

BG = "#0f121a"
SURFACE = "#171b26"
SURFACE_2 = "#1f2433"
BORDER = "#2a3042"
TEXT = "#e9ecf3"
MUTED = "#8a93a8"
ACCENT = "#ffd166"
GREEN = "#3ecf8e"
RED = "#ff5d6c"
BLUE = "#5aa9ff"

RARITY_COLORS = {"Legendary": "#ffb547", "Exotic": "#c77dff", "Epic": "#5aa9ff", "Rare": "#3ecf8e", "Common": "#aab2c5"}
TIER_COLORS = {"F": "#ff8a7a", "D": "#ff8a7a", "C": "#ffd166", "B": "#ffd166", "A": "#6be38a", "S": "#6be38a"}

STATE_STYLES = {  # текст статуса -> (цвет точки, фон «пилюли»)
    "running": (GREEN, "#123326"),
    "paused": (ACCENT, "#3a3016"),
    "stopped": (MUTED, SURFACE_2),
}

QSS = f"""
* {{ font-family: 'Segoe UI'; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}

QFrame#header {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QLabel#appTitle {{ font-size: 20px; font-weight: 800; color: {TEXT}; }}
QLabel#appSubtitle {{ color: {MUTED}; }}
QLabel#statusPill {{ border-radius: 14px; padding: 5px 14px; font-weight: 700; }}

QListWidget#sidebar {{ background: {SURFACE}; border: none; border-right: 1px solid {BORDER}; border-radius: 0;
    padding: 10px 10px; outline: 0; }}
QListWidget#sidebar::item {{ color: {MUTED}; padding: 6px 12px; margin: 1px 0; border-radius: 9px; font-size: 14px;
    font-weight: 600; }}
QListWidget#sidebar::item:disabled {{ color: #5d6579; font-size: 11px; font-weight: 700; padding: 16px 12px 4px 12px; }}
QListWidget#sidebar::item:hover:!selected:enabled {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#sidebar::item:selected {{ background: #2a2615; color: {ACCENT}; border-left: 3px solid {ACCENT}; }}

QListWidget#categories {{ background: transparent; border: none; outline: 0; }}
QListWidget#categories::item {{ color: {MUTED}; padding: 6px 14px; margin: 2px 0; border-radius: 10px; font-size: 14px;
    font-weight: 600; }}
QListWidget#categories::item:hover:!selected {{ background: {SURFACE}; color: {TEXT}; }}
QListWidget#categories::item:selected {{ background: {SURFACE}; color: {ACCENT}; border: 1px solid {BORDER}; }}

QLabel#pageTitle {{ font-size: 22px; font-weight: 800; color: {TEXT}; }}
QLabel#savedMark {{ color: {GREEN}; font-weight: 700; }}
QFrame#settingsBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#settingRow {{ background: transparent; border: none; }}
QFrame#settingRow:hover {{ background: #1b2030; border-radius: 14px; }}
QFrame#rowLine {{ background: {BORDER}; border: none; margin: 0 18px; }}
QLabel#settingTitle {{ font-size: 14px; font-weight: 600; color: {TEXT}; background: transparent; }}
QLabel#settingHint {{ color: {MUTED}; font-size: 12px; background: transparent; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 18px; border: none; background: transparent; }}

QTabWidget::pane {{ border: none; background: {BG}; top: -1px; }}
QTabBar {{ background: {SURFACE}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 10px 18px; border: none;
               border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#cardValue {{ font-size: 26px; font-weight: 800; color: {TEXT}; }}
QLabel#cardCaption {{ color: {MUTED}; font-size: 12px; }}
QLabel#sectionTitle {{ font-size: 15px; font-weight: 700; color: {TEXT}; }}
QLabel#hint {{ color: {MUTED}; }}
QLabel#warn {{ color: {ACCENT}; }}
QLabel#ok {{ color: {GREEN}; }}
QLabel#bigState {{ font-size: 15px; color: {TEXT}; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px; padding: 8px 14px;
               font-weight: 600; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #565e72; border-color: {SURFACE_2}; }}
QPushButton#start {{ background: {GREEN}; color: #06140d; border: none; font-size: 15px; }}
QPushButton#start:hover {{ background: #5be0a3; }}
QPushButton#pause {{ background: {ACCENT}; color: #1d1604; border: none; font-size: 15px; }}
QPushButton#pause:hover {{ background: #ffdc85; }}
QPushButton#stop {{ background: {RED}; color: #22070a; border: none; font-size: 15px; }}
QPushButton#stop:hover {{ background: #ff7c88; }}
QPushButton#primary {{ background: {ACCENT}; color: #1d1604; border: none; }}
QPushButton#primary:hover {{ background: #ffdc85; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {SURFACE_2}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 6px 10px; selection-background-color: {ACCENT}; selection-color: #1d1604; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {BORDER}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER}; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QTableWidget, QListWidget, QPlainTextEdit {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    alternate-background-color: #1a1f2b; gridline-color: transparent; }}
QTableWidget::item, QListWidget::item {{ padding: 4px; border: none; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: #2b3350; color: {TEXT}; }}
QTableWidget::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER}; background: {SURFACE_2}; }}
QTableWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px 6px; font-weight: 600; }}
QPlainTextEdit {{ font-family: 'Cascadia Mono', 'Consolas'; font-size: 12px; }}

QGroupBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; margin-top: 18px; padding: 14px 12px 12px 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; top: 2px; color: {TEXT}; font-weight: 700; font-size: 14px; }}

QScrollArea {{ border: none; background: {BG}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4258; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""


def apply(app) -> None:
    """Общее для всего приложения — только стиль и иконка: оверлей HUD рисуется сам и тему не получает."""
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())


def style_window(window) -> None:
    """Тема для окна бота и всех его дочерних окон (диалоги, сообщения)."""
    palette = QPalette()
    for role, color in ((QPalette.Window, BG), (QPalette.Base, SURFACE), (QPalette.AlternateBase, "#1a1f2b"),
                        (QPalette.Text, TEXT), (QPalette.WindowText, TEXT), (QPalette.Button, SURFACE_2),
                        (QPalette.ButtonText, TEXT), (QPalette.Highlight, "#2b3350"), (QPalette.HighlightedText, TEXT),
                        (QPalette.ToolTipBase, SURFACE_2), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, MUTED)):
        palette.setColor(role, QColor(color))
    window.setPalette(palette)
    window.setStyleSheet(QSS)


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(ACCENT))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(pixmap.rect(), 14, 14)
    painter.setPen(QColor("#12141c"))
    painter.setFont(QFont("Segoe UI", 32, QFont.Black))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "M")
    painter.end()
    return QIcon(pixmap)


def pill_style(state: str) -> str:
    dot, bg = STATE_STYLES.get(state, STATE_STYLES["stopped"])
    return f"background: {bg}; color: {dot};"


ELEMENT_COLORS = {"Fire": "#ff7a59", "Water": "#5aa9ff", "Nature": "#5fd068", "Earth": "#c9955c",
                  "Lightning": "#f5d442", "Wind": "#8fd8ec"}


def element_color(element: str) -> str:
    return next((c for name, c in ELEMENT_COLORS.items() if element.startswith(name)), MUTED)


def rank_color(rank: str) -> str:
    return TIER_COLORS.get(rank[:1], MUTED) if rank else MUTED
