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


def monitors_on_image() -> list:
    """Прямоугольники мониторов (x, y, w, h) в координатах скриншота рабочего стола."""
    ox, oy = origin()
    return [(m["left"] - ox, m["top"] - oy, m["width"], m["height"]) for m in _mss().monitors[1:]]


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


def find_scored(image: np.ndarray, template: np.ndarray, near=None) -> tuple:
    """(прямоугольник лучшего совпадения (x, y, w, h), оценка 0..1); прямоугольник None, если искать негде.
    near — исходный rect шаблона: ищем только в его окрестности."""
    ox = oy = 0
    area = image
    if near is not None:
        x, y, w, h = near
        ox, oy = max(x - SEARCH_MARGIN, 0), max(y - SEARCH_MARGIN, 0)
        area = image[oy:y + h + SEARCH_MARGIN, ox:x + w + SEARCH_MARGIN]
    th, tw = template.shape[:2]
    if area.shape[0] < th or area.shape[1] < tw:
        return None, 0.0
    result = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (mx, my) = cv2.minMaxLoc(result)
    return (ox + mx, oy + my, tw, th), float(score)


def find(image: np.ndarray, template: np.ndarray, threshold: float, near=None):
    """Прямоугольник лучшего совпадения шаблона или None, если оно хуже порога."""
    rect, score = find_scored(image, template, near)
    return rect if rect is not None and score >= threshold else None


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

    def _variants(self, image: np.ndarray, whitelist: str | None):
        """Распознанный текст разными способами, по одному. Tesseract капризен: одну и ту же надпись он
        иногда читает только как «слово» (psm 8), а не как «строку» (psm 7), только по серому, а не по
        чёрно-белому, поэтому пробуем по очереди, пока не получится что-то осмысленное."""
        extra = f" -c tessedit_char_whitelist={whitelist}" if whitelist else ""
        outlined = white_text(image)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        pictures = [outlined] if outlined is not None else []
        pictures += [cv2.copyMakeBorder(v, 20, 20, 20, 20, cv2.BORDER_REPLICATE) for v in (gray, binary, 255 - binary)]
        for psm in (7, 8):
            for picture in pictures:
                yield self._tess.image_to_string(picture, config=f"--psm {psm}{extra}").strip()

    def read(self, image: np.ndarray, parse, whitelist: str | None = None):
        """Первое значение parse([текст]), которое не None, перебирая способы распознавания."""
        for text in self._variants(image, whitelist):
            value = parse([text])
            if value is not None:
                return value
        return None

    def text(self, image: np.ndarray, whitelist: str | None = None) -> list:
        """Все варианты распознанного текста (для проверки экрана и старых вызовов)."""
        return list(self._variants(image, whitelist))

    def rank(self, image: np.ndarray) -> str | None:
        """Ранг со значка в бою: цветная буква с тёмной обводкой и отдельный «+»."""
        parts = rank_glyph(image)
        if parts is None:
            return parse_rank(self.text(image))
        letter, plus = parts
        # слишком крупную букву Tesseract не узнаёт — приводим к высоте около 50 px
        k = 50 / letter.shape[0]
        letter = cv2.resize(letter, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        _, letter = cv2.threshold(letter, 127, 255, cv2.THRESH_BINARY)
        padded = cv2.copyMakeBorder(letter, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
        guesses = []
        for psm in (10, 7, 8):
            text = self._tess.image_to_string(padded, config=f"--psm {psm} -c tessedit_char_whitelist=SABCDF").strip()
            if text and text[0] in "SABCDF":
                guesses.append(text[0])
        found = letter_by_shape(letter, guesses)
        return found + ("+" if plus else "") if found else None


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


def letter_holes(letter: np.ndarray) -> list:
    """Площади «дырок» буквы (чёрная буква на белом) относительно её рамки; мелкие блики не в счёт."""
    background = (letter > 127).astype(np.uint8) * 255
    inside = (_flood_outside(background.copy()) == 255).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(inside)
    total = letter.shape[0] * letter.shape[1]
    return [stats[i, cv2.CC_STAT_AREA] / total for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] / total >= 0.03]


# сколько дырок у буквы ранга: по этому числу проверяем (и, если надо, исправляем) Tesseract
HOLES = {"B": 2, "A": 1, "D": 1, "C": 0, "F": 0, "S": 0}


def letter_by_shape(letter: np.ndarray, guesses) -> str | None:
    holes = letter_holes(letter)
    for g in guesses:
        if HOLES.get(g) == len(holes):
            return g
    if len(holes) >= 2:
        return "B"
    if len(holes) == 1:
        # у D дырка большая и по центру, у A — маленький треугольник вверху
        return "D" if holes[0] >= 0.12 else "A"
    return guesses[0] if guesses else None


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
    # обводка — тёмный вариант цвета буквы; порог считаем от яркости заливки, иначе буквы с тёмной
    # заливкой (у низких рангов) целиком сливаются с обводкой
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    h, w = val.shape
    middle = (slice(h // 4, 3 * h // 4), slice(w // 6, 2 * w // 3))
    colored = val[middle][sat[middle] > 70]
    fill = float(np.percentile(colored, 80)) if colored.size else 200.0
    outline = ((val < 0.62 * fill) & (sat > 50)) | (val < 50)
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
