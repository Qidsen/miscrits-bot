import numpy as np

from mbot.settings import Settings, load_settings, save_settings
from mbot.storage import Snapshot, Step, Teaching, load_teaching, save_teaching


def test_teaching_roundtrip(tmp_path):
    img = np.zeros((10, 20, 3), np.uint8)
    img[2:5, 3:9] = (10, 200, 30)
    t = Teaching(
        {"battle": Snapshot((1, 2, 20, 10), img), "my_hp": Snapshot((5, 6, 70, 20))},
        [Snapshot((100, 100, 20, 10), img)],
        {"heal": [Step(Snapshot((0, 0, 20, 10), img), optional=True)]},
    )
    path = tmp_path / "t.json"
    save_teaching(path, t)
    back = load_teaching(path)
    assert back.elements["my_hp"].rect == (5, 6, 70, 20) and back.elements["my_hp"].image is None
    assert np.array_equal(back.elements["battle"].image, img)
    assert back.routes["heal"][0].optional and back.spots[0].rect == (100, 100, 20, 10)


def test_missing_required_lists_spots_and_elements():
    missing = Teaching().missing_required()
    assert "Точки поиска" in missing and "Признак боя" in missing


def test_load_missing_file(tmp_path):
    assert load_teaching(tmp_path / "nope.json").spots == []


def test_settings_ignore_unknown_keys(tmp_path):
    path = tmp_path / "s.json"
    save_settings(path, Settings(heal_below=10))
    path.write_text(path.read_text().replace("{", '{"junk": 1,', 1))
    assert load_settings(path).heal_below == 10
