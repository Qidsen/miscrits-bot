"""Конечный автомат бота: охота → бой → после боя → тренировка/лечение. Работает в своём потоке."""

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field

import cv2

from .brain.capture import CAPTURE, decide
from .brain.combat import ATTACK, STALL, DamageModel, choose_capture, choose_kill, moves_from_catalog
from .collection import Collection
from .mouse import FailSafe
from .storage import ABILITY_SLOTS, POPUPS

log = logging.getLogger(__name__)

GAME_EXE = "miscrits.exe"
MAX_ABILITY_PAGES = 5


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
                 on_event=lambda kind, data: None, foreground=None):
        """hands(rect) — клик; catalog_fn() -> Catalog; player_fn() -> Player | None (коллекция из HUD);
        foreground() -> имя exe активного окна; on_event(kind, data) — для GUI."""
        self.eyes = eyes
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
        spots = self.eyes.teaching.spots
        # Кулдаун точки идёт с момента клика по ней, бой входит в это время — считаем его сами.
        cooldown = self.settings.spot_cooldown + 1
        now = time.monotonic()
        order = [(self._spot + k) % len(spots) for k in range(len(spots))]
        ready = [i for i in order if now - self._spot_used.get(i, float("-inf")) >= cooldown]
        # точки, где водится цель охоты, — первыми, как только остыли
        targets = set(self.settings.hunt_targets)
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
            self._press(rect, f"spot {i + 1}")
            self._spot_used[i] = time.monotonic()
            found, _ = self._wait_for(("battle", "come_back_later", *POPUPS), timeout=6)
            if found == "battle":
                enemy = self._battle()
                if enemy is not None:
                    seen = spots[i].seen
                    seen[enemy.names[0]] = seen.get(enemy.names[0], 0) + 1
                    self._emit("teaching_changed", None)
            # come_back_later: наш отсчёт разошёлся с игрой — он уже начат заново с момента клика;
            # попап (предмет/золото) закроется на следующем шаге
            return
        raise Stuck("не вижу ни одной точки поиска")

    # ---- бой ----

    def _battle(self):
        catalog = self._catalog_fn()
        if catalog is None:
            raise Stuck("нет каталога игры")
        by_name = {n: s for s in catalog.species for n in s.names}
        self.stats.battles += 1
        self._page = 0
        enemy = rank = decision = None
        plat_used = 0
        last = None  # (имя моего крита, Move, HP противника до удара)
        my_ratio = 1.0
        captured = False
        self._state("бой")
        while True:
            turn, _ = self._wait_for(("my_turn", "battle_won", "captured"), timeout=60, while_visible="battle")
            if turn is None:
                self.eyes.look()
                if self.eyes.sees("battle") is None:
                    break
                raise Stuck("бой: не дождался своего хода")
            if turn == "captured":
                captured = True
                break
            if turn == "battle_won":
                break
            self._sleep(0.4)  # анимации панели HP
            self.eyes.look()
            if enemy is None:
                name = self.eyes.read_name("enemy_name", by_name)
                enemy = by_name.get(name)
                rank = self.eyes.read_rank("enemy_rank") if self.eyes.knows("enemy_rank") else None
                if enemy is None:
                    self._say("противник не распознан — бью")
                decision = decide(enemy.id if enemy else None, rank, enemy.rarity if enemy else "", self._collection())
                who = f"{enemy.names[0]} ({enemy.rarity}) {rank or '?'}" if enemy else "?"
                self._say(f"бой: {who} → {'ЛОВИМ' if decision.action == CAPTURE else 'убиваем'} — {decision.reason}")
            hp = self.eyes.read_hp("enemy_hp")
            mine = self.eyes.read_hp("my_hp")
            if mine:
                my_ratio = mine[0] / mine[1]
            my_name = self.eyes.read_name("my_name", by_name)
            me = by_name.get(my_name)
            target_element = enemy.element if enemy else ""
            if last and hp and last[0] == my_name:
                self.model.observe(last[0], last[1], target_element, last[2] - hp[0])
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
                action = choose_capture(moves, self.model, my_name, target_element,
                                        hp[0] if hp else 1, hp[1] if hp else 1, chance,
                                        self.settings.capture_min_chance, (can_capture or plat) is not None)
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
                    self._use_any(extras or [min(moves, key=lambda m: m.power).name], my_name)
                    last = None
                    continue
                move = action.move
            else:
                move = choose_kill(moves, self.model, my_name, target_element)
            self._use(move.name, my_name)
            last = (my_name, move, hp[0]) if hp else None
            self._sleep(1.0)
        self._after_battle(enemy, rank, captured, plat_used, my_ratio)
        return enemy

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
            self._run_route("train")
            self.stats.trainings += 1
        if my_ratio * 100 < self.settings.heal_below:
            self._say(f"HP {my_ratio:.0%} — иду лечиться")
            self._run_route("heal")
            self.stats.heals += 1
        self._publish_stats()

    def _should_train(self) -> bool:
        if "train" not in self.eyes.teaching.routes:
            return False
        if self.eyes.knows("train_ready"):
            self.eyes.look()
            return self.eyes.sees("train_ready") is not None
        every = self.settings.train_every
        return bool(every) and self.stats.battles % every == 0

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
                rect = self.eyes.sees_snap(step.snap, anywhere=True)
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
