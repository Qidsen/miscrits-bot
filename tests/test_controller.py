import json
from concurrent.futures import Future

import pytest

from miscrits_hud.catalog import Catalog
from miscrits_hud.controller import EMPTY, UNKNOWN_ZONE, WAITING, Controller
from miscrits_hud.game_api import AuthError, NetworkError, Player
from miscrits_hud.log_watcher import Activity, LocationChanged, TokenSeen

NOW = 1_800_000_000.0
CATALOG = Catalog.from_json(json.dumps([
    {"id": 1, "element": "Fire", "names": ["Flue", "b", "c", "d"], "rarity": "Common", "locations": {"Forest": {"1": []}}},
]))
FOREST = Player("Forest", 1, [{"m": 1, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 3}], location_id=2)


class Rig:
    def __init__(self, cached=None):
        self.saved = []
        self.events = []
        self.t = 0.0
        self.results = []
        self.calls = []
        self.c = Controller(
            watcher=self, catalogs=lambda: CATALOG, fetch=self.fetch, submit=self.submit,
            clock=lambda: self.t, wall=lambda: NOW + self.t,
            cached=cached, on_update=lambda *args: self.saved.append(args),
        )

    def poll(self):
        events, self.events = self.events, []
        return events

    def fetch(self, token):
        self.calls.append(token)
        result = self.results.pop(0) if self.results else FOREST
        if isinstance(result, Exception):
            raise result
        return result

    @staticmethod
    def submit(fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as e:
            future.set_exception(e)
        return future

    def tick(self, advance=0.0, *events):
        self.t += advance
        self.events.extend(events)
        return self.c.tick()


@pytest.fixture
def rig():
    return Rig()


def test_no_token_waits(rig):
    assert rig.tick(0, Activity()).message == WAITING
    assert rig.calls == []


def test_expired_token_waits_without_fetch(rig):
    state = rig.tick(0, TokenSeen("old", NOW - 60))
    assert state.message == WAITING
    assert rig.calls == []


def test_valid_token_fetches_and_shows_view(rig):
    state = rig.tick(0, TokenSeen("tok", NOW + 3600))
    assert rig.calls == ["tok"]
    assert state.message is None
    assert state.view.rows[0].ranks == ("S+",)


def test_unchanged_state_returns_none(rig):
    rig.tick(0, TokenSeen("tok", NOW + 3600))
    assert rig.tick(0.5) is None


def test_activity_refreshes_every_20s(rig):
    rig.tick(0, TokenSeen("tok", NOW + 3600))
    rig.tick(10, Activity())
    assert len(rig.calls) == 1
    rig.tick(10)
    assert len(rig.calls) == 2
    rig.tick(30)
    assert len(rig.calls) == 2  # без новой активности не дёргаем сервер


def test_location_change_refreshes_immediately(rig):
    rig.tick(0, TokenSeen("tok", NOW + 3600))
    rig.tick(1, LocationChanged(7, 1))
    assert len(rig.calls) == 2


def test_manual_refresh_respects_5s(rig):
    rig.tick(0, TokenSeen("tok", NOW + 3600))
    rig.c.request_refresh()
    rig.tick(2)
    assert len(rig.calls) == 1
    rig.tick(3)
    assert len(rig.calls) == 2


def test_auth_error_stops_until_new_token(rig):
    rig.results = [AuthError("403")]
    state = rig.tick(0, TokenSeen("tok", NOW + 3600))
    assert state.message == WAITING
    rig.tick(30, Activity(), LocationChanged(7, 1))
    assert rig.calls == ["tok"]
    rig.tick(1, TokenSeen("tok2", NOW + 3600))
    assert rig.calls == ["tok", "tok2"]


def test_network_error_keeps_view_and_marks_stale(rig):
    rig.tick(0, TokenSeen("tok", NOW + 3600))
    rig.results = [NetworkError("down")]
    state = rig.tick(180, LocationChanged(2, 1))
    assert state.view is not None
    assert state.note == "⚠ нет связи · обновлено 3 мин назад"


def test_empty_zone_message(rig):
    rig.results = [Player("", 0, [])]
    state = rig.tick(0, TokenSeen("tok", NOW + 3600))
    assert state.message == EMPTY


def test_no_retry_storm_on_initial_failure(rig):
    rig.results = [NetworkError("down"), NetworkError("down")]
    rig.tick(0, TokenSeen("tok", NOW + 3600))
    rig.tick(1)
    rig.tick(1)
    assert len(rig.calls) == 1
    rig.tick(3)
    assert len(rig.calls) == 2


def test_expired_token_keeps_collection_and_follows_log(rig):
    rig.tick(0, TokenSeen("tok", NOW + 60), LocationChanged(2, 1))
    state = rig.tick(60, LocationChanged(7, 1))  # ключ истёк, зона ещё неизвестна
    assert len(rig.calls) == 1
    assert state.view is None and state.message == UNKNOWN_ZONE
    assert "коллекция от" in state.note
    state = rig.tick(1, LocationChanged(2, 1))
    assert state.view.location_name == "Forest"
    assert state.view.rows[0].ranks == ("S+",)
    assert "открой в игре Коллекции" in state.note


def test_cached_collection_shown_without_token():
    rig = Rig(cached=(FOREST, {2: "Forest"}, NOW - 600))
    state = rig.tick(0)
    assert rig.calls == []
    assert state.view.rows[0].ranks == ("S+",)
    assert "коллекция от" in state.note


def test_successful_fetch_is_saved(rig):
    rig.tick(0, TokenSeen("tok", NOW + 3600))
    player, names, when = rig.saved[-1]
    assert player == FOREST and names == {2: "Forest"} and when == NOW
