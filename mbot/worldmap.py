"""Карты локаций и маркеры с Miscrits Companion: где сейчас экран игры на карте и где на экране точки.

Карта на сайте нарисована той же графикой, что и игра, только мельче (у Mansion экран 2560 px ≈ 970 px карты),
поэтому положение экрана находится обычным поиском картинки: экран, уменьшенный до масштаба карты."""

import json
import logging
import time
import urllib.request
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger(__name__)

SITE = "https://qidsen.github.io/miscrits-companion/data/"
REFRESH_DAYS = 7
SCALES = np.arange(0.10, 0.80, 0.02)  # «пикселей карты на пиксель экрана», первый поиск
COARSE = 0.25  # первый поиск — по уменьшенной карте
GOOD_SCORE = 0.6
NEAR_WINDOW = 500  # px карты вокруг прошлого места для быстрого поиска


@dataclass(frozen=True)
class Marker:
    name: str
    species_id: int
    rarity: str
    x: float  # px карты
    y: float


@dataclass(frozen=True)
class Placement:
    """Где кусок экрана view лежит на карте: map = (mx, my) + (точка view) × scale."""
    scale: float
    mx: float
    my: float
    score: float


def to_map(place: Placement, point) -> tuple:
    return place.mx + point[0] * place.scale, place.my + point[1] * place.scale


def to_view(place: Placement, point) -> tuple:
    return (point[0] - place.mx) / place.scale, (point[1] - place.my) / place.scale


def _http(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (miscrits-bot)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class Companion:
    """Данные сайта с кэшем на диске: регионы, маркеры, картинки карт."""

    def __init__(self, cache_dir, fetch=_http):
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fetch = fetch
        self._json = {}
        self._maps = {}

    def _file(self, name: str) -> bytes | None:
        path = self.dir / name.replace("/", "_")
        fresh = path.exists() and time.time() - path.stat().st_mtime < REFRESH_DAYS * 86400
        if not fresh:
            try:
                data = self._fetch(SITE + name)
                path.write_bytes(data)
                return data
            except Exception as e:  # нет сети — берём, что есть в кэше
                log.warning("cannot download %s: %r", name, e)
        return path.read_bytes() if path.exists() else None

    def _load_json(self, name: str):
        if name not in self._json:
            data = self._file(name)
            self._json[name] = json.loads(data) if data else None
        return self._json[name]

    def region(self, location: str) -> dict | None:
        regions = self._load_json("regions.json") or []
        return next((r for r in regions if r.get("name") == location), None)

    def all_markers(self) -> dict:
        """Сырые маркеры сайта: {локация: [{name, x, y, ...}]}."""
        return self._load_json("markers.json") or {}

    def markers(self, location: str) -> list:
        region = self.region(location)
        raw = (self._load_json("markers.json") or {}).get(location, [])
        if not region:
            return []
        w, h = region["map"]["width"], region["map"]["height"]
        return [Marker(m["name"], int(m.get("miscritId") or 0), m.get("rarity", ""), m["x"] / 100 * w, m["y"] / 100 * h)
                for m in raw]

    def map_image(self, location: str):
        if location not in self._maps:
            region = self.region(location)
            data = self._file("maps/" + region["map"]["file"]) if region else None
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
            if image is not None and region:
                # картинка на сайте может быть другого размера, чем в regions.json, — маркеры в процентах
                w, h = region["map"]["width"], region["map"]["height"]
                if image.shape[1] != w or image.shape[0] != h:
                    image = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
            self._maps[location] = image
        return self._maps[location]


def _match(world, template):
    if template.shape[0] >= world.shape[0] or template.shape[1] >= world.shape[1] or min(template.shape[:2]) < 8:
        return -1.0, (0, 0)
    if float(template.std()) < 6:  # однотонный экран (загрузка, меню) «совпадает» с чем угодно
        return -1.0, (0, 0)
    result = cv2.matchTemplate(world, template, cv2.TM_CCOEFF_NORMED)
    result[~np.isfinite(result)] = -1
    _, score, _, loc = cv2.minMaxLoc(result)
    return float(score), loc


class Locator:
    """Ищет кусок экрана на карте. Масштаб узнаётся один раз (он зависит только от разрешения),
    дальше ищем рядом с прошлым местом — это быстро."""

    def __init__(self, world, scale: float | None = None):
        self.world = world
        self.scale = scale
        self.last = None
        self._small = cv2.resize(world, None, fx=COARSE, fy=COARSE, interpolation=cv2.INTER_AREA)

    def _at(self, view, scale, area=None):
        template = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if area is None:
            score, (x, y) = _match(self.world, template)
            return Placement(scale, x, y, score)
        x0, y0, x1, y1 = area
        score, (x, y) = _match(self.world[y0:y1, x0:x1], template)
        return Placement(scale, x0 + x, y0 + y, score)

    def _coarse(self, view, scales):
        best = None
        for scale in scales:
            template = cv2.resize(view, None, fx=scale * COARSE, fy=scale * COARSE, interpolation=cv2.INTER_AREA)
            score, (x, y) = _match(self._small, template)
            if best is None or score > best.score:
                best = Placement(float(scale), x / COARSE, y / COARSE, score)
        return best

    def _refine(self, view, rough, scales):
        h, w = view.shape[:2]
        pad = 40
        best = None
        for scale in scales:
            x0, y0 = max(int(rough.mx) - pad, 0), max(int(rough.my) - pad, 0)
            area = (x0, y0, min(int(rough.mx + w * scale) + pad, self.world.shape[1]),
                    min(int(rough.my + h * scale) + pad, self.world.shape[0]))
            place = self._at(view, scale, area)
            if best is None or place.score > best.score:
                best = place
        return best

    def locate(self, view) -> Placement | None:
        place = None
        if self.scale is not None and self.last is not None:
            h, w = view.shape[:2]
            x0, y0 = max(int(self.last.mx) - NEAR_WINDOW, 0), max(int(self.last.my) - NEAR_WINDOW, 0)
            area = (x0, y0, min(int(self.last.mx + w * self.scale) + NEAR_WINDOW, self.world.shape[1]),
                    min(int(self.last.my + h * self.scale) + NEAR_WINDOW, self.world.shape[0]))
            place = self._at(view, self.scale, area)
        if place is None or place.score < GOOD_SCORE:
            scales = SCALES if self.scale is None else [self.scale]
            rough = self._coarse(view, scales)
            if rough is None or rough.score < GOOD_SCORE * 0.8:
                return None
            fine = np.arange(rough.scale - 0.02, rough.scale + 0.021, 0.005) if self.scale is None else [self.scale]
            place = self._refine(view, rough, fine)
        if place is None or place.score < GOOD_SCORE:
            return None
        self.scale = place.scale
        self.last = place
        return place


def view_rect(game_rect) -> tuple:
    """Часть окна игры без интерфейса (верхняя панель, иконки слева, друзья снизу) — её и ищем на карте."""
    x, y, w, h = game_rect
    left, top, right, bottom = int(w * 0.07), int(h * 0.16), int(w * 0.94), int(h * 0.85)
    return x + left, y + top, right - left, bottom - top


def species_in_zone(species, location: str, area_id: int) -> bool:
    return str(area_id) in (species.locations.get(location) or {})


def env_cache_dir():
    from .settings import bot_dir
    return bot_dir() / "companion"


__all__ = ["Companion", "Locator", "Marker", "Placement", "to_map", "to_view", "view_rect", "species_in_zone",
           "env_cache_dir"]
