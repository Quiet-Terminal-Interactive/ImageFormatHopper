from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .exceptions import UnsupportedFormatError

if TYPE_CHECKING:
    from .base import BaseConverter

_CONVERTERS: dict[str, "BaseConverter"] = {}
_EXTENSION_INDEX: dict[str, str] = {}

def register(format_name: str, extensions: list[str]):
    def decorator(cls):
        cls.format_name = format_name
        cls.extensions = tuple(e.lower() for e in extensions)
        instance = cls()
        _CONVERTERS[format_name] = instance
        for ext in cls.extensions:
            _EXTENSION_INDEX[ext] = format_name
        return cls

    return decorator


def get_converter(format_name: str) -> "BaseConverter":
    try:
        return _CONVERTERS[format_name]
    except KeyError:
        raise UnsupportedFormatError(
            f"No converter registered for format {format_name!r}. "
            f"Known formats: {sorted(_CONVERTERS)}"
        ) from None


def get_converter_for_path(path: str | Path) -> "BaseConverter":
    ext = Path(path).suffix.lower()
    try:
        format_name = _EXTENSION_INDEX[ext]
    except KeyError:
        raise UnsupportedFormatError(
            f"No converter registered for extension {ext!r} (from {path!r}). "
            f"Known extensions: {sorted(_EXTENSION_INDEX)}"
        ) from None
    return get_converter(format_name)


def list_formats() -> dict[str, tuple[str, ...]]:
    return {name: conv.extensions for name, conv in _CONVERTERS.items()}
