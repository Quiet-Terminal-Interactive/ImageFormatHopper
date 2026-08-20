from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image


@dataclass
class Cel:
    image: Image.Image
    x: int = 0
    y: int = 0
    opacity: int = 255


@dataclass
class Layer:
    name: str
    cels: dict[int, Cel] = field(default_factory=dict)
    visible: bool = True
    opacity: int = 255
    blend_mode: str = "normal"

    def cel_at(self, frame_index: int) -> Cel | None:
        return self.cels.get(frame_index)


@dataclass
class Frame:
    duration_ms: int = 100


@dataclass
class Document:
    width: int
    height: int
    layers: list[Layer] = field(default_factory=list)
    frames: list[Frame] = field(default_factory=list)
    palette: list[tuple[int, int, int, int]] | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_image(cls, image: Image.Image, *, layer_name: str = "Layer 1") -> "Document":
        image = image.convert("RGBA")
        doc = cls(width=image.width, height=image.height)
        doc.frames.append(Frame())
        layer = Layer(name=layer_name)
        layer.cels[0] = Cel(image=image)
        doc.layers.append(layer)
        return doc

    def flatten_frame(self, frame_index: int) -> Image.Image:
        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        for layer in self.layers:
            if not layer.visible:
                continue
            cel = layer.cel_at(frame_index)
            if cel is None:
                continue
            piece = cel.image
            combined_opacity = (layer.opacity * cel.opacity) // 255
            if combined_opacity < 255:
                alpha = piece.getchannel("A").point(lambda a, o=combined_opacity: a * o // 255)
                piece = piece.copy()
                piece.putalpha(alpha)
            canvas.alpha_composite(piece, dest=(cel.x, cel.y))
        return canvas

    def flatten_all(self) -> list[Image.Image]:
        return [self.flatten_frame(i) for i in range(len(self.frames))]

    @property
    def frame_durations_ms(self) -> list[int]:
        return [f.duration_ms for f in self.frames]

    def is_animated(self) -> bool:
        return len(self.frames) > 1
