import logging
import struct
import zlib
from pathlib import Path
from typing import NamedTuple

from PIL import Image

from ..core import BaseConverter, Cel, Document, Frame, Layer, ParseError, register

logger = logging.getLogger(__name__)

_HEADER_STRUCT = struct.Struct(
    "<"
    "I"  # file size
    "H"  # magic number
    "H"  # frames
    "H"  # width
    "H"  # height
    "H"  # color depth
)
_FRAME_HEADER_STRUCT = struct.Struct(
    "<"
    "I"  # frame size
    "H"  # magic number
    "H"  # old chunk count
    "H"  # frame duration
    "2x" # reserved
    "I"  # new chunk count
)
_CHUNK_STRUCT = struct.Struct(
    "<"
    "I"  # chunk size
    "H"  # chunk type
)
_LAYER_CHUNK_STRUCT = struct.Struct(
    "<"
    "H"  # flags
    "H"  # layer type
    "H"  # child level
    "H"  # default width (ignored)
    "H"  # default height (ignored)
    "H"  # blend mode
    "B"  # opacity
    "3x" # reserved
)
_CEL_CHUNK_STRUCT = struct.Struct(
    "<"
    "H"  # layer index
    "h"  # x position
    "h"  # y position
    "B"  # opacity
    "H"  # cel type
    "h"  # z-index
    "5x" # reserved
)
_PALETTE_CHUNK_STRUCT = struct.Struct(
    "<"
    "I"  # new palette size
    "I"  # first color index
    "I"  # last color index
    "8x" # reserved
)
_PALETTE_ENTRY_STRUCT = struct.Struct(
    "<"
    "H"  # entry flags
    "B"  # red
    "B"  # green
    "B"  # blue
    "B"  # alpha
)

_FILE_HEADER_SIZE = 128
_MAGIC_NUMBER = 0xA5E0
_FRAME_MAGIC_NUMBER = 0xF1FA

_CHUNK_TYPE_LAYER = 0x2004
_CHUNK_TYPE_CEL = 0x2005
_CHUNK_TYPE_PALETTE = 0x2019

_CEL_TYPE_RAW_IMAGE = 0
_CEL_TYPE_LINKED = 1
_CEL_TYPE_COMPRESSED_IMAGE = 2

_Palette = list[tuple[int, int, int, int]]


class _ImageCelChunk(NamedTuple):
    layer_index: int
    x: int
    y: int
    opacity: int
    width: int
    height: int
    raw: bytes


class _LinkedCelChunk(NamedTuple):
    layer_index: int
    x: int
    y: int
    linked_frame: int


class _ParsedFrame(NamedTuple):
    duration_ms: int
    layers: list[Layer]
    image_cels: list[_ImageCelChunk]
    linked_cels: list[_LinkedCelChunk]
    palette: _Palette | None


def _build_cel_chunk_body(cel: Cel, layer_index: int) -> bytes:
    pixel_bytes = cel.image.tobytes()
    compressed = zlib.compress(pixel_bytes)

    fixed_fields = _CEL_CHUNK_STRUCT.pack(
        layer_index,
        cel.x,
        cel.y,
        cel.opacity,
        _CEL_TYPE_COMPRESSED_IMAGE,
        0,
    )
    dimensions = struct.pack("<HH", cel.image.width, cel.image.height)

    return fixed_fields + dimensions + compressed


def _build_layer_chunk_body(layer: Layer) -> bytes:
    flags = (1 if layer.visible else 0) | 2
    fixed_fields = _LAYER_CHUNK_STRUCT.pack(flags, 0, 0, 0, 0, 0, layer.opacity)
    name_bytes = layer.name.encode("utf-8")
    name_len = struct.pack("<H", len(name_bytes))
    return fixed_fields + name_len + name_bytes


def _wrap_chunk(chunk_type: int, body: bytes) -> bytes:
    chunk_size = _CHUNK_STRUCT.size + len(body)
    header = _CHUNK_STRUCT.pack(chunk_size, chunk_type)
    return header + body


def _build_frame_bytes(chunks: list[bytes], duration_ms: int) -> bytes:
    body = b"".join(chunks)
    chunk_count = len(chunks)
    frame_size = _FRAME_HEADER_STRUCT.size + len(body)
    header = _FRAME_HEADER_STRUCT.pack(frame_size, _FRAME_MAGIC_NUMBER, chunk_count, duration_ms, chunk_count)
    return header + body


def _read_file_header(data: bytes, path: str | Path) -> tuple[int, int, int, int]:
    if len(data) < _HEADER_STRUCT.size:
        raise ParseError(f"{path}: file too small to be an Aseprite file")

    _file_size, magic, frame_count, width, height, color_depth = _HEADER_STRUCT.unpack_from(data, 0)
    if magic != _MAGIC_NUMBER:
        raise ParseError(f"{path}: bad magic number 0x{magic:04x}, not an Aseprite file")

    logger.debug(
        "file header: declared_size=%d frame_count=%d canvas=%dx%d color_depth=%d actual_size=%d",
        _file_size,
        frame_count,
        width,
        height,
        color_depth,
        len(data),
    )
    if _file_size != len(data):
        logger.debug("declared file size does not match actual size (off by %d bytes)", len(data) - _file_size)

    return frame_count, width, height, color_depth


def _read_frame_header(data: bytes, offset: int, frame_index: int, path: str | Path) -> tuple[int, int, int]:
    frame_size, magic, old_count, duration_ms, new_count = _FRAME_HEADER_STRUCT.unpack_from(data, offset)
    if magic != _FRAME_MAGIC_NUMBER:
        raise ParseError(f"{path}: bad magic number in frame {frame_index}, corrupted Aseprite file.")

    chunk_count = new_count if new_count != 0 else old_count
    logger.debug(
        "frame %d header at %d: frame_size=%d old_count=%d new_count=%d duration_ms=%d",
        frame_index,
        offset,
        frame_size,
        old_count,
        new_count,
        duration_ms,
    )
    return frame_size, chunk_count, duration_ms


def _parse_layer_chunk(data: bytes, offset: int) -> Layer:
    flags, _layer_type, _child_level, _dw, _dh, _blend_mode, opacity = _LAYER_CHUNK_STRUCT.unpack_from(data, offset)
    name_len_offset = offset + _LAYER_CHUNK_STRUCT.size
    name_len = struct.unpack_from("<H", data, name_len_offset)[0]
    name_bytes_offset = name_len_offset + 2
    name = data[name_bytes_offset : name_bytes_offset + name_len].decode("utf-8")

    logger.debug("layer chunk: name=%r visible=%s opacity=%d", name, bool(flags & 1), opacity)
    return Layer(name=name, visible=bool(flags & 1), opacity=opacity)


def _parse_cel_chunk(data: bytes, offset: int, chunk_end: int) -> _ImageCelChunk | _LinkedCelChunk:
    layer_index, x, y, opacity, cel_type = _CEL_CHUNK_STRUCT.unpack_from(data, offset)[:5]
    body_offset = offset + _CEL_CHUNK_STRUCT.size

    if cel_type in (_CEL_TYPE_RAW_IMAGE, _CEL_TYPE_COMPRESSED_IMAGE):
        width, height = struct.unpack_from("<HH", data, body_offset)
        pixel_offset = body_offset + 4
        raw = (
            data[pixel_offset:chunk_end]
            if cel_type == _CEL_TYPE_RAW_IMAGE
            else zlib.decompress(data[pixel_offset:chunk_end])
        )
        logger.debug(
            "cel chunk: layer_index=%d pos=(%d,%d) opacity=%d type=%s size=%dx%d raw_len=%d",
            layer_index,
            x,
            y,
            opacity,
            "raw" if cel_type == _CEL_TYPE_RAW_IMAGE else "compressed",
            width,
            height,
            len(raw),
        )
        return _ImageCelChunk(layer_index, x, y, opacity, width, height, raw)

    linked_frame = struct.unpack_from("<H", data, body_offset)[0]
    logger.debug("cel chunk: layer_index=%d pos=(%d,%d) type=linked linked_frame=%d", layer_index, x, y, linked_frame)
    return _LinkedCelChunk(layer_index, x, y, linked_frame)


def _parse_palette_chunk(data: bytes, offset: int, palette: _Palette | None) -> _Palette:
    palette_size, first_index, last_index = _PALETTE_CHUNK_STRUCT.unpack_from(data, offset)[:3]
    logger.debug(
        "palette chunk: palette_size=%d first_index=%d last_index=%d",
        palette_size,
        first_index,
        last_index,
    )

    if palette is None:
        palette = [(0, 0, 0, 0)] * palette_size
    elif len(palette) < palette_size:
        palette = palette + [(0, 0, 0, 0)] * (palette_size - len(palette))
    elif len(palette) > palette_size:
        palette = palette[:palette_size]

    entry_offset = offset + _PALETTE_CHUNK_STRUCT.size
    for index in range(first_index, last_index + 1):
        flags, r, g, b, a = _PALETTE_ENTRY_STRUCT.unpack_from(data, entry_offset)
        palette[index] = (r, g, b, a)
        entry_offset += _PALETTE_ENTRY_STRUCT.size

        if flags & 1:
            name_len = struct.unpack_from("<H", data, entry_offset)[0]
            entry_offset += 2 + name_len

    return palette


def _parse_frame_chunks(
    data: bytes, offset: int, chunk_count: int, duration_ms: int, palette: _Palette | None
) -> tuple[_ParsedFrame, int]:
    layers: list[Layer] = []
    image_cels: list[_ImageCelChunk] = []
    linked_cels: list[_LinkedCelChunk] = []

    cursor = offset
    for _ in range(chunk_count):
        chunk_size, chunk_type = _CHUNK_STRUCT.unpack_from(data, cursor)
        chunk_data_start = cursor + _CHUNK_STRUCT.size
        chunk_end = cursor + chunk_size
        cursor = chunk_end

        if chunk_type == _CHUNK_TYPE_LAYER:
            layers.append(_parse_layer_chunk(data, chunk_data_start))
        elif chunk_type == _CHUNK_TYPE_CEL:
            cel = _parse_cel_chunk(data, chunk_data_start, chunk_end)
            if isinstance(cel, _ImageCelChunk):
                image_cels.append(cel)
            else:
                linked_cels.append(cel)
        elif chunk_type == _CHUNK_TYPE_PALETTE:
            palette = _parse_palette_chunk(data, chunk_data_start, palette)
        else:
            logger.debug("skipping unhandled chunk type 0x%04x (size=%d) at %d", chunk_type, chunk_size, cursor - chunk_size)

    return _ParsedFrame(duration_ms, layers, image_cels, linked_cels, palette), cursor


def _expand_indexed_pixels(raw: bytes, palette: _Palette) -> bytes:
    pixels = bytearray(len(raw) * 4)
    for i, index in enumerate(raw):
        pixels[i * 4 : i * 4 + 4] = palette[index]
    return bytes(pixels)


def _decode_cel_image(
    color_depth: int,
    width: int,
    height: int,
    raw: bytes,
    palette: _Palette | None,
    path: str | Path,
) -> Image.Image:
    if color_depth == 8:
        if palette is None:
            raise ParseError(f"{path}: indexed-color cel with no palette chunk")
        return Image.frombytes("RGBA", (width, height), _expand_indexed_pixels(raw, palette))
    if color_depth == 16:
        return Image.frombytes("LA", (width, height), raw).convert("RGBA")
    return Image.frombytes("RGBA", (width, height), raw)


@register("aseprite", extensions=[".aseprite", ".ase"])
class AsepriteConverter(BaseConverter):
    def read(self, path: str | Path) -> Document:
        logger.debug("reading Aseprite file: %s", path)
        with open(path, "rb") as f:
            data = f.read()

        frame_count, width, height, color_depth = _read_file_header(data, path)
        doc = Document(width=width, height=height)

        cursor = _FILE_HEADER_SIZE
        palette: _Palette | None = None

        for frame_index in range(frame_count):
            frame_start = cursor
            frame_size, chunk_count, duration_ms = _read_frame_header(data, cursor, frame_index, path)
            cursor += _FRAME_HEADER_STRUCT.size

            parsed, chunks_end = _parse_frame_chunks(data, cursor, chunk_count, duration_ms, palette)
            palette = parsed.palette

            expected_end = frame_start + frame_size
            if chunks_end != expected_end:
                raise ParseError(
                    f"{path}: frame {frame_index} chunk data ended at byte {chunks_end}, "
                    f"expected {expected_end} from the frame header's declared size"
                )

            logger.debug(
                "frame %d: %d layer(s), %d image cel(s), %d linked cel(s)",
                frame_index,
                len(parsed.layers),
                len(parsed.image_cels),
                len(parsed.linked_cels),
            )

            doc.frames.append(Frame(duration_ms=parsed.duration_ms))
            doc.layers.extend(parsed.layers)

            for image_cel in parsed.image_cels:
                if image_cel.layer_index >= len(doc.layers):
                    logger.debug(
                        "cel references layer_index=%d but only %d layer(s) exist so far",
                        image_cel.layer_index,
                        len(doc.layers),
                    )
                image = _decode_cel_image(
                    color_depth, image_cel.width, image_cel.height, image_cel.raw, palette, path
                )
                doc.layers[image_cel.layer_index].cels[frame_index] = Cel(
                    image=image, x=image_cel.x, y=image_cel.y, opacity=image_cel.opacity
                )

            for linked_cel in parsed.linked_cels:
                layer = doc.layers[linked_cel.layer_index]
                layer.cels[frame_index] = layer.cels[linked_cel.linked_frame]

            cursor = expected_end

        doc.palette = palette
        logger.debug("finished reading: %d layer(s), %d frame(s)", len(doc.layers), len(doc.frames))
        return doc

    def write(self, document: Document, path: str | Path) -> None:
        logger.debug(
            "writing Aseprite file: %s (%dx%d, %d layer(s), %d frame(s))",
            path,
            document.width,
            document.height,
            len(document.layers),
            len(document.frames),
        )
        frame_bytes = []

        for frame_index, frame in enumerate(document.frames):
            chunks = []

            if frame_index == 0:
                for layer in document.layers:
                    chunks.append(_wrap_chunk(_CHUNK_TYPE_LAYER, _build_layer_chunk_body(layer)))

            cel_count = 0
            for layer_index, layer in enumerate(document.layers):
                cel = layer.cel_at(frame_index)
                if cel is not None:
                    chunks.append(_wrap_chunk(_CHUNK_TYPE_CEL, _build_cel_chunk_body(cel, layer_index)))
                    cel_count += 1

            logger.debug(
                "frame %d: %d chunk(s) (%d layer chunk(s), %d cel chunk(s)), duration_ms=%d",
                frame_index,
                len(chunks),
                len(document.layers) if frame_index == 0 else 0,
                cel_count,
                frame.duration_ms,
            )
            frame_bytes.append(_build_frame_bytes(chunks, frame.duration_ms))

        body = b"".join(frame_bytes)

        file_size = _FILE_HEADER_SIZE + len(body)
        header = _HEADER_STRUCT.pack(
            file_size,
            _MAGIC_NUMBER,
            len(document.frames),
            document.width,
            document.height,
            32,
        )
        header += b"\x00" * (_FILE_HEADER_SIZE - _HEADER_STRUCT.size)
        logger.debug("writing header: file_size=%d", file_size)

        with open(path, "wb") as f:
            f.write(header + body)
