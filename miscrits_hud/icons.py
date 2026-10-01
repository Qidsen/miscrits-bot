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
