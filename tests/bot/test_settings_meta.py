from dataclasses import fields

from mbot.settings import Settings
from mbot.settings_meta import CATEGORIES


def test_every_setting_is_shown_once():
    shown = [f.name for c in CATEGORIES for f in c.fields]
    hidden = {"hunt_targets"}  # задаётся на вкладке «Охота»
    assert sorted(shown) == sorted(f.name for f in fields(Settings) if f.name not in hidden)
    assert len(shown) == len(set(shown))


def test_defaults_fit_their_ranges():
    defaults = Settings()
    for c in CATEGORIES:
        for f in c.fields:
            value = getattr(defaults, f.name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                assert f.low <= value <= f.high, f.name
