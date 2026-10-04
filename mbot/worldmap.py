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


TILE_COLS, TILE_ROWS = 3, 2
TILE_SCORE = 0.7
AGREE_PX = 8  # px карты: насколько должны сойтись куски, чтобы им поверить
MIN_AGREE = 2


class Locator:
    """Ищет экран на карте по кускам. Каждый кусок ищется отдельно, положению верим, если хотя бы два
    куска с ним согласны: так не мешают персонаж, HUD поверх игры, надписи и край мира (у края часть экрана
    показывает то, чего на карте сайта нет). Масштаб узнаётся один раз, дальше ищем рядом с прошлым местом."""

    def __init__(self, world, scale: float | None = None):
        self.world = world
        self.scale = scale
        self.last = None
        self._small = cv2.resize(world, None, fx=COARSE, fy=COARSE, interpolation=cv2.INTER_AREA)

    @staticmethod
    def _tiles(view):
        h, w = view.shape[:2]
        tw, th = w // TILE_COLS, h // TILE_ROWS
        return [(c * tw, r * th, view[r * th:(r + 1) * th, c * tw:(c + 1) * tw])
                for r in range(TILE_ROWS) for c in range(TILE_COLS)]

    def _vote(self, view, scale, small=False, near=None):
        """(Placement, сколько кусков согласны) — положение левого верхнего угла экрана на карте."""
        world, factor = (self._small, COARSE) if small else (self.world, 1.0)
        votes = []
        for ox, oy, tile in self._tiles(view):
            template = cv2.resize(tile, None, fx=scale * factor, fy=scale * factor, interpolation=cv2.INTER_AREA)
            x0 = y0 = 0
            area = world
            if near is not None:
                cx, cy = (near.mx + ox * scale) * factor, (near.my + oy * scale) * factor
                pad = NEAR_WINDOW * factor
                x0, y0 = int(max(cx - pad, 0)), int(max(cy - pad, 0))
                x1 = int(min(cx + template.shape[1] + pad, world.shape[1]))
                y1 = int(min(cy + template.shape[0] + pad, world.shape[0]))
                if x1 <= x0 or y1 <= y0:
                    continue
                area = world[y0:y1, x0:x1]
            score, (x, y) = _match(area, template)
            if score >= TILE_SCORE:
                votes.append(((x0 + x) / factor - ox * scale, (y0 + y) / factor - oy * scale, score))
        tolerance = AGREE_PX / factor
        best = []
        for mx, my, _ in votes:
            group = [v for v in votes if abs(v[0] - mx) <= tolerance and abs(v[1] - my) <= tolerance]
            if len(group) > len(best) or (len(group) == len(best) and sum(g[2] for g in group) > sum(g[2] for g in best)):
                best = group
        if not best:
            return None, 0
        place = Placement(float(scale), float(np.median([g[0] for g in best])), float(np.median([g[1] for g in best])),
                          float(np.mean([g[2] for g in best])))
        return place, len(best)

    def _accept(self, place):
        self.scale = place.scale
        self.last = place
        return place

    def locate(self, view) -> Placement | None:
        if self.scale is not None and self.last is not None:
            place, agree = self._vote(view, self.scale, near=self.last)
            if agree >= MIN_AGREE:
                return self._accept(place)
        scales = SCALES if self.scale is None else [self.scale]
        rough, rough_agree = None, 0
        for scale in scales:
            place, agree = self._vote(view, scale, small=True)
            if place is not None and (agree, place.score) > (rough_agree, rough.score if rough else -1):
                rough, rough_agree = place, agree
        if rough is None or rough_agree < MIN_AGREE:
            return None
        fine = np.arange(rough.scale - 0.02, rough.scale + 0.021, 0.005) if self.scale is None else [self.scale]
        best, best_agree = None, 0
        for scale in fine:
            place, agree = self._vote(view, float(scale), near=rough)
            if place is not None and (agree, place.score) > (best_agree, best.score if best else -1):
                best, best_agree = place, agree
        if best is None or best_agree < MIN_AGREE:
            return None
        return self._accept(best)


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
