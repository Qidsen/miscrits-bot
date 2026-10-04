"""Скриншоты, поиск картинок и чтение текста с экрана."""

import difflib
import re
import threading

import cv2
import mss
import numpy as np

SEARCH_MARGIN = 160  # элементы UI ищем рядом с местом, где их показали, — быстрее и меньше ложных находок

_local = threading.local()


def _mss():
    if not hasattr(_local, "mss"):
        _local.mss = getattr(mss, "MSS", mss.mss)()  # mss не любит, когда один объект дёргают из разных потоков
    return _local.mss


def origin() -> tuple:
    """Левый верхний угол всего рабочего стола. При двух мониторах может быть отрицательным:
    координаты картинок отсчитываются от него, координаты мыши — от основного монитора."""
    desktop = _mss().monitors[0]
    return desktop["left"], desktop["top"]


def grab() -> np.ndarray:
    """Скриншот всего рабочего стола (все мониторы) в BGR — игра может быть на любом."""
    shot = _mss().grab(_mss().monitors[0])
    return np.asarray(shot)[:, :, :3].copy()


def to_image(point) -> tuple:
    """Позиция курсора → координаты на скриншоте."""
    ox, oy = origin()
    return point[0] - ox, point[1] - oy


def to_screen(rect) -> tuple:
    """Прямоугольник на скриншоте → координаты для мыши."""
    ox, oy = origin()
    x, y, w, h = rect
    return x + ox, y + oy, w, h


def crop(image: np.ndarray, rect) -> np.ndarray:
    x, y, w, h = rect
    return image[max(y, 0):y + h, max(x, 0):x + w]


def around(point, size: int, image_shape) -> tuple:
    """Квадрат size×size с центром в точке, прижатый к краям экрана."""
    height, width = image_shape[:2]
    x = min(max(point[0] - size // 2, 0), max(width - size, 0))
    y = min(max(point[1] - size // 2, 0), max(height - size, 0))
    return x, y, min(size, width), min(size, height)


def find(image: np.ndarray, template: np.ndarray, threshold: float, near=None):
    """Прямоугольник лучшего совпадения шаблона (x, y, w, h) или None.
    near — исходный rect шаблона: ищем только в его окрестности."""
    ox = oy = 0
    area = image
    if near is not None:
        x, y, w, h = near
        ox, oy = max(x - SEARCH_MARGIN, 0), max(y - SEARCH_MARGIN, 0)
        area = image[oy:y + h + SEARCH_MARGIN, ox:x + w + SEARCH_MARGIN]
    th, tw = template.shape[:2]
    if area.shape[0] < th or area.shape[1] < tw:
        return None
    result = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (mx, my) = cv2.minMaxLoc(result)
    if score < threshold:
        return None
    return ox + mx, oy + my, tw, th


class Ocr:
    def __init__(self, tesseract_cmd: str):
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        self._tess = pytesseract

    def available(self) -> bool:
        try:
            self._tess.get_tesseract_version()
            return True
        except Exception:
            return False

    def text(self, image: np.ndarray, whitelist: str | None = None) -> list:
        """Варианты распознанного текста: для светлого и для тёмного текста."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        config = "--psm 7"
        if whitelist:
            config += f" -c tessedit_char_whitelist={whitelist}"
        out = []
        for variant in (binary, 255 - binary):
            padded = cv2.copyMakeBorder(variant, 20, 20, 20, 20, cv2.BORDER_REPLICATE)
            out.append(self._tess.image_to_string(padded, config=config).strip())
        return out


def parse_hp(texts) -> tuple | None:
    """'73/73' → (73, 73)."""
    for t in texts:
        m = re.search(r"(\d+)\s*/\s*(\d+)", t)
        if m and 0 <= int(m.group(1)) <= int(m.group(2)) > 0:
            return int(m.group(1)), int(m.group(2))
    return None


def parse_percent(texts) -> int | None:
    for t in texts:
        m = re.search(r"(\d{1,3})\s*%?", t)
        if m and 0 <= int(m.group(1)) <= 100:
            return int(m.group(1))
    return None


RANK_RE = re.compile(r"\b([SABCDF])\s*([+-]?)")


def parse_rank(texts) -> str | None:
    for t in texts:
        m = RANK_RE.search(t.upper().replace("5", "S").replace("8", "B"))
        if m:
            rank = m.group(1) + ("+" if m.group(2) == "+" else "")
            return rank
    return None


def best_name(texts, names, cutoff: float = 0.7) -> str | None:
    """Ближайшее имя из списка; OCR путает буквы, поэтому сравнение нечёткое."""
    lowered = {n.lower(): n for n in names}
    best, score = None, 0.0
    for t in texts:
        t = re.sub(r"[^a-z ]", "", t.lower()).strip()
        if not t:
            continue
        for match in difflib.get_close_matches(t, lowered, n=1, cutoff=cutoff):
            s = difflib.SequenceMatcher(None, t, match).ratio()
            if s > score:
                best, score = lowered[match], s
    return best
