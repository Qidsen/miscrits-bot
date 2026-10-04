"""Обучаемые элементы UI, точки поиска и маршруты. Всё — в одном JSON, картинки в base64 PNG."""

import base64
import json
import os
from dataclasses import dataclass, field

import cv2
import numpy as np

BUTTON, REGION = "button", "region"


@dataclass(frozen=True)
class ElementDef:
    id: str
    kind: str
    required: bool
    title: str
    hint: str


ELEMENTS = (
    ElementDef("battle", BUTTON, True, "Признак боя",
               "Элемент, который виден всё время боя и только в бою (например, кнопка побега)."),
    ElementDef("my_turn", BUTTON, True, "Мой ход",
               "Вкладка Abilities в момент, когда можно ходить (яркая, не серая)."),
    ElementDef("ability_1", REGION, True, "Способность 1", "Кнопка первой способности (рамкой)."),
    ElementDef("ability_2", REGION, True, "Способность 2", "Кнопка второй способности."),
    ElementDef("ability_3", REGION, True, "Способность 3", "Кнопка третьей способности."),
    ElementDef("ability_4", REGION, True, "Способность 4", "Кнопка четвёртой способности."),
    ElementDef("ability_next", BUTTON, False, "Стрелка «след. способности»", "Стрелка справа от способностей."),
    ElementDef("ability_prev", BUTTON, False, "Стрелка «пред. способности»", "Стрелка слева от способностей."),
    ElementDef("my_name", REGION, True, "Имя моего крита", "Имя вашего крита в его панели HP."),
    ElementDef("my_hp", REGION, True, "HP моего крита", "Цифры HP вашего крита, например 182/182."),
    ElementDef("enemy_name", REGION, True, "Имя противника", "Имя дикого крита в его панели."),
    ElementDef("enemy_hp", REGION, True, "HP противника", "Цифры HP противника, например 73/73."),
    ElementDef("enemy_rank", REGION, False, "Ранг противника", "Где в бою написан ранг (A+, S…)."),
    ElementDef("capture", BUTTON, True, "Кнопка Capture", "Обычная кнопка поимки."),
    ElementDef("capture_chance", REGION, False, "Шанс поимки", "Процент шанса поимки."),
    ElementDef("plat_capture", BUTTON, False, "Платиновая поимка", "Кнопка поимки за платину."),
    ElementDef("captured", BUTTON, False, "Поймали!", "Элемент окна успешной поимки (например, кнопка Keep)."),
    ElementDef("battle_won", BUTTON, True, "Конец боя", "Кнопка Continue после боя."),
    ElementDef("come_back_later", BUTTON, False, "Точка на кулдауне",
               "Необязательно: бот сам считает кулдаун точек. Надпись «Come back later!» нужна только для подстраховки."),
    ElementDef("train_ready", BUTTON, False, "Есть кого тренировать",
               "Кнопка Train в верхней панели, когда она подсвечена (крит готов)."),
    ElementDef("popup_1", BUTTON, False, "Попап 1", "Любая кнопка, которую надо просто нажать (Okay, Continue…)."),
    ElementDef("popup_2", BUTTON, False, "Попап 2", "Ещё одна такая кнопка."),
    ElementDef("popup_3", BUTTON, False, "Попап 3", "Ещё одна такая кнопка."),
    ElementDef("popup_4", BUTTON, False, "Попап 4", "Ещё одна такая кнопка."),
)
ELEMENT_BY_ID = {e.id: e for e in ELEMENTS}
POPUPS = ("popup_1", "popup_2", "popup_3", "popup_4")
ABILITY_SLOTS = ("ability_1", "ability_2", "ability_3", "ability_4")
ROUTES = {"train": "Тренировка", "heal": "Лечение (до хила и обратно)"}


def encode_image(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("cannot encode image")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def decode_image(data: str) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(base64.b64decode(data), np.uint8), cv2.IMREAD_COLOR)


@dataclass
class Snapshot:
    """Прямоугольник на экране (x, y, w, h) и картинка этого места в момент обучения.
    Для точек поиска ещё подпись (кто тут водится, со слов пользователя) и кого бот тут встречал."""
    rect: tuple
    image: np.ndarray | None = None
    label: str = ""
    seen: dict = field(default_factory=dict)  # имя вида -> сколько раз встречен

    def to_json(self) -> dict:
        data = {"rect": list(self.rect)}
        if self.image is not None:
            data["image"] = encode_image(self.image)
        if self.label:
            data["label"] = self.label
        if self.seen:
            data["seen"] = self.seen
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Snapshot":
        image = decode_image(data["image"]) if data.get("image") else None
        return cls(tuple(int(v) for v in data["rect"]), image, str(data.get("label") or ""),
                   {str(k): int(v) for k, v in (data.get("seen") or {}).items()})

    def species_here(self) -> set:
        return ({self.label} if self.label else set()) | set(self.seen)


@dataclass
class Step:
    snap: Snapshot
    optional: bool = False

    def to_json(self) -> dict:
        return {**self.snap.to_json(), "optional": self.optional}

    @classmethod
    def from_json(cls, data: dict) -> "Step":
        return cls(Snapshot.from_json(data), bool(data.get("optional")))


@dataclass
class Teaching:
    elements: dict = field(default_factory=dict)  # id -> Snapshot
    spots: list = field(default_factory=list)  # [Snapshot]
    routes: dict = field(default_factory=dict)  # имя -> [Step]
    # Полный снимок локации, на котором отмечены точки: по нему бот считает сдвиг камеры.
    # Картинка большая, поэтому хранится отдельным PNG рядом с JSON.
    location: Snapshot | None = None

    def missing_required(self) -> list:
        missing = [e.title for e in ELEMENTS if e.required and e.id not in self.elements]
        if not self.spots:
            missing.append("Точки поиска")
        return missing

    def to_json(self) -> dict:
        return {
            "elements": {k: v.to_json() for k, v in self.elements.items()},
            "spots": [s.to_json() for s in self.spots],
            "routes": {k: [s.to_json() for s in v] for k, v in self.routes.items()},
            "location_rect": list(self.location.rect) if self.location else None,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Teaching":
        return cls(
            {k: Snapshot.from_json(v) for k, v in data.get("elements", {}).items() if k in ELEMENT_BY_ID},
            [Snapshot.from_json(v) for v in data.get("spots", [])],
            {k: [Step.from_json(s) for s in v] for k, v in data.get("routes", {}).items()},
        )


def _location_path(path) -> str:
    root, _ = os.path.splitext(str(path))
    return root + "_location.png"


def load_teaching(path) -> Teaching:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return Teaching()
    teaching = Teaching.from_json(data)
    rect = data.get("location_rect")
    if rect:
        try:
            with open(_location_path(path), "rb") as f:
                image = cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)
            if image is not None:
                teaching.location = Snapshot(tuple(int(v) for v in rect), image)
        except OSError:
            pass
    return teaching


def save_teaching(path, teaching: Teaching, with_location: bool = False) -> None:
    """with_location — переписать и PNG снимка локации (он большой, поэтому только когда он поменялся)."""
    if with_location and teaching.location is not None:
        ok, buf = cv2.imencode(".png", teaching.location.image)
        if ok:
            with open(_location_path(path), "wb") as f:
                f.write(buf.tobytes())
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(teaching.to_json(), f)
    os.replace(tmp, path)
