import pytest
from pathlib import Path

from miscrits_hud.catalog import Catalog, Species
from miscrits_hud.game_api import Player
from mbot.bot import Bot
from mbot.settings import Settings
from mbot.storage import Snapshot, Teaching

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
    bot._schedule_break()
    return bot, clicks


def test_kill_battle_uses_strongest_attack(tmp_path):
    turn = {"see": {"battle", "my_turn"}, "enemy_hp": (50, 50), "my_hp": (100, 100)}
    eyes = FakeEyes([turn, turn, {"see": {"battle_won"}}, {"see": set()}])
    owned = Player("", 0, [{"m": 2, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 3}])  # Flue S+ уже есть
    bot, clicks = make_bot(eyes, tmp_path, owned)
    bot._battle()
    assert (len("ability_2"), 0, 1, 1) in clicks  # Bash
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
    routes = []
    bot._run_route = routes.append
    bot._battle()
    assert routes == ["heal"]


class SpotEyes(FakeEyes):
    def __init__(self, count):
        super().__init__([{"see": set()}])
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
