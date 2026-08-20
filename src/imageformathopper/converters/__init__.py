from __future__ import annotations

import importlib
import pkgutil

for _module_info in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module_info.name}")

del importlib, pkgutil
