from pathlib import Path

from PIL import Image, ImageSequence

from ..core import BaseConverter, Document, Frame, Layer, register
from ..core.document import Cel


@register("gif", extensions=[".gif"])
class GifConverter(BaseConverter):
    def read(self, path: str | Path) -> Document:
        with Image.open(path) as im:
            doc = Document(width=im.width, height=im.height)
            layer = Layer(name="Layer 1")
            for index, frame in enumerate(ImageSequence.Iterator(im)):
                doc.frames.append(Frame(duration_ms=frame.info.get("duration", 100)))
                layer.cels[index] = Cel(image=frame.convert("RGBA"))
            doc.layers.append(layer)
            return doc

    def write(self, document: Document, path: str | Path) -> None:
        frames = document.flatten_all()
        if not frames:
            frames = [Image.new("RGBA", (document.width, document.height), (0, 0, 0, 0))]
            durations = [100]
        else:
            durations = document.frame_durations_ms or [100] * len(frames)

        rgb_frames = []
        for frame in frames:
            background = Image.new("RGB", frame.size, (255, 255, 255))
            background.paste(frame, mask=frame.getchannel("A"))
            rgb_frames.append(background)

        rgb_frames[0].save(
            path,
            format="GIF",
            save_all=True,
            append_images=rgb_frames[1:],
            duration=durations,
            loop=0,
            disposal=2,
        )
