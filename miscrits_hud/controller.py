"""Когда звать сервер и что показывать. Без Qt: тикается таймером снаружи."""

import dataclasses
import logging
import time
from dataclasses import dataclass

from .game_api import AuthError
from .log_watcher import Activity, LocationChanged, TokenSeen
from .model import ZoneView, build_view, utc_weekday

log = logging.getLogger(__name__)

WAITING = "Ждём игру…"
EMPTY = "Здесь мискритов нет"
UNKNOWN_ZONE = "Зона пока неизвестна · открой в игре Достижения и проведи бой"
EXPIRED = "Ключ игры истёк · открой в игре Достижения и проведи бой"


@dataclass(frozen=True)
class UiState:
    view: ZoneView | None
    message: str | None
    note: str | None  # предупреждение под списком (нет связи, устаревшая коллекция)


class Controller:
    POLL_INTERVAL = 15.0
    IDLE_LIMIT = 600.0  # лог не растёт 10 минут — игра закрыта или стоит в меню
    MIN_INTERVAL = 5.0
    LOCATION_SETTLE = 2.0  # «Sending update_location» пишется до того, как сервер применил переход
    TOKEN_MARGIN = 30.0

    def __init__(self, watcher, catalogs, fetch, submit, clock=time.monotonic, wall=time.time,
                 cached=None, on_update=None, weekday=utc_weekday):
        """cached: (Player, {location_id: name}, время получения) из прошлого запуска.
        on_update(player, names, when) вызывается после каждого успешного запроса."""
        self._watcher = watcher
        self._catalogs = catalogs
        self._fetch = fetch
        self._submit = submit
        self._clock = clock
        self._wall = wall
        self._on_update = on_update or (lambda player, names, when: None)
        self._weekday = weekday
        self._token = None
        self._exp = 0.0
        self._blocked_token = None
        self._manual = False
        self._force_at = None
        self._location_seq = 0
        self._inflight_seq = 0
        self._last_activity = None
        self._future = None
        self._inflight_token = None
        self._last_start = None
        self._network_failed = False
        self._last_state = None
        self._player, self._names, self._last_ok = cached if cached else (None, {}, None)
        self._names = dict(self._names)
        self._location = (self._player.location_id, self._player.area_id) if self._player else None

    @property
    def player(self):
        """Последний ответ get_player (или из кэша прошлого запуска); None, пока данных нет."""
        return self._player

    @property
    def location(self):
        """(название локации, номер зоны) по последнему переходу из лога или ответу сервера; None, если неизвестно."""
        if self._location is None:
            return None
        location_id, area_id = self._location
        name = self._names.get(location_id, "") if location_id else (self._player.location_name if self._player else "")
        return (name, area_id) if name else None

    @property
    def key_expired(self) -> bool:
        """Ключ сессии был, но истёк (или сервер его отверг): коллекция и зона больше не обновляются."""
        return self._token is not None and not self._token_valid()

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
            # Зону берём из лога сразу: это работает и когда ключ уже истёк.
            self._location = (event.location_id, event.area_id)
            self._location_seq += 1
            self._force_at = self._clock() + self.LOCATION_SETTLE
        elif isinstance(event, Activity):
            self._last_activity = self._clock()

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
            (self._force_at is not None and self._clock() >= self._force_at)
            or (self._last_ok is None and since >= self.MIN_INTERVAL)
            or (self._manual and since >= self.MIN_INTERVAL)
            or (since >= self.POLL_INTERVAL and self._game_active())
        )
        if not due:
            return
        self._force_at = None
        self._manual = False
        self._last_start = self._clock()
        self._inflight_token = self._token
        self._inflight_seq = self._location_seq
        self._future = self._submit(self._fetch, self._token)

    def _game_active(self) -> bool:
        # Игра пишет лог блоками по 4 КБ, поэтому зону и коллекцию опрашиваем сами,
        # пока лог хоть иногда растёт.
        return self._last_activity is not None and self._clock() - self._last_activity <= self.IDLE_LIMIT

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
            if player.location_id:
                self._names[player.location_id] = player.location_name
            if self._inflight_seq == self._location_seq:
                # Пока запрос летел, лог не сообщал о новом переходе — зоне сервера можно верить.
                self._location = (player.location_id, player.area_id)
            self._on_update(player, dict(self._names), self._last_ok)

    def _note(self) -> str | None:
        if self._last_ok is None:
            return None
        if not self._token_valid():
            when = time.strftime("%H:%M", time.localtime(self._last_ok))
            # Игра продлевает ключ, когда её HTTP-запрос получает 401 (окно Достижений делает запрос
            # каждый раз), а лог пишется блоками по 4 КБ — новый ключ доходит до HUD после боя.
            return f"⚠ коллекция от {when} · открой в игре Достижения и проведи бой"
        if self._network_failed:
            return f"⚠ нет связи · обновлено {int((self._wall() - self._last_ok) // 60)} мин назад"
        return None

    def _state(self) -> UiState:
        catalog = self._catalogs()
        if self._player is None or catalog is None:
            return UiState(None, EXPIRED if self._token is not None and not self._token_valid() else WAITING, None)
        note = self._note()
        location_id, area_id = self._location
        name = self._names.get(location_id, "") if location_id else self._player.location_name
        if location_id and not name:
            return UiState(None, UNKNOWN_ZONE, note)
        player = dataclasses.replace(self._player, location_name=name, location_id=location_id, area_id=area_id)
        view = build_view(catalog, player, self._weekday())
        return UiState(view, EMPTY if view.total == 0 else None, note)
