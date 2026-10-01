from miscrits_hud.config import Config, app_dir, game_data_dir, load_cache, load_config, save_cache, save_config
from miscrits_hud.game_api import Player


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


def test_cache_roundtrip(tmp_path):
    path = tmp_path / "collection.json"
    player = Player("Forest", 1, [{"m": 1, "h": 3}], location_id=2)
    save_cache(path, player, {2: "Forest", 7: "Hidden Forest"}, 1790000000.0)
    assert load_cache(path) == (player, {2: "Forest", 7: "Hidden Forest"}, 1790000000.0)


def test_cache_missing_or_broken(tmp_path):
    assert load_cache(tmp_path / "none.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text('{"player": 1}', encoding="utf-8")
    assert load_cache(broken) is None
