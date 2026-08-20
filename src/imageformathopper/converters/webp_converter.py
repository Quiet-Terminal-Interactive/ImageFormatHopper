from pathlib import Path

from PIL import Image, ImageSequence

from ..core import BaseConverter, Document, Frame, Layer, register
from ..core.document import Cel


@register("webp", extensions=[".webp"])
class WebpConverter(BaseConverter):
    def read(self, path: str | Path) -> Document:
        with Image.open(path) as im:
            doc = Document(width=im.width, height=im.height)
            layer = Layer(name="Layer 1")
            is_animated = getattr(im, "is_animated", False)
            if is_animated:
                for index, frame in enumerate(ImageSequence.Iterator(im)):
                    doc.frames.append(Frame(duration_ms=frame.info.get("duration", 100)))
                    layer.cels[index] = Cel(image=frame.convert("RGBA"))
            else:
                doc.frames.append(Frame())
                layer.cels[0] = Cel(image=im.convert("RGBA"))
            doc.layers.append(layer)
            return doc

    def write(self, document: Document, path: str | Path) -> None:
        frames = document.flatten_all()
        if not frames:
            frames = [Image.new("RGBA", (document.width, document.height), (0, 0, 0, 0))]

        if len(frames) == 1:
            frames[0].save(path, format="WEBP", lossless=True)
            return

        durations = document.frame_durations_ms or [100] * len(frames)
        frames[0].save(
            path,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            lossless=True,
        )
