import base64

import cv2
import numpy as np

import mbot.ranks as R
from mbot.ranks import RankBook


def _decode(data):
    return cv2.imdecode(np.frombuffer(base64.b64decode(data), np.uint8), cv2.IMREAD_COLOR)


def test_every_seed_is_recognised_by_the_others(monkeypatch):
    seeds = list(R.SEEDS)
    for i, (rank, data) in enumerate(seeds):
        monkeypatch.setattr(R, "SEEDS", [s for j, s in enumerate(seeds) if j != i])
        got = RankBook().classify(_decode(data))
        others = [r for j, (r, _) in enumerate(seeds) if j != i]
        if any(r[0] == rank[0] for r in others):
            assert got == rank  # буква известна (хотя бы с плюсом или без) — ранг узнаём, плюс по углу
        else:
            assert got is None  # такой буквы больше нет в базе — ранг неизвестен


def test_plus_flips_when_corner_differs():
    a_plus = _decode(next(d for r, d in R.SEEDS if r == "A+"))
    s = _decode(next(d for r, d in R.SEEDS if r == "S"))
    a = a_plus.copy()
    a[17:, 20:] = s[17:, 20:]
    book = RankBook()
    assert book.classify(a_plus) == "A+" and book.classify(a) == "A"


def test_learned_sample_is_kept_on_disk(tmp_path):
    s_img = _decode(next(d for r, d in R.SEEDS if r == "S"))
    book = RankBook(tmp_path)
    book.learn("S", s_img)
    again = RankBook(tmp_path)
    assert again.counts()["S"] == 2 and again.classify(s_img) == "S"
