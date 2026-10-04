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
        outlined = white_text(image)
        if outlined is not None:
            out.append(self._tess.image_to_string(outlined, config=config).strip())
        for variant in (binary, 255 - binary):
            padded = cv2.copyMakeBorder(variant, 20, 20, 20, 20, cv2.BORDER_REPLICATE)
            out.append(self._tess.image_to_string(padded, config=config).strip())
        return out

    def rank(self, image: np.ndarray) -> str | None:
        """Ранг со значка в бою: цветная буква с тёмной обводкой и отдельный «+»."""
        parts = rank_glyph(image)
        if parts is None:
            return parse_rank(self.text(image))
        letter, plus = parts
        padded = cv2.copyMakeBorder(letter, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
        for psm in (10, 7, 8):
            text = self._tess.image_to_string(padded, config=f"--psm {psm} -c tessedit_char_whitelist=SABCDF").strip()
            if text[:1] in "SABCDF" and text:
                return text[0] + ("+" if plus else "")
        return None


def white_text(image: np.ndarray, scale: int = 4):
    """Белые буквы с тёмной обводкой (HP, кнопки способностей) → чёрный текст на белом, или None.
    Фон и иконки отбрасываются: берётся самая многочисленная строка фигур похожего размера."""
    big = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 185)).astype(np.uint8)
    height, width = white.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(white)
    shapes = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if h < 0.15 * height or w > 0.5 * width or area < 30:
            continue
        if (x == 0 and x + w >= width) or (y == 0 and y + h >= height):
            continue  # полоса фона через всю область
        shapes.append(i)
    if not shapes:
        return None
    shapes.sort(key=lambda i: stats[i, cv2.CC_STAT_LEFT])
    groups, current = [], [shapes[0]]
    for i in shapes[1:]:
        prev = current[-1]
        gap = stats[i, cv2.CC_STAT_LEFT] - (stats[prev, cv2.CC_STAT_LEFT] + stats[prev, cv2.CC_STAT_WIDTH])
        if gap < max(stats[i, cv2.CC_STAT_HEIGHT], stats[prev, cv2.CC_STAT_HEIGHT]) * 0.9:
            current.append(i)
        else:
            groups.append(current)
            current = [i]
    groups.append(current)
    text = max(groups, key=lambda g: (len(g), sum(stats[i, cv2.CC_STAT_AREA] for i in g)))
    mask = np.isin(labels, text)
    ys, xs = np.where(mask)
    out = np.where(mask, 0, 255).astype(np.uint8)[max(ys.min() - 10, 0):ys.max() + 10, max(xs.min() - 10, 0):xs.max() + 10]
    return cv2.copyMakeBorder(out, 25, 25, 25, 25, cv2.BORDER_CONSTANT, value=255)


def _flood_outside(free: np.ndarray) -> np.ndarray:
    """Пиксели free (255), достижимые от края картинки, помечаются 128."""
    h, w = free.shape
    mask = np.zeros((h + 2, w + 2), np.uint8)
    edges = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    for x, y in edges:
        if free[y, x] == 255:
            cv2.floodFill(free, mask, (x, y), 128)
    return free


def rank_glyph(image: np.ndarray, scale: int = 6):
    """(картинка буквы чёрным по белому, есть ли «+») или None.
    Значок ранга — заливка внутри тёмной обводки, поэтому берём всё, что обводкой отрезано от краёв."""
    big = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    outline = (hsv[:, :, 2] < 120) & (hsv[:, :, 1] > 70)
    free = np.where(outline, 0, 255).astype(np.uint8)
    inside = (_flood_outside(free) == 255).astype(np.uint8)
    inside = cv2.morphologyEx(inside, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(inside)
    blobs = sorted(range(1, count), key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    if not blobs or stats[blobs[0], cv2.CC_STAT_AREA] < big.shape[0] * big.shape[1] * 0.05:
        return None
    letter_id = blobs[0]
    lx, ly, lw, lh, larea = stats[letter_id]
    plus = False
    for i in blobs[1:]:
        x, y, w, h, area = stats[i]
        # «+»: заметная фигура правее середины буквы, почти квадратная, заполнена примерно наполовину
        if area >= larea * 0.08 and x > lx + lw / 2 and 0.6 < w / h < 1.6 and 0.35 < area / (w * h) < 0.75:
            plus = True
    letter = np.where(labels == letter_id, 0, 255).astype(np.uint8)[ly:ly + lh, lx:lx + lw]
    return letter, plus


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
