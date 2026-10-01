# Miscrits HUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Оверлей поверх Miscrits, который сам определяет зону и показывает всех её мискритов с рангами всех пойманных копий или пометкой «не пойман».

**Architecture:** Чистые модули без Qt (`rank`, `log_watcher`, `game_api`, `catalog`, `icons`, `model`, `controller`, `config`) покрыты pytest. `controller.Controller.tick()` раз в 0.5 с опрашивает лог, решает, когда звать `get_player` (в пуле потоков), и отдаёт `UiState`. Тонкий слой Qt (`overlay`, `hotkeys`, `app`) только рисует состояние и ловит горячие клавиши.

**Tech Stack:** Python 3.13, PySide6 ≥ 6.11, stdlib (`urllib`, `concurrent.futures`, `ctypes`), pytest ≥ 8, PyInstaller (только для сборки exe).

**Spec:** `docs/superpowers/specs/2026-10-01-miscrits-hud-design.md`

## Global Constraints

- Только Windows. Python 3.13, PySide6 ≥ 6.11; HTTP — только stdlib `urllib`.
- Данные игры: `%APPDATA%\Godot\app_userdata\Miscrits` (`logs/godot.log`, `image_cache/miscrits.json`, `image_cache/miscrits/`).
- Данные программы: `%APPDATA%\miscrits-hud` (`config.json`, `hud.log`, `icons/`).
- `get_player`: `POST https://worldofmiscrits.com/v2/rpc/get_player`, тело `""`, `Authorization: Bearer <JWT>`, `User-Agent: GodotEngine/4.6.stable (Windows)`.
- Иконки: `https://cdn.worldofmiscrits.com/avatars/<slug>_avatar.png`, `slug = re.sub(r"\s+", "_", name.lower())`; файл кэша игры = `sha256(url)`, PNG после 12-байтного заголовка.
- Ранг = сумма `h+s+e+d+p+pd`: ≤7 F, 8 F+, 9 D, 10 D+, 11 C, 12 C+, 13 B, 14 B+, 15 A, 16 A+, 17 S, 18 S+.
- Ключ сессии никогда не пишется на диск и в лог программы.
- Обновление: при смене зоны — сразу; при активности лога — не чаще раза в 20 с; F9 — не чаще раза в 5 с; таймаут HTTP 10 с.
- Тексты интерфейса на русском: «Ждём игру…», «Здесь мискритов нет», «не пойман», «⚠ нет связи · обновлено N мин назад».
- Все команды выполняются из `C:\Users\Yaros\Desktop\miscrits-hud` через `.venv\Scripts\python`.

## Review Focus

1. Игра закрыта, но в логе остался истёкший ключ — никаких запросов, показываем «Ждём игру…» (Task 7, `test_expired_token_waits_without_fetch`).
2. Игра перезапущена при работающем HUD: `godot.log` переименован и создан заново — читаем новый файл с начала (Task 2, `test_rotation_reads_new_file_from_start`).
3. Игра дописывает строку лога не до конца — неполная строка не разбирается, пока не придёт `\n` (Task 2, `test_partial_line_waits_for_newline`).
4. Зона без мискритов или `area` = null в ответе — «Здесь мискритов нет» без падения (Task 3, `test_parse_player_without_area`; Task 7, `test_empty_zone_message`).
5. Иконки нет на CDN (404) — запасной квадрат, повторно не качаем (Task 5, `test_fetch_failure_is_remembered`).

---

### Task 1: Каркас проекта и ранги

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `miscrits_hud/__init__.py`, `miscrits_hud/rank.py`, `tests/__init__.py`, `tests/test_rank.py`

**Interfaces:**
- Produces: `RANKS: tuple[str, ...]`, `rank_from_sum(total: int) -> str`, `rank_of(miscrit: dict) -> str`, `rank_index(rank: str) -> int`, `rank_tier(rank: str) -> str` (`"low" | "mid" | "high"`).

- [ ] **Step 1: Каркас и окружение**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "miscrits-hud"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["PySide6>=6.11"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.setuptools]
packages = ["miscrits_hud"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
*.egg-info/
build/
dist/
*.spec
```

`miscrits_hud/__init__.py` и `tests/__init__.py` — пустые.

Run: `python -m venv .venv && .venv/Scripts/python -m pip install -e .[dev]`
Expected: успешная установка PySide6 и pytest.

- [ ] **Step 2: Падающий тест**

`tests/test_rank.py`:
```python
import pytest

from miscrits_hud.rank import RANKS, rank_from_sum, rank_index, rank_of, rank_tier


def rolls(*values):
    return dict(zip(("h", "s", "e", "d", "p", "pd"), values))


@pytest.mark.parametrize(
    "total, expected",
    [(6, "F"), (7, "F"), (8, "F+"), (9, "D"), (10, "D+"), (11, "C"), (12, "C+"),
     (13, "B"), (14, "B+"), (15, "A"), (16, "A+"), (17, "S"), (18, "S+")],
)
def test_rank_from_sum(total, expected):
    assert rank_from_sum(total) == expected


@pytest.mark.parametrize(
    "values, expected",
    [  # подтверждено пользователем в игре
        ((1, 1, 1, 1, 2, 1), "F"),   # Flowerpiller
        ((3, 1, 1, 2, 1, 3), "C"),   # Squirmle
        ((2, 3, 2, 2, 3, 2), "B+"),  # Dark Sparkupine
        ((3, 1, 3, 3, 3, 3), "A+"),  # Flue
        ((3, 3, 3, 3, 3, 3), "S+"),  # Prawnja, Dark Weylani
    ],
)
def test_rank_of_confirmed_examples(values, expected):
    assert rank_of(rolls(*values)) == expected


def test_rank_index_orders_best_last():
    assert rank_index("S+") == len(RANKS) - 1
    assert rank_index("F") == 0


@pytest.mark.parametrize("rank, tier", [("F+", "low"), ("D", "low"), ("C+", "mid"), ("B", "mid"), ("A+", "high"), ("S", "high")])
def test_rank_tier(rank, tier):
    assert rank_tier(rank) == tier
```

- [ ] **Step 3: Убедиться, что падает**

Run: `.venv/Scripts/python -m pytest tests/test_rank.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'miscrits_hud.rank'`

- [ ] **Step 4: Реализация**

`miscrits_hud/rank.py`:
```python
"""Ранг мискрита по сумме шести бросков статов (каждый 1..3)."""

RANKS = ("F", "F+", "D", "D+", "C", "C+", "B", "B+", "A", "A+", "S", "S+")
ROLL_KEYS = ("h", "s", "e", "d", "p", "pd")
_TIERS = {"F": "low", "D": "low", "C": "mid", "B": "mid", "A": "high", "S": "high"}


def rank_from_sum(total: int) -> str:
    # Сумма 7 = F, дальше по одной ступени на единицу; 6 (F-) в игре не встречалась.
    return RANKS[min(max(total, 7), 18) - 7]


def rank_of(miscrit: dict) -> str:
    return rank_from_sum(sum(int(miscrit[key]) for key in ROLL_KEYS))


def rank_index(rank: str) -> int:
    return RANKS.index(rank)


def rank_tier(rank: str) -> str:
    return _TIERS[rank[0]]
```

- [ ] **Step 5: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_rank.py -q`
Expected: PASS (все)

- [ ] **Step 6: Коммит**

```bash
git add pyproject.toml .gitignore miscrits_hud tests
git commit -m "feat: project scaffold and rank formula"
```

---

### Task 2: Разбор лога игры

**Files:**
- Create: `miscrits_hud/log_watcher.py`, `tests/conftest.py`, `tests/test_log_watcher.py`

**Interfaces:**
- Produces: dataclass-события `LocationChanged(location_id: int, area_id: int)`, `TokenSeen(token: str, exp: float)`, `Activity()`; `jwt_exp(token: str) -> float | None`; `parse_line(line: str) -> LocationChanged | TokenSeen | None`; `LogWatcher(path).poll() -> list[event]` (первым идёт `Activity()`, если прочитаны новые байты).
- Produces (tests): фикстура `make_jwt(exp: float) -> str` в `tests/conftest.py`.

- [ ] **Step 1: Падающие тесты**

`tests/conftest.py`:
```python
import base64
import json

import pytest


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


@pytest.fixture
def make_jwt():
    def make(exp: float) -> str:
        return f"{_b64({'alg': 'HS256'})}.{_b64({'exp': exp, 'uid': 'test'})}.c2ln"
    return make
```

`tests/test_log_watcher.py`:
```python
import os

from miscrits_hud.log_watcher import Activity, LocationChanged, LogWatcher, TokenSeen, jwt_exp, parse_line

LOCATION_LINE = '=== Nakama : DEBUG === Sending async request: http_key: <null>, id: update_location, payload: {"areaId":1,"locationId":7}, '


def bearer_line(token):
    return ('=== Nakama : DEBUG === Sending request [ID: 4, Method: POST, Uri: https://worldofmiscrits.com:443/v2/rpc/get_inventory, '
            f'Headers: {{ "Authorization": "Bearer {token}" }}, Body: Body, Timeout: 30, Retries: 3, Backoff base: 10 ms]')


def test_parse_location():
    assert parse_line(LOCATION_LINE) == LocationChanged(location_id=7, area_id=1)


def test_parse_location_malformed_payload():
    assert parse_line('id: update_location, payload: {"areaId":"x"}, ') is None


def test_parse_bearer_token(make_jwt):
    token = make_jwt(1790000000)
    assert parse_line(bearer_line(token)) == TokenSeen(token=token, exp=1790000000.0)


def test_basic_auth_is_ignored():
    line = '=== Nakama : DEBUG === Sending request [ID: 1, Method: POST, Uri: x, Headers: { "Authorization": "Basic YWJjOmRlZg==" }, Body: Body]'
    assert parse_line(line) is None


def test_garbage_lines():
    assert parse_line("Spawning battle") is None
    assert parse_line("") is None


def test_jwt_exp_invalid():
    assert jwt_exp("not-a-jwt") is None
    assert jwt_exp("a.!!!.c") is None


def write(path, text, mode="a"):
    with open(path, mode, encoding="utf-8", newline="") as f:
        f.write(text)


def test_initial_read_and_incremental(tmp_path, make_jwt):
    log = tmp_path / "godot.log"
    token = make_jwt(2000000000)
    write(log, f"Godot Engine v4.6\n{bearer_line(token)}\n{LOCATION_LINE}\n", "w")
    watcher = LogWatcher(log)
    assert watcher.poll() == [Activity(), TokenSeen(token, 2000000000.0), LocationChanged(7, 1)]
    assert watcher.poll() == []
    write(log, 'id: update_location, payload: {"areaId":2,"locationId":2}, \n')
    assert watcher.poll() == [Activity(), LocationChanged(2, 2)]


def test_partial_line_waits_for_newline(tmp_path):
    log = tmp_path / "godot.log"
    write(log, LOCATION_LINE[:40], "w")
    watcher = LogWatcher(log)
    assert watcher.poll() == [Activity()]
    write(log, LOCATION_LINE[40:] + "\n")
    assert watcher.poll() == [Activity(), LocationChanged(7, 1)]


def test_rotation_reads_new_file_from_start(tmp_path):
    log = tmp_path / "godot.log"
    write(log, "x" * 500 + "\n", "w")
    watcher = LogWatcher(log)
    watcher.poll()
    os.replace(log, tmp_path / "godot2026-10-01T10.00.00.log")
    write(log, LOCATION_LINE + "\n", "w")
    assert watcher.poll() == [Activity(), LocationChanged(7, 1)]


def test_missing_file(tmp_path):
    assert LogWatcher(tmp_path / "nope.log").poll() == []
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv/Scripts/python -m pytest tests/test_log_watcher.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Реализация**

`miscrits_hud/log_watcher.py`:
```python
"""Хвост godot.log: смена зоны, ключ сессии, признак активности игры."""

import base64
import json
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LocationChanged:
    location_id: int
    area_id: int


@dataclass(frozen=True)
class TokenSeen:
    token: str
    exp: float


@dataclass(frozen=True)
class Activity:
    pass


_LOCATION_RE = re.compile(r"id: update_location, payload: (\{[^}]*\})")
_TOKEN_RE = re.compile(r'"Authorization": "Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"')


def jwt_exp(token: str) -> float | None:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return float(json.loads(base64.urlsafe_b64decode(part))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def parse_line(line: str):
    match = _LOCATION_RE.search(line)
    if match:
        try:
            payload = json.loads(match.group(1))
            return LocationChanged(int(payload["locationId"]), int(payload["areaId"]))
        except (ValueError, KeyError, TypeError):
            return None
    match = _TOKEN_RE.search(line)
    if match:
        exp = jwt_exp(match.group(1))
        if exp is not None:
            return TokenSeen(match.group(1), exp)
    return None


class LogWatcher:
    def __init__(self, path):
        self.path = path
        self._offset = 0
        self._file_id = None
        self._pending = b""

    def poll(self) -> list:
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        # Игра при старте переименовывает старый лог и пишет новый. На Windows время создания
        # может «туннелироваться» со старого файла, поэтому сравниваем индекс файла и размер.
        if self._file_id is not None and (st.st_ino != self._file_id or st.st_size < self._offset):
            self._offset = 0
            self._pending = b""
        self._file_id = st.st_ino
        if st.st_size == self._offset:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self._offset)
                chunk = f.read()
        except OSError:
            return []
        self._offset += len(chunk)
        *lines, self._pending = (self._pending + chunk).split(b"\n")
        events = [Activity()]
        for raw in lines:
            event = parse_line(raw.decode("utf-8", errors="replace"))
            if event is not None:
                events.append(event)
        return events
```

- [ ] **Step 4: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_log_watcher.py -q`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add miscrits_hud/log_watcher.py tests/conftest.py tests/test_log_watcher.py
git commit -m "feat: game log watcher (location, token, rotation)"
```

---

### Task 3: Запрос get_player

**Files:**
- Create: `miscrits_hud/game_api.py`, `tests/test_game_api.py`

**Interfaces:**
- Produces: `Player(location_name: str, area_id: int, miscrits: list[dict])`; исключения `ApiError`, `AuthError(ApiError)`, `NetworkError(ApiError)`; `parse_player(raw: str) -> Player`; `fetch_player(token: str, opener=urllib.request.urlopen, timeout: float = 10.0) -> Player`; константы `URL`, `USER_AGENT`.

- [ ] **Step 1: Падающие тесты**

`tests/test_game_api.py`:
```python
import json
import urllib.error

import pytest

from miscrits_hud.game_api import USER_AGENT, AuthError, NetworkError, Player, fetch_player, parse_player

MISCRIT = {"m": 1, "l": 35, "h": 3, "s": 1, "e": 3, "d": 3, "p": 3, "pd": 3}


def response(data) -> str:
    inner = {"success": True, "data": json.dumps(data)}
    return json.dumps({"payload": json.dumps(inner)})


AREA = {"area_id": 1, "location": {"id": 7, "name": "Hidden Forest"}, "name": "Hidden Forest"}


def test_parse_player():
    player = parse_player(response({"area": AREA, "miscrits": [MISCRIT]}))
    assert player == Player("Hidden Forest", 1, [MISCRIT])


def test_parse_player_without_area():
    assert parse_player(response({"area": None, "miscrits": []})) == Player("", 0, [])


def test_parse_player_success_false():
    raw = json.dumps({"payload": json.dumps({"success": False, "data": "{}"})})
    with pytest.raises(NetworkError):
        parse_player(raw)


def test_parse_player_garbage():
    with pytest.raises(NetworkError):
        parse_player("<html>oops</html>")


class FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_sends_game_headers():
    seen = {}

    def opener(req, timeout):
        seen["req"], seen["timeout"] = req, timeout
        return FakeResponse(response({"area": AREA, "miscrits": []}))

    assert fetch_player("tok", opener=opener).location_name == "Hidden Forest"
    req = seen["req"]
    assert req.get_method() == "POST"
    assert req.data == b'""'
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.get_header("User-agent") == USER_AGENT
    assert seen["timeout"] == 10.0


@pytest.mark.parametrize("code", [401, 403])
def test_fetch_auth_errors(code):
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "no", None, None)

    with pytest.raises(AuthError):
        fetch_player("tok", opener=opener)


def test_fetch_server_error_is_network():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 502, "bad gateway", None, None)

    with pytest.raises(NetworkError):
        fetch_player("tok", opener=opener)


def test_fetch_timeout_is_network():
    def opener(req, timeout):
        raise TimeoutError("timed out")

    with pytest.raises(NetworkError):
        fetch_player("tok", opener=opener)
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv/Scripts/python -m pytest tests/test_game_api.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Реализация**

`miscrits_hud/game_api.py`:
```python
"""Запрос get_player к серверу игры (Nakama) от имени клиента."""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

URL = "https://worldofmiscrits.com/v2/rpc/get_player"
# Без UA клиента Godot Cloudflare/nginx отвечает 403.
USER_AGENT = "GodotEngine/4.6.stable (Windows)"


class ApiError(Exception):
    pass


class AuthError(ApiError):
    pass


class NetworkError(ApiError):
    pass


@dataclass(frozen=True)
class Player:
    location_name: str
    area_id: int
    miscrits: list


def parse_player(raw: str) -> Player:
    try:
        inner = json.loads(json.loads(raw)["payload"])
        if not inner.get("success"):
            raise NetworkError("server returned success=false")
        data = inner["data"]
        if isinstance(data, str):
            data = json.loads(data)
        area = data.get("area") or {}
        location = area.get("location") or {}
        return Player(str(location.get("name") or ""), int(area.get("area_id") or 0), list(data["miscrits"]))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise NetworkError(f"unexpected response: {e!r}") from e


def fetch_player(token: str, opener=urllib.request.urlopen, timeout: float = 10.0) -> Player:
    request = urllib.request.Request(
        URL,
        data=b'""',
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with opener(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError(f"HTTP {e.code}") from e
        raise NetworkError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise NetworkError(repr(e)) from e
    return parse_player(raw)
```

- [ ] **Step 4: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_game_api.py -q`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add miscrits_hud/game_api.py tests/test_game_api.py
git commit -m "feat: get_player API client"
```

---

### Task 4: Справочник видов

**Files:**
- Create: `miscrits_hud/catalog.py`, `tests/test_catalog.py`

**Interfaces:**
- Produces: `Species(id: int, names: tuple[str, ...], element: str, rarity: str, locations: dict)`; `Catalog(species: list[Species])` с `.species`, `.by_id`, `Catalog.from_json(raw: str)`, `Catalog.load(path)`, `.species_in(location_name: str, area_id: int) -> list[Species]`; `CatalogCache(path).get() -> Catalog | None` (перечитывает при смене mtime, при ошибке оставляет прежний).

- [ ] **Step 1: Падающие тесты**

`tests/test_catalog.py`:
```python
import json
import os

from miscrits_hud.catalog import Catalog, CatalogCache

DATA = [
    {"id": 1, "element": "Fire", "names": ["Flue", "Chimnay", "Firebrawl", "Afterburn"], "rarity": "Common",
     "locations": {"Forest": {"1": []}}, "abilities": []},
    {"id": 5, "element": "Nature", "names": ["Cubsprout", "b", "c", "d"], "rarity": "Common",
     "locations": {"Forest": {"1": [], "2": []}, "Hidden Forest": {"1": [0, 1]}}},
    {"id": 9, "element": "Water", "names": ["Nonwild", "b", "c", "d"], "rarity": "Legendary"},
]


def test_species_in_zone_keeps_file_order():
    catalog = Catalog.from_json(json.dumps(DATA))
    assert [s.id for s in catalog.species_in("Forest", 1)] == [1, 5]
    assert [s.id for s in catalog.species_in("Forest", 2)] == [5]
    assert [s.id for s in catalog.species_in("Hidden Forest", 1)] == [5]
    assert catalog.species_in("Forest", 3) == []
    assert catalog.species_in("", 0) == []


def test_species_fields():
    flue = Catalog.from_json(json.dumps(DATA)).by_id[1]
    assert flue.names[0] == "Flue" and flue.element == "Fire" and flue.rarity == "Common"


def test_cache_reloads_on_change(tmp_path):
    path = tmp_path / "miscrits.json"
    path.write_text(json.dumps(DATA[:1]), encoding="utf-8")
    cache = CatalogCache(path)
    assert len(cache.get().species) == 1
    path.write_text(json.dumps(DATA), encoding="utf-8")
    os.utime(path, (1, 2_000_000_000))
    assert len(cache.get().species) == 3


def test_cache_keeps_previous_on_broken_file(tmp_path):
    path = tmp_path / "miscrits.json"
    path.write_text(json.dumps(DATA), encoding="utf-8")
    cache = CatalogCache(path)
    assert cache.get() is not None
    path.write_text("{broken", encoding="utf-8")
    os.utime(path, (1, 2_000_000_000))
    assert len(cache.get().species) == 3


def test_cache_missing_file(tmp_path):
    assert CatalogCache(tmp_path / "none.json").get() is None
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv/Scripts/python -m pytest tests/test_catalog.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Реализация**

`miscrits_hud/catalog.py`:
```python
"""Справочник видов из кэша игры (image_cache/miscrits.json)."""

import json
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class Species:
    id: int
    names: tuple
    element: str
    rarity: str
    locations: dict  # {"Forest": {"1": [...], "2": [...]}}


class Catalog:
    def __init__(self, species: list):
        self.species = species
        self.by_id = {s.id: s for s in species}

    @classmethod
    def from_json(cls, raw: str) -> "Catalog":
        return cls([
            Species(int(x["id"]), tuple(x["names"]), x.get("element", ""), x.get("rarity", ""), x.get("locations") or {})
            for x in json.loads(raw)
        ])

    @classmethod
    def load(cls, path) -> "Catalog":
        with open(path, encoding="utf-8") as f:
            return cls.from_json(f.read())

    def species_in(self, location_name: str, area_id: int) -> list:
        key = str(area_id)
        return [s for s in self.species if key in s.locations.get(location_name, {})]


class CatalogCache:
    def __init__(self, path):
        self.path = path
        self._mtime = None
        self._catalog = None

    def get(self):
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            return self._catalog
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                self._catalog = Catalog.load(self.path)
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.warning("cannot load catalog %s: %r", self.path, e)
        return self._catalog
```

- [ ] **Step 4: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_catalog.py -q`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add miscrits_hud/catalog.py tests/test_catalog.py
git commit -m "feat: species catalog from game cache"
```

---

### Task 5: Иконки

**Files:**
- Create: `miscrits_hud/icons.py`, `tests/test_icons.py`

**Interfaces:**
- Produces: `slug(name) -> str`, `avatar_url(name) -> str`, `game_cache_name(name) -> str`, `strip_godot_header(data: bytes) -> bytes | None`, `http_get(url, timeout=10.0) -> bytes`, `IconStore(game_cache_dir, own_dir, download=http_get)` с `.get(name) -> bytes | None` (только диск) и `.fetch(name) -> bytes | None` (качает, сохраняет, неудачи запоминает).

- [ ] **Step 1: Падающие тесты**

`tests/test_icons.py`:
```python
import hashlib
import struct

from miscrits_hud.icons import IconStore, avatar_url, game_cache_name, slug, strip_godot_header

PNG = b"\x89PNG\r\n\x1a\n" + b"rest-of-png"


def godot_wrap(png: bytes) -> bytes:
    return struct.pack("<III", len(png) + 8, 0x1D, len(png)) + png


def test_slug_and_url():
    assert slug("Dark Sparkupine") == "dark_sparkupine"
    assert avatar_url("Flue") == "https://cdn.worldofmiscrits.com/avatars/flue_avatar.png"


def test_game_cache_name_is_sha256_of_url():
    assert game_cache_name("Flue") == hashlib.sha256(b"https://cdn.worldofmiscrits.com/avatars/flue_avatar.png").hexdigest()


def test_strip_godot_header():
    assert strip_godot_header(godot_wrap(PNG)) == PNG
    assert strip_godot_header(PNG) == PNG
    assert strip_godot_header(b"garbage-garbage-garbage") is None


def test_get_prefers_game_cache(tmp_path):
    game, own = tmp_path / "game", tmp_path / "own"
    game.mkdir()
    (game / game_cache_name("Flue")).write_bytes(godot_wrap(PNG))
    assert IconStore(game, own).get("Flue") == PNG


def test_get_falls_back_to_own_cache(tmp_path):
    own = tmp_path / "own"
    own.mkdir()
    (own / "flue.png").write_bytes(PNG)
    assert IconStore(tmp_path / "missing", own).get("Flue") == PNG


def test_fetch_downloads_and_saves(tmp_path):
    calls = []

    def download(url):
        calls.append(url)
        return PNG

    store = IconStore(tmp_path / "game", tmp_path / "own", download=download)
    assert store.get("Dark Sparkupine") is None
    assert store.fetch("Dark Sparkupine") == PNG
    assert calls == ["https://cdn.worldofmiscrits.com/avatars/dark_sparkupine_avatar.png"]
    assert store.get("Dark Sparkupine") == PNG


def test_fetch_failure_is_remembered(tmp_path):
    calls = []

    def download(url):
        calls.append(url)
        raise OSError("404")

    store = IconStore(tmp_path / "game", tmp_path / "own", download=download)
    assert store.fetch("Nope") is None
    assert store.fetch("Nope") is None
    assert len(calls) == 1


def test_fetch_rejects_non_png(tmp_path):
    store = IconStore(tmp_path / "game", tmp_path / "own", download=lambda url: b"<html>")
    assert store.fetch("Flue") is None
    assert not (tmp_path / "own" / "flue.png").exists()
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv/Scripts/python -m pytest tests/test_icons.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Реализация**

`miscrits_hud/icons.py`:
```python
"""Иконки мискритов: кэш игры → свой кэш → публичный CDN."""

import hashlib
import logging
import re
import threading
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

CDN = "https://cdn.worldofmiscrits.com/avatars/"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def slug(name: str) -> str:
    # Тот же шаблон, что в Miscripedia на сайте игры.
    return re.sub(r"\s+", "_", name.lower())


def avatar_url(name: str) -> str:
    return f"{CDN}{slug(name)}_avatar.png"


def game_cache_name(name: str) -> str:
    return hashlib.sha256(avatar_url(name).encode()).hexdigest()


def strip_godot_header(data: bytes) -> bytes | None:
    # Игра хранит PNG как сериализованный PackedByteArray: 12 байт заголовка, затем PNG.
    if data.startswith(PNG_MAGIC):
        return data
    if data[12:20] == PNG_MAGIC:
        return data[12:]
    return None


def http_get(url: str, timeout: float = 10.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


class IconStore:
    def __init__(self, game_cache_dir, own_dir, download=http_get):
        self.game_cache_dir = Path(game_cache_dir)
        self.own_dir = Path(own_dir)
        self._download = download
        self._failed = set()
        self._lock = threading.Lock()

    def get(self, name: str) -> bytes | None:
        for path in (self.game_cache_dir / game_cache_name(name), self.own_dir / f"{slug(name)}.png"):
            try:
                data = strip_godot_header(path.read_bytes())
            except OSError:
                continue
            if data is not None:
                return data
        return None

    def fetch(self, name: str) -> bytes | None:
        with self._lock:
            if name in self._failed:
                return None
        try:
            data = self._download(avatar_url(name))
            if not data.startswith(PNG_MAGIC):
                raise ValueError("not a PNG")
            self.own_dir.mkdir(parents=True, exist_ok=True)
            (self.own_dir / f"{slug(name)}.png").write_bytes(data)
            return data
        except Exception as e:  # сеть, 404, диск — всё равно показываем запасную иконку
            log.info("icon %s unavailable: %r", name, e)
            with self._lock:
                self._failed.add(name)
            return None
```

- [ ] **Step 4: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_icons.py -q`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add miscrits_hud/icons.py tests/test_icons.py
git commit -m "feat: icon store (game cache, own cache, CDN)"
```

---

### Task 6: Картина зоны

**Files:**
- Create: `miscrits_hud/model.py`, `tests/test_model.py`

**Interfaces:**
- Consumes: `Catalog`, `Species` (Task 4), `Player` (Task 3), `rank_of`, `rank_index` (Task 1).
- Produces: `Row(species_id: int, name: str, element: str, rarity: str, ranks: tuple[str, ...])` со свойством `caught`; `ZoneView(location_name: str, area_id: int, rows: tuple[Row, ...])` со свойствами `caught_count`, `total`; `build_view(catalog, player) -> ZoneView`; `format_ranks(ranks, limit=5) -> str`.

- [ ] **Step 1: Падающие тесты**

`tests/test_model.py`:
```python
import json

from miscrits_hud.catalog import Catalog
from miscrits_hud.game_api import Player
from miscrits_hud.model import build_view, format_ranks

CATALOG = Catalog.from_json(json.dumps([
    {"id": 1, "element": "Fire", "names": ["Flue", "b", "c", "d"], "rarity": "Common", "locations": {"Forest": {"1": []}}},
    {"id": 2, "element": "Water", "names": ["Prawnja", "b", "c", "d"], "rarity": "Common", "locations": {"Forest": {"1": []}}},
    {"id": 3, "element": "Nature", "names": ["Elsewhere", "b", "c", "d"], "rarity": "Rare", "locations": {"Moon": {"1": []}}},
]))


def mc(species_id, *rolls):
    return dict(zip(("m", "h", "s", "e", "d", "p", "pd"), (species_id, *rolls)))


def test_build_view_caught_and_missing():
    player = Player("Forest", 1, [
        mc(1, 1, 1, 1, 1, 2, 1),   # F
        mc(1, 3, 1, 3, 3, 3, 3),   # A+
        mc(1, 3, 1, 1, 2, 1, 3),   # C
        mc(3, 3, 3, 3, 3, 3, 3),   # другой зоны — не показывается
    ])
    view = build_view(CATALOG, player)
    assert (view.location_name, view.area_id) == ("Forest", 1)
    assert [r.name for r in view.rows] == ["Flue", "Prawnja"]
    assert view.rows[0].ranks == ("A+", "C", "F")
    assert view.rows[0].caught and not view.rows[1].caught
    assert (view.caught_count, view.total) == (1, 2)


def test_build_view_skips_malformed_miscrit():
    player = Player("Forest", 1, [{"m": 1, "h": 3}, mc(2, 3, 3, 3, 3, 3, 3)])
    view = build_view(CATALOG, player)
    assert view.rows[0].ranks == ()
    assert view.rows[1].ranks == ("S+",)


def test_build_view_empty_zone():
    assert build_view(CATALOG, Player("", 0, [])).total == 0


def test_format_ranks():
    assert format_ranks(()) == "не пойман"
    assert format_ranks(("S+",)) == "S+"
    assert format_ranks(("S+", "A", "A", "B", "C")) == "S+ · A · A · B · C"
    assert format_ranks(("S+", "A", "A", "B", "C", "D", "F")) == "S+ · A · A · B · C +2"
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv/Scripts/python -m pytest tests/test_model.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Реализация**

`miscrits_hud/model.py`:
```python
"""Сборка картины зоны: виды зоны + ранги пойманных копий."""

import logging
from collections import defaultdict
from dataclasses import dataclass

from .rank import rank_index, rank_of

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Row:
    species_id: int
    name: str
    element: str
    rarity: str
    ranks: tuple  # от лучшего к худшему

    @property
    def caught(self) -> bool:
        return bool(self.ranks)


@dataclass(frozen=True)
class ZoneView:
    location_name: str
    area_id: int
    rows: tuple

    @property
    def caught_count(self) -> int:
        return sum(1 for r in self.rows if r.caught)

    @property
    def total(self) -> int:
        return len(self.rows)


def build_view(catalog, player) -> ZoneView:
    owned = defaultdict(list)
    for miscrit in player.miscrits:
        try:
            owned[int(miscrit["m"])].append(rank_of(miscrit))
        except (KeyError, ValueError, TypeError):
            log.warning("skipping malformed miscrit record: %r", miscrit)
    rows = tuple(
        Row(s.id, s.names[0], s.element, s.rarity, tuple(sorted(owned.get(s.id, ()), key=rank_index, reverse=True)))
        for s in catalog.species_in(player.location_name, player.area_id)
    )
    return ZoneView(player.location_name, player.area_id, rows)


def format_ranks(ranks, limit: int = 5) -> str:
    if not ranks:
        return "не пойман"
    shown = " · ".join(ranks[:limit])
    extra = len(ranks) - limit
    return f"{shown} +{extra}" if extra > 0 else shown
```

- [ ] **Step 4: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_model.py -q`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add miscrits_hud/model.py tests/test_model.py
git commit -m "feat: zone view model"
```

---

### Task 7: Контроллер обновлений

**Files:**
- Create: `miscrits_hud/controller.py`, `tests/test_controller.py`

**Interfaces:**
- Consumes: события из Task 2, `AuthError`/`Player` из Task 3, `build_view` из Task 6.
- Produces: `UiState(view: ZoneView | None, message: str | None, stale_minutes: int | None)`; `Controller(watcher, catalogs, fetch, submit, clock=time.monotonic, wall=time.time)` с `.tick() -> UiState | None` (None — ничего не изменилось) и `.request_refresh()`. `watcher.poll() -> list`, `catalogs() -> Catalog | None`, `fetch(token) -> Player`, `submit(fn, *args) -> concurrent.futures.Future`. Константы `WAITING = "Ждём игру…"`, `EMPTY = "Здесь мискритов нет"`.

- [ ] **Step 1: Падающие тесты**

`tests/test_controller.py`:
```python
import json
from concurrent.futures import Future

import pytest

from miscrits_hud.catalog import Catalog
from miscrits_hud.controller import EMPTY, WAITING, Controller
from miscrits_hud.game_api import AuthError, NetworkError, Player
from miscrits_hud.log_watcher import Activity, LocationChanged, TokenSeen

NOW = 1_800_000_000.0
CATALOG = Catalog.from_json(json.dumps([
    {"id": 1, "element": "Fire", "names": ["Flue", "b", "c", "d"], "rarity": "Common", "locations": {"Forest": {"1": []}}},
]))
FOREST = Player("Forest", 1, [{"m": 1, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 3}])


class Rig:
    def __init__(self):
        self.events = []
        self.t = 0.0
        self.results = []
        self.calls = []
        self.c = Controller(
            watcher=self, catalogs=lambda: CATALOG, fetch=self.fetch, submit=self.submit,
            clock=lambda: self.t, wall=lambda: NOW + self.t,
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


def test_expired_token_waits_without_fetch(rig, make_jwt):
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
    state = rig.tick(180, LocationChanged(7, 1))
    assert state.view is not None
    assert state.stale_minutes == 3


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
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv/Scripts/python -m pytest tests/test_controller.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Реализация**

`miscrits_hud/controller.py`:
```python
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
```

- [ ] **Step 4: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_controller.py -q`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add miscrits_hud/controller.py tests/test_controller.py
git commit -m "feat: refresh controller"
```

---

### Task 8: Пути и конфиг

**Files:**
- Create: `miscrits_hud/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `game_data_dir() -> Path`, `app_dir() -> Path` (создаёт папку); `Config(x: int | None = None, y: int | None = None, visible: bool = True)`; `load_config(path) -> Config`; `save_config(path, cfg) -> None`.

- [ ] **Step 1: Падающие тесты**

`tests/test_config.py`:
```python
from miscrits_hud.config import Config, app_dir, game_data_dir, load_config, save_config


def test_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    save_config(path, Config(x=10, y=20, visible=False))
    assert load_config(path) == Config(x=10, y=20, visible=False)


def test_missing_or_broken_gives_defaults(tmp_path):
    assert load_config(tmp_path / "none.json") == Config()
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    assert load_config(broken) == Config()


def test_paths_from_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert game_data_dir() == tmp_path / "Godot" / "app_userdata" / "Miscrits"
    assert app_dir() == tmp_path / "miscrits-hud"
    assert app_dir().is_dir()
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Реализация**

`miscrits_hud/config.py`:
```python
"""Пути к данным игры/программы и настройки окна."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def game_data_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Godot" / "app_userdata" / "Miscrits"


def app_dir() -> Path:
    path = Path(os.environ["APPDATA"]) / "miscrits-hud"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Config:
    x: int | None = None
    y: int | None = None
    visible: bool = True


def load_config(path) -> Config:
    try:
        with open(path, encoding="utf-8") as f:
            return Config(**json.load(f))
    except (OSError, ValueError, TypeError):
        return Config()


def save_config(path, cfg: Config) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f)
```

- [ ] **Step 4: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add miscrits_hud/config.py tests/test_config.py
git commit -m "feat: paths and window config"
```

---

### Task 9: Окно оверлея и горячие клавиши

**Files:**
- Create: `miscrits_hud/hotkeys.py`, `miscrits_hud/overlay.py`, `tests/test_overlay.py`

**Interfaces:**
- Consumes: `UiState` (Task 7), `ZoneView`/`Row`/`format_ranks` (Task 6), `rank_tier` (Task 1).
- Produces: `hotkeys.register(hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool`, `hotkeys.unregister(hwnd, hotkey_id)`, `hotkeys.set_click_through(hwnd: int, enabled: bool)`, константы `MOD_CONTROL`, `MOD_NOREPEAT`, `VK_F8`, `VK_F9`, `WM_HOTKEY`; `overlay.OverlayWindow(icon_lookup: Callable[[str], bytes | None])` с методами `render(state: UiState)`, `rerender()`, `set_move_mode(on: bool)`, свойством `move_mode`, сигналами `hotkey_pressed(int)` и `moved()`.

- [ ] **Step 1: Падающий smoke-тест (offscreen Qt)**

`tests/test_overlay.py`:
```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from miscrits_hud.controller import UiState
from miscrits_hud.model import Row, ZoneView
from miscrits_hud.overlay import OverlayWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def texts(window):
    return [label.text() for label in window.findChildren(QLabel)]


def test_renders_rows_and_header(qapp):
    window = OverlayWindow(icon_lookup=lambda name: None)
    view = ZoneView("Hidden Forest", 1, (
        Row(1, "Flue", "Fire", "Common", ("S+", "C")),
        Row(2, "Prawnja", "Water", "Common", ()),
    ))
    window.render(UiState(view, None, None))
    all_text = " ".join(texts(window))
    assert "Hidden Forest · зона 1" in all_text
    assert "1/2" in all_text
    assert "Flue" in all_text and "S+" in all_text
    assert "не пойман" in all_text


def test_renders_message_and_stale(qapp):
    window = OverlayWindow(icon_lookup=lambda name: None)
    window.render(UiState(None, "Ждём игру…", None))
    assert "Ждём игру…" in " ".join(texts(window))
    view = ZoneView("Forest", 2, (Row(1, "Flue", "Fire", "Common", ("A",)),))
    window.render(UiState(view, None, 3))
    assert "обновлено 3 мин назад" in " ".join(texts(window))


def test_rerender_replaces_rows(qapp):
    window = OverlayWindow(icon_lookup=lambda name: None)
    view = ZoneView("Forest", 1, (Row(1, "Flue", "Fire", "Common", ()),))
    window.render(UiState(view, None, None))
    window.rerender()
    assert sum("Flue" in t for t in texts(window)) == 1
```

- [ ] **Step 2: Убедиться, что падает**

Run: `.venv/Scripts/python -m pytest tests/test_overlay.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'miscrits_hud.overlay'`

- [ ] **Step 3: Win32-хелперы**

`miscrits_hud/hotkeys.py`:
```python
"""Глобальные горячие клавиши и прозрачность для кликов через Win32."""

import ctypes
from ctypes import wintypes

WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_F8 = 0x77
VK_F9 = 0x78

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t


def register(hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
    return bool(_user32.RegisterHotKey(hwnd, hotkey_id, modifiers | MOD_NOREPEAT, vk))


def unregister(hwnd: int, hotkey_id: int) -> None:
    _user32.UnregisterHotKey(hwnd, hotkey_id)


def set_click_through(hwnd: int, enabled: bool) -> None:
    style = _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE) | _WS_EX_LAYERED
    style = style | _WS_EX_TRANSPARENT if enabled else style & ~_WS_EX_TRANSPARENT
    _user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, style)
```

- [ ] **Step 4: Окно**

`miscrits_hud/overlay.py`:
```python
"""Прозрачное окно поверх игры: заголовок зоны и строки мискритов."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import hotkeys
from .model import format_ranks
from .rank import rank_tier

ICON = 40
WIDTH = 320
TIER_COLORS = {"low": "#ff8a7a", "mid": "#ffd166", "high": "#6be38a"}
ELEMENT_COLORS = {
    "Fire": "#e8553d", "Water": "#3d8be8", "Nature": "#4caf50", "Earth": "#a0703d",
    "Lightning": "#e8c22e", "Wind": "#7fc8e0", "Physical": "#9e9e9e",
}


def _ranks_html(ranks) -> str:
    if not ranks:
        return '<span style="color:#9a9a9a">не пойман</span>'
    text = format_ranks(ranks)
    shown, _, extra = text.partition(" +")
    parts = [f'<span style="color:{TIER_COLORS[rank_tier(r)]}">{r}</span>' for r in shown.split(" · ")]
    html = " · ".join(parts)
    return f'{html} <span style="color:#9a9a9a">+{extra}</span>' if extra else html


def _fallback_pixmap(name: str, element: str) -> QPixmap:
    pixmap = QPixmap(ICON, ICON)
    pixmap.fill(QColor(ELEMENT_COLORS.get(element, "#666666")))
    painter = QPainter(pixmap)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Segoe UI", 16, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, name[:1].upper())
    painter.end()
    return pixmap


def _icon_pixmap(data, name: str, element: str, caught: bool) -> QPixmap:
    image = QImage()
    if not data or not image.loadFromData(data):
        image = _fallback_pixmap(name, element).toImage()
    image = image.scaled(ICON, ICON, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if caught:
        return QPixmap.fromImage(image)
    gray = image.convertToFormat(QImage.Format_Grayscale8).convertToFormat(QImage.Format_ARGB32)
    faded = QPixmap(gray.size())
    faded.fill(Qt.transparent)
    painter = QPainter(faded)
    painter.setOpacity(0.4)
    painter.drawImage(0, 0, gray)
    painter.end()
    return faded


class OverlayWindow(QWidget):
    hotkey_pressed = Signal(int)
    moved = Signal()

    def __init__(self, icon_lookup):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(WIDTH)
        self._icon_lookup = icon_lookup
        self._state = None
        self._move_mode = False
        self._drag_from = None

        self._panel = QWidget(self)
        self._panel.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._panel)
        self._layout = QVBoxLayout(self._panel)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(4)
        self._apply_style()

    @property
    def move_mode(self) -> bool:
        return self._move_mode

    def _apply_style(self):
        border = "#ffd166" if self._move_mode else "transparent"
        self._panel.setStyleSheet(
            f"#panel {{ background: rgba(18, 20, 28, 200); border-radius: 8px; border: 2px solid {border}; }}"
            "QLabel { color: #f0f0f0; font-family: 'Segoe UI'; font-size: 13px; background: transparent; }"
        )

    def render(self, state):
        self._state = state
        self.rerender()

    def rerender(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)  # сразу убрать из дерева, deleteLater удалит позже
                item.widget().deleteLater()
        state = self._state
        if state is None:
            return
        if state.view is not None and state.view.location_name:
            view = state.view
            header = QLabel(
                f"<b>{view.location_name} · зона {view.area_id}</b>"
                f"<span style='color:#9a9a9a'>&nbsp;&nbsp;{view.caught_count}/{view.total}</span>"
            )
            self._layout.addWidget(header)
            if state.stale_minutes is not None:
                self._layout.addWidget(QLabel(
                    f"<span style='color:#ffd166'>⚠ нет связи · обновлено {state.stale_minutes} мин назад</span>"))
            for row in view.rows:
                self._layout.addWidget(self._row_widget(row))
        if state.message:
            self._layout.addWidget(QLabel(f"<span style='color:#c8c8c8'>{state.message}</span>"))
        self.adjustSize()

    def _row_widget(self, row) -> QWidget:
        widget = QWidget()
        line = QHBoxLayout(widget)
        line.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        icon.setPixmap(_icon_pixmap(self._icon_lookup(row.name), row.name, row.element, row.caught))
        icon.setFixedSize(ICON, ICON)
        name = QLabel(row.name if row.caught else f"<span style='color:#8a8a8a'>{row.name}</span>")
        ranks = QLabel(_ranks_html(row.ranks))
        ranks.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        line.addWidget(icon)
        line.addWidget(name, 1)
        line.addWidget(ranks)
        return widget

    # --- перемещение и клики насквозь ---

    def set_move_mode(self, on: bool):
        self._move_mode = on
        hotkeys.set_click_through(int(self.winId()), not on)
        self.setCursor(Qt.SizeAllCursor if on else Qt.ArrowCursor)
        self._apply_style()
        if not on:
            self.moved.emit()

    def mousePressEvent(self, event):
        if self._move_mode and event.button() == Qt.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._move_mode and self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, event):
        self._drag_from = None

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == hotkeys.WM_HOTKEY:
            self.hotkey_pressed.emit(int(msg.wParam))
            return True, 0
        return super().nativeEvent(event_type, message)
```

- [ ] **Step 5: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_overlay.py -q`
Expected: PASS

- [ ] **Step 6: Весь набор тестов**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS (все)

- [ ] **Step 7: Коммит**

```bash
git add miscrits_hud/hotkeys.py miscrits_hud/overlay.py tests/test_overlay.py
git commit -m "feat: overlay window and win32 hotkey helpers"
```

---

### Task 10: Сборка приложения, запуск, README, exe

**Files:**
- Create: `miscrits_hud/app.py`, `miscrits_hud/__main__.py`, `README.md`, `build.ps1`

**Interfaces:**
- Consumes: всё из Task 2–9.
- Produces: `python -m miscrits_hud` и **`dist/MiscritsHUD.exe` — основной способ запуска для пользователя** (обязательное требование).

- [ ] **Step 1: Точка входа**

`miscrits_hud/app.py`:
```python
"""Склейка: таймер → Controller.tick() → OverlayWindow.render()."""

import logging
import queue
import sys
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import hotkeys
from .catalog import CatalogCache
from .config import app_dir, game_data_dir, load_config, save_config
from .controller import Controller
from .game_api import fetch_player
from .icons import IconStore
from .log_watcher import LogWatcher
from .overlay import OverlayWindow

HK_TOGGLE, HK_REFRESH, HK_MOVE = 1, 2, 3
TICK_MS = 500


def main() -> int:
    home = app_dir()
    logging.basicConfig(
        filename=home / "hud.log", level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    game = game_data_dir()
    app = QApplication(sys.argv)
    pool = ThreadPoolExecutor(max_workers=2)

    icons = IconStore(game / "image_cache" / "miscrits", home / "icons")
    icons_ready = queue.Queue()
    requested = set()

    def icon_lookup(name):
        data = icons.get(name)
        if data is None and name not in requested:
            requested.add(name)
            pool.submit(icons.fetch, name).add_done_callback(lambda f: icons_ready.put(f.result()))
        return data

    controller = Controller(
        LogWatcher(game / "logs" / "godot.log"),
        CatalogCache(game / "image_cache" / "miscrits.json").get,
        fetch_player,
        pool.submit,
    )
    window = OverlayWindow(icon_lookup)

    config_path = home / "config.json"
    cfg = load_config(config_path)
    screen = app.primaryScreen().availableGeometry()
    window.move(cfg.x if cfg.x is not None else screen.right() - window.width() - 20,
                cfg.y if cfg.y is not None else screen.top() + 20)

    def save_position():
        cfg.x, cfg.y = window.x(), window.y()
        save_config(config_path, cfg)

    def on_hotkey(hotkey_id):
        if hotkey_id == HK_TOGGLE:
            cfg.visible = not window.isVisible()
            window.setVisible(cfg.visible)
            save_config(config_path, cfg)
        elif hotkey_id == HK_REFRESH:
            controller.request_refresh()
        elif hotkey_id == HK_MOVE:
            window.set_move_mode(not window.move_mode)

    window.hotkey_pressed.connect(on_hotkey)
    window.moved.connect(save_position)

    window.show()
    hwnd = int(window.winId())
    hotkeys.set_click_through(hwnd, True)
    for hotkey_id, mods, vk in ((HK_TOGGLE, 0, hotkeys.VK_F8), (HK_REFRESH, 0, hotkeys.VK_F9),
                                (HK_MOVE, hotkeys.MOD_CONTROL, hotkeys.VK_F8)):
        if not hotkeys.register(hwnd, hotkey_id, mods, vk):
            logging.warning("hotkey %s is taken by another program", hotkey_id)
    if not cfg.visible:
        window.hide()

    def tick():
        state = controller.tick()
        if state is not None:
            window.render(state)
            return
        got_icon = False
        while not icons_ready.empty():
            got_icon = icons_ready.get_nowait() is not None or got_icon
        if got_icon:
            window.rerender()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(TICK_MS)
    tick()
    logging.info("HUD started")
    try:
        return app.exec()
    finally:
        for hotkey_id in (HK_TOGGLE, HK_REFRESH, HK_MOVE):
            hotkeys.unregister(hwnd, hotkey_id)
        pool.shutdown(wait=False, cancel_futures=True)
```

`miscrits_hud/__main__.py`:
```python
import sys

from .app import main

sys.exit(main())
```

- [ ] **Step 2: README и сборка**

`README.md`:
```markdown
# Miscrits HUD

Оверлей поверх Miscrits: World of Creatures. Сам определяет текущую зону и показывает всех её мискритов: у пойманных — ранги всех копий (от лучшего), у остальных — «не пойман».

## Запуск

    python -m venv .venv
    .venv\Scripts\python -m pip install -e .[dev]
    .venv\Scripts\python -m miscrits_hud

Или собрать exe: `powershell -File build.ps1` → `dist\MiscritsHUD.exe`.

## Клавиши

- **F8** — показать/скрыть
- **F9** — обновить сейчас
- **Ctrl+F8** — режим перемещения (перетащить мышью, повторное нажатие — закрепить)

## Как это работает

- Зона и ключ сессии — из лога игры `%APPDATA%\Godot\app_userdata\Miscrits\logs\godot.log`.
- Коллекция — запрос `get_player` к серверу игры (только чтение) при смене зоны и раз в 20 с во время игры.
- Ранг — сумма шести бросков статов (7 = F … 18 = S+).
- Иконки — из кэша игры, иначе с публичного CDN игры.

Ключ сессии нигде не сохраняется. Лог программы: `%APPDATA%\miscrits-hud\hud.log`.

Если оверлей не виден поверх игры — переключите игру в оконный режим без рамки.

Использование неофициального API — на свой риск.
```

`build.ps1`:
```powershell
.venv\Scripts\python -m pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name MiscritsHUD miscrits_hud\__main__.py
```

- [ ] **Step 3: Тесты и запуск-проверка**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS (все)

Run (фоном 8 секунд, затем остановить): `.venv/Scripts/python -m miscrits_hud`
Expected: процесс не падает; в `%APPDATA%\miscrits-hud\hud.log` есть `HUD started` и нет traceback.

- [ ] **Step 4: Сборка exe**

Run: `powershell -File build.ps1`
Expected: `dist\MiscritsHUD.exe` создан.

- [ ] **Step 5: Коммит**

```bash
git add miscrits_hud/app.py miscrits_hud/__main__.py README.md build.ps1
git commit -m "feat: app wiring, README and exe build"
```

- [ ] **Step 6: Ручная проверка на игре (с пользователем)**

1. Запустить игру, затем `dist\MiscritsHUD.exe`.
2. Панель в правом верхнем углу показывает текущую зону и мискритов с рангами.
3. Перейти в другую зону — панель переключается за 1–2 с.
4. F8 скрывает/показывает, F9 обновляет, Ctrl+F8 позволяет перетащить панель; позиция сохраняется после перезапуска.
5. Клики сквозь панель проходят в игру.
