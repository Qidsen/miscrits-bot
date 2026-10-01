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
