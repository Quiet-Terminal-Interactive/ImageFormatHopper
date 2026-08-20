from pathlib import Path

from PIL import Image

from ..core import SingleImageConverter, register


@register("jpg", extensions=[".jpg", ".jpeg"])
class JpgConverter(SingleImageConverter):
    def load_image(self, path: str | Path) -> Image.Image:
        return Image.open(path)

    def save_image(self, image: Image.Image, path: str | Path) -> None:
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.getchannel("A"))
            image = background
        image.save(path, format="JPEG", quality=95)
