"""Пути к данным игры/программы и настройки окна."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .game_api import Player


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


def save_cache(path, player: Player, names: dict, when: float) -> None:
    """Последняя полученная коллекция — чтобы пережить истечение ключа сессии. Ключ не пишется."""
    data = {"player": asdict(player), "names": {str(k): v for k, v in names.items()}, "time": when}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        names = {int(k): str(v) for k, v in data["names"].items()}
        return Player(**data["player"]), names, float(data["time"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
