"""Конечный автомат бота: охота → бой → после боя → тренировка/лечение. Работает в своём потоке."""

import json
import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .brain.capture import CAPTURE, PLAT_RARITIES, decide
from .brain.combat import (ATTACK, PRECIOUS_EXTRA, PRECIOUS_SEEN, STALL, Action, DamageModel, choose_capture,
                           choose_kill, moves_from_catalog, multiplier)
from .brain.formula import my_stats, rank_roll, stats_at
from .brain.hits import CaptureView, HitBook
from .collection import Collection
from .mouse import VK_ESCAPE, FailSafe, press_key
from .screen import crop as crop_area
from .screen import find
from .storage import ABILITY_SLOTS, POPUPS, TEAM_SLOTS
from .worldmap import Locator, species_in_zone, to_map, to_view, view_rect

log = logging.getLogger(__name__)

GAME_EXE = "miscrits.exe"
MAX_ABILITY_PAGES = 5
WALK_STEPS = 14
CLICK_HALF = 14
MISS_RETRIES = 2
EXPLORE_ENOUGH = 3  # ударов на пару «атака → стихия цели», после которых больше не изучаем
EXPLORE_MIN_HP = 0.5  # изучаем, только пока у моего крита больше половины HP
MAX_TRAININGS = 4  # критов в команде
LIGHT_BUTTONS_AFTER = 4.0  # с: столько ждём обученный «Мой ход», прежде чем верить светлым кнопкам
EXPLORE_SWITCH_CHANCE = 0.3  # доля боёв «на убой», в которых пробуем другого крита команды
PORTRAIT_MATCH = 0.8  # насколько портрет в столбике должен совпасть с запомненным
SWITCH_FAR = 0.15  # смена ради поимки — только если до порога HP ещё больше 15% макс. HP цели
BLINK_FRAMES, BLINK_INTERVAL, BLINK_DELTA = 7, 0.15, 12.0
SUMMARY_MAX_WAIT = 2.0  # с: дольше анимация опыта в сводке не идёт
SUMMARY_STILL = 12  # изменившихся пикселей (в уменьшенном кадре), меньше которых сводка неподвижна


def ready_label_of(row):
    """Из снимка строки готового крита — только низ с меткой «READY TO TRAIN», без имени и портрета:
    так шаблон подходит к любому готовому криту."""
    h = row.shape[0]
    return row[int(h * 0.6):int(h * 0.95), :]


def summary_label_of(snap):
    """Из снимка сводки боя — только полоса «READY TO TRAIN» (без портрета, уровня и «+N опыта»,
    которые у каждого крита свои): средняя по высоте полоса снимка."""
    h = snap.shape[0]
    return snap[int(h * 0.42):int(h * 0.66), :]


class Stopped(Exception):
    pass


class Stuck(Exception):
    """Экран не похож ни на что знакомое — бот встаёт на паузу, а не кликает вслепую."""


@dataclass
class Stats:
    battles: int = 0
    captures: int = 0
    plat_captures: int = 0
    trainings: int = 0
    heals: int = 0
    started: float = field(default_factory=time.time)
    catches: list = field(default_factory=list)  # [(id вида, ранг)]


class Bot:
    def __init__(self, eyes, hands, catalog_fn, player_fn, settings, learn_path, logs_dir,
                 on_event=lambda kind, data: None, foreground=None, location_fn=None, companion=None,
                 game_rect_fn=None):
        """hands(rect) — клик; catalog_fn() -> Catalog; player_fn() -> Player | None (коллекция из HUD);
        foreground() -> имя exe активного окна; on_event(kind, data) — для GUI;
        location_fn() -> (локация, зона) | None; companion — карты и маркеры сайта;
        game_rect_fn() -> окно игры (x, y, w, h) в координатах скриншота."""
        self.eyes = eyes
        self._press_key = press_key  # подменяется в тестах: настоящие нажатия клавиш там не нужны
        self._location_fn = location_fn
        self._companion = companion
        self._game_rect_fn = game_rect_fn
        self._locators = {}
        self._marker_used = {}
        self._all_caught_said = False
        self._portraits = {}  # имя крита -> картинка его портрета в столбике команды (узнаём при смене)
        self._switch_broken = False
        self._train_seen = None  # что сказала сводка последнего боя про тренировку
        self._last_max_hp = None  # (локация, имя, x, y) -> time.monotonic() клика
        self._click = hands
        self._catalog_fn = catalog_fn
        self._player_fn = player_fn
        self.settings = settings
        self._learn_path = learn_path
        self._logs_dir = logs_dir
        self._emit = on_event
        self._foreground = foreground
        self._stop = threading.Event()
        self._paused = threading.Event()
        self.stats = Stats()
        self.model = self._load_model()
        # журнал ударов + прогноз: по похожим ударам, иначе по формуле со статами
        self.hits = HitBook(learn_path.with_name("hits.csv"), self.model, stats=self._stats_pair)
        self._pages = {}  # имя моего крита -> [[имена способностей по слотам] по страницам]
        self._page = 0
        self._spot = 0
        self._spot_used = {}  # номер точки -> time.monotonic() последнего клика
        self._next_break = None
        self._thread = None
        self.pause_reason = ""

    # ---- управление из GUI ----

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._paused.clear()
        self.stats = Stats()
        self._thread = threading.Thread(target=self._run, name="bot", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def toggle_pause(self):
        if self._paused.is_set():
            self.resume()
        else:
            self.pause("пауза (F6)")

    def pause(self, reason: str):
        self.pause_reason = reason
        self._paused.set()
        self._emit("paused", reason)

    def resume(self):
        self._paused.clear()
        self._emit("resumed", None)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---- служебное ----

    def _say(self, text: str):
        log.info(text)
        self._emit("log", text)

    def _state(self, text: str):
        self._emit("state", text)

    def _load_model(self) -> DamageModel:
        try:
            with open(self._learn_path, encoding="utf-8") as f:
                return DamageModel(json.load(f))
        except (OSError, ValueError):
            return DamageModel()

    def _save_model(self):
        try:
            with open(self._learn_path, "w", encoding="utf-8") as f:
                json.dump(self.model.to_json(), f)
        except OSError as e:
            log.warning("cannot save learn.json: %r", e)

    def _checkpoint(self):
        """Точка, где бот может остановиться или переждать паузу."""
        if self._stop.is_set():
            raise Stopped()
        while self._paused.is_set() or not self._game_focused():
            if self._stop.is_set():
                raise Stopped()
            time.sleep(0.2)

    def _game_focused(self) -> bool:
        if self._foreground is None:
            return True
        focused = self._foreground() == GAME_EXE
        if not focused and not self._paused.is_set():
            self._state("жду: окно игры не активно")
        return focused

    def _sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._checkpoint()
            time.sleep(min(0.1, max(end - time.monotonic(), 0)))

    def _press(self, rect, what: str = ""):
        self._sleep(random.uniform(self.settings.delay_min, self.settings.delay_max))
        self._checkpoint()
        if what:
            log.info("click %s", what)
        self._click(rect)

    def _wait_for(self, ids, timeout: float, interval: float = 0.35, while_visible: str | None = None):
        """Ждёт, пока на экране появится один из элементов. (id, rect) или (None, None).
        while_visible — перестать ждать, если этот элемент пропал с экрана на два кадра подряд."""
        end = time.monotonic() + timeout
        gone = 0
        while True:
            self._checkpoint()
            self.eyes.look()
            for element_id in ids:
                rect = self.eyes.sees(element_id)
                if rect is not None:
                    return element_id, rect
            if while_visible is not None:
                gone = gone + 1 if self.eyes.sees(while_visible) is None else 0
                if gone >= 2:
                    return None, None
            if time.monotonic() >= end:
                return None, None
            time.sleep(interval)

    def _screenshot(self, name: str) -> str:
        path = self._logs_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.png"
        if self.eyes.image is not None:
            ok, buf = cv2.imencode(".png", self.eyes.image)
            if ok:
                path.write_bytes(buf.tobytes())
        return str(path)

    def _collection(self) -> Collection:
        player = self._player_fn()
        base = Collection.from_player(player) if player is not None else Collection()
        return base.merged(self.stats.catches)

    def _publish_stats(self):
        self._emit("stats", self.stats)

    # ---- главный цикл ----

    def _run(self):
        self._say("бот запущен")
        self._schedule_break()
        stuck = 0
        try:
            while True:
                self._checkpoint()
                self._maybe_break()
                try:
                    self._step()
                    stuck = 0
                except Stuck as e:
                    stuck += 1
                    if stuck >= 3:
                        shot = self._screenshot("stuck")
                        self._say(f"не понимаю, что на экране ({e}); скриншот: {shot}")
                        self.pause(f"непонятный экран: {e}")
                        stuck = 0
        except Stopped:
            self._say("бот остановлен")
        except FailSafe:
            self._say("аварийный стоп: мышь в левом верхнем углу")
        except Exception:
            log.exception("bot crashed")
            self._say("ошибка в боте, подробности в журнале")
        finally:
            self._save_model()
            self._emit("stopped", None)

    def _session_over(self) -> bool:
        limit = self.settings.session_limit_min
        return bool(limit) and time.time() - self.stats.started >= limit * 60

    def _schedule_break(self):
        s = self.settings
        self._next_break = time.monotonic() + random.uniform(s.break_every_min, s.break_every_max) * 60

    def _maybe_break(self):
        if self._session_over():
            self._say("лимит сессии исчерпан")
            raise Stopped()
        if time.monotonic() < self._next_break:
            return
        s = self.settings
        minutes = random.uniform(s.break_len_min, s.break_len_max)
        self._say(f"перерыв {minutes:.1f} мин")
        self._state(f"перерыв до {time.strftime('%H:%M', time.localtime(time.time() + minutes * 60))}")
        self._sleep(minutes * 60)
        self._schedule_break()

    def _step(self):
        self.eyes.look()
        if self.eyes.sees("battle") is not None:
            self._battle()
            return
        if self._dismiss_popups():
            return
        self._hunt()

    def _dismiss_popups(self) -> bool:
        for element_id in ("battle_won", "captured", *POPUPS):
            rect = self.eyes.sees(element_id)
            if rect is not None:
                self._press(rect, element_id)
                self._sleep(0.8)
                return True
        return False

    # ---- охота ----

    def _hunt(self):
        if self._map_hunt():
            return
        spots = self.eyes.teaching.spots
        if not spots:
            # целей нет (или все пойманы), а вручную точки не размечены — фармим опыт на всех точках зоны с карты сайта
            if self._map_hunt(farm=True):
                return
            raise Stuck("нет точек поиска: ни целей, ни размеченных точек, ни карты этой зоны на сайте")
        # Кулдаун точки идёт с момента клика по ней, бой входит в это время — считаем его сами.
        cooldown = self.settings.spot_cooldown + 1
        now = time.monotonic()
        order = [(self._spot + k) % len(spots) for k in range(len(spots))]
        ready = [i for i in order if now - self._spot_used.get(i, float("-inf")) >= cooldown]
        # точки, где водится цель охоты, — первыми, как только остыли
        catalog = self._catalog_fn()
        targets = self._open_targets(catalog) if catalog is not None else set(self.settings.hunt_targets)
        ready.sort(key=lambda i: not (spots[i].species_here() & targets))
        if not ready:
            wait = min(cooldown - (now - self._spot_used[i]) for i in order)
            self._state(f"все точки на кулдауне, жду {wait:.0f} с")
            self._sleep(wait + random.uniform(0.3, 1.5))
            return
        self._state("ищу мискрита")
        self.eyes.look()
        for i in ready:
            rect = self.eyes.locate_spot(spots[i])
            if rect is None:
                continue
            self._spot = i + 1
            clicked_at, enemy = self._search(rect, lambda i=i: self.eyes.locate_spot(spots[i]), f"spot {i + 1}")
            self._spot_used[i] = clicked_at
            if enemy is not None:
                seen = spots[i].seen
                seen[enemy.names[0]] = seen.get(enemy.names[0], 0) + 1
                self._emit("teaching_changed", None)
            return
        raise Stuck("не вижу ни одной точки поиска")

    def _search(self, rect, relocate, label):
        """Клик по точке поиска и бой, если он начался. (время клика, вид противника или None).
        Ни боя, ни попапа, ни «Come back later» — скорее всего, промахнулись: персонаж подбежал к точке,
        кликаем ещё раз уже оттуда. come_back_later — наш отсчёт кулдауна разошёлся с игрой, он начнётся
        заново с этого клика; попап (предмет/золото) закроется на следующем шаге."""
        found = None
        clicked_at = time.monotonic()
        for attempt in range(MISS_RETRIES + 1):
            self._press(rect, label + (f" (ещё раз, {attempt})" if attempt else ""))
            clicked_at = time.monotonic()
            found, _ = self._wait_for(("battle", "come_back_later", *POPUPS), timeout=6)
            if found is not None:
                break
            self.eyes.look()
            rect = relocate()
            if rect is None:
                break
        return clicked_at, (self._battle() if found == "battle" else None)

    # ---- охота по карте сайта ----

    def _open_targets(self, catalog) -> set:
        """Цели охоты, которых ещё нет в коллекции: поймали Fubby — дальше ищем только остальных."""
        collection = self._collection()
        by_name = {s.names[0]: s for s in catalog.species}
        targets = {t for t in self.settings.hunt_targets if t not in by_name or not collection.owns(by_name[t].id)}
        if self.settings.hunt_targets and not targets and not self._all_caught_said:
            self._all_caught_said = True
            self._say("все цели охоты пойманы 🎉 — дальше обычные точки")
            self._emit("targets_done", None)
        return targets

    def _map_targets(self, farm=False):
        """(локация, маркеры целей в текущей зоне) или None, если охотиться по карте нельзя.
        farm — все точки зоны с карты сайта (фарм опыта, когда целей нет)."""
        if self._companion is None or self._location_fn is None or (not farm and not self.settings.hunt_targets):
            return None
        where = self._location_fn()
        catalog = self._catalog_fn()
        if not where or catalog is None:
            return None
        location, area = where
        targets = None if farm else self._open_targets(catalog)
        if not farm and not targets:
            return None
        by_id = {s.id: s for s in catalog.species}
        markers = []
        for m in self._companion.markers(location):
            species = by_id.get(m.species_id)
            in_zone = species is None or species_in_zone(species, location, area)
            if in_zone and (farm or m.name in targets):
                markers.append(m)
        return (location, markers) if markers else None

    def _view(self):
        """(прямоугольник обзора на скриншоте, картинка обзора) — окно игры без интерфейса."""
        game = self._game_rect_fn() if self._game_rect_fn else None
        if game is None:
            raise Stuck("не нашёл окно игры")
        x, y, w, h = view_rect(game)
        return (x, y, w, h), self.eyes.image[y:y + h, x:x + w]

    def _where(self, location):
        """Где сейчас экран на карте локации: (обзор, Placement)."""
        locator = self._locators.get(location)
        if locator is None:
            world = self._companion.map_image(location)
            if world is None:
                raise Stuck(f"нет карты локации {location}")
            locator = self._locators[location] = Locator(world, scale=self._map_scales().get(self._scale_key(location)))
        self.eyes.look()
        rect, view = self._view()
        known = locator.scale
        place = locator.locate(view)
        if place is None:
            raise Stuck("не нашёл себя на карте локации")
        if known is None:
            # масштаб карты зависит от локации и разрешения — запоминаем, чтобы в следующий раз не искать его 10 с
            scales = self._map_scales()
            scales[self._scale_key(location)] = place.scale
            try:
                self._learn_path.with_name("map_scale.json").write_text(json.dumps(scales), encoding="utf-8")
            except OSError:
                pass
        self._emit("position", (location, place, rect))
        return rect, place

    def _scale_key(self, location):
        game = self._game_rect_fn() if self._game_rect_fn else None
        return f"{location}|{game[2]}x{game[3]}" if game else location

    def _map_scales(self) -> dict:
        try:
            return json.loads(self._learn_path.with_name("map_scale.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _marker_on_screen(self, location, marker, margin=60):
        rect, place = self._where(location)
        vx, vy = to_view(place, (marker.x, marker.y))
        x, y, w, h = rect
        inside = margin <= vx <= w - margin and margin <= vy <= h - margin
        return rect, place, (x + vx, y + vy), inside

    def _walk_to(self, location, marker):
        """Идёт к маркеру кликами по земле, пока он не окажется на экране. Прямоугольник для клика или None."""
        turns = [0.0, 0.6, -0.6, 1.2, -1.2]
        turn = 0
        for _ in range(WALK_STEPS):
            rect, place, (sx, sy), inside = self._marker_on_screen(location, marker)
            if inside:
                return int(sx) - CLICK_HALF, int(sy) - CLICK_HALF, 2 * CLICK_HALF, 2 * CLICK_HALF
            x, y, w, h = rect
            cx, cy = x + w / 2, y + h / 2  # камера держит персонажа примерно в центре
            angle = math.atan2(sy - cy, sx - cx) + turns[turn % len(turns)]
            px = min(max(cx + math.cos(angle) * w * 0.36, x + 40), x + w - 40)
            py = min(max(cy + math.sin(angle) * h * 0.36, y + 40), y + h - 40)
            self._state(f"иду к точке {marker.name}")
            self._press((int(px) - 10, int(py) - 10, 20, 20), f"walk → {marker.name}")
            before = place
            place = self._settle(location, before)
            moved = math.hypot(place.mx - before.mx, place.my - before.my) / place.scale
            turn = 0 if moved > 40 else turn + 1  # упёрлись — пробуем обойти под другим углом
        return None

    def _settle(self, location, before):
        """Ждёт, пока камера перестанет ехать (персонаж дошёл). Последнее положение на карте."""
        last = before
        still = 0
        end = time.monotonic() + 8
        self._sleep(0.6)
        while time.monotonic() < end:
            _, place = self._where(location)
            if math.hypot(place.mx - last.mx, place.my - last.my) / place.scale < 4:
                still += 1
                if still >= 2:
                    return place
            else:
                still = 0
            last = place
            self._sleep(0.3)
        return last

    def _map_hunt(self, farm=False) -> bool:
        found = self._map_targets(farm)
        if found is None:
            return False
        location, markers = found
        cooldown = self.settings.spot_cooldown + 1
        now = time.monotonic()
        key = lambda m: (location, m.name, round(m.x), round(m.y))  # noqa: E731
        ready = [m for m in markers if now - self._marker_used.get(key(m), float("-inf")) >= cooldown]
        if not ready:
            wait = min(cooldown - (now - self._marker_used[key(m)]) for m in markers)
            self._state(f"точки целей на кулдауне, жду {wait:.0f} с")
            self._sleep(wait + random.uniform(0.3, 1.5))
            return True
        _, place = self._where(location)
        centre = to_map(place, (self._view()[0][2] / 2, self._view()[0][3] / 2))
        marker = min(ready, key=lambda m: math.hypot(m.x - centre[0], m.y - centre[1]))
        rect = self._walk_to(location, marker)
        if rect is None:
            self._say(f"не дошёл до точки {marker.name} — попробую позже")
            self._marker_used[key(marker)] = time.monotonic()
            return True

        def relocate():
            _, _, (sx, sy), inside = self._marker_on_screen(location, marker)
            return (int(sx) - CLICK_HALF, int(sy) - CLICK_HALF, 2 * CLICK_HALF, 2 * CLICK_HALF) if inside else None

        clicked_at, enemy = self._search(rect, relocate, f"точка {marker.name}")
        self._marker_used[key(marker)] = clicked_at
        return True

    # ---- бой ----

    def _battle(self):
        catalog = self._catalog_fn()
        if catalog is None:
            raise Stuck("нет каталога игры")
        by_name = {n: s for s in catalog.species for n in s.names}
        self.stats.battles += 1
        self._page = 0
        enemy = rank = decision = None
        reads = 0  # сколько раз пробовали прочитать противника (не больше двух ходов)
        plat_used = 0
        last = None  # (имя моего крита, Move, HP противника до удара)
        my_ratio = 1.0
        captured = False
        switched = None  # (портрет, кто был до смены) — на следующем ходу узнаем, получилось ли
        explore_switch_done = False
        self._state("бой")
        while True:
            turn = self._wait_turn()
            if turn is None:
                self.eyes.look()
                if self.eyes.sees("battle") is None:
                    break
                raise Stuck("бой: не дождался своего хода")
            if turn == "captured":
                captured = True
                break
            if turn == "battle_won":
                if decision is not None and decision.action == CAPTURE and last is not None:
                    self._killed_while_catching(enemy, rank, last)
                self._train_seen = self._summary_says_train()
                break
            self._sleep(0.4)  # анимации панели HP
            self.eyes.look()
            if decision is None or (enemy is None and reads < 2):
                # имя и ранг читаем один раз; если с первого хода не вышло (анимация входа), — ещё раз на втором
                reads += 1
                name = self.eyes.read_name("enemy_name", by_name)
                enemy = by_name.get(name)
                rank = self.eyes.read_rank("enemy_rank") if self.eyes.knows("enemy_rank") else None
                if enemy is None and reads < 2:
                    decision = decide(None, None, "", self._collection())
                    self._sleep(0.8)
                    continue
                if rank is None and self.eyes.knows("enemy_rank"):
                    self._save_rank_sample()
                if enemy is None:
                    shot = self._screenshot("enemy-unknown")
                    self._say(f"противник не распознан — бью (скриншот: {shot})")
                enemy_level = self.eyes.read_level("enemy")
                self.hits.level = enemy_level
                self.hits.enemy = enemy.names[0] if enemy else None
                self.hits.enemy_rank = rank
                decision = decide(enemy.id if enemy else None, rank, enemy.rarity if enemy else "", self._collection())
                who = f"{enemy.names[0]} ({enemy.rarity}) {rank or '?'}" if enemy else "?"
                self._say(f"бой: {who} → {'ЛОВИМ' if decision.action == CAPTURE else 'убиваем'} — {decision.reason}")
                self._emit("battle_log", f"──── {who} → {'ЛОВИМ' if decision.action == CAPTURE else 'убиваем'} "
                                         f"({decision.reason}) ────")
            hp = self.eyes.read_hp("enemy_hp")
            if hp:
                self._last_max_hp = hp[1]
            mine = self.eyes.read_hp("my_hp")
            if mine:
                my_ratio = mine[0] / mine[1]
            my_name = self.eyes.read_name("my_name", by_name)
            me = by_name.get(my_name)
            my_level = self.eyes.read_level("my")
            self.hits.attacker_level = my_level
            if switched is not None and my_name:
                portrait, before = switched
                switched = None
                if my_name == before:
                    # крит не сменился (окно подтверждения не нашлось или не сработало) — закрываем окно
                    # и до конца сессии не пробуем: чужой портрет не запоминаем
                    self._press_key(VK_ESCAPE)
                    self._switch_broken = True
                    self._say("⚠ смена крита не сработала — до конца сессии без смен (проверьте «Подтвердить смену крита»)")
                    continue
                self._portraits[my_name] = portrait  # теперь знаем, чей это портрет
                self._say(f"сменил крита: теперь {my_name}")
            target_element = enemy.element if enemy else ""
            if last and hp and last[0] == my_name and hp[0] <= last[2]:
                # один наш удар за ход: урон = HP цели до него минус HP сейчас. Если HP выросло (противник
                # подлечился), удар не записываем — разница была бы неправдой
                self.hits.record(last[0], my_level, last[1], enemy.names[0] if enemy else "?",
                                 target_element, self.hits.level, hp[1], last[2] - hp[0], rank)
            if me is None:
                raise Stuck("не распознал своего крита")
            moves, extras = self._known_moves(my_name, me)
            if not moves:
                raise Stuck(f"не нашёл атак у {my_name}")
            if decision.action == CAPTURE:
                can_capture = self.eyes.sees("capture")
                plat = None
                if (can_capture is None and decision.allow_plat
                        and plat_used < self.settings.plat_capture_limit):
                    plat = self.eyes.sees("plat_capture")
                chance = self.eyes.read_percent("capture_chance") if self.eyes.knows("capture_chance") else None
                precious = bool(enemy) and enemy.rarity in PLAT_RARITIES
                action = choose_capture(moves, CaptureView(self.hits), my_name, target_element,
                                        hp[0] if hp else 1, hp[1] if hp else 1, chance,
                                        self.settings.capture_min_chance, (can_capture or plat) is not None,
                                        precious=precious, floor=self.settings.capture_hp_floor)
                if action.kind in (CAPTURE, STALL) and hp and hp[0] - self.settings.capture_hp_floor > SWITCH_FAR * hp[1]:
                    # у текущего крита нет удара, безопасно приближающего цель к порогу, а до порога далеко —
                    # ищем в команде того, у кого такой удар есть
                    better = self._better_catcher(my_name, by_name, target_element, hp, precious)
                    if better is not None:
                        slot, name, reason = better
                        self._say(f"меняю {my_name} на {name}: {reason}")
                        switched = (self._switch(slot), my_name)
                        last = None
                        continue
                self._explain(enemy, rank, hp, my_name, mine, moves, target_element, decision, action, chance,
                              precious)
                if action.kind == CAPTURE:
                    if can_capture is not None:
                        self._press(can_capture, "capture")
                        self._say(f"пробую поймать (шанс {chance if chance is not None else '?'}%)")
                    else:
                        plat_used += 1
                        self._press(plat, "plat_capture")
                        self._say(f"платиновая поимка {plat_used}/{self.settings.plat_capture_limit}")
                    last = None
                    self._sleep(2.5)
                    continue
                if action.kind == STALL:
                    if not extras and precious:
                        # нечем безопасно занять ход, а ошибка тут — убитый экзотик/легендарка: пусть решит человек
                        self._say(f"⚠ {enemy.names[0]} ({enemy.rarity}): нет безопасного хода — пауза, сходите сами")
                        self.pause(f"{enemy.names[0]}: нет безопасного хода — сделайте ход сами и снимите паузу")
                        self._checkpoint()
                        last = None
                        continue
                    self._use_any(extras or [min(moves, key=lambda m: m.power).name], my_name)
                    last = None
                    continue
                move = action.move
            else:
                move = None
                if (not explore_switch_done and self.settings.explore_switch and my_ratio > EXPLORE_MIN_HP
                        and random.random() < EXPLORE_SWITCH_CHANCE):
                    explore_switch_done = True
                    slot = self._least_known_slot()
                    if slot is not None:
                        who = self._who_in(slot) or "незнакомого крита"
                        self._say(f"убиваем, можно поучиться: пробую {who} — по нему мало данных об уроне")
                        switched = (self._switch(slot), my_name)
                        last = None
                        continue
                explore_switch_done = True
                if self.settings.explore_damage and my_ratio > EXPLORE_MIN_HP:
                    # убивать можно — заодно пробуем атаку, по которой меньше всего данных против этой стихии
                    least = min(moves, key=lambda m: self.hits.observed(my_name, m, target_element))
                    if self.hits.observed(my_name, least, target_element) < EXPLORE_ENOUGH:
                        move = least
                        self._state(f"изучаю урон: {least.name}")
                if move is None and self.settings.kill_with_first:
                    move = self._first_ability(my_name, moves)
                if move is None:
                    move = choose_kill(moves, self.hits, my_name, target_element)
                    why = "самая сильная атака по ожиданию"
                elif self._first_ability(my_name, moves) is move:
                    why = "первая способность (лечит)"
                else:
                    why = f"изучаю урон: по {move.element} → {target_element or '?'} мало данных"
                self._explain(enemy, rank, hp, my_name, mine, moves, target_element, decision,
                              Action(ATTACK, move), None, False, why)
            self._use(move.name, my_name)
            last = (my_name, move, hp[0]) if hp else None
            self._sleep(1.0)
        self._emit("battle_end", None)
        self._after_battle(enemy, rank, captured, plat_used, my_ratio)
        return enemy

    def _wait_turn(self):
        """Ждёт своего хода (или конца боя). «Мой ход» часто обучен на кнопке способности первой страницы —
        если после удара со второй страницы она так и открыта, ход не узнаётся; тогда листаем назад."""
        start = time.monotonic()
        end = start + 60
        flipped_at = start
        shot_taken = False
        while time.monotonic() < end:
            if not shot_taken and time.monotonic() - start > 10:
                # долго не узнаём свой ход — сохраним, как выглядит экран: по нему правится распознавание
                shot_taken = True
                self.eyes.look()
                self._say(f"долго жду свой ход — скриншот: {self._screenshot('turn-wait')}")
            turn, _ = self._wait_for(("my_turn", "battle_won", "captured"), timeout=3, while_visible="battle")
            if turn is not None:
                return turn
            self.eyes.look()
            if self.eyes.sees("battle") is None:
                return None
            # «Мой ход» обучен на кнопке конкретного крита — после смены она другая. Тогда узнаём ход по светлым
            # кнопкам, но только как запасной признак: они светлые и во время анимаций, и раньше времени ходить нельзя
            if time.monotonic() - start > LIGHT_BUTTONS_AFTER and self.eyes.abilities_active():
                return "my_turn"
            if self._page != 0 and time.monotonic() - flipped_at > 2:
                arrow = self.eyes.sees("ability_prev")
                if arrow is not None:
                    self._press(arrow, "ability_prev (вернуться на первую страницу)")
                    self._page -= 1
                    flipped_at = time.monotonic()
        return None

    # ---- смена крита ----

    def _team(self):
        if self._switch_broken or not self.eyes.knows("switch_confirm"):
            return []  # без подтверждения смена не пройдёт — не пробуем
        return [slot for slot in TEAM_SLOTS if self.eyes.knows(slot)]

    def _portrait(self, slot):
        rect = self.eyes.region(slot)
        x, y, w, h = rect
        return self.eyes.image[y:y + h, x:x + w].copy()

    def _who_in(self, slot):
        """Чей портрет сейчас в этой ячейке столбика (по запомненным портретам) или None."""
        image = self._portrait(slot)
        best, score = None, PORTRAIT_MATCH
        for name, portrait in self._portraits.items():
            if portrait.shape != image.shape:
                continue
            s = float(cv2.matchTemplate(image, portrait, cv2.TM_CCOEFF_NORMED).max())
            if s >= score:
                best, score = name, s
        return best

    def _switch(self, slot):
        """Клик по портрету + подтверждение. Ход тратится — результат увидим на следующем ходу.
        Возвращает картинку портрета, чтобы потом запомнить, чей он."""
        portrait = self._portrait(slot)
        self._press(self.eyes.region(slot), f"смена крита ({slot})")
        if self.eyes.knows("switch_confirm"):
            found, rect = self._wait_for(("switch_confirm",), timeout=6)
            if rect is not None:
                self._press(rect, "подтвердить смену")
        self._page = 0
        self._sleep(1.0)
        return portrait

    def _least_known_slot(self):
        """Ячейка с критом, по которому меньше всего ударов в журнале (незнакомые — первыми)."""
        slots = self._team()
        if not slots:
            return None
        def known(slot):
            name = self._who_in(slot)
            return -1 if name is None else sum(1 for h in self.hits.hits if h.attacker == name)
        return min(slots, key=lambda s: (known(s), random.random()))

    def _better_catcher(self, my_name, by_name, target_element, hp, precious):
        """(ячейка, имя) крита команды, у которого есть безопасный удар, приближающий цель к порогу, или None.
        Смотрим только критов, чьи способности бот уже видел (читал их кнопки)."""
        for slot in self._team():
            name = self._who_in(slot)
            if not name or name == my_name or name not in self._pages:
                continue
            species = by_name.get(name)
            if species is None:
                continue
            names = {n for page in self._pages[name] for n in page if n}
            moves = moves_from_catalog(species.abilities, names)
            current_level, self.hits.attacker_level = self.hits.attacker_level, None
            action = choose_capture(moves, CaptureView(self.hits), name, target_element, hp[0], hp[1], None, 101, True,
                                    precious=precious, floor=self.settings.capture_hp_floor)
            if action.kind != ATTACK:
                self.hits.attacker_level = current_level
            if action.kind == ATTACK:
                expected, worst = self.hits.estimate(name, action.move, target_element, hp[1])
                self.hits.attacker_level = current_level
                return slot, name, (f"у него {action.move.name}: ожидаемо {expected:.0f}, худший случай {worst:.0f} — "
                                    f"у цели {hp[0]} HP, останется не меньше {self.settings.capture_hp_floor}")
        return None

    def _explain(self, enemy, rank, hp, my_name, mine, moves, target_element, decision, action, chance, precious,
                 why=None):
        """Что бот видит и почему так ходит — в «События» и в карточку «Текущий бой»."""
        floor = self.settings.capture_hp_floor
        rows = []
        for m in moves:
            expected, worst, source = self.hits.estimate_with_source(my_name, m, target_element, hp[1] if hp else None)
            seen = self.hits.observed(my_name, m, target_element)
            if decision.action == CAPTURE and hp:
                worst = CaptureView(self.hits).estimate(my_name, m, target_element, hp[1])[1]
                worst_used = worst * (PRECIOUS_EXTRA if precious else 1)
                ok = worst_used <= hp[0] - floor and (not precious or seen >= PRECIOUS_SEEN)
                verdict = "безопасно" if ok else ("мало данных" if precious and seen < PRECIOUS_SEEN else "может добить")
            else:
                verdict = ""
            if action.move is m:
                verdict = "выбрана" + (f" · {verdict}" if verdict else "")
            cal = self.hits.calibration()
            mult = cal.multiplier(m.element, target_element) if cal else multiplier(m.element, target_element)
            rows.append({"name": m.name, "element": m.element, "mult": round(mult, 2), "source": source,
                         "expected": expected, "worst": worst, "seen": seen, "verdict": verdict})
        if action.kind == CAPTURE:
            text = (f"ловлю: шанс {chance}% ≥ {self.settings.capture_min_chance}%" if chance is not None
                    and chance >= self.settings.capture_min_chance else
                    f"ловлю: безопасных ударов нет — любой может опустить ниже {floor} HP (шанс {chance if chance is not None else '?'}%)")
        elif action.kind == STALL:
            text = "пропускаю удар: любой может добить, а поймать сейчас нельзя — безопасная способность"
        else:
            row = next(r for r in rows if r["name"] == action.move.name)
            text = f"{action.move.name}: ожидаемо {row['expected']:.0f}, худший случай {row['worst']:.0f}"
            if decision.action == CAPTURE and hp:
                text += f" — у цели {hp[0]} HP, останется не меньше {floor} → бью"
            elif why:
                text += f" — {why}"
        log.info(text)
        self._emit("battle_log", text)
        self._emit("battle", {
            "enemy": enemy.names[0] if enemy else "?", "rarity": enemy.rarity if enemy else "", "rank": rank,
            "level": self.hits.level, "hp": hp, "me": my_name, "my_hp": mine, "mode": decision.action,
            "reason": decision.reason, "moves": rows, "action": text, "floor": floor,
        })

    def _killed_while_catching(self, enemy, rank, last):
        """Цель умерла от удара, который должен был её только подвести: записываем как «урон не меньше остатка HP»,
        чтобы худший случай для таких ударов вырос, и сохраняем инцидент со скриншотом."""
        attacker, move, hp_before = last
        name = enemy.names[0] if enemy else "?"
        target_element = enemy.element if enemy else ""
        forecast = self.hits.estimate_with_source(attacker, move, target_element, self._last_max_hp)
        shot = self._screenshot("killed-while-catching")
        self._emit("battle_log", "⚠ добил при поимке — см. «События»")
        self._say(f"⚠ добил при поимке: {name} {rank or ''} — {move.name} при {hp_before} HP "
                  f"(прогноз: ожидаемо {forecast[0]:.0f}, худший {forecast[1]:.0f}, {forecast[2]}). Скриншот: {shot}")
        self.hits.record(attacker, self.hits.attacker_level, move, name, target_element, self.hits.level,
                         self._last_max_hp, hp_before, rank, kill=True)
        path = self._learn_path.with_name("incidents.csv")
        new = not path.exists()
        with open(path, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write("time,enemy,rank,level,attacker,ability,hp_before,expected,worst,source\n")
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{name},{rank or ''},{self.hits.level or ''},{attacker},"
                    f"{move.name},{hp_before},{forecast[0]:.0f},{forecast[1]:.0f},{forecast[2]}\n")

    def _stats_pair(self, attacker, attacker_level, enemy, enemy_level, enemy_rank):
        """(статы моего крита, статы противника) для формулы урона или None.
        Мои — по данным игры (уровень, броски, бонусы); если копий вида несколько — та, что на этом уровне.
        Противник — по тирам вида, уровню с панели и среднему броску по рангу."""
        catalog = self._catalog_fn()
        if catalog is None:
            return None
        by_name = {n: s for s in catalog.species for n in s.names}
        mine, other = by_name.get(attacker), by_name.get(enemy)
        if mine is None or other is None:
            return None
        player = self._player_fn()
        copies = [m for m in (player.miscrits if player else []) if m.get("m") == mine.id]
        if attacker_level:
            copies = [m for m in copies if m.get("l") == attacker_level] or copies
        if copies:
            attacker_stats = my_stats(mine, max(copies, key=lambda m: m.get("l", 0)))
        else:
            attacker_stats = stats_at(mine, attacker_level or 1)
        roll = rank_roll(enemy_rank)
        enemy_stats = stats_at(other, enemy_level or 1, {k: roll for k in ("hp", "spd", "ea", "pa", "ed", "pd")})
        return attacker_stats, enemy_stats

    def _first_ability(self, my_name, moves):
        """Первая способность на первой странице, если это атака (у многих критов она лечит)."""
        pages = self._pages.get(my_name) or [[]]
        first = pages[0][0] if pages[0] else None
        return next((m for m in moves if m.name == first), None)

    def _save_rank_sample(self):
        """Нераспознанный значок ранга — в logs/ranks: по таким образцам доучиваем распознавание."""
        rect = self.eyes.region("enemy_rank")
        if rect is None or self.eyes.image is None:
            return
        folder = self._logs_dir / "ranks"
        folder.mkdir(exist_ok=True)
        x, y, w, h = rect
        ok, buf = cv2.imencode(".png", self.eyes.image[y:y + h, x:x + w])
        if ok:
            (folder / f"{time.strftime('%Y%m%d-%H%M%S')}.png").write_bytes(buf.tobytes())
            self._emit("rank_unknown", None)

    def _known_moves(self, my_name, species):
        """Атаки и прочие способности крита, которые видны на кнопках (все страницы)."""
        if my_name not in self._pages:
            self._pages[my_name] = self._read_pages(species)
        names = {n for page in self._pages[my_name] for n in page if n}
        moves = moves_from_catalog(species.abilities, names)
        attack_names = {m.name for m in moves}
        extras = [n for n in names if n not in attack_names]
        return moves, extras

    def _read_slots(self, species) -> list:
        ability_names = [a["name"] for a in species.abilities]
        return [self.eyes.read_name(slot, ability_names) for slot in ABILITY_SLOTS]

    def _read_pages(self, species) -> list:
        self.eyes.look()
        pages = [self._read_slots(species)]
        if not self.eyes.knows("ability_next"):
            return pages
        for _ in range(MAX_ABILITY_PAGES - 1):
            arrow = self.eyes.sees("ability_next")
            if arrow is None:
                break
            self._press(arrow, "ability_next")
            self._sleep(0.6)
            self.eyes.look()
            page = self._read_slots(species)
            if page == pages[-1]:
                break
            pages.append(page)
        self._page = len(pages) - 1
        self._say(f"способности: {' | '.join(', '.join(n or '?' for n in p) for p in pages)}")
        return pages

    def _goto_page(self, target: int):
        while self._page != target:
            arrow_id = "ability_next" if target > self._page else "ability_prev"
            self.eyes.look()
            arrow = self.eyes.sees(arrow_id)
            if arrow is None:
                raise Stuck(f"не вижу {arrow_id}")
            self._press(arrow, arrow_id)
            self._page += 1 if target > self._page else -1
            self._sleep(0.5)

    def _use(self, ability: str, my_name: str):
        for page_index, page in enumerate(self._pages[my_name]):
            if ability in page:
                self._goto_page(page_index)
                slot = ABILITY_SLOTS[page.index(ability)]
                self._press(self.eyes.region(slot), ability)
                return
        raise Stuck(f"не нашёл кнопку {ability}")

    def _use_any(self, names, my_name):
        self._use(random.choice(names), my_name)

    # ---- после боя ----

    def _after_battle(self, enemy, rank, captured, plat_used, my_ratio):
        if captured and enemy is not None:
            self.stats.catches.append((enemy.id, rank))
            self.stats.captures += 1
            if plat_used:
                self.stats.plat_captures += 1
            self._say(f"ПОЙМАН: {enemy.names[0]} ({enemy.rarity}) {rank or ''}")
        self._save_model()
        # закрываем окна после боя, пока они появляются
        for _ in range(8):
            found, rect = self._wait_for(("battle_won", "captured", *POPUPS), timeout=3)
            if found is None:
                break
            self._press(rect, found)
            self._sleep(0.8)
        self._publish_stats()
        if self._should_train():
            self.stats.trainings += self._train()
        if my_ratio * 100 < self.settings.heal_below:
            self._say(f"HP {my_ratio:.0%} — иду лечиться")
            self._run_route("heal")
            self.stats.heals += 1
        self._publish_stats()

    def _summary_says_train(self):
        """В сводке после боя (экран с Continue) у готового крита полоса «READY TO TRAIN».
        True/False, или None, если элемент «Есть кого тренировать» не обучен."""
        label = self._train_label()
        if label is None:
            return None
        # Опыт в сводке «капает» с анимацией, и крит может стать готовым прямо в ней. Ждём не фиксированное
        # время, а пока сводка перестанет меняться: без анимации это мгновенно, с ней — сколько она идёт.
        threshold = max(self.eyes.threshold, 0.8)
        area = self._summary_area()
        previous = None
        end = time.monotonic() + SUMMARY_MAX_WAIT
        while True:
            self.eyes.look()
            if find(self.eyes.image, label, threshold) is not None:
                return True
            current = cv2.resize(crop_area(self.eyes.image, area), None, fx=0.25, fy=0.25,
                                 interpolation=cv2.INTER_AREA).astype(np.int16)
            # анимируется маленькая полоска опыта — считаем изменившиеся пиксели, а не среднюю разницу
            if previous is not None and int((np.abs(current - previous).max(axis=2) > 30).sum()) < SUMMARY_STILL:
                return False
            if time.monotonic() >= end:
                return False
            previous = current
            time.sleep(0.15)

    def _summary_area(self):
        """Где на экране сводка: вокруг обученной полосы READY TO TRAIN и кнопки Continue."""
        rects = [self.eyes.teaching.elements[k].rect for k in ("train_ready", "battle_won") if k in self.eyes.teaching.elements]
        x0 = min(r[0] for r in rects) - 500
        y0 = min(r[1] for r in rects) - 400
        x1 = max(r[0] + r[2] for r in rects) + 500
        y1 = max(r[1] + r[3] for r in rects) + 100
        return max(x0, 0), max(y0, 0), x1 - max(x0, 0), y1 - max(y0, 0)

    def _train_label(self):
        snap = self.eyes.teaching.elements.get("train_ready")
        if snap is None or snap.image is None:
            return None
        return summary_label_of(snap.image)

    def _should_train(self) -> bool:
        steps = self.eyes.teaching.routes.get("train") or []
        if len(steps) < 3:
            return False
        every = self.settings.train_every
        if every and self.stats.battles % every == 0:
            return True
        seen, self._train_seen = self._train_seen, None
        if seen is not None:
            return seen  # сводка боя уже сказала, есть ли кого тренировать
        return self._train_button_blinks(steps[0].snap)

    def _train_button_blinks(self, snap) -> bool:
        """Когда кто-то готов к тренировке, кнопка Train мигает: в отдельном кадре она может выглядеть
        обычной, поэтому смотрим на неё полторы секунды и сравниваем яркость кадров."""
        self.eyes.look()
        rect = self.eyes.sees_snap(snap, threshold=0.6, anywhere=True)
        if rect is None:
            return False
        x, y, w, h = rect
        levels = []
        for _ in range(BLINK_FRAMES):
            self.eyes.look()
            levels.append(float(self.eyes.image[y:y + h, x:x + w].mean()))
            time.sleep(BLINK_INTERVAL)
        return max(levels) - min(levels) >= BLINK_DELTA

    def _train(self) -> int:
        """Тренирует всех готовых. Шаги маршрута «Тренировка»: [0] кнопка Train, [1] строка крита с меткой
        READY (берём только метку — подойдёт любой готовый крит), [2..-2] кнопки тренировки и попапы
        (TRAIN NOW, Continue, эволюция…), [-1] закрыть окно. Возвращает, скольких натренировали."""
        steps = self.eyes.teaching.routes["train"]
        open_step, ready_step, middle, close_step = steps[0], steps[1], steps[2:-1], steps[-1]
        ready_label = ready_label_of(ready_step.snap.image)
        self._state("тренировка")
        if not self._click_step(open_step, timeout=6):
            return 0
        trained = 0
        for _ in range(MAX_TRAININGS):
            row = self._find_anywhere(ready_label, timeout=3, threshold=0.75, clear_popups=True)
            if row is None:
                break
            self._press(row, "READY")
            self._sleep(0.4)
            for step in middle:
                self._click_step(step, timeout=4 if step.optional else 8)
            self._dismiss_popups_quietly()
            trained += 1
        self._say(f"натренировано: {trained}" if trained else "тренировать некого — закрываю окно")
        if not self._click_step(close_step, timeout=4):
            self._press_key(VK_ESCAPE)
        self._sleep(0.8)
        return trained

    def _find_anywhere(self, image, timeout, threshold=None, near=None, clear_popups=False):
        """Ищет картинку: сначала рядом с местом, где её показали при обучении (миллисекунды),
        потом по всему экрану (около 0,4 с на двух мониторах).
        clear_popups — закрывать по дороге известные попапы (rank up и т.п. всплывают поверх окна)."""
        end = time.monotonic() + timeout
        threshold = threshold or self.eyes.threshold
        while True:
            self._checkpoint()
            self.eyes.look()
            if clear_popups:
                popup = next(((p, r) for p in POPUPS if (r := self.eyes.sees(p)) is not None), None)
                if popup is not None:
                    self._press(popup[1], popup[0])
                    self._sleep(0.4)
                    continue
            rect = find(self.eyes.image, image, threshold, near=near) if near is not None else None
            if rect is None:
                rect = find(self.eyes.image, image, threshold)
            if rect is not None or time.monotonic() >= end:
                return rect
            time.sleep(0.15)

    def _click_step(self, step, timeout) -> bool:
        rect = self._find_anywhere(step.snap.image, timeout, near=step.snap.rect, clear_popups=True)
        if rect is None:
            return False
        for attempt in range(3):
            self._press(rect, "train step" + (f" (ещё раз, {attempt})" if attempt else ""))
            self._sleep(random.uniform(0.35, 0.6))
            # кнопка осталась на месте — нажатие не сработало (промах или окно ещё не ожило), жмём ещё
            self.eyes.look()
            again = find(self.eyes.image, step.snap.image, self.eyes.threshold, near=rect)
            if again is None:
                return True
            rect = again
        return True

    def _dismiss_popups_quietly(self):
        for _ in range(4):
            found, rect = self._wait_for(POPUPS, timeout=0.6, interval=0.15)
            if found is None:
                return
            self._press(rect, found)
            self._sleep(0.6)

    def _run_route(self, name: str):
        steps = self.eyes.teaching.routes.get(name)
        if not steps:
            self._say(f"маршрут «{name}» не записан — пропускаю")
            return
        self._state(f"маршрут: {name}")
        for i, step in enumerate(steps, 1):
            end = time.monotonic() + (4 if step.optional else 15)
            rect = None
            while rect is None and time.monotonic() < end:
                self._checkpoint()
                self.eyes.look()
                rect = self.eyes.sees_snap(step.snap) or self.eyes.sees_snap(step.snap, anywhere=True)
                if rect is None:
                    time.sleep(0.4)
            if rect is None:
                if step.optional:
                    continue
                shot = self._screenshot(f"route-{name}-{i}")
                self.pause(f"маршрут «{name}»: не нашёл шаг {i}")
                self._say(f"маршрут «{name}»: не нашёл шаг {i}, скриншот {shot}")
                self._checkpoint()  # ждём, пока пользователь поправит и снимет паузу
                return
            self._press(rect, f"{name} step {i}")
            self._sleep(random.uniform(0.8, 1.5))
