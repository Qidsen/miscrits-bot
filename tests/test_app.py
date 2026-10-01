import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from miscrits_hud.app import acquire_single_instance


def test_second_instance_is_refused(tmp_path):
    first = acquire_single_instance(tmp_path)
    assert first is not None
    assert acquire_single_instance(tmp_path) is None
    first.unlock()
    assert acquire_single_instance(tmp_path) is not None
