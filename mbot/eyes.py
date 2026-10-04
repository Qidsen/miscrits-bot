"""Что бот видит: обученные элементы на текущем скриншоте и прочитанные с них значения."""

from .screen import best_name, crop, find, grab, parse_hp, parse_percent, parse_rank

SPOT_THRESHOLD = 0.7  # точки поиска — куски пейзажа, им можно чуть меньше точности


class Eyes:
    def __init__(self, teaching, ocr, threshold: float, grabber=grab):
        self.teaching = teaching
        self.ocr = ocr
        self.threshold = threshold
        self._grab = grabber
        self.image = None

    def look(self):
        self.image = self._grab()
        return self.image

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

    def region(self, element_id: str):
        snap = self.teaching.elements.get(element_id)
        return snap.rect if snap else None

    def _texts(self, element_id: str, whitelist=None):
        rect = self.region(element_id)
        if rect is None or self.ocr is None:
            return []
        return self.ocr.text(crop(self.image, rect), whitelist)

    def read_hp(self, element_id: str):
        return parse_hp(self._texts(element_id, "0123456789/"))

    def read_percent(self, element_id: str):
        return parse_percent(self._texts(element_id, "0123456789%"))

    def read_rank(self, element_id: str):
        return parse_rank(self._texts(element_id))

    def read_name(self, element_id: str, names):
        return best_name(self._texts(element_id), names)
