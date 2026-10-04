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
        if sum(1 for r, _ in seeds if r == rank) > 1:
            assert got == rank
        else:
            assert got is None  # единственный образец своего ранга — без него ранг неизвестен


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
