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
