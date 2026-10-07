import pytest
from pathlib import Path

from miscrits_hud.catalog import Catalog, Species
from miscrits_hud.game_api import Player
from mbot.bot import Bot
from mbot.settings import Settings
from mbot.storage import Snapshot, Step, Teaching

ABILITIES = (
    {"name": "Smack", "ap": 7, "type": "Attack", "element": "Physical"},
    {"name": "Bash", "ap": 15, "type": "Attack", "element": "Physical"},
    {"name": "Power Up", "ap": 5, "type": "Buff", "element": "Misc"},
)
ME = Species(1, ("Patriot",), "FireWind", "Rare", {}, ABILITIES)
FLUE = Species(2, ("Flue",), "Fire", "Common", {})
GOLD = Species(3, ("Goldy",), "Earth", "Exotic", {})


class FakeEyes:
    """Сценарий: список кадров; каждый кадр — множество видимых элементов и прочитанные значения."""

    def __init__(self, frames, enemy="Flue"):
        self.teaching = Teaching({k: Snapshot((0, 0, 10, 10)) for k in (
            "battle", "my_turn", "battle_won", "captured", "capture", "my_hp", "enemy_hp",
            "enemy_name", "enemy_rank", "my_name", "ability_1", "ability_2", "ability_3", "ability_4")})
        self.frames = frames
        self.i = 0
        self.enemy = enemy
        self.image = None

    def look(self):
        self.frame = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1

    def knows(self, element_id):
        return element_id in self.teaching.elements

    def sees(self, element_id):
        return (1, 1, 5, 5) if element_id in self.frame.get("see", ()) else None

    def region(self, element_id):
        return (len(element_id), 0, 1, 1)

    def read_hp(self, element_id):
        return self.frame.get(element_id)

    def read_percent(self, element_id):
        return None

    def read_rank(self, element_id):
        return "B"

    def read_level(self, who):
        return 12 if who == "enemy" else 35

    def abilities_active(self):
        return "my_turn" in self.frame.get("see", ())

    def turn_message(self):
        return "It's your turn" if "my_turn" in self.frame.get("see", ()) else ""

    def sees_strictly(self, element_id, threshold):
        return self.sees(element_id)

    def read_name(self, element_id, names):
        if element_id == "enemy_name":
            return self.enemy
        if element_id == "my_name":
            return "Patriot"
        return {"ability_1": "Smack", "ability_2": "Bash", "ability_3": "Power Up"}.get(element_id)


class FastTime:
    """Часы, которые прыгают на секунду при каждом вопросе: ожидания в тестах не тянутся."""

    def __init__(self):
        import time
        self._real = time
        self.now = 0.0

    def monotonic(self):
        self.now += 1.0
        return self.now

    def sleep(self, seconds):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture(autouse=True)
def fast_time(monkeypatch):
    import mbot.bot
    monkeypatch.setattr(mbot.bot, "time", FastTime())


def make_bot(eyes, tmp_path, player=None):
    clicks = []
    bot = Bot(eyes, lambda rect: clicks.append(rect), lambda: Catalog([ME, FLUE, GOLD]), lambda: player,
              Settings(delay_min=0, delay_max=0), tmp_path / "learn.json", Path(tmp_path))
    bot._sleep = lambda s: None
    bot._press_key = lambda vk: None
    bot._schedule_break()
    return bot, clicks


def test_kill_battle_uses_strongest_attack(tmp_path):
    turn = {"see": {"battle", "my_turn"}, "enemy_hp": (50, 50), "my_hp": (100, 100)}
    eyes = FakeEyes([turn, turn, turn, turn, {"see": {"battle_won"}}, {"see": set()}])
    owned = Player("", 0, [{"m": 2, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 3}])  # Flue S+ уже есть
    bot, clicks = make_bot(eyes, tmp_path, owned)
    bot._battle()
    assert (len("ability_1"), 0, 1, 1) in clicks  # Smack — первая способность (kill_with_first)
    assert bot.stats.captures == 0 and bot.stats.battles == 1


def test_capture_new_species(tmp_path):
    turn = {"see": {"battle", "my_turn", "capture"}, "enemy_hp": (5, 50), "my_hp": (100, 100)}
    eyes = FakeEyes([turn, turn, turn, {"see": {"captured"}}, {"see": set()}], enemy="Goldy")
    bot, clicks = make_bot(eyes, tmp_path)
    bot._battle()
    assert (1, 1, 5, 5) in clicks  # Capture
    assert bot.stats.captures == 1 and bot.stats.catches == [(3, "B")]


def test_low_hp_triggers_heal_route(tmp_path):
    turn = {"see": {"battle", "my_turn"}, "enemy_hp": (50, 50), "my_hp": (10, 100)}
    eyes = FakeEyes([turn, turn, {"see": {"battle_won"}}, {"see": set()}])
    bot, _ = make_bot(eyes, tmp_path)
    eyes.teaching.routes["heal"] = [Step(Snapshot((0, 0, 5, 5)))]
    routes = []
    bot._run_route = routes.append
    bot._battle()
    assert routes == ["heal"]


def test_heal_by_the_most_wounded_crit_and_never_pretend_without_a_route(tmp_path):
    # бой закончил здоровый крит, но у Spiker (он не в бою) 20% — лечиться надо
    turn = {"see": {"battle", "my_turn"}, "enemy_hp": (50, 50), "my_hp": (100, 100)}
    eyes = FakeEyes([turn, turn, {"see": {"battle_won"}}, {"see": set()}])
    bot, _ = make_bot(eyes, tmp_path)
    bot._crit_hp["Spiker"] = 0.2
    routes = []
    bot._run_route = routes.append
    bot._battle()
    assert routes == []  # маршрута лечения нет — не «лечим» и HP не забываем
    assert bot._crit_hp["Spiker"] == 0.2
    eyes.teaching.routes["heal"] = [Step(Snapshot((0, 0, 5, 5)))]
    bot._after_battle(None, None, False, 0, 1.0)
    assert routes == ["heal"] and bot._crit_hp == {}


class SpotEyes(FakeEyes):
    def __init__(self, count):
        super().__init__([{"see": {"come_back_later"}}])  # клик сразу «отвечает» — без повторов
        self.teaching.spots = [Snapshot((i * 100, 0, 10, 10)) for i in range(count)]

    def locate_spot(self, snap):
        return snap.rect


def test_spots_rotate_and_respect_cooldown_from_click(tmp_path):
    eyes = SpotEyes(2)
    bot, clicks = make_bot(eyes, tmp_path)
    clock = mbot_time()
    bot._hunt()
    bot._hunt()
    assert clicks == [(0, 0, 10, 10), (100, 0, 10, 10)]
    waited = []
    bot._sleep = waited.append
    bot._hunt()  # обе точки нажаты только что — ждём, ничего не кликаем
    assert len(clicks) == 2 and waited and waited[0] > 0
    clock.now += 30
    bot._hunt()
    assert clicks[-1] == (0, 0, 10, 10)


def mbot_time():
    import mbot.bot
    return mbot.bot.time


def test_target_spot_goes_first_when_ready(tmp_path):
    eyes = SpotEyes(3)
    eyes.teaching.spots[2].label = "Goldy"
    bot, clicks = make_bot(eyes, tmp_path)
    bot.settings.hunt_targets = ["Goldy"]
    bot._hunt()
    assert clicks == [(200, 0, 10, 10)]
    bot._hunt()
    assert clicks[-1] == (0, 0, 10, 10)  # цель на кулдауне — пока остальные


def test_battle_records_who_came_from_spot(tmp_path):
    turn = {"see": {"battle", "my_turn"}, "enemy_hp": (50, 50), "my_hp": (100, 100)}
    eyes = SpotEyes(1)
    eyes.frames = [{"see": {"battle"}}, turn, turn, turn, {"see": {"battle_won"}}, {"see": set()}]
    bot, _ = make_bot(eyes, tmp_path)
    events = []
    bot._emit = lambda kind, data: events.append(kind)
    bot._hunt()
    assert eyes.teaching.spots[0].seen == {"Flue": 1} and "teaching_changed" in events


def test_missed_spot_is_clicked_again(tmp_path):
    eyes = SpotEyes(1)
    eyes.frames = [{"see": set()}] * 30 + [{"see": {"come_back_later"}}]
    bot, clicks = make_bot(eyes, tmp_path)
    bot._hunt()
    assert len(clicks) == 3  # клик + 2 повтора


def test_kill_uses_strongest_when_first_ability_disabled(tmp_path):
    turn = {"see": {"battle", "my_turn"}, "enemy_hp": (50, 50), "my_hp": (100, 100)}
    eyes = FakeEyes([turn, turn, turn, turn, {"see": {"battle_won"}}, {"see": set()}])
    owned = Player("", 0, [{"m": 2, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 3}])
    bot, clicks = make_bot(eyes, tmp_path, owned)
    bot.settings.kill_with_first = False
    bot._battle()
    assert (len("ability_2"), 0, 1, 1) in clicks  # Bash


def test_caught_target_is_skipped(tmp_path):
    from miscrits_hud.catalog import Catalog
    from mbot.worldmap import Marker

    fubby = Species(417, ("Fubby",), "Earth", "Exotic", {"Mansion": {"1": []}})
    keeper = Species(538, ("Keeper",), "NatureEarth", "Legendary", {"Mansion": {"1": []}})
    owned = Player("", 0, [{"m": 417, "h": 2, "s": 2, "e": 2, "d": 2, "p": 2, "pd": 2}])
    eyes = FakeEyes([{"see": set()}])
    bot = Bot(eyes, lambda r: None, lambda: Catalog([fubby, keeper]), lambda: owned,
              Settings(hunt_targets=["Fubby", "Keeper"]), tmp_path / "l.json", tmp_path,
              location_fn=lambda: ("Mansion", 1))

    class Maps:
        def markers(self, location):
            return [Marker("Fubby", 417, "Exotic", 27, 3513), Marker("Keeper", 538, "Legendary", 754, 3594)]

    bot._companion = Maps()
    location, markers = bot._map_targets()
    assert [m.name for m in markers] == ["Keeper"]
    bot.stats.catches.append((538, "B"))  # поймали и Keeper
    assert bot._map_targets() is None


def _train_bot(tmp_path, ready_rows):
    import numpy as np

    from mbot.storage import Step

    eyes = FakeEyes([{"see": set()}])
    img = np.zeros((110, 110, 3), np.uint8)
    eyes.teaching.routes["train"] = [Step(Snapshot((i, 0, 5, 5), img)) for i in range(5)]
    bot, clicks = make_bot(eyes, tmp_path)
    rows = iter(ready_rows)
    bot._find_anywhere = lambda image, timeout, threshold=None, near=None, clear_popups=False: (9, 9, 5, 5) if next(rows, False) else None
    steps = []
    bot._click_step = lambda step, timeout: steps.append(step.snap.rect[0]) or True
    bot._dismiss_popups_quietly = lambda: None
    return bot, clicks, steps


def test_train_all_ready_then_close(tmp_path):
    bot, clicks, steps = _train_bot(tmp_path, [True, True, False])
    assert bot._train() == 2
    # открыть (0), два раза TRAIN NOW и Continue (2, 3), закрыть (4); строки READY кликаются отдельно
    assert steps == [0, 2, 3, 2, 3, 4] and clicks.count((9, 9, 5, 5)) == 2


def test_train_nobody_ready_closes_without_pause(tmp_path):
    bot, clicks, steps = _train_bot(tmp_path, [False])
    assert bot._train() == 0
    assert steps == [0, 4] and not bot._paused.is_set()


def test_battle_summary_decides_training(tmp_path):
    import numpy as np

    from mbot.storage import Step

    eyes = FakeEyes([{"see": set()}])
    eyes.teaching.routes["train"] = [Step(Snapshot((i, 0, 5, 5), np.zeros((5, 5, 3), np.uint8))) for i in range(5)]
    bot, _ = make_bot(eyes, tmp_path)
    bot._train_button_blinks = lambda snap: (_ for _ in ()).throw(AssertionError("не должен смотреть на кнопку"))
    bot._train_seen = False
    assert bot._should_train() is False
    bot._train_seen = True
    assert bot._should_train() is True
    assert bot._train_seen is None  # ответ сводки используется один раз


def test_summary_waits_only_while_xp_animates(tmp_path, monkeypatch):
    import numpy as np

    import mbot.bot
    monkeypatch.setattr(mbot.bot, "SUMMARY_MAX_WAIT", 100)  # часы в тестах прыгают на секунду за вызов

    label_band = np.zeros((110, 110, 3), np.uint8)
    label_band[45:72, 5:105] = 255
    label_band[50:66, 10:100:7] = 0  # узор «READY TO TRAIN»
    eyes = FakeEyes([{"see": set()}])
    eyes.threshold = 0.82
    eyes.teaching.elements["train_ready"] = Snapshot((300, 300, 110, 110), label_band)
    eyes.teaching.elements["battle_won"] = Snapshot((350, 500, 110, 110))
    rng = np.random.default_rng(0)
    still = rng.integers(0, 60, (900, 1200, 3), dtype=np.uint8)
    frames = []

    def look():
        eyes.image = frames.pop(0) if len(frames) > 1 else frames[0]
    eyes.look = look
    bot, _ = make_bot(eyes, tmp_path)

    frames[:] = [still.copy(), still.copy(), still.copy()]  # ничего не меняется и метки нет
    assert bot._summary_says_train() is False

    animated = [still.copy() for _ in range(4)]
    for i, frame in enumerate(animated):
        frame[600:620, 100:100 + 80 * (i + 1)] = 250  # полоска опыта растёт
    ready = animated[-1].copy()
    ready[700:727, 400:510] = label_band[45:72]  # в конце анимации появилась метка
    frames[:] = animated + [ready]
    assert bot._summary_says_train() is True


def test_farm_uses_all_zone_markers_when_no_targets(tmp_path):
    from miscrits_hud.catalog import Catalog
    from mbot.worldmap import Marker

    fubby = Species(417, ("Fubby",), "Earth", "Exotic", {"Mansion": {"1": []}})
    attic = Species(9, ("Batty",), "Wind", "Epic", {"Mansion": {"4": []}})
    eyes = FakeEyes([{"see": set()}])
    bot = Bot(eyes, lambda r: None, lambda: Catalog([fubby, attic]), lambda: None, Settings(), tmp_path / "l.json",
              tmp_path, location_fn=lambda: ("Mansion", 1))

    class Maps:
        def markers(self, location):
            return [Marker("Fubby", 417, "Exotic", 27, 3513), Marker("Batty", 9, "Epic", 900, 900)]

    bot._companion = Maps()
    assert bot._map_targets() is None  # целей нет — охоты по целям нет
    location, markers = bot._map_targets(farm=True)
    assert [m.name for m in markers] == ["Fubby"]  # Batty в другой зоне (чердак)


def test_kill_battle_explores_until_element_is_known(tmp_path):
    from mbot.brain.combat import Move

    def run(known):
        turn = {"see": {"battle", "my_turn"}, "enemy_hp": (50, 50), "my_hp": (100, 100)}
        eyes = FakeEyes([turn, turn, {"see": {"battle_won"}}, {"see": set()}])
        owned = Player("", 0, [{"m": 2, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 3}])
        bot, clicks = make_bot(eyes, tmp_path / str(known), owned)
        (tmp_path / str(known)).mkdir(exist_ok=True)
        if known:
            for _ in range(3):  # Physical по Fire уже изучен
                bot.hits.record("Patriot", 35, Move("Smack", 7, 1, 100, "Physical"), "Flue", "Fire", 12, 50, 10)
        states = []
        bot._state = states.append
        bot._battle()
        return states

    assert any("изучаю урон" in s for s in run(False))
    assert not any("изучаю урон" in s for s in run(True))


def test_switches_to_crit_with_safe_hit_when_catching(tmp_path):
    from mbot.brain.combat import Move

    turn = {"see": {"battle", "my_turn", "capture"}, "enemy_hp": (60, 60), "my_hp": (100, 100)}
    eyes = FakeEyes([turn] * 3 + [{"see": {"captured"}}, {"see": set()}], enemy="Goldy")
    eyes.teaching.elements["team_1"] = Snapshot((500, 0, 10, 10))
    eyes.teaching.elements["switch_confirm"] = Snapshot((600, 0, 10, 10))
    bot, clicks = make_bot(eyes, tmp_path)
    bot.settings.explore_switch = False
    weak = Species(5, ("Weakling",), "Fire", "Common", {},
                   ({"name": "Poke", "ap": 3, "type": "Attack", "element": "Physical"},))
    bot._catalog_fn = lambda: Catalog([ME, FLUE, GOLD, weak])
    bot._pages["Weakling"] = [["Poke", None, None, None]]
    bot._who_in = lambda slot: "Weakling"
    for _ in range(3):  # Patriot бьёт Goldy сильно (60 из 60), Weakling — по 6
        bot.hits.record("Patriot", 35, Move("Smack", 7, 1, 100, "Physical"), "Goldy", "Earth", 12, 60, 60)
        bot.hits.record("Patriot", 35, Move("Bash", 15, 1, 100, "Physical"), "Goldy", "Earth", 12, 60, 60)
        bot.hits.record("Weakling", 5, Move("Poke", 3, 1, 100, "Physical"), "Goldy", "Earth", 12, 60, 6)
    switches = []
    bot._switch = lambda slot: switches.append(slot)
    bot._battle()
    assert switches == ["team_1"]


def test_finishing_blow_is_recorded_as_lower_bound(tmp_path):
    turn = {"see": {"battle", "my_turn"}, "enemy_hp": (40, 50), "my_hp": (100, 100)}
    won = {"see": {"battle_won"}}
    eyes = FakeEyes([turn, turn, turn, won, won, {"see": set()}])
    owned = Player("", 0, [{"m": 2, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 3}])
    bot, _ = make_bot(eyes, tmp_path, owned)
    bot.settings.explore_damage = False
    bot._battle()
    [hit] = bot.hits.hits
    assert hit.kill and hit.damage == 40 and hit.enemy == "Flue"


def test_finishing_blow_recorded_when_battle_screen_vanishes_before_victory(tmp_path):
    turn = {"see": {"battle", "my_turn"}, "enemy_hp": (40, 50), "my_hp": (100, 100)}
    gap = {"see": set()}  # бой уже исчез, окна победы ещё нет
    won = {"see": {"battle_won"}}
    eyes = FakeEyes([turn, turn, turn, gap, gap, gap, won, won, {"see": set()}])
    owned = Player("", 0, [{"m": 2, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 3}])
    bot, _ = make_bot(eyes, tmp_path, owned)
    bot.settings.explore_damage = False
    bot._battle()
    assert [h.kill for h in bot.hits.hits] == [True]


def test_no_crit_switch_when_capture_chance_is_already_high(tmp_path):
    turn = {"see": {"battle", "my_turn", "capture"}, "enemy_hp": (45, 136), "my_hp": (100, 100)}
    eyes = FakeEyes([turn] * 3 + [{"see": {"captured"}}, {"see": set()}], enemy="Goldy")
    eyes.read_percent = lambda element_id: 100
    eyes.teaching.elements["capture_chance"] = Snapshot((0, 0, 10, 10))
    eyes.teaching.elements["team_1"] = Snapshot((500, 0, 10, 10))
    eyes.teaching.elements["switch_confirm"] = Snapshot((600, 0, 10, 10))
    bot, clicks = make_bot(eyes, tmp_path)
    switches = []
    bot._switch = lambda slot: switches.append(slot)
    bot._better_catcher = lambda *a: ("team_1", "Other", "есть безопасный удар")
    bot._battle()
    assert switches == [] and bot.stats.captures == 1


def test_use_finds_ability_on_any_button_and_turns_back_at_the_edge(tmp_path):
    """Раскладка сдвинута относительно запомненной; сначала листаем не туда — упираемся в край и разворачиваемся."""
    eyes = FakeEyes([{"see": {"battle", "my_turn", "ability_next", "ability_prev"}}])
    view = {"page": 1}
    layouts = {0: ["Sleep", "Bite", "Debaser", "Burn"], 1: ["Bite", "Debaser", "Burn", "Ember"]}

    def read_name(element_id, names):
        slots = ("ability_1", "ability_2", "ability_3", "ability_4")
        return layouts[view["page"]][slots.index(element_id)] if element_id in slots else None
    eyes.read_name = read_name
    bot, clicks = make_bot(eyes, tmp_path)
    bot._pages["Blazertooth"] = [["Bite", "Debaser", "Burn", None]]
    bot._ability_names["Blazertooth"] = ["Sleep", "Bite", "Debaser", "Burn", "Ember"]

    def press(rect, what=""):
        if what == "ability_prev":
            view["page"] = 0
        elif what == "ability_next":
            view["page"] = 1
        clicks.append((what, rect))
    bot._press = press
    bot._act = lambda rect, what="": clicks.append((what, rect))
    bot._use("Sleep", "Blazertooth")  # Sleep только на «первой» раскладке
    assert clicks[-1][0] == "Sleep"
    bot._use("Ember", "Blazertooth")  # Ember только на второй
    assert clicks[-1][0] == "Ember"


def test_team_choice_respects_level_and_hp(tmp_path):
    eyes = FakeEyes([{"see": set()}])
    for k in ("team_1", "team_2", "team_3", "switch_confirm"):
        eyes.teaching.elements[k] = Snapshot((0, 0, 10, 10))
    bot, _ = make_bot(eyes, tmp_path)
    who = {"team_1": "Baby", "team_2": "Tired", "team_3": "Strong"}
    bot._who_in = lambda slot: who[slot]
    levels = {"Baby": 7, "Tired": 30, "Strong": 30}
    bot._crit_level = lambda name: levels.get(name)
    bot._crit_hp = {"Tired": 0.2, "Strong": 0.9}
    assert bot._least_known_slot(enemy_level=16) == "team_3"  # 7-й уровень против 16-го и полуживой — нет
    assert bot._healthy_slot(enemy_level=16) == "team_3"
    assert bot._healthy_slot(enemy_level=16, exclude="Strong") is None


def _keeper_battle(tmp_path, who_in, seen_levels):
    """Бой «на убой» против Flue 12-го уровня; в ячейке team_1 — Keeper 2-го уровня (так видно на экране)."""
    turn = {"see": {"battle", "my_turn"}, "enemy_hp": (50, 50), "my_hp": (100, 100)}
    eyes = FakeEyes([turn] * 6 + [{"see": {"battle_won"}}, {"see": set()}])
    for k in ("team_1", "switch_confirm"):
        eyes.teaching.elements[k] = Snapshot((0, 0, 10, 10))
    keeper = Species(9, ("Keeper",), "NatureEarth", "Legendary", {}, ABILITIES)
    active = {"name": "Patriot"}
    levels = {"Patriot": 35, "Keeper": 2}
    eyes.read_name = lambda element_id, names: (
        active["name"] if element_id == "my_name" else "Flue" if element_id == "enemy_name"
        else {"ability_1": "Smack", "ability_2": "Bash", "ability_3": "Power Up"}.get(element_id))
    eyes.read_level = lambda who: 12 if who == "enemy" else levels[active["name"]]
    owned = Player("", 0, [{"m": 2, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 3}])
    bot, _ = make_bot(eyes, tmp_path, owned)
    bot._catalog_fn = lambda: Catalog([ME, FLUE, GOLD, keeper])
    bot.settings.explore_switch_pct = 100
    bot._who_in = who_in
    bot._seen_levels.update(seen_levels)
    switches = []

    def switch(slot):
        switches.append(slot)
        active["name"] = "Keeper" if active["name"] == "Patriot" else "Patriot"
        return None
    bot._switch = switch
    bot._battle()
    return bot, switches


def test_unknown_crit_is_not_sent_against_a_stronger_enemy(tmp_path):
    # портрет в ячейке незнаком — не знаем ни кто там, ни уровень; смена стоит хода, слабого за него убьют
    _, switches = _keeper_battle(tmp_path, who_in=lambda slot: None, seen_levels={})
    assert switches == []


def test_too_weak_crit_after_switch_goes_back_through_the_same_slot(tmp_path):
    # по старым данным Keeper 30-го уровня, а на экране боя — 2-й: возвращаем прежнего через ту же ячейку,
    # хотя, кто теперь в ней, бот не знает в лицо
    bot, switches = _keeper_battle(tmp_path, who_in=lambda slot: "Keeper", seen_levels={"Keeper": 30})
    assert switches[:2] == ["team_1", "team_1"]  # пробный и обратно
    assert bot._crit_level("Keeper") == 2  # уровень с экрана запомнен — в следующий раз не выпустим


def test_slot_levels_from_top_bar_choose_before_switching(tmp_path):
    # никого в столбике бот в лицо не знает, но уровни ячеек прочитаны с верхней панели перед боем
    eyes = FakeEyes([{"see": set()}])
    for k in ("team_1", "team_2", "team_3", "switch_confirm"):
        eyes.teaching.elements[k] = Snapshot((0, 0, 10, 10))
    bot, _ = make_bot(eyes, tmp_path)
    bot._who_in = lambda slot: None
    bot._battle_levels = {"active": 35, "team_1": 2, "team_2": 2, "team_3": 30}
    assert bot._least_known_slot(enemy_level=12) == "team_3"  # 2-й против 12-го не выходит, 30-й — да
    bot._battle_levels = {"active": 35, "team_1": 2, "team_2": 2, "team_3": 2}
    assert bot._least_known_slot(enemy_level=12) is None  # все слабые — не меняем вовсе
    assert bot._least_known_slot(enemy_level=3) in ("team_1", "team_2", "team_3")  # против 3-го — можно


def test_pages_are_read_from_the_first_even_after_a_hit_from_the_last(tmp_path):
    # Defender: после удара Rubble игра оставила открытой последнюю страницу — раньше бот читал только её
    layouts = [["Red Card", "Landslide", "Mother Nature", "Safeguard"],
               ["Penalty Shot", "Power Up", "Hit", "Leaves"],
               ["Rubble", None, None, None]]
    view = {"page": 2}
    eyes = FakeEyes([{"see": {"battle", "my_turn", "ability_next", "ability_prev"}}])
    for k in ("ability_next", "ability_prev"):
        eyes.teaching.elements[k] = Snapshot((0, 0, 10, 10))
    slots = ("ability_1", "ability_2", "ability_3", "ability_4")
    eyes.read_name = lambda element_id, names: layouts[view["page"]][slots.index(element_id)] if element_id in slots else None
    bot, _ = make_bot(eyes, tmp_path)

    def press(rect, what=""):
        if what == "ability_prev":
            view["page"] = max(0, view["page"] - 1)
        elif what == "ability_next":
            view["page"] = min(2, view["page"] + 1)
    bot._press = press
    defender = Species(7, ("Defender",), "Earth", "Common", {},
                       tuple({"name": n, "ap": 10, "type": "Attack", "element": "Earth"} for page in layouts for n in page if n))
    assert bot._read_pages(defender) == layouts
    # нераспознанных нет: пустые слоты в конце последней страницы — просто нет способностей
    from mbot.bot import has_gaps
    assert not has_gaps(layouts)
    assert has_gaps([["Red Card", None, "Hit", "Leaves"], ["Rubble", None, None, None]])
    # перечитали хуже, чем знали, — прежний список не затирается
    bot._pages["Defender"] = layouts
    bot._page_reads["Defender"] = 0
    bot._read_pages = lambda species: [["Rubble", None, None, "Leaves"]]
    bot._pages["Defender"] = [["Red Card", None, "Mother Nature", "Safeguard"]] + layouts[1:]  # есть пробел — перечитает
    moves, _ = bot._known_moves("Defender", defender)
    assert {m.name for m in moves} >= {"Red Card", "Penalty Shot", "Rubble"}


def test_team_level_never_drops_from_a_misread(tmp_path):
    eyes = FakeEyes([{"see": set()}])
    eyes.teaching.routes["train"] = [Step(Snapshot((0, 0, 5, 5)))]
    readings = iter([[35, 26, 26, 25], [5, 26, 26, 25], [2, 2, 2, 2]])
    eyes.read_team_levels = lambda snap: next(readings)
    bot, _ = make_bot(eyes, tmp_path)
    bot._read_team()
    bot._read_team()  # «5» вместо «35», остальные на месте — ошибка чтения
    assert bot._team_levels["active"] == 35
    bot._read_team()  # поменялась вся команда — принимаем
    assert list(bot._team_levels.values()) == [2, 2, 2, 2]
