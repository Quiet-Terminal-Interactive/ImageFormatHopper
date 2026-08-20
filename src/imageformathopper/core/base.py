from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from PIL import Image

from .document import Document


class BaseConverter(ABC):
    format_name: str
    extensions: tuple[str, ...]

    @abstractmethod
    def read(self, path: str | Path) -> Document:
        """Parse ``path`` and return a Document. Raise ParseError on bad input."""

    @abstractmethod
    def write(self, document: Document, path: str | Path) -> None:
        """Serialize ``document`` to ``path``. Raise WriteError if it can't be represented."""


class SingleImageConverter(BaseConverter):
    @abstractmethod
    def load_image(self, path: str | Path) -> Image.Image:
        """Return a PIL Image loaded from ``path``."""

    @abstractmethod
    def save_image(self, image: Image.Image, path: str | Path) -> None:
        """Save a flat RGBA PIL Image to ``path``."""

    def read(self, path: str | Path) -> Document:
        return Document.from_image(self.load_image(path))

    def write(self, document: Document, path: str | Path) -> None:
        flattened = document.flatten_frame(0) if document.frames else Image.new(
            "RGBA", (document.width, document.height), (0, 0, 0, 0)
        )
        self.save_image(flattened, path)
