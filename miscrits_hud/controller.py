"""Когда звать сервер и что показывать. Без Qt: тикается таймером снаружи."""

import logging
import time
from dataclasses import dataclass

from .game_api import AuthError
from .log_watcher import Activity, LocationChanged, TokenSeen
from .model import ZoneView, build_view

log = logging.getLogger(__name__)

WAITING = "Ждём игру…"
EMPTY = "Здесь мискритов нет"


@dataclass(frozen=True)
class UiState:
    view: ZoneView | None
    message: str | None
    stale_minutes: int | None


class Controller:
    REFRESH_INTERVAL = 20.0
    MIN_INTERVAL = 5.0
    TOKEN_MARGIN = 30.0

    def __init__(self, watcher, catalogs, fetch, submit, clock=time.monotonic, wall=time.time):
        self._watcher = watcher
        self._catalogs = catalogs
        self._fetch = fetch
        self._submit = submit
        self._clock = clock
        self._wall = wall
        self._token = None
        self._exp = 0.0
        self._blocked_token = None
        self._force = self._dirty = self._manual = False
        self._future = None
        self._inflight_token = None
        self._last_start = None
        self._player = None
        self._last_ok = None
        self._network_failed = False
        self._last_state = None

    def request_refresh(self):
        self._manual = True

    def tick(self):
        for event in self._watcher.poll():
            self._handle(event)
        self._collect()
        self._maybe_start()
        self._collect()
        state = self._state()
        if state == self._last_state:
            return None
        self._last_state = state
        return state

    def _handle(self, event):
        if isinstance(event, TokenSeen):
            self._token, self._exp = event.token, event.exp
        elif isinstance(event, LocationChanged):
            self._force = True
        elif isinstance(event, Activity):
            self._dirty = True

    def _token_valid(self) -> bool:
        return (
            self._token is not None
            and self._token != self._blocked_token
            and self._exp - self._wall() > self.TOKEN_MARGIN
        )

    def _maybe_start(self):
        if self._future is not None or not self._token_valid():
            return
        since = float("inf") if self._last_start is None else self._clock() - self._last_start
        due = (
            self._force
            or (self._player is None and since >= self.MIN_INTERVAL)
            or (self._manual and since >= self.MIN_INTERVAL)
            or (self._dirty and since >= self.REFRESH_INTERVAL)
        )
        if not due:
            return
        self._force = self._dirty = self._manual = False
        self._last_start = self._clock()
        self._inflight_token = self._token
        self._future = self._submit(self._fetch, self._token)

    def _collect(self):
        if self._future is None or not self._future.done():
            return
        future, self._future = self._future, None
        try:
            player = future.result()
        except AuthError as e:
            log.warning("auth rejected (%s); waiting for a new session token", e)
            self._blocked_token = self._inflight_token
        except Exception as e:
            log.warning("get_player failed: %r", e)
            self._network_failed = True
        else:
            self._player = player
            self._last_ok = self._wall()
            self._network_failed = False

    def _state(self) -> UiState:
        catalog = self._catalogs()
        if self._player is None or not self._token_valid() or catalog is None:
            return UiState(None, WAITING, None)
        view = build_view(catalog, self._player)
        stale = int((self._wall() - self._last_ok) // 60) if self._network_failed else None
        return UiState(view, EMPTY if view.total == 0 else None, stale)
