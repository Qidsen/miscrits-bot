import random

from mbot.mouse import bezier_path, random_point


def test_random_point_stays_inside_rect():
    rng = random.Random(3)
    for _ in range(500):
        x, y = random_point((100, 200, 50, 20), rng)
        assert 107 <= x <= 143 and 203 <= y <= 217


def test_bezier_path_ends_at_target():
    path = bezier_path((0, 0), (1000, 500), random.Random(1))
    assert path[-1] == (1000, 500) and len(path) >= 40
