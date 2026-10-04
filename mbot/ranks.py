"""Ранг дикого крита по значку в бою — сравнением с размеченными образцами, а не OCR.

У каждой буквы ранга свой цвет и форма (C — фиолетовая, A — красная, F — серо-зелёная, S — оранжевая…),
а портрет крита под значком у всех разный. Поэтому сравниваем только пиксели самого значка: букву — по левой
части, плюс — по правому нижнему углу. Незнакомый значок не угадываем: он уходит на разметку пользователю."""

import base64
import os
import time
from pathlib import Path

import cv2
import numpy as np

from .rank_seeds import SEEDS

SIZE = (32, 31)  # (w, h), как обученная область ранга на 2560×1440
LETTER_MAX = 20.0  # средняя разница (Lab) по букве, больше — «не знаю»
PLUS_FLIP = 30.0  # угол с плюсом отличается сильнее — значит, плюс «наоборот»
RANKS = ("S+", "S", "A+", "A", "B+", "B", "C+", "C", "D+", "D", "F+", "F")


def _prep(image):
    img = cv2.resize(image, SIZE, interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)
    w, h = SIZE
    badge = ((s > 90) & (v > 90)) | ((v < 140) & (s > 60))  # цветная заливка или тёмная обводка
    letter = badge.copy()
    letter[: int(h * 0.38)] = False  # сверху портрет
    letter[:, int(w * 0.62):] = False  # справа — место плюса
    plus = np.zeros_like(badge)
    plus[int(h * 0.55):, int(w * 0.62):] = True
    return lab, letter, plus


def _file_name(rank: str) -> str:
    return rank.replace("+", "p")


def _rank_of_file(name: str) -> str:
    return name.split("_")[0].replace("p", "+")


class RankBook:
    """Образцы: встроенные (SEEDS) + размеченные пользователем (папка book_dir, файл <ранг>_<время>.png)."""

    def __init__(self, book_dir=None):
        self.dir = Path(book_dir) if book_dir else None
        self.samples = []
        for rank, data in SEEDS:
            self._add(rank, cv2.imdecode(np.frombuffer(base64.b64decode(data), np.uint8), cv2.IMREAD_COLOR))
        if self.dir and self.dir.exists():
            for path in sorted(self.dir.glob("*.png")):
                image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
                if image is not None:
                    self._add(_rank_of_file(path.stem), image)

    def _add(self, rank, image):
        self.samples.append((rank, *_prep(image)))

    def learn(self, rank: str, image) -> None:
        """Пользователь подписал значок — запоминаем навсегда."""
        if rank not in RANKS:
            raise ValueError(rank)
        self._add(rank, image)
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode(".png", image)
            if ok:
                (self.dir / f"{_file_name(rank)}_{int(time.time() * 1000)}.png").write_bytes(buf.tobytes())

    def classify(self, image):
        """Ранг или None (такого значка ещё не видели — его надо разметить)."""
        lab, _, plus_area = _prep(image)
        best = None
        for rank, s_lab, s_letter, _ in self.samples:
            if not s_letter.any():
                continue
            d = float(np.abs(lab - s_lab).max(axis=2)[s_letter].mean())
            if best is None or d < best[0]:
                best = (d, rank, s_lab)
        if best is None or best[0] > LETTER_MAX:
            return None
        _, rank, s_lab = best
        letter, has_plus = rank[0], rank.endswith("+")
        plus_d = float(np.abs(lab - s_lab).max(axis=2)[plus_area].mean())
        if plus_d > PLUS_FLIP:
            has_plus = not has_plus
        return letter + ("+" if has_plus else "")

    def counts(self) -> dict:
        out = {}
        for rank, *_ in self.samples:
            out[rank] = out.get(rank, 0) + 1
        return out


def unknown_samples(folder) -> list:
    """Нераспознанные значки, которые бот сохранил в logs/ranks, — новые сначала."""
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted(folder.glob("*.png"), key=os.path.getmtime, reverse=True)
