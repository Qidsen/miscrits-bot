"""Настройки бота и пути к его данным."""

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def bot_dir() -> Path:
    path = Path(os.environ["APPDATA"]) / "miscrits-bot"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    delay_min: float = 0.3        # пауза перед каждым кликом, с
    delay_max: float = 0.8
    break_every_min: int = 30     # перерыв раз в N..M минут
    break_every_max: int = 60
    break_len_min: int = 2        # длина перерыва, минут
    break_len_max: int = 8
    session_limit_min: int = 240  # 0 — без лимита
    spot_cooldown: int = 20       # кулдаун точки поиска с момента клика, с
    heal_below: int = 40          # % HP моего крита после боя, ниже которого идём лечиться
    plat_capture_limit: int = 3   # платиновых попыток за бой (только Exotic/Legendary)
    capture_min_chance: int = 95  # %: при таком шансе ловим сразу, не добивая до порога
    capture_hp_floor: int = 10    # до скольких HP подводить цель перед поимкой
    train_every: int = 0          # если «Есть кого тренировать» не обучено: тренировка раз в N боёв (0 — никогда)
    match_threshold: float = 0.82
    button_size: int = 110        # сторона квадрата, который снимается вокруг курсора по F4
    tesseract_cmd: str = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    kill_with_first: bool = True  # в боях «на убой» бить атакой, которая заодно лечит (если есть)
    explore_switch: bool = True   # в части боёв «на убой» пробовать других критов команды (нужны «Крит в команде 1–3»)
    explore_damage: bool = True   # в боях «на убой» пробовать атаки, по которым мало данных
    explore_enough: int = 3       # ударов одной атакой по одной стихии, после которых атака считается изученной
    explore_switch_pct: int = 30  # в каком проценте боёв «на убой» пробовать другого крита команды
    low_hp_switch_pct: int = 35   # в боях «на убой» менять крита, если у него HP ниже, %
    test_min_hp_pct: int = 50     # выпускать на проверки только критов с HP не ниже, %
    level_gap: int = 3            # на сколько уровней мой крит может быть ниже противника, чтобы выйти на проверку
    hunt_targets: list = field(default_factory=list)  # имена видов (names[0]) — цели охоты


def load_settings(path) -> Settings:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return Settings()
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in known})


def save_settings(path, settings: Settings) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, ensure_ascii=False, indent=1)
