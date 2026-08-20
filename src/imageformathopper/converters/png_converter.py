from pathlib import Path

from PIL import Image

from ..core import SingleImageConverter, register


@register("png", extensions=[".png"])
class PngConverter(SingleImageConverter):
    def load_image(self, path: str | Path) -> Image.Image:
        return Image.open(path)

    def save_image(self, image: Image.Image, path: str | Path) -> None:
        image.save(path, format="PNG")
