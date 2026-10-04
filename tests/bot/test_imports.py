import importlib
import pkgutil

import mbot


def test_every_module_imports():
    """Синтаксическая ошибка в окне не ловится тестами логики — а exe тогда не запускается."""
    for module in pkgutil.walk_packages(mbot.__path__, "mbot."):
        importlib.import_module(module.name)
