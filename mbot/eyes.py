"""Что бот видит: обученные элементы на текущем скриншоте и прочитанные с них значения."""

import cv2
import numpy as np

from .screen import best_name, crop, find, find_scored, grab, parse_hp, parse_percent

SPOT_THRESHOLD = 0.7  # точки поиска — куски пейзажа, им можно чуть меньше точности
SPOT_MARGIN = 400  # камера ходит за персонажем, поэтому точка может сдвинуться заметно
PIECE_THRESHOLD = 0.8
OBJECT_DIFF = 60  # насколько пиксель объекта отличается от фона по краям снимка


def object_mask(image):
    """Пиксели самого объекта на снимке точки: то, что заметно отличается от фона по краям снимка."""
    ref = image.astype(np.int16)
    border = np.concatenate([ref[0], ref[-1], ref[:, 0], ref[:, -1]])
    return np.abs(ref - np.median(border, axis=0)).max(axis=2) > OBJECT_DIFF


def object_center(image):
    """Центр объекта на снимке точки (x, y) — того пятна, что ближе к центру снимка
    (при обучении курсор стоял на объекте). None, если объект не выделяется."""
    mask = object_mask(image).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    h, w = mask.shape
    best, best_d = None, None
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < 40:
            continue
        cx, cy = centroids[i]
        d = (cx - w / 2) ** 2 + (cy - h / 2) ** 2
        if best_d is None or d < best_d:
            best, best_d = (int(cx), int(cy)), d
    if best is None:
        return None
    # центроид «рогалика» может лежать вне объекта — берём ближайший к нему пиксель самого объекта
    ys, xs = np.nonzero(labels == labels[best[1], best[0]] if labels[best[1], best[0]] else mask)
    k = int(np.argmin((xs - best[0]) ** 2 + (ys - best[1]) ** 2))
    return int(xs[k]), int(ys[k])


def object_pieces(image):
    """Кусочки снимка точки, где почти всё — сам объект, а не трава: (dx, dy, картинка)."""
    ref = image.astype(np.int16)
    h, w = image.shape[:2]
    border = np.concatenate([ref[0], ref[-1], ref[:, 0], ref[:, -1]])
    thing = np.abs(ref - np.median(border, axis=0)).max(axis=2) > OBJECT_DIFF
    ys, xs = np.nonzero(thing)
    if len(xs) < 50:
        return []
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    pieces = []
    for parts in (3, 4):  # чем сильнее закрыто, тем мельче нужны кусочки
        pw, ph = max((x1 - x0) // parts, 12), max((y1 - y0) // parts, 12)
        steps = [i / parts for i in range(parts + 1)]
        for fy in steps:
            for fx in steps:
                px, py = int(x0 + fx * (x1 - x0 - pw)), int(y0 + fy * (y1 - y0 - ph))
                if thing[py:py + ph, px:px + pw].mean() >= 0.6:
                    pieces.append((px, py, image[py:py + ph, px:px + pw]))
    return pieces


ANCHOR_SIZE = 200  # куски пейзажа со снимка локации, по которым ищем сдвиг камеры
ANCHOR_COUNT = 10
ANCHOR_THRESHOLD = 0.8
SHIFT_MARGIN = 600  # насколько далеко камера могла уехать от снимка
AGREE_PX = 10
CLICK_HALF = 14


def pick_anchors(image, count: int = ANCHOR_COUNT, size: int = ANCHOR_SIZE) -> list:
    """Самые «узорчатые» непересекающиеся квадраты снимка (x, y) — их проще всего узнать снова."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detail = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    h, w = gray.shape
    step = size // 2
    candidates = []
    for y in range(0, h - size + 1, step):
        for x in range(0, w - size + 1, step):
            candidates.append((float(detail[y:y + size, x:x + size].mean()), x, y))
    candidates.sort(reverse=True)
    chosen = []
    for _, x, y in candidates:
        if all(abs(x - cx) >= size or abs(y - cy) >= size for cx, cy in chosen):
            chosen.append((x, y))
        if len(chosen) == count:
            break
    return chosen


def camera_shift(image, location, anchors) -> tuple | None:
    """(dx, dy) — на сколько сдвинулся пейзаж относительно снимка локации, или None (другой экран).
    Ищем в половинном разрешении — так в разы быстрее, а точности в пару пикселей для клика хватает."""
    lx, ly = location.rect[:2]
    half = cv2.resize(image, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    shifts = []
    for ax, ay in anchors:
        patch = location.image[ay:ay + ANCHOR_SIZE, ax:ax + ANCHOR_SIZE]
        patch = cv2.resize(patch, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        gx, gy = (lx + ax) // 2, (ly + ay) // 2
        m = SHIFT_MARGIN // 2
        x0, y0 = max(gx - m, 0), max(gy - m, 0)
        area = half[y0:gy + patch.shape[0] + m, x0:gx + patch.shape[1] + m]
        if area.shape[0] < patch.shape[0] or area.shape[1] < patch.shape[1]:
            continue
        result = cv2.matchTemplate(area, patch, cv2.TM_CCOEFF_NORMED)
        _, score, _, (mx, my) = cv2.minMaxLoc(result)
        if score >= ANCHOR_THRESHOLD:
            shifts.append(((x0 + mx - gx) * 2, (y0 + my - gy) * 2))
    # верим сдвигу, о котором договорились хотя бы два куска (персонаж и анимации путают отдельные)
    best = []
    for dx, dy in shifts:
        group = [(sx, sy) for sx, sy in shifts if abs(sx - dx) <= AGREE_PX and abs(sy - dy) <= AGREE_PX]
        if len(group) > len(best):
            best = group
    if len(best) < 2:
        return None
    return int(np.median([g[0] for g in best])), int(np.median([g[1] for g in best]))


class Eyes:
    def __init__(self, teaching, ocr, threshold: float, grabber=grab):
        self.teaching = teaching
        self.ocr = ocr
        self.threshold = threshold
        self._grab = grabber
        self.image = None
        self._anchors = None
        self._anchors_for = None
        self._shift = None
        self._shift_ready = False

    def look(self):
        self.image = self._grab()
        self._shift_ready = False
        return self.image

    def shift(self):
        """Сдвиг камеры относительно снимка локации на текущем кадре (считается раз на кадр)."""
        location = self.teaching.location
        if location is None or self.image is None:
            return None
        if self._anchors_for is not location:
            self._anchors = pick_anchors(location.image)
            self._anchors_for = location
        if not self._shift_ready:
            self._shift = camera_shift(self.image, location, self._anchors)
            self._shift_ready = True
        return self._shift

    def knows(self, element_id: str) -> bool:
        return element_id in self.teaching.elements

    def sees(self, element_id: str):
        """Прямоугольник элемента-кнопки на текущем скриншоте или None."""
        snap = self.teaching.elements.get(element_id)
        if snap is None or snap.image is None:
            return None
        return find(self.image, snap.image, self.threshold, near=snap.rect)

    def sees_snap(self, snap, threshold=None, anywhere=False):
        return find(self.image, snap.image, threshold or self.threshold, near=None if anywhere else snap.rect)

    def locate_spot(self, snap):
        """Где кликнуть по точке поиска. Персонаж может встать перед ней и закрыть её почти целиком,
        поэтому, если целиком точка не находится, кликаем в ту её часть, что совпадает со снимком."""
        x, y, w, h = snap.rect
        shift = self.shift()
        if shift is not None:
            # по снимку локации знаем, где точка сейчас, даже если её загородили
            x, y = x + shift[0], y + shift[1]
            near = (x - 20, y - 20, w + 40, h + 40)
            visible, score = None, PIECE_THRESHOLD
            for _, _, piece in object_pieces(snap.image):
                found, s = find_scored(self.image, piece, near=near)
                if found is not None and s >= score:
                    visible, score = found, s
            if visible is not None and abs(visible[0] - x - w / 2) < w and abs(visible[1] - y - h / 2) < h:
                return visible
            cx, cy = x + w // 2, y + h // 2
            return cx - CLICK_HALF, cy - CLICK_HALF, 2 * CLICK_HALF, 2 * CLICK_HALF
        near = (x - SPOT_MARGIN // 2, y - SPOT_MARGIN // 2, w + SPOT_MARGIN, h + SPOT_MARGIN)
        rect = find(self.image, snap.image, SPOT_THRESHOLD, near=near)
        if rect is not None:
            # кликаем в сам объект, а не в траву вокруг: мимо — персонаж подбежит, но поиска не будет
            centre = object_center(snap.image)
            if centre is None:
                centre = (rect[2] // 2, rect[3] // 2)
            cx, cy = rect[0] + centre[0], rect[1] + centre[1]
            return cx - CLICK_HALF, cy - CLICK_HALF, 2 * CLICK_HALF, 2 * CLICK_HALF
        # точку загородили (обычно персонаж) — ищем незакрытые кусочки самого объекта
        best, best_score = None, PIECE_THRESHOLD
        for _, _, piece in object_pieces(snap.image):
            found, score = find_scored(self.image, piece, near=near)
            if found is not None and score >= best_score:
                best, best_score = found, score
        return best

    def region(self, element_id: str):
        snap = self.teaching.elements.get(element_id)
        return snap.rect if snap else None

    def _texts(self, element_id: str, whitelist=None):
        rect = self.region(element_id)
        if rect is None or self.ocr is None:
            return []
        return self.ocr.text(crop(self.image, rect), whitelist)

    def _read(self, element_id: str, parse, whitelist=None):
        rect = self.region(element_id)
        if rect is None or self.ocr is None:
            return None
        return self.ocr.read(crop(self.image, rect), parse, whitelist)

    def read_hp(self, element_id: str):
        return self._read(element_id, parse_hp, "0123456789/")

    def read_percent(self, element_id: str):
        return self._read(element_id, parse_percent, "0123456789%")

    def read_rank(self, element_id: str):
        rect = self.region(element_id)
        if rect is None or self.ocr is None:
            return None
        return self.ocr.rank(crop(self.image, rect))

    def read_name(self, element_id: str, names):
        return self._read(element_id, lambda texts: best_name(texts, names))
