import json

import cv2
import numpy as np

from miscrits_hud.catalog import Species
from mbot.worldmap import Companion, Locator, species_in_zone, to_map, to_view, view_rect


def _world(h=1500, w=2000):
    rng = np.random.default_rng(7)
    small = rng.integers(0, 255, (h // 25, w // 25, 3), dtype=np.uint8)
    big = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    return cv2.add(big, rng.integers(0, 30, (h, w, 3), dtype=np.uint8))


def _screen_of(world, x, y, w, h, scale):
    """Что видно на экране игры: кусок карты, увеличенный в 1/scale раз."""
    return cv2.resize(world[y:y + h, x:x + w], None, fx=1 / scale, fy=1 / scale, interpolation=cv2.INTER_CUBIC)


def test_locator_finds_scale_then_position_fast():
    world = _world()
    view = _screen_of(world, 700, 500, 380, 200, 0.4)
    loc = Locator(world)
    place = loc.locate(view)
    assert abs(place.scale - 0.4) <= 0.011 and abs(place.mx - 700) <= 6 and abs(place.my - 500) <= 6
    # камера сдвинулась — ищем рядом, масштаб уже известен
    moved = _screen_of(world, 820, 560, 380, 200, 0.4)
    place = loc.locate(moved)
    assert abs(place.mx - 820) <= 6 and abs(place.my - 560) <= 6


def test_locator_none_on_foreign_screen():
    loc = Locator(_world())
    assert loc.locate(np.full((500, 900, 3), 40, np.uint8)) is None


def test_map_view_roundtrip_and_view_rect():
    from mbot.worldmap import Placement

    p = Placement(0.38, 23, 3484, 0.95)
    assert to_view(p, to_map(p, (100, 50))) == (100.0, 50.0)
    assert view_rect((2560, 0, 2560, 1440)) == (2560 + 179, 230, 2406 - 179, 1224 - 230)


def test_companion_reads_markers_in_map_pixels(tmp_path):
    files = {
        "regions.json": json.dumps([{"name": "Mansion", "zones": {"1": "Outside"},
                                     "map": {"file": "mansion.webp", "width": 3200, "height": 4529}}]).encode(),
        "markers.json": json.dumps({"Mansion": [{"name": "Keeper", "miscritId": 538, "rarity": "Legendary",
                                                 "x": 23.571926719972687, "y": 79.34602025451446}]}).encode(),
    }
    c = Companion(tmp_path, fetch=lambda url: files[url.rsplit("/data/", 1)[1]])
    [keeper] = c.markers("Mansion")
    assert keeper.species_id == 538 and round(keeper.x) == 754 and round(keeper.y) == 3594
    assert c.markers("Moon") == []


def test_species_in_zone():
    keeper = Species(538, ("Keeper",), "NatureEarth", "Legendary", {"Mansion": {"1": []}})
    assert species_in_zone(keeper, "Mansion", 1) and not species_in_zone(keeper, "Mansion", 3)


class WalkWorld:
    """Игра в миниатюре: окно 1000×600, камера над картой; клик по земле — персонаж идёт туда, камера едет."""

    SCALE = 0.4

    def __init__(self, world, start):
        from mbot.storage import Teaching
        self.world = world
        self.cam = list(start)  # точка карты в центре окна
        self.teaching = Teaching()
        self.image = None
        self.clicks = []

    def look(self):
        w, h = int(1000 * self.SCALE), int(600 * self.SCALE)
        x, y = int(self.cam[0] - w / 2), int(self.cam[1] - h / 2)
        self.image = cv2.resize(self.world[y:y + h, x:x + w], (1000, 600), interpolation=cv2.INTER_CUBIC)
        return self.image

    def click(self, rect):
        px, py = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        self.clicks.append((px, py))
        self.cam[0] += (px - 500) * self.SCALE
        self.cam[1] += (py - 300) * self.SCALE


def test_bot_walks_to_offscreen_marker(tmp_path):
    from mbot.bot import Bot
    from mbot.settings import Settings
    from mbot.worldmap import Marker

    world = _world(2400, 3200)
    game = WalkWorld(world, (900, 800))
    bot = Bot(game, game.click, lambda: None, lambda: None, Settings(delay_min=0, delay_max=0),
              tmp_path / "l.json", tmp_path, game_rect_fn=lambda: (0, 0, 1000, 600))
    bot._sleep = lambda s: None

    class Maps:
        def map_image(self, location):
            return world

    bot._companion = Maps()
    target = Marker("Keeper", 538, "Legendary", 1900, 1250)  # ~1000 px карты вправо — за экраном
    rect = bot._walk_to("Mansion", target)
    assert rect is not None and len(game.clicks) >= 2
    # клик по точке попадёт в маркер: пересчитаем, где маркер на экране сейчас
    sx = (target.x - game.cam[0]) / WalkWorld.SCALE + 500
    sy = (target.y - game.cam[1]) / WalkWorld.SCALE + 300
    assert abs(rect[0] + rect[2] / 2 - sx) <= 6 and abs(rect[1] + rect[3] / 2 - sy) <= 6
