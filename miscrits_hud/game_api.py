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
