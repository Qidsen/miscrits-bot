import numpy as np

from mbot.screen import around, best_name, find, parse_hp, parse_percent, parse_rank


def _scene():
    rng = np.random.default_rng(1)
    image = rng.integers(0, 255, (400, 600, 3), dtype=np.uint8)
    return image


def test_find_locates_template_and_respects_threshold():
    image = _scene()
    template = image[100:140, 200:260].copy()
    assert find(image, template, 0.9) == (200, 100, 60, 40)
    assert find(image, template, 0.9, near=(210, 110, 60, 40)) == (200, 100, 60, 40)
    other = np.full((40, 60, 3), 7, np.uint8)
    other[::2] = 250
    assert find(image, other, 0.9) is None


def test_find_near_does_not_see_far_matches():
    image = _scene()
    template = image[10:40, 10:50].copy()
    assert find(image, template, 0.9, near=(500, 300, 40, 30)) is None


def test_around_clamps_to_screen():
    assert around((5, 5), 100, (400, 600, 3)) == (0, 0, 100, 100)
    assert around((590, 395), 100, (400, 600, 3)) == (500, 300, 100, 100)


def test_parsers():
    assert parse_hp(["junk", "73 / 75"]) == (73, 75)
    assert parse_hp(["80/75"]) is None
    assert parse_percent(["Capture 45%"]) == 45
    assert parse_rank(["Rank: A+"]) == "A+"
    assert parse_rank(["5"]) == "S"
    assert parse_rank(["nothing"]) is None


def test_best_name_is_fuzzy():
    names = ["Lavarilla", "Flue", "Dark Lavarilla"]
    assert best_name(["Lavari11a"], names) == "Lavarilla"
    assert best_name(["dark lavarila"], names) == "Dark Lavarilla"
    assert best_name(["zzz"], names) is None
