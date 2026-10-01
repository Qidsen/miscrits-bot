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
