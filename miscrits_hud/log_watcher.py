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
