"""Конечный автомат бота: охота → бой → после боя → тренировка/лечение. Работает в своём потоке."""

import json
import logging
import math
import random
import re
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .brain.capture import CAPTURE, PLAT_RARITIES, decide
from .brain.combat import (ATTACK, PRECIOUS_EXTRA, PRECIOUS_SEEN, PRECIOUS_UNSEEN, STALL, Action, DamageModel, choose_capture,
                           choose_kill, moves_from_catalog, multiplier)
from .brain.effects import FOE, ME, BattleState, effects_of
from .brain.formula import owned_copy, stats_at, stats_pair
from .brain.hits import CaptureView, HitBook
from .collection import Collection
from .eyes import CORE_THRESHOLD, core_of
from .mouse import VK_ESCAPE, FailSafe, press_key
from .screen import crop as crop_area
from .screen import best_name, find, find_scored, fix_hp
from .storage import ABILITY_SLOTS, POPUPS, TEAM_SLOTS
from .worldmap import Locator, species_in_zone, to_map, to_view, view_rect

log = logging.getLogger(__name__)

GAME_EXE = "miscrits.exe"
MAX_ABILITY_PAGES = 5
WALK_STEPS = 14
CLICK_HALF = 14
MISS_RETRIES = 2
EXPLORE_MIN_HP = 0.5  # изучаем, только пока у моего крита больше половины HP
MAX_TRAININGS = 4  # критов в команде
SWITCH_FAILS_MAX = 3  # столько неудачных смен подряд — и смена выключается до конца сессии
NO_CAPTURE_TURNS = 2  # столько ходов подряд не видим Capture, когда надо ловить, — пауза и скриншот
# способности, которыми можно тянуть время при поимке: не трогают HP цели
SAFE_STALL_TYPES = {"Buff", "Heal", "Hot", "Block", "Cleanser", "Negate", "Antiheal", "Sleep", "Paralyze"}
DAMAGING_TYPES = {"Attack", "Dot", "Poison", "Bleed", "TimeBomb", "Disease", "Confuse", "Barbed", "LifeSteal", "Bot"}


def safe_stall(species, names) -> list:
    """Способности из names, которые точно не нанесут урон цели (и в дополнительных эффектах тоже)."""
    by_name = {a.get("name"): a for a in species.abilities} if species else {}
    out = []
    for name in names:
        a = by_name.get(name)
        if a is None or a.get("type") not in SAFE_STALL_TYPES:
            continue
        extra = {x.get("type") for x in (a.get("additional") or []) if isinstance(x, dict)}
        if extra & DAMAGING_TYPES:
            continue
        out.append(name)
    # сначала то, что на себя (баффы, лечение, блок), — меньше всего риска
    return sorted(out, key=lambda n: (by_name[n].get("target") != "Self", n))
PAGE_READ_TRIES = 3  # столько раз перечитываем страницы способностей, если на них остались «?»
ACTION_LEAVE_WAIT = 6.0  # с: столько ждём, пока после нашего действия «мой ход» сменится чужим
LIGHT_BUTTONS_AFTER = 4.0  # с: столько ждём обученный «Мой ход», прежде чем верить светлым кнопкам
MY_TURN_STRICT = 0.93  # картинка «Мой ход» — строгий порог: в строке сообщений любой текст похож на любой
PORTRAIT_MATCH = 0.8  # насколько портрет в столбике должен совпасть с запомненным
SWITCH_FAR = 0.15  # смена ради поимки — только если до порога HP ещё больше 15% макс. HP цели
BLINK_FRAMES, BLINK_INTERVAL, BLINK_DELTA = 7, 0.15, 12.0
SUMMARY_MAX_WAIT = 2.0  # с: дольше анимация опыта в сводке не идёт
SUMMARY_STILL = 12  # изменившихся пикселей (в уменьшенном кадре), меньше которых сводка неподвижна
SUMMARY_SHOTS = 60  # столько последних снимков сводки храним в logs/summary
TEAM_SHOTS = 80  # и вырезок плашек уровней команды в logs/team
KEY_RETRY = 600  # с: если ключ после маршрута «Обновить ключ» и боя так и не обновился — повторяем не чаще


def ready_label_of(row):
    """Из снимка строки готового крита — только низ с меткой «READY TO TRAIN», без имени и портрета:
    так шаблон подходит к любому готовому криту."""
    h = row.shape[0]
    return row[int(h * 0.6):int(h * 0.95), :]


def count_names(pages) -> int:
    return len({n for page in pages for n in page if n})


def has_gaps(pages) -> bool:
    """Есть ли нераспознанные кнопки. Пустые слоты в конце последней страницы — не пробел: у крита просто
    меньше способностей (или следующие ещё закрыты по уровню)."""
    if any(n is None for page in pages[:-1] for n in page):
        return True
    last = pages[-1] if pages else []
    named = [i for i, n in enumerate(last) if n]
    return bool(named) and any(n is None for n in last[:max(named) + 1])


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
                 game_rect_fn=None, key_expired_fn=None):
        """hands(rect) — клик; catalog_fn() -> Catalog; player_fn() -> Player | None (коллекция из HUD);
        foreground() -> имя exe активного окна; on_event(kind, data) — для GUI;
        location_fn() -> (локация, зона) | None; companion — карты и маркеры сайта;
        game_rect_fn() -> окно игры (x, y, w, h) в координатах скриншота;
        key_expired_fn() -> истёк ли ключ игры (HUD больше не получает коллекцию и зону)."""
        self.eyes = eyes
        self._key_expired_fn = key_expired_fn
        self._key_refreshed_at = None  # когда последний раз проходили маршрут «Обновить ключ»
        self._press_key = press_key  # подменяется в тестах: настоящие нажатия клавиш там не нужны
        self._move_mouse = None  # (x, y) на скриншоте -> подвести курсор; задаёт окно программы
        self._acted = False  # только что сделали боевое действие — следующий «мой ход» ждём после чужого
        self._location_fn = location_fn
        self._companion = companion
        self._game_rect_fn = game_rect_fn
        self._locators = {}
        self._marker_used = {}
        self._all_caught_said = False
        self._portraits = {}  # имя крита -> картинка его портрета в столбике команды (узнаём при смене)
        self._crit_hp = {}  # имя крита -> доля HP, когда видели его последний раз
        self._heal_warned = False  # предупреждали ли, что маршрут лечения не записан
        self._seen_levels = {}  # имя крита -> уровень, увиденный на экране боя
        self._no_targets_said = None  # (локация, зона), где уже сказали, что целей охоты тут нет
        self._team_levels = {}  # "active"/"team_1".. -> уровень с верхней панели мира перед боем
        self._battle_levels = {}  # то же, но с учётом смен в текущем бою
        self._team_suspect = None  # чтение команды, похожее на потерянную цифру, — ждёт подтверждения
        self._crit_hp_abs = {}  # имя крита -> HP в единицах, когда видели его последний раз
        self._enemy_hit = None
        self.fx = BattleState()  # эффекты текущего боя на моём крите и на противнике
        self._messages = []  # строки «X uses Y», увиденные с прошлого хода
        self._ability_names = {}  # имя крита -> названия всех его способностей (для чтения кнопок)
        self._page_reads = {}  # имя крита -> сколько раз читали его страницы способностей
        self._switch_broken = False
        self._switch_fails = 0  # неудачных смен подряд
        self._blocked_slots = set()  # ячейки, смена на которые в этом бою не прошла
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
        self.hits = HitBook(learn_path.with_name("hits.csv"), self.model, stats=self._stats_pair,
                            species_of=self._species_named)
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

    def _act(self, rect, what: str = ""):
        """Боевое действие (удар, поимка, смена крита): после него ждём конца своего хода."""
        self._press(rect, what)
        self._acted = True

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
        self._refresh_key_if_needed()
        self._hunt()

    def _refresh_key_if_needed(self):
        """Ключ игры истёк — игра продлевает его, только когда её окно (Quests) получает отказ сервера, а
        в лог новый ключ попадает после боя. Поэтому: проходим записанный маршрут «книжка → Quests → закрыть»,
        а бой будет следующим шагом сам. Не чаще раза в KEY_RETRY: если не помогло — пишем и ждём."""
        if self._key_expired_fn is None or not self._key_expired_fn():
            return
        if not self.eyes.teaching.routes.get("refresh_key"):
            if self._key_refreshed_at is None:
                self._key_refreshed_at = time.monotonic()
                self._say("ключ игры истёк, а маршрут «Обновить ключ игры» не записан — коллекция и зона "
                          "не обновляются (запишите его на вкладке «Точки и маршруты»)")
            return
        now = time.monotonic()
        if self._key_refreshed_at is not None and now - self._key_refreshed_at < KEY_RETRY:
            return
        again = self._key_refreshed_at is not None
        self._key_refreshed_at = now
        self._say("ключ игры истёк — открываю Quests, чтобы игра его продлила" + (" (ещё раз)" if again else ""))
        self._run_route("refresh_key")

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
        self._read_team()
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
        if not farm and not markers and self._no_targets_said != (location, area):
            # цели есть, но не в этой зоне — бот уйдёт фармить ближайшие точки; объясняем, почему
            self._no_targets_said = (location, area)
            where_else = []
            for name in sorted(targets):
                species = next((s for s in catalog.species if s.names[0] == name), None)
                zones = sorted((species.locations.get(location) or {}).keys()) if species else []
                where_else.append(f"{name} — {'зона ' + ', '.join(zones) if zones else 'не в ' + location}")
            self._say(f"целей охоты в зоне {area} нет ({'; '.join(where_else)}) — фармлю ближайшие точки")
        return (location, markers) if markers else None

    def _view(self):
        """(прямоугольник обзора на скриншоте, картинка обзора) — окно игры без интерфейса."""
        game = self._game_rect_fn() if self._game_rect_fn else None
        if game is None:
            raise Stuck("не нашёл окно игры")
        x, y, w, h = view_rect(game)
        return (x, y, w, h), self.eyes.image[y:y + h, x:x + w]

    def _where(self, location):
        """Где сейчас экран на карте локации: (обзор, Placement).
        Зоны одной локации нарисованы на карте сайта в разном масштабе (у Mansion: улица 0.375, чердак 0.40),
        поэтому масштаб — свой у каждой зоны; не нашли себя с известным масштабом — подбираем его заново."""
        key = self._scale_key(location)
        locator = self._locators.get(key)
        world = self._companion.map_image(location)
        if world is None:
            raise Stuck(f"нет карты локации {location}")
        if locator is None:
            locator = self._locators[key] = Locator(world, scale=self._map_scales().get(key))
        self.eyes.look()
        rect, view = self._view()
        known = locator.scale
        place = locator.locate(view)
        if place is None and known is not None:
            self._state("подбираю масштаб карты для этой зоны…")
            locator = self._locators[key] = Locator(world)
            known = None
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
        where = self._location_fn() if self._location_fn else None
        zone = where[1] if where and where[0] == location else "?"
        return f"{location}|{zone}|{game[2]}x{game[3]}" if game else f"{location}|{zone}"

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
        self._blocked_slots = set()
        self._battle_levels = dict(self._team_levels)
        self._enemy_hit = None  # самый сильный удар противника по моим критам в этом бою
        my_prev = None  # (имя моего крита, его HP на прошлом ходу)
        self.fx = BattleState()
        self._messages = []
        self.hits.modifiers = None
        fx_seen = ""
        enemy = rank = decision = None
        reads = 0  # сколько раз пробовали прочитать противника (не больше двух ходов)
        plat_used = 0
        last = None  # (имя моего крита, Move, HP противника до удара)
        my_ratio = 1.0
        captured = False
        switched = None  # (портрет, кто был до смены) — на следующем ходу узнаем, получилось ли
        no_capture_turns = 0  # сколько ходов подряд хотим поймать, а кнопки Capture нет
        explore_switch_done = False
        self._state("бой")
        while True:
            turn = self._wait_turn()
            if turn is None:
                self.eyes.look()
                if self.eyes.sees("battle") is not None:
                    raise Stuck("бой: не дождался своего хода")
                # экран боя пропал, а окно победы/поимки ещё не появилось — подождём его, чтобы не потерять
                # добивающий удар (иначе он не попадёт в журнал)
                turn, _ = self._wait_for(("battle_won", "captured"), timeout=4)
                if turn is None:
                    break
            if turn == "captured":
                captured = True
                break
            if turn == "battle_won":
                if decision is not None and decision.action == CAPTURE and last is not None:
                    self._killed_while_catching(enemy, rank, last)
                elif last is not None and self._last_max_hp:
                    # бой кончился нашим ударом — следующего хода нет, точный урон не увидеть: записываем
                    # «не меньше HP, что было у цели», иначе удары, которые убивают сразу, в журнал не попадали бы
                    attacker, move, hp_before = last
                    self._record_hit(attacker, self.hits.attacker_level, move, enemy.names[0] if enemy else "?",
                                     enemy.element if enemy else "", self.hits.level, self._last_max_hp, hp_before,
                                     rank, kill=True)
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
                who = f"{enemy.names[0]} ({enemy.rarity}) {rank or '?'} ур. {enemy_level or '?'}" if enemy else "?"
                self._say(f"бой: {who} → {'ЛОВИМ' if decision.action == CAPTURE else 'убиваем'} — {decision.reason}")
                self._emit("battle_log", f"──── {who} → {'ЛОВИМ' if decision.action == CAPTURE else 'убиваем'} "
                                         f"({decision.reason}) ────")
            hp = self._enemy_hp(enemy)
            if hp:
                self._last_max_hp = hp[1]
            mine = self.eyes.read_hp("my_hp")
            if mine:
                my_ratio = mine[0] / mine[1]
            my_name = self.eyes.read_name("my_name", by_name)
            me = by_name.get(my_name)
            if my_name and mine:
                self._crit_hp[my_name] = my_ratio  # HP сохраняется между боями до лечения
                self._crit_hp_abs[my_name] = mine[0]
                if my_prev and my_prev[0] == my_name and mine[0] < my_prev[1]:
                    # сколько противник снял за свой ход (с ядом и прочим) — по этому и судим, переживёт ли крит
                    self._enemy_hit = max(self._enemy_hit or 0, my_prev[1] - mine[0])
                my_prev = (my_name, mine[0])
            my_level = self.eyes.read_level("my")
            self.hits.attacker_level = my_level
            if my_name and my_level:
                self._seen_levels[my_name] = my_level
                if switched is None:
                    self._battle_levels["active"] = my_level
            if switched is not None and my_name:
                portrait, before, slot = switched
                switched = None
                if my_name == before:
                    # крит не сменился (окно подтверждения не появилось: крит без HP, окно не успело…) — закрываем
                    # окно, чужой портрет не запоминаем; эту ячейку до конца боя не трогаем, а совсем выключаем
                    # смену, только если не выходит несколько раз подряд
                    self._press_key(VK_ESCAPE)
                    self._blocked_slots.add(slot)
                    self._switch_fails += 1
                    shot = self._screenshot("switch-failed")
                    if self._switch_fails >= SWITCH_FAILS_MAX:
                        self._switch_broken = True
                        self._say(f"⚠ смена крита не сработала {self._switch_fails} раза подряд — до конца сессии без смен "
                                  f"(проверьте «Подтвердить смену крита»). Скриншот: {shot}")
                    else:
                        self._say(f"⚠ смена крита ({slot}) не сработала — в этом бою этого крита не трогаю. Скриншот: {shot}")
                    continue
                self._switch_fails = 0
                self._portraits[my_name] = portrait  # теперь знаем, чей это портрет
                # смена меняет местами активного и того, кто был в ячейке
                self._battle_levels["active"], self._battle_levels[slot] = (
                    self._battle_levels.get(slot), self._battle_levels.get("active"))
                self._say(f"сменил крита: теперь {my_name}")
                self.fx.switched(ME)  # всё, что висело на ушедшем, ушло вместе с ним
                level = my_level or self._crit_level(my_name)  # с экрана боя надёжнее: коллекция HUD могла устареть
                enemy_level = self.hits.level
                if decision.action != CAPTURE:
                    if not level or not enemy_level:
                        self._say(f"не вижу уровня ({my_name}: {level or '?'}, противник: {enemy_level or '?'}) — "
                                  "не могу проверить, не слишком ли он слабый")
                    elif level < enemy_level - self.settings.level_gap:
                        # прежний крит после смены стоит в той ячейке, через которую меняли, — туда и возвращаемся,
                        # даже если портреты остальных ещё незнакомы
                        back = self._healthy_slot(enemy_level, exclude=my_name)
                        back = slot if back is None else back
                        self._say(f"{my_name} ур. {level} против ур. {enemy_level} — слишком слабый, меняю обратно")
                        switched = (self._switch(back), my_name, back)
                        last = None
                        continue
            target_element = enemy.element if enemy else ""
            self._take_messages(enemy, my_name)
            if last and hp and last[0] == my_name and hp[0] <= last[2]:
                # один наш удар за ход: урон = HP цели до него минус HP сейчас. Если HP выросло (противник
                # подлечился), удар не записываем — разница была бы неправдой
                # что искажает разницу HP цели: эффекты на ней самой, а с моей стороны — только баффы урона
                # и непонятное (лечение моего крита, как у The Big Finale, урон по цели не меняет)
                busy = self.fx.dirty(FOE) + [e.name for e in self.fx.on[ME] if e.kind == "unknown"]
                damage = last[2] - hp[0]
                factor, unsure = self.hits._mod_factor(last[0], last[1], target_element)
                if busy or unsure:
                    # яд, лечение, блок на цели или непонятный эффект: разница HP — не урон удара, не пишем
                    why = ", ".join(sorted(set(busy))) or "баффы без статов для пересчёта"
                    self._emit("battle_log", f"📝 не записал {last[1].name}: на поле {why}")
                else:
                    if factor != 1.0:
                        # баффы и дебаффы статов во время удара: пишем урон, приведённый к бою без них
                        self._emit("battle_log", f"📝 {last[1].name}: {damage} при эффектах ×{factor:.2f} → "
                                                 f"в журнал {round(damage / factor)}")
                        damage = round(damage / factor)
                    self._record_hit(last[0], my_level, last[1], enemy.names[0] if enemy else "?",
                                     target_element, self.hits.level, hp[1], damage, rank)
            self.fx.turn_passed(ME)
            self.fx.turn_passed(FOE)
            mods = (self.fx.stat_delta(ME), self.fx.stat_delta(FOE), self.fx.negated(FOE))
            self.hits.modifiers = mods if (mods[0] or mods[1] or mods[2]) else None
            described = f"на противнике: {self.fx.describe(FOE) or '—'}; на мне: {self.fx.describe(ME) or '—'}"
            if described != fx_seen and (self.fx.on[ME] or self.fx.on[FOE] or fx_seen):
                self._say(f"эффекты — {described}")
                fx_seen = described
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
                # неразобранный эффект на поле — урон непредсказуем: берём запас как для Exotic/Legendary
                careful = precious or bool(self.fx.unknown())
                action = choose_capture(moves, CaptureView(self.hits), my_name, target_element,
                                        hp[0] if hp else 1, hp[1] if hp else 1, chance,
                                        self.settings.capture_min_chance, (can_capture or plat) is not None,
                                        precious=careful, floor=self.settings.capture_hp_floor,
                                        extra=self.fx.dot(FOE))
                high_chance = chance is not None and chance >= self.settings.capture_min_chance
                if (action.kind in (CAPTURE, STALL) and not high_chance and hp
                        and hp[0] - self.settings.capture_hp_floor > SWITCH_FAR * hp[1]):
                    # ловить приходится не от хорошего шанса, а потому что бить нечем, — может, другой крит подведёт
                    # у текущего крита нет удара, безопасно приближающего цель к порогу, а до порога далеко —
                    # ищем в команде того, у кого такой удар есть
                    better = self._better_catcher(my_name, by_name, target_element, hp, precious)
                    if better is not None:
                        slot, name, reason = better
                        self._say(f"меняю {my_name} на {name}: {reason}")
                        switched = (self._switch(slot), my_name, slot)
                        last = None
                        continue
                self._explain(enemy, rank, hp, my_name, mine, moves, target_element, decision, action, chance,
                              precious)
                if action.kind == CAPTURE:
                    if can_capture is not None:
                        self._act(can_capture, "capture")
                        self._say(f"пробую поймать (шанс {chance if chance is not None else '?'}%)")
                    else:
                        plat_used += 1
                        self._act(plat, "plat_capture")
                        self._say(f"платиновая поимка {plat_used}/{self.settings.plat_capture_limit}")
                    last = None
                    self._sleep(2.5)
                    continue
                if action.kind == STALL:
                    # поймать сейчас нельзя, а бить опасно: занимаем ход тем, что точно не тронет HP цели
                    # (никакого яда, кровотечения и прочего урона по ходам — так однажды добили Quirk)
                    safe = safe_stall(me, extras)
                    no_capture_turns += 1
                    if not safe or no_capture_turns >= NO_CAPTURE_TURNS:
                        why = ("кнопку Capture не вижу уже " + str(no_capture_turns) + " хода") if safe else \
                            "нет способности, которая точно не нанесёт урон"
                        shot = self._screenshot("cannot-capture")
                        self._say(f"⚠ {enemy.names[0] if enemy else '?'}: {why} — пауза, сходите сами (скриншот: {shot})")
                        self.pause(f"поимка: {why} — сделайте ход сами и снимите паузу")
                        self._checkpoint()
                        no_capture_turns = 0
                        last = None
                        continue
                    self._say(f"тяну время: {safe[0]} (не наносит урон), поймать сейчас нельзя")
                    self._use(safe[0], my_name)
                    self._applied(me, safe[0])
                    self._park_mouse()
                    last = None
                    continue
                no_capture_turns = 0
                move = action.move
            else:
                move = None
                in_danger = bool(mine and self._enemy_hit and mine[0] <= 2 * self._enemy_hit)
                if (my_ratio * 100 < self.settings.low_hp_switch_pct or in_danger) and self.settings.explore_switch:
                    slot = self._healthy_slot(self.hits.level, exclude=my_name)
                    if slot is not None:
                        who = self._who_in(slot) or "другого крита"
                        why = (f"у {my_name} {mine[0]} HP, а противник снимает до {self._enemy_hit} за ход"
                               if in_danger else f"у {my_name} HP {my_ratio:.0%}")
                        self._say(f"{why} — меняю на {who} ({self._slot_note(slot)}), чтобы не умер")
                        switched = (self._switch(slot), my_name, slot)
                        last = None
                        continue
                if (not explore_switch_done and self.settings.explore_switch and my_ratio > EXPLORE_MIN_HP
                        and random.random() * 100 < self.settings.explore_switch_pct):
                    explore_switch_done = True
                    slot = self._least_known_slot(self.hits.level, exclude=my_name)
                    if slot is not None:
                        who = self._who_in(slot) or "незнакомого крита"
                        self._say(f"убиваем, можно поучиться: пробую {who} ({self._slot_note(slot)}) против "
                                  f"ур. {self.hits.level or '?'} — по нему мало данных об уроне")
                        switched = (self._switch(slot), my_name, slot)
                        last = None
                        continue
                explore_switch_done = True
                if self.settings.explore_damage and my_ratio > EXPLORE_MIN_HP:
                    # убивать можно — заодно пробуем атаку, по которой меньше всего данных против этой стихии
                    least = min(moves, key=lambda m: self.hits.observed(my_name, m, target_element))
                    if self.hits.observed(my_name, least, target_element) < self.settings.explore_enough:
                        move = least
                        self._state(f"изучаю урон: {least.name}")
                if move is None and self.settings.kill_with_first:
                    move = self._first_ability(my_name, moves)
                if move is None:
                    move = choose_kill(moves, self.hits, my_name, target_element)
                    why = "самая сильная атака по ожиданию"
                elif self._first_ability(my_name, moves) is move:
                    why = "атака с лечением"
                else:
                    why = f"изучаю урон: по {move.element} → {target_element or '?'} мало данных"
                self._explain(enemy, rank, hp, my_name, mine, moves, target_element, decision,
                              Action(ATTACK, move), None, False, why)
            self._use(move.name, my_name)
            self._applied(me, move.name)
            last = (my_name, move, hp[0]) if hp else None
            self._park_mouse()
            self._sleep(1.0)
        self._emit("battle_end", None)
        self._after_battle(enemy, rank, captured, plat_used, my_ratio)
        return enemy

    def _take_messages(self, enemy, my_name):
        """Строки «X uses Y» с прошлого хода: способности противника — в состояние боя (свои бот учитывает сам,
        когда нажимает). Название сверяется со списком способностей этого вида из каталога."""
        messages, self._messages = self._messages, []
        if enemy is None:
            return
        names = [a["name"] for a in enemy.abilities]
        for text in messages:
            m = re.match(r"\s*(.*?)\s+uses?\s+(.+?)[\s.!]*$", text, re.IGNORECASE)
            if not m:
                continue
            who, what = m.group(1), m.group(2)
            if my_name and best_name([who], [my_name]) and not best_name([who], enemy.names):
                continue  # это наш ход
            ability = best_name([what], names)
            if ability is None:
                self._say(f"противник: «{text}» — способность не узнал")
                continue
            entry = next(a for a in enemy.abilities if a["name"] == ability)
            if effects_of(entry):
                self.fx.apply(entry, FOE)
                self._emit("battle_log", f"⚑ {enemy.names[0]}: {ability} — {entry.get('desc', '')}")

    def _applied(self, me, ability_name):
        """Свою способность нажали — её эффекты (яд на противнике, бафф на себя…) в состояние боя."""
        if me is None:
            return
        entry = next((a for a in me.abilities if a.get("name") == ability_name), None)
        if entry is not None and effects_of(entry):
            self.fx.apply(entry, ME)

    def _enemy_hp(self, enemy):
        """HP противника с проверкой: сколько его бывает у этого вида на этом уровне (по прошлым боям, иначе
        по формуле). Потерянная «1» (112 → 12) восстанавливается, невозможное чтение — None (не знаю)."""
        raw = self.eyes.read_hp("enemy_hp")
        if raw is None or enemy is None:
            return raw
        level = self.hits.level
        seen = self.hits.typical_max_hp(enemy.names[0], level)
        low = stats_at(enemy, level, {"hp": 1})["hp"] if level else None
        fixed = fix_hp(raw, seen, low, getattr(self.eyes, "last_hp_bar", None))
        if fixed != raw:
            self._say(f"HP противника прочитано как {raw[0]}/{raw[1]} — "
                      + (f"исправил на {fixed[0]}/{fixed[1]}" if fixed else "не похоже на правду, считаю непрочитанным")
                      + f" (обычно у {enemy.names[0]} ур. {level or '?'}: {seen or '?'})")
        return fixed

    def _park_mouse(self):
        """Увести курсор с кнопок способностей: иначе игра показывает подсказку («Attack Power…»),
        она закрывает строку «It's your turn!», и бот не узнаёт свой ход."""
        if self._move_mouse is None or self._game_rect_fn is None:
            return
        game = self._game_rect_fn()
        if game is None:
            return
        x, y, w, h = game
        self._move_mouse((int(x + w * random.uniform(0.35, 0.65)), int(y + h * random.uniform(0.25, 0.45))))

    def _wait_turn(self):
        """Ждёт своего хода (или конца боя). «Мой ход» часто обучен на кнопке способности первой страницы —
        если после удара со второй страницы она так и открыта, ход не узнаётся; тогда листаем назад."""
        start = time.monotonic()
        end = start + 60
        gone = 0
        shot_taken = False
        if self._acted:
            # только что сходили: строка «It's your turn!» и светлые кнопки ещё видны, пока идёт анимация
            # (так бот однажды ударил сразу после Capture) — ждём, пока наш ход действительно закончится
            self._acted = False
            leave = start + ACTION_LEAVE_WAIT
            while time.monotonic() < leave:
                self._checkpoint()
                self.eyes.look()
                for done in ("battle_won", "captured"):
                    if self.eyes.sees(done) is not None:
                        return done
                if self.eyes.sees("battle") is None or not self._is_my_turn(float("inf")):
                    break
                time.sleep(0.25)
        while time.monotonic() < end:
            if not shot_taken and time.monotonic() - start > 10:
                # долго не узнаём свой ход — сохраним, как выглядит экран: по нему правится распознавание
                shot_taken = True
                self.eyes.look()
                self._say(f"долго жду свой ход — скриншот: {self._screenshot('turn-wait')}")
            self._checkpoint()
            self.eyes.look()
            for done in ("battle_won", "captured"):
                if self.eyes.sees(done) is not None:
                    return done
            if self.eyes.sees("battle") is None:
                gone += 1
                if gone >= 2:
                    return None
                time.sleep(0.3)
                continue
            gone = 0
            if self._is_my_turn(start):
                return "my_turn"
            time.sleep(0.25)
        return None

    def _is_my_turn(self, waiting_since) -> bool:
        """Свой ход. Главное — строка сообщений боя: «It's your turn» — наш ход, любой другой текст
        («Spike uses Bite») — ещё идёт чужой ход или анимация. Если строка пустая — запасные признаки:
        обученная картинка «Мой ход» (строгий порог) или, если долго ничего, светлые кнопки способностей."""
        text = self.eyes.turn_message()
        message = text.lower()
        if re.search(r"\byour\b|s your|your tur", message):
            return True
        if re.search(r"\buses?\b|\bused\b|\bmissed\b|\bturns?\b", message):
            if re.search(r"\buses?\b", message) and text not in self._messages:
                self._messages.append(text)  # «Humbug uses Debilitate» — разберём, какой эффект он наложил
            return False  # «Spike uses Bite» и т.п. — идёт чужой ход или анимация
        # строка пустая или нечитаемая (например, её закрыла подсказка) — запасные признаки
        if self.eyes.sees_strictly("my_turn", MY_TURN_STRICT) is not None:
            return True
        return time.monotonic() - waiting_since > LIGHT_BUTTONS_AFTER and self.eyes.abilities_active()

    # ---- смена крита ----

    def _read_team(self):
        """Перед боем — уровни команды с верхней панели мира (там же кнопка Train): первый — тот, кто выйдет
        в бой, дальше — ячейки столбика по порядку. Так уровень каждого известен ДО смены."""
        steps = self.eyes.teaching.routes.get("train") or []
        if not steps or not hasattr(self.eyes, "read_team_levels"):
            return
        self.eyes.look()
        levels = self.eyes.read_team_levels(steps[0].snap)
        if not any(levels):
            return  # панель не видно (окно, попап) — оставляем прошлые
        slots = ("active", *TEAM_SLOTS)
        team = dict(zip(slots, levels))
        old = self._team_levels
        dropped = [s for s in slots if old.get(s) and team.get(s) and team[s] < old[s]]
        lost_digit = len(dropped) == 1 and str(old[dropped[0]]) != str(team[dropped[0]]) and (
            str(old[dropped[0]]).startswith(str(team[dropped[0]])) or str(old[dropped[0]]).endswith(str(team[dropped[0]])))
        if lost_digit and levels != self._team_suspect:
            # одна плашка «потеряла» цифру (35 → 5), остальные на месте — похоже на сбой чтения: оставляем прежний
            # уровень, пока то же самое не прочитается второй раз подряд. Сменили критов (35 → 2, сразу несколько
            # плашек, или повторилось) — принимаем
            s = dropped[0]
            self._say(f"команда: {s} {old[s]}→{team[s]} — похоже на потерянную цифру, проверю в следующий раз")
            self._team_suspect = levels
            team[s] = old[s]
        else:
            self._team_suspect = None
        if team != old:
            self._say("команда: " + " · ".join(str(team[s]) if team[s] else "?" for s in slots))
            self._save_team_badges(levels)
        self._team_levels = team

    def _save_team_badges(self, levels):
        """Вырезки плашек уровней — в logs/team: при новом сбое чтения будет что разобрать."""
        boxes = getattr(self.eyes, "last_team_boxes", None) or []
        if self.eyes.image is None or not boxes:
            return
        folder = self._logs_dir / "team"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for i, (box, level) in enumerate(zip(boxes, levels)):
            if box is None:
                continue
            ok, buf = cv2.imencode(".png", crop_area(self.eyes.image, box))
            if ok:
                (folder / f"{stamp}-{i}-{level or 'x'}.png").write_bytes(buf.tobytes())
        for old in sorted(folder.glob("*.png"))[:-TEAM_SHOTS]:
            old.unlink(missing_ok=True)

    def _slot_level(self, slot, name=None):
        """Уровень крита в ячейке: с верхней панели (с учётом смен в этом бою), иначе по имени."""
        level = self._battle_levels.get(slot)
        if level is None and name:
            level = self._crit_level(name)
        return level

    def _slot_note(self, slot) -> str:
        """«ур. 22, HP 37%» про того, кто в ячейке, — для журнала."""
        name = self._who_in(slot)
        level = self._slot_level(slot, name)
        hp = self._crit_hp.get(name) if name else None
        return f"ур. {level or '?'}, HP {f'{hp:.0%}' if hp is not None else '?'}"

    def _survives(self, name) -> bool:
        """Переживёт ли крит два хода противника по самому сильному его удару в этом бою (если HP известно)."""
        hp = self._crit_hp_abs.get(name) if name else None
        return not (hp is not None and self._enemy_hit and hp <= 2 * self._enemy_hit)

    def _slot_fits(self, slot, name, enemy_level) -> bool:
        """Можно ли выпускать того, кто в ячейке: уровень известен и не ниже противника больше чем на level_gap
        (неизвестный — только против совсем слабых), и не полуживой."""
        level = self._slot_level(slot, name)
        gap = self.settings.level_gap
        if level is None:
            if not enemy_level or enemy_level > 1 + gap:
                return False
        elif enemy_level and level < enemy_level - gap:
            return False
        if not self._survives(name):
            return False
        return name is None or self._crit_hp.get(name, 1.0) * 100 >= self.settings.test_min_hp_pct

    def _team(self):
        if self._switch_broken or not self.eyes.knows("switch_confirm"):
            return []  # без подтверждения смена не пройдёт — не пробуем
        return [slot for slot in TEAM_SLOTS if self.eyes.knows(slot) and slot not in self._blocked_slots]

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
        self._act(self.eyes.region(slot), f"смена крита ({slot})")
        if self.eyes.knows("switch_confirm"):
            found, rect = self._wait_for(("switch_confirm",), timeout=6)
            if rect is not None:
                self._press(rect, "подтвердить смену")
        self._page = 0
        self._sleep(1.0)
        return portrait

    def _crit_level(self, name):
        """Уровень моего крита: последний увиденный на экране боя, иначе из коллекции HUD."""
        if name in self._seen_levels:
            return self._seen_levels[name]
        species = self._species_named(name) if name else None
        copy = owned_copy(self._player_fn(), species) if species else None
        return copy.get("l") if copy else None

    def _fit_for(self, name, enemy_level) -> bool:
        """Можно ли выпускать этого крита: не ниже противника больше чем на level_gap уровней и не полуживой."""
        level = self._crit_level(name)
        if level and enemy_level and level < enemy_level - self.settings.level_gap:
            return False
        return self._crit_hp.get(name, 1.0) * 100 >= self.settings.test_min_hp_pct

    def _least_known_slot(self, enemy_level=None, exclude=None):
        """Ячейка с подходящим критом, по которому меньше всего ударов в журнале.
        Уровень проверяем ДО смены: смена стоит хода, и слабого крита за этот ход могут убить. Поэтому незнакомого
        (портрет ещё не видели — не знаем, кто там и какого он уровня) выпускаем, только если противник настолько
        слабый, что не страшен даже крит 1-го уровня. Уровень противника не прочитан — не меняем вовсе."""
        if not enemy_level:
            return None
        best, best_key = None, None
        for slot in self._team():
            name = self._who_in(slot)
            if name is not None and name == exclude:
                continue
            if not self._slot_fits(slot, name, enemy_level):
                continue
            canon = self.hits.canon(name) if name else None
            known = -1 if name is None else sum(1 for h in self.hits.hits if h.attacker == canon)
            key = (known, random.random())
            if best_key is None or key < best_key:
                best, best_key = slot, key
        return best

    def _healthy_slot(self, enemy_level=None, exclude=None):
        """Знакомый крит с запасом HP и подходящим уровнем — самый здоровый."""
        best, best_hp = None, None
        for slot in self._team():
            name = self._who_in(slot)
            if name is None or name == exclude or not self._fit_for(name, enemy_level):
                continue
            level = self._battle_levels.get(slot)
            if level and enemy_level and level < enemy_level - self.settings.level_gap:
                continue  # по верхней панели в этой ячейке сейчас слабый
            if not self._survives(name):
                continue  # не переживёт и двух ходов противника
            hp = self._crit_hp.get(name, 1.0)
            if best_hp is None or hp > best_hp:
                best, best_hp = slot, hp
        return best

    def _better_catcher(self, my_name, by_name, target_element, hp, precious):
        """(ячейка, имя) крита команды, у которого есть безопасный удар, приближающий цель к порогу, или None.
        Смотрим только критов, чьи способности бот уже видел (читал их кнопки)."""
        for slot in self._team():
            name = self._who_in(slot)
            if not name or name == my_name or name not in self._pages:
                continue
            if self._crit_hp.get(name, 1.0) * 100 < self.settings.test_min_hp_pct:
                continue  # полуживой не продержится, пока подводим HP цели
            species = by_name.get(name)
            if species is None:
                continue
            names = {n for page in self._pages[name] for n in page if n}
            moves = moves_from_catalog(species.abilities, names, self._enchanted(species))
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
                unseen = precious and seen < PRECIOUS_SEEN
                worst_used = worst * (PRECIOUS_UNSEEN if unseen else PRECIOUS_EXTRA if precious else 1)
                ok = worst_used <= hp[0] - floor
                verdict = ("безопасно" if ok else "может добить") + (" · мало данных, запас ×2.5" if unseen else "")
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
        self._record_hit(attacker, self.hits.attacker_level, move, name, target_element, self.hits.level,
                         self._last_max_hp, hp_before, rank, kill=True)
        path = self._learn_path.with_name("incidents.csv")
        new = not path.exists()
        with open(path, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write("time,enemy,rank,level,attacker,ability,hp_before,expected,worst,source\n")
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{name},{rank or ''},{self.hits.level or ''},{attacker},"
                    f"{move.name},{hp_before},{forecast[0]:.0f},{forecast[1]:.0f},{forecast[2]}\n")

    def _record_hit(self, attacker, attacker_level, move, enemy_name, enemy_element, enemy_level, enemy_max_hp,
                    damage, enemy_rank=None, kill=False):
        """Записать удар в журнал и сразу показать это в «Ход боя» и в карточке боя."""
        self.hits.record(attacker, attacker_level, move, enemy_name, enemy_element, enemy_level, enemy_max_hp, damage,
                         enemy_rank, kill=kill)
        what = f"≥{damage}, добивающий" if kill else ("промах" if damage == 0 else str(damage))
        seen = self.hits.observed(attacker, move, enemy_element)
        self._emit("battle_log", f"📝 записал: {move.name} → {enemy_name} ({enemy_element or '?'}): {what} "
                                 f"· видел таких ударов: {seen}")
        self._emit("hit", {"ability": move.name, "seen": seen})

    def _enchanted(self, species):
        """Прокачанные способности моего экземпляра этого вида (поле en в данных игры)."""
        copy = owned_copy(self._player_fn(), species, self.hits.attacker_level)
        return tuple((copy or {}).get("en") or ())

    def _species_named(self, name):
        catalog = self._catalog_fn()
        if catalog is None:
            return None
        return next((s for s in catalog.species if name in s.names), None)

    def _stats_pair(self, attacker, attacker_level, enemy, enemy_level, enemy_rank):
        return stats_pair(self._catalog_fn(), self._player_fn(), attacker, attacker_level, enemy, enemy_level,
                          enemy_rank)

    def _first_ability(self, my_name, moves):
        """Атака, которая заодно лечит моего крита (по каталогу: дополнительный эффект Heal), — самая сильная
        из таких. Раньше бралась «первая кнопка», но раскладка у разных критов разная и первая не обязательно лечит."""
        species = self._species_named(my_name)
        if species is None:
            return None
        healing = {a.get("name") for a in species.abilities
                   if any(isinstance(x, dict) and x.get("type") == "Heal" for x in (a.get("additional") or []))}
        candidates = [m for m in moves if m.name in healing]
        return max(candidates, key=lambda m: m.power) if candidates else None

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
        tries = self._page_reads.get(my_name, 0)
        unknown = my_name in self._pages and has_gaps(self._pages[my_name])
        if my_name not in self._pages or (unknown and tries < PAGE_READ_TRIES):
            # на странице есть нераспознанные кнопки (читали во время анимации) — перечитываем на следующем ходу
            self._page_reads[my_name] = tries + 1
            pages = self._read_pages(species)
            old = self._pages.get(my_name)
            if old and count_names(pages) < count_names(old):
                # прочитали меньше, чем знали (не та страница, анимация) — старое не затираем
                self._say(f"способности {my_name}: прочитал {count_names(pages)} вместо {count_names(old)} — "
                          "оставляю прежний список")
            else:
                self._pages[my_name] = pages
        names = {n for page in self._pages[my_name] for n in page if n}
        moves = moves_from_catalog(species.abilities, names, self._enchanted(species))
        attack_names = {m.name for m in moves}
        extras = [n for n in names if n not in attack_names]
        return moves, extras

    def _read_slots(self, species) -> list:
        ability_names = [a["name"] for a in species.abilities]
        for name in species.names:
            self._ability_names[name] = ability_names
        return [self.eyes.read_name(slot, ability_names) for slot in ABILITY_SLOTS]

    def _read_pages(self, species) -> list:
        """Все страницы способностей по порядку. Игра оставляет открытой страницу последнего удара, поэтому
        сначала листаем назад до первой — иначе после удара с последней страницы бот видел только её."""
        self.eyes.look()
        if self.eyes.knows("ability_prev"):
            current = self._read_slots(species)
            for _ in range(MAX_ABILITY_PAGES - 1):
                arrow = self.eyes.sees("ability_prev")
                if arrow is None:
                    break
                self._press(arrow, "ability_prev")
                self._sleep(0.6)
                self.eyes.look()
                page = self._read_slots(species)
                if page == current:
                    break  # дальше назад некуда
                current = page
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
        """Нажать способность, глядя на кнопки, а не на запомненную раскладку страниц: игра сама сбрасывает
        страницу между ходами, а у критов с малым числом способностей стрелки листают не по четыре.
        Читаем все четыре кнопки; нужной нет — листаем и читаем снова; листание ничего не меняет (край) —
        идём в другую сторону."""
        names = self._ability_names.get(my_name, [ability])
        pages = self._pages.get(my_name) or []
        target = next((i for i, page in enumerate(pages) if ability in page), None)
        direction = "ability_next" if target is not None and target > self._page else "ability_prev"
        turned = False
        previous = None
        for _ in range(2 * MAX_ABILITY_PAGES + 4):
            self.eyes.look()
            if self.eyes.sees("battle") is None or any(self.eyes.sees(k) for k in ("captured", "battle_won")):
                # бой уже кончился (поймали или победили) — не листаем способности, окно закроется дальше
                self._say("бой уже закончился — удар не нужен")
                return
            seen = [self.eyes.read_name(slot, names) for slot in ABILITY_SLOTS]
            if ability in seen:
                self._act(self.eyes.region(ABILITY_SLOTS[seen.index(ability)]), ability)
                if target is not None:
                    self._page = target
                return
            if previous is not None and seen == previous:
                if turned:
                    break  # прошли в обе стороны до края — способности нет
                direction = "ability_next" if direction == "ability_prev" else "ability_prev"
                turned = True
            previous = seen
            arrow = self.eyes.sees(direction)
            if arrow is None:
                if turned:
                    break
                direction = "ability_next" if direction == "ability_prev" else "ability_prev"
                turned = True
                continue
            self._press(arrow, direction)
            self._sleep(0.5)
        # раскладка, которую бот запомнил, не сходится с экраном — перечитаем её на следующем ходу
        self._pages.pop(my_name, None)
        self._page_reads.pop(my_name, None)
        raise Stuck(f"не нашёл кнопку {ability} — пролистал в обе стороны")

    def _visible_page(self, pages, names):
        """Какая страница способностей сейчас открыта — по совпадению надписей на кнопках."""
        seen = [self.eyes.read_name(slot, names) for slot in ABILITY_SLOTS]
        best, score = None, 0
        for i, page in enumerate(pages):
            hits = sum(1 for a, b in zip(seen, page) if a and a == b)
            if hits > score:
                best, score = i, hits
        return best

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
        # лечимся по самому раненому криту команды, а не по тому, кто закончил бой: иначе полуживой
        # Patriot выходит первым в каждый бой, его меняют, а к лекарю бот не идёт, потому что сменщик здоров
        who, lowest = min(((n, r) for n, r in self._crit_hp.items()), key=lambda p: p[1], default=(None, my_ratio))
        if my_ratio < lowest:
            who, lowest = None, my_ratio
        if lowest * 100 < self.settings.heal_below:
            if not self.eyes.teaching.routes.get("heal"):
                if not self._heal_warned:
                    self._heal_warned = True
                    self._say(f"⚠ HP {(who + ' ') if who else ''}{lowest:.0%} — пора лечиться, но маршрут лечения "
                              "не записан (вкладка «Точки и маршруты»). Продолжаю без лечения, меняя раненых")
            else:
                self._say(f"HP {(who + ' ') if who else ''}{lowest:.0%} — иду лечиться")
                self._run_route("heal")
                self.stats.heals += 1
                self._crit_hp.clear()  # вылечили всех
                self._crit_hp_abs.clear()
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
        best = 0.0
        frames = 0
        start = time.monotonic()
        end = start + SUMMARY_MAX_WAIT
        while True:
            self.eyes.look()
            frames += 1
            _, score = find_scored(self.eyes.image, label)
            best = max(best, score)
            if score >= threshold:
                return self._summary_verdict(True, best, frames, start, "метка найдена", area)
            current = cv2.resize(crop_area(self.eyes.image, area), None, fx=0.25, fy=0.25,
                                 interpolation=cv2.INTER_AREA).astype(np.int16)
            # анимируется маленькая полоска опыта — считаем изменившиеся пиксели, а не среднюю разницу
            if previous is not None:
                changed = int((np.abs(current - previous).max(axis=2) > 30).sum())
                if changed < SUMMARY_STILL:
                    return self._summary_verdict(False, best, frames, start, f"сводка не меняется ({changed} px)", area)
            if time.monotonic() >= end:
                return self._summary_verdict(False, best, frames, start, "время вышло", area)
            previous = current
            time.sleep(0.15)

    def _summary_verdict(self, ready, best, frames, start, why, area):
        """Пишем в лог, что решили по сводке и почему, и сохраняем её снимок — чтобы разбирать пропуски."""
        log.info("сводка: %s (совпадение с READY TO TRAIN %.2f, кадров %d, %.1f с, %s)",
                 "есть кого тренировать" if ready else "тренировать некого", best, frames,
                 time.monotonic() - start, why)
        folder = self._logs_dir / "summary"
        folder.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".jpg", crop_area(self.eyes.image, area), [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            (folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{'yes' if ready else 'no'}-{best:.2f}.jpg").write_bytes(buf.tobytes())
        for old in sorted(folder.glob("*.jpg"))[:-SUMMARY_SHOTS]:
            old.unlink(missing_ok=True)
        return ready

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

    def _locate_step(self, snap, near=None):
        """Кнопка шага маршрута: целиком, а если фон вокруг не тот (снимок захватил кусок локации, где учили),
        — по её середине, без краёв."""
        rect = find(self.eyes.image, snap.image, self.eyes.threshold, near=near) if near is not None else None
        if rect is None:
            rect = find(self.eyes.image, snap.image, self.eyes.threshold)
        if rect is not None:
            return rect
        core, core_rect = core_of(snap)
        found = find(self.eyes.image, core, CORE_THRESHOLD)
        if found is None:
            return None
        dx, dy = core_rect[0] - snap.rect[0], core_rect[1] - snap.rect[1]
        return found[0] - dx, found[1] - dy, snap.rect[2], snap.rect[3]

    def _find_anywhere(self, image, timeout, threshold=None, near=None, clear_popups=False, snap=None):
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
            if snap is not None:
                rect = self._locate_step(snap, near)
            else:
                rect = find(self.eyes.image, image, threshold, near=near) if near is not None else None
                if rect is None:
                    rect = find(self.eyes.image, image, threshold)
            if rect is not None or time.monotonic() >= end:
                return rect
            time.sleep(0.15)

    def _click_step(self, step, timeout) -> bool:
        rect = self._find_anywhere(step.snap.image, timeout, near=step.snap.rect, clear_popups=True, snap=step.snap)
        if rect is None:
            log.info("шаг тренировки не найден на экране: %s", step.snap.rect)
            return False
        for attempt in range(3):
            self._press(rect, "train step" + (f" (ещё раз, {attempt})" if attempt else ""))
            self._sleep(random.uniform(0.35, 0.6))
            # кнопка осталась на месте — нажатие не сработало (промах или окно ещё не ожило), жмём ещё
            self.eyes.look()
            again = self._locate_step(step.snap, near=rect)
            if again is not None and abs(again[0] - rect[0]) + abs(again[1] - rect[1]) > 20:
                again = None  # нашлась такая же кнопка в другом месте — это уже не та
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
                # как у тренировки: снимок шага мог захватить фон локации, где его учили, — ищем и по середине
                rect = self._locate_step(step.snap, near=step.snap.rect)
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
