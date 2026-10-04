"""Окно разметки: полный снимок локации, точки поиска отмечаются кликами."""

import cv2
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .screen import around, crop

MAX_W, MAX_H = 1500, 820
REMOVE_RADIUS = 40  # px на снимке: правый клик удаляет ближайшую точку в этом радиусе


class _Canvas(QLabel):
    def __init__(self, dialog):
        super().__init__()
        self._dialog = dialog

    def mousePressEvent(self, event):
        self._dialog.clicked(event.position().toPoint(), event.button())


class LocationDialog(QDialog):
    """image — снимок локации (BGR), origin — где он лежит на скриншоте рабочего стола,
    points — уже отмеченные точки (в координатах снимка)."""

    def __init__(self, parent, image, points):
        super().__init__(parent)
        self.setWindowTitle("Разметка точек поиска")
        self.image = image
        self.points = list(points)
        h, w = image.shape[:2]
        self.scale = min(MAX_W / w, MAX_H / h, 1.0)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        self.base = QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()).scaled(
            int(w * self.scale), int(h * self.scale), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        v = QVBoxLayout(self)
        info = QLabel("Левый клик — отметить точку поиска (кликайте прямо в объект). "
                      "Правый клик — убрать ближайшую отметку.")
        info.setWordWrap(True)
        v.addWidget(info)
        self.canvas = _Canvas(self)
        v.addWidget(self.canvas)
        self.counter = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.counter, 1)
        ok = QPushButton("Сохранить")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        v.addLayout(row)
        self._redraw()

    def clicked(self, pos: QPoint, button):
        x, y = int(pos.x() / self.scale), int(pos.y() / self.scale)
        if button == Qt.LeftButton:
            self.points.append((x, y))
        elif button == Qt.RightButton and self.points:
            nearest = min(self.points, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
            if (nearest[0] - x) ** 2 + (nearest[1] - y) ** 2 <= (REMOVE_RADIUS / self.scale) ** 2 * 4:
                self.points.remove(nearest)
        self._redraw()

    def _redraw(self):
        pixmap = QPixmap(self.base)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        for i, (x, y) in enumerate(self.points, 1):
            cx, cy = int(x * self.scale), int(y * self.scale)
            painter.setPen(QPen(QColor("#ff2d55"), 3))
            painter.drawEllipse(QPoint(cx, cy), 14, 14)
            painter.drawText(cx + 16, cy - 10, str(i))
        painter.end()
        self.canvas.setPixmap(pixmap)
        self.counter.setText(f"Отмечено точек: {len(self.points)}")


def spots_from_points(image, origin, points, size):
    """Точки на снимке → Snapshot'ы точек в координатах рабочего стола."""
    from .storage import Snapshot

    ox, oy = origin
    spots = []
    for x, y in points:
        local = around((x, y), size, image.shape)
        spots.append(Snapshot((local[0] + ox, local[1] + oy, local[2], local[3]), crop(image, local).copy()))
    return spots
