from __future__ import annotations

import logging
import struct
import time
import zlib
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree

from PIL import Image

from ..core import BaseConverter, Cel, Document, Frame, Layer, ParseError, register

logger = logging.getLogger(__name__)

_FILE_MAGIC = b"mdipack\x00"
_FILE_HEADER_STRUCT = struct.Struct(
    "<"
    "I"  # reserved
    "I"  # xml length
    "I"  # data length
)

_PAC_MAGIC = b"PAC "
_PAC_HEADER_STRUCT = struct.Struct(
    "<"
    "I"  # total block size
    "I"  # flag (1 = flat zlib image, 0 = tiled)
    "I"  # payload length
)
_PAC_RESERVED_SIZE = 52
_PAC_NAME_SIZE = 64
_PAC_FLAG_FLAT = 1
_PAC_FLAG_TILED = 0

_TILE_COUNT_STRUCT = struct.Struct("<I")  # tile_count alone (empty layer: nothing follows)
_TILE_HEADER_STRUCT = struct.Struct(
    "<"
    "I"  # tile_count
    "I"  # tile_size
)
_TILE_RECORD_STRUCT = struct.Struct(
    "<"
    "I"  # tile_x (grid index, not pixels)
    "I"  # tile_y (grid index, not pixels)
    "I"  # reserved
    "I"  # compressed_len
)
_TILE_DIM = 128

_THUMB_DIM = 256


class _PacBlock(NamedTuple):
    name: str
    flag: int
    payload: bytes
    total_size: int


_Blocks = dict[str, _PacBlock]


def _bgra_bytes_to_rgba_image(raw: bytes, width: int, height: int) -> Image.Image:
    bgra = Image.frombytes("RGBA", (width, height), raw)
    b, g, r, a = bgra.split()
    return Image.merge("RGBA", (r, g, b, a))


def _rgba_image_to_bgra_bytes(image: Image.Image) -> bytes:
    r, g, b, a = image.convert("RGBA").split()
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def _pad4(length: int) -> int:
    return (-length) % 4


def _read_file_header(data: bytes, path: str | Path) -> tuple[bytes, int, int]:
    if data[:8] != _FILE_MAGIC:
        raise ParseError(f"{path}: bad magic {data[:8]!r}, not a FireAlpaca file")

    _reserved, xml_len, data_len = _FILE_HEADER_STRUCT.unpack_from(data, 8)
    header_size = 8 + _FILE_HEADER_STRUCT.size
    xml_bytes = data[header_size : header_size + xml_len]
    blocks_start = header_size + xml_len
    logger.debug(
        "file header: total_bytes=%d xml_len=%d data_len=%d blocks_start=%d expected_end=%d",
        len(data),
        xml_len,
        data_len,
        blocks_start,
        blocks_start + data_len,
    )
    if blocks_start + data_len != len(data):
        logger.debug(
            "header/data_len does not match actual file size (off by %d bytes)",
            len(data) - (blocks_start + data_len),
        )
    return xml_bytes, blocks_start, data_len


def _parse_metadata_xml(xml_bytes: bytes, path: str | Path) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ParseError(f"{path}: bad metadata XML: {exc}") from None


def _read_pac_block(data: bytes, offset: int, path: str | Path) -> _PacBlock:
    if data[offset : offset + 4] != _PAC_MAGIC:
        raise ParseError(f"{path}: expected PAC block at byte {offset}, found {data[offset:offset+4]!r}")

    total_size, flag, payload_len = _PAC_HEADER_STRUCT.unpack_from(data, offset + 4)
    name_offset = offset + 4 + _PAC_HEADER_STRUCT.size + _PAC_RESERVED_SIZE
    name = data[name_offset : name_offset + _PAC_NAME_SIZE].split(b"\x00", 1)[0].decode("utf-8")
    payload_offset = name_offset + _PAC_NAME_SIZE
    payload = data[payload_offset : payload_offset + payload_len]
    logger.debug(
        "PAC block at %d: name=%r flag=%d payload_len=%d total_size=%d",
        offset,
        name,
        flag,
        payload_len,
        total_size,
    )
    return _PacBlock(name, flag, payload, total_size)


def _read_blocks(data: bytes, start: int, end: int, path: str | Path) -> _Blocks:
    blocks: _Blocks = {}
    cursor = start
    while cursor < end:
        block = _read_pac_block(data, cursor, path)
        blocks[block.name] = block
        cursor += block.total_size
    logger.debug("read %d block(s): %s", len(blocks), sorted(blocks))
    return blocks


def _decode_flat_image(payload: bytes, width: int, height: int) -> Image.Image:
    raw = zlib.decompress(payload)
    return _bgra_bytes_to_rgba_image(raw, width, height)


def _decode_tiled_image(payload: bytes, width: int, height: int) -> Image.Image | None:
    tile_count, = _TILE_COUNT_STRUCT.unpack_from(payload, 0)
    if tile_count == 0:
        # An empty layer stores only the 4-byte tile_count, no tile_size field
        logger.debug("tiled layer (%dx%d): tile_count=0, treating as empty", width, height)
        return None
    tile_size = _TILE_HEADER_STRUCT.unpack_from(payload, 0)[1]
    logger.debug(
        "tiled layer (%dx%d): tile_count=%d tile_size=%d payload_len=%d",
        width,
        height,
        tile_count,
        tile_size,
        len(payload),
    )

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    cursor = _TILE_HEADER_STRUCT.size
    for i in range(tile_count):
        tile_x, tile_y, _reserved, compressed_len = _TILE_RECORD_STRUCT.unpack_from(payload, cursor)
        cursor += _TILE_RECORD_STRUCT.size
        compressed = payload[cursor : cursor + compressed_len]
        cursor += compressed_len + _pad4(compressed_len)

        logger.debug(
            "  tile %d/%d: grid=(%d,%d) compressed_len=%d cursor=%d",
            i + 1,
            tile_count,
            tile_x,
            tile_y,
            compressed_len,
            cursor,
        )
        raw = zlib.decompress(compressed)
        tile = _bgra_bytes_to_rgba_image(raw, tile_size, tile_size)
        canvas.paste(tile, (tile_x * tile_size, tile_y * tile_size))

    if cursor != len(payload):
        logger.debug(
            "tiled payload has %d trailing byte(s) after last tile (cursor=%d, payload_len=%d)",
            len(payload) - cursor,
            cursor,
            len(payload),
        )

    return canvas.crop((0, 0, width, height))


def _parse_layer_element(layer_el: ElementTree.Element, blocks: _Blocks, width: int, height: int) -> Layer:
    layer = Layer(
        name=layer_el.get("name", ""),
        opacity=int(layer_el.get("alpha", "255")),
        blend_mode=layer_el.get("mode", "normal"),
    )
    layer_width = int(layer_el.get("width", width))
    layer_height = int(layer_el.get("height", height))
    offset_x = int(layer_el.get("ofsx", "0"))
    offset_y = int(layer_el.get("ofsy", "0"))

    bin_name = layer_el.get("bin")
    logger.debug(
        "parsing layer %r: bin=%r size=%dx%d offset=(%d,%d) mode=%r alpha=%s",
        layer.name,
        bin_name,
        layer_width,
        layer_height,
        offset_x,
        offset_y,
        layer.blend_mode,
        layer_el.get("alpha"),
    )
    block = blocks[bin_name]
    image = (
        _decode_flat_image(block.payload, layer_width, layer_height)
        if block.flag == _PAC_FLAG_FLAT
        else _decode_tiled_image(block.payload, layer_width, layer_height)
    )
    if image is None:
        logger.debug("layer %r decoded to no image (empty)", layer.name)
    layer_el_id = layer_el.get("parentId")
    if layer_el_id not in (None, "-1"):
        logger.debug("layer %r has non-root parentId=%r (grouping is not modeled)", layer.name, layer_el_id)
    if image is not None:
        layer.cels[0] = Cel(image=image, x=offset_x, y=offset_y)

    return layer


def _build_pac_block(name: str, flag: int, payload: bytes) -> bytes:
    name_bytes = name.encode("utf-8")
    if len(name_bytes) > _PAC_NAME_SIZE:
        raise ValueError(f"block name {name!r} too long for {_PAC_NAME_SIZE}-byte field")
    name_field = name_bytes.ljust(_PAC_NAME_SIZE, b"\x00")

    total_size = (
        4 + _PAC_HEADER_STRUCT.size + _PAC_RESERVED_SIZE + _PAC_NAME_SIZE + len(payload)
    )
    header = _PAC_MAGIC + _PAC_HEADER_STRUCT.pack(total_size, flag, len(payload))
    return header + b"\x00" * _PAC_RESERVED_SIZE + name_field + payload


def _build_flat_block(name: str, image: Image.Image) -> bytes:
    compressed = zlib.compress(_rgba_image_to_bgra_bytes(image))
    return _build_pac_block(name, _PAC_FLAG_FLAT, compressed)


def _build_tiled_block(name: str, image: Image.Image | None, width: int, height: int) -> bytes:
    if image is None:
        return _build_pac_block(name, _PAC_FLAG_TILED, _TILE_COUNT_STRUCT.pack(0))

    tiles_x = -(-width // _TILE_DIM)
    tiles_y = -(-height // _TILE_DIM)
    padded = Image.new("RGBA", (tiles_x * _TILE_DIM, tiles_y * _TILE_DIM), (0, 0, 0, 0))
    padded.paste(image, (0, 0))

    tile_count = tiles_x * tiles_y
    payload = [_TILE_HEADER_STRUCT.pack(tile_count, _TILE_DIM)]
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            tile = padded.crop((tx * _TILE_DIM, ty * _TILE_DIM, (tx + 1) * _TILE_DIM, (ty + 1) * _TILE_DIM))
            compressed = zlib.compress(_rgba_image_to_bgra_bytes(tile))
            payload.append(_TILE_RECORD_STRUCT.pack(tx, ty, 0, len(compressed)))
            payload.append(compressed)
            payload.append(b"\x00" * _pad4(len(compressed)))

    return _build_pac_block(name, _PAC_FLAG_TILED, b"".join(payload))


def _build_layer_xml(layer: Layer, index: int, bin_name: str, width: int, height: int) -> str:
    return (
        f'        <Layer name="{layer.name}" width="{width}" '
        f'height="{height}" mode="{layer.blend_mode}" alpha="{layer.opacity}" '
        f'id="{index}" parentId="-1" binType="2" bin="{bin_name}" type="32bpp" />'
    )


@register("firealpaca", extensions=[".mdp"])
class FireAlpacaConverter(BaseConverter):
    def read(self, path: str | Path) -> Document:
        logger.debug("reading FireAlpaca file: %s", path)
        with open(path, "rb") as f:
            data = f.read()

        xml_bytes, blocks_start, data_len = _read_file_header(data, path)
        root = _parse_metadata_xml(xml_bytes, path)

        width = int(root.get("width"))
        height = int(root.get("height"))
        logger.debug("canvas size: %dx%d", width, height)

        blocks = _read_blocks(data, blocks_start, blocks_start + data_len, path)

        doc = Document(width=width, height=height)
        doc.frames.append(Frame())
        doc.metadata["dpi"] = root.get("dpi")
        doc.metadata["checker_bg"] = root.get("checkerBG")

        layers_el = root.find("Layers")
        layer_els = layers_el.findall("Layer") if layers_el is not None else []
        logger.debug("found %d <Layer> element(s) in metadata", len(layer_els))
        for layer_el in layer_els:
            doc.layers.append(_parse_layer_element(layer_el, blocks, width, height))

        logger.debug("finished reading: %d layer(s) built", len(doc.layers))
        return doc

    def write(self, document: Document, path: str | Path) -> None:
        logger.debug(
            "writing FireAlpaca file: %s (%dx%d, %d layer(s))",
            path,
            document.width,
            document.height,
            len(document.layers),
        )
        layer_bins = [f"layer{i}img" for i in range(len(document.layers))]

        layer_xml = [
            _build_layer_xml(layer, index, bin_name, document.width, document.height)
            for index, (layer, bin_name) in enumerate(zip(document.layers, layer_bins))
        ]

        now = int(time.time())
        now_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
        active = max(len(document.layers) - 1, 0)
        xml = (
            '<?xml version="1.0" encoding="UTF-8" ?>\n'
            f'<Mdiapp width="{document.width}" height="{document.height}" dpi="350" '
            'checkerBG="true" bgColorR="255" bgColorG="255" bgColorB="255">\n'
            f'    <CreateTime time="{now}" timeString="{now_str}" />\n'
            f'    <UpdateTime time="{now}" timeString="{now_str}" rev="1" />\n'
            '    <Thumb width="256" height="256" bin="thumb" />\n'
            "    <Snaps />\n"
            "    <Guides />\n"
            '    <ICCProfiles enabled="false" cmykView="false" blackPoint="true" renderingIntent="perceptual" />\n'
            '    <Animation enabled="false" showNextPrev="true" showNextPrevLoop="false" baseLayer="false" fps="24" />\n'
            f'    <Layers active="{active}">\n' + "\n".join(layer_xml) + "\n    </Layers>\n"
            "</Mdiapp>"
        ).encode("utf-8")

        thumb_source = (
            document.flatten_frame(0)
            if document.frames
            else Image.new("RGBA", (document.width, document.height), (0, 0, 0, 0))
        )
        thumb = thumb_source.resize((_THUMB_DIM, _THUMB_DIM), Image.NEAREST)

        blocks = [_build_flat_block("thumb", thumb)]
        for layer, bin_name in zip(document.layers, layer_bins):
            cel = layer.cel_at(0)
            image = None
            if cel is not None:
                image = Image.new("RGBA", (document.width, document.height), (0, 0, 0, 0))
                piece = cel.image
                if cel.opacity < 255:
                    alpha = piece.getchannel("A").point(lambda a, o=cel.opacity: a * o // 255)
                    piece = piece.copy()
                    piece.putalpha(alpha)
                image.alpha_composite(piece, dest=(cel.x, cel.y))
            else:
                logger.debug("layer %r has no cel at frame 0, writing empty block", layer.name)
            blocks.append(_build_tiled_block(bin_name, image, document.width, document.height))
            logger.debug("built block %r: %d byte(s)", bin_name, len(blocks[-1]))

        data_section = b"".join(blocks)
        header = _FILE_MAGIC + _FILE_HEADER_STRUCT.pack(0, len(xml), len(data_section))
        logger.debug(
            "writing header: xml_len=%d data_len=%d total_file_size=%d",
            len(xml),
            len(data_section),
            len(header) + len(xml) + len(data_section),
        )

        with open(path, "wb") as f:
            f.write(header + xml + data_section)
