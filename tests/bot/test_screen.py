import numpy as np

from mbot.screen import around, best_name, find, parse_hp, parse_percent, parse_rank


def _scene():
    rng = np.random.default_rng(1)
    image = rng.integers(0, 255, (400, 600, 3), dtype=np.uint8)
    return image


def test_find_locates_template_and_respects_threshold():
    image = _scene()
    template = image[100:140, 200:260].copy()
    assert find(image, template, 0.9) == (200, 100, 60, 40)
    assert find(image, template, 0.9, near=(210, 110, 60, 40)) == (200, 100, 60, 40)
    other = np.full((40, 60, 3), 7, np.uint8)
    other[::2] = 250
    assert find(image, other, 0.9) is None


def test_find_near_does_not_see_far_matches():
    image = _scene()
    template = image[10:40, 10:50].copy()
    assert find(image, template, 0.9, near=(500, 300, 40, 30)) is None


def test_around_clamps_to_screen():
    assert around((5, 5), 100, (400, 600, 3)) == (0, 0, 100, 100)
    assert around((590, 395), 100, (400, 600, 3)) == (500, 300, 100, 100)


def test_parsers():
    assert parse_hp(["junk", "73 / 75"]) == (73, 75)
    assert parse_hp(["80/75"]) is None
    assert parse_percent(["Capture 45%"]) == 45
    assert parse_rank(["Rank: A+"]) == "A+"
    assert parse_rank(["5"]) == "S"
    assert parse_rank(["nothing"]) is None


def test_best_name_is_fuzzy():
    names = ["Lavarilla", "Flue", "Dark Lavarilla"]
    assert best_name(["Lavari11a"], names) == "Lavarilla"
    assert best_name(["dark lavarila"], names) == "Dark Lavarilla"
    assert best_name(["zzz"], names) is None


def test_rank_badge_from_game():
    from pathlib import Path

    import cv2

    from mbot.screen import Ocr, rank_glyph

    image = cv2.imread(str(Path(__file__).parent / "data" / "rank_c_plus.png"))
    letter, plus = rank_glyph(image)
    assert plus and letter.shape[0] > 50
    ocr = Ocr(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if ocr.available():
        assert ocr.rank(image) == "C+"


def test_outlined_white_text_from_game():
    from pathlib import Path

    import cv2

    from mbot.screen import Ocr

    ocr = Ocr(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if not ocr.available():
        return
    data = Path(__file__).parent / "data"

    def read(name, whitelist=None):
        return ocr.text(cv2.imread(str(data / name)), whitelist)

    assert parse_hp(read("hp_182.png", "0123456789/")) == (182, 182)
    assert parse_hp(read("hp_71.png", "0123456789/")) == (71, 71)
    abilities = ["The Big Finale", "Veto", "Hurricane", "Hyper Power", "Mush", "Swipe"]
    assert best_name(read("ability_big_finale.png"), abilities) == "The Big Finale"
    assert best_name(read("ability_veto.png"), abilities) == "Veto"
    assert best_name(read("ability_hurricane.png"), abilities) == "Hurricane"
    assert best_name(read("ability_hyper_power.png"), abilities) == "Hyper Power"


def test_names_and_second_rank_sample_from_game():
    from pathlib import Path

    import cv2

    from mbot.screen import Ocr

    ocr = Ocr(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if not ocr.available():
        return
    data = Path(__file__).parent / "data"
    names = ["Flameling", "Dark Flameling", "Flaring", "Patriot", "Quirk", "Lavarilla"]
    read = lambda name: ocr.read(cv2.imread(str(data / name)), lambda t: best_name(t, names))  # noqa: E731
    assert read("name_flameling.png") == "Flameling"  # psm 7 на ней молчит — нужен перебор режимов
    assert read("name_my_name.png") == "Patriot"
    assert read("name_enemy_name.png") == "Quirk"
    assert ocr.rank(cv2.imread(str(data / "rank_c_plus_2.png"))) == "C+"
    assert ocr.rank(cv2.imread(str(data / "rank_c_plus.png"))) == "C+"


def test_pick_hp_votes_and_prefers_the_reading_with_all_digits():
    from mbot.screen import pick_hp
    # Tesseract теряет первую цифру: 156/182 читается как 56/182 — так Patriot «оказался» с 31% HP
    assert pick_hp([(56, 182), (156, 182), (156, 182)]) == (156, 182)
    assert pick_hp([(56, 182), (156, 182)]) == (156, 182)  # поровну — берём с бóльшим текущим
    assert pick_hp([(3, 83), (83, 83), (3, 63), (83, 83)]) == (83, 83)
    assert pick_hp([]) is None


def test_pick_hp_drops_readings_that_contradict_the_bar():
    from mbot.screen import pick_hp
    assert pick_hp([(56, 182), (56, 182)], bar=0.86) == (157, 182)  # цифры против полоски — верим полоске
    assert pick_hp([(60, 71), (0, 71)], bar=0.85) == (60, 71)


def test_tesseract_from_settings_wins_then_bundled(tmp_path, monkeypatch):
    from mbot import screen
    installed = tmp_path / "installed.exe"
    installed.write_bytes(b"")
    bundled = tmp_path / "tesseract" / "tesseract.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"")
    monkeypatch.setattr(screen, "bundled_tesseract", lambda: bundled)
    monkeypatch.setattr(screen.shutil, "which", lambda name: None)
    # установлен там, где сказано в настройках, — им и пользуемся
    assert screen.resolve_tesseract(str(installed)) == str(installed)
    # не установлен (у другого человека) — вшитый в программу
    assert screen.resolve_tesseract(str(tmp_path / "Tesseract-OCR" / "tesseract.exe")) == str(bundled)
    # нигде нет — возвращаем путь из настроек как есть (проверка готовности скажет, что не найден)
    monkeypatch.setattr(screen, "bundled_tesseract", lambda: tmp_path / "none.exe")
    assert screen.resolve_tesseract("X:/nope.exe") == "X:/nope.exe"
