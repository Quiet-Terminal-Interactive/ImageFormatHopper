import bisect
import logging
import zipfile
from pathlib import Path
from typing import Iterator, NamedTuple
from xml.etree import ElementTree
from xml.sax.saxutils import quoteattr as _xml_quoteattr

from PIL import Image

from ..core import BaseConverter, Cel, Document, Frame, Layer, ParseError, register

logger = logging.getLogger(__name__)

_MIMETYPE_CONTENT = b"application/x-krita"
_IMAGE_MIME = "application/x-kra"

_TILE_DIM = 64
_PIXEL_SIZE = 4  # 8-bit RGBA
_RAW_FLAG = 0
_COMPRESSED_FLAG = 1

_DEFAULT_FPS = 24

_NODETYPE_PAINT = "paintlayer"
_NODETYPE_GROUP = "grouplayer"


class _Tile(NamedTuple):
    x: int
    y: int
    data: bytes


class _RasterKeyframe(NamedTuple):
    time: int
    cel: Cel | None


def _build_tile_header(tile_count: int) -> bytes:
    return (
        f"VERSION 2\nTILEWIDTH {_TILE_DIM}\nTILEHEIGHT {_TILE_DIM}\n"
        f"PIXELSIZE {_PIXEL_SIZE}\nDATA {tile_count}\n"
    ).encode("ascii")


def _build_tile_store(image: Image.Image | None, origin_x: int, origin_y: int) -> bytes:
    if image is None or image.width == 0 or image.height == 0:
        return _build_tile_header(0)

    tile_left = (origin_x // _TILE_DIM) * _TILE_DIM
    tile_top = (origin_y // _TILE_DIM) * _TILE_DIM
    tile_right = -(-(origin_x + image.width) // _TILE_DIM) * _TILE_DIM
    tile_bottom = -(-(origin_y + image.height) // _TILE_DIM) * _TILE_DIM

    padded = Image.new("RGBA", (tile_right - tile_left, tile_bottom - tile_top), (0, 0, 0, 0))
    padded.paste(image, (origin_x - tile_left, origin_y - tile_top))

    tiles_x = (tile_right - tile_left) // _TILE_DIM
    tiles_y = (tile_bottom - tile_top) // _TILE_DIM

    records = []
    tile_count = 0
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            box = (tx * _TILE_DIM, ty * _TILE_DIM, (tx + 1) * _TILE_DIM, (ty + 1) * _TILE_DIM)
            tile_img = padded.crop(box)
            if tile_img.getchannel("A").getbbox() is None:
                continue

            r, g, b, a = tile_img.split()
            bgra_bytes = Image.merge("RGBA", (b, g, r, a)).tobytes()
            payload = bytes([_RAW_FLAG]) + bgra_bytes
            x = tile_left + tx * _TILE_DIM
            y = tile_top + ty * _TILE_DIM
            records.append(f"{x},{y},LZF,{len(payload)}\n".encode("ascii") + payload)
            tile_count += 1

    return _build_tile_header(tile_count) + b"".join(records)


def _lzf_decompress(data: bytes, expected_size: int, path: str | Path) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        ctrl = data[i]
        i += 1
        if ctrl < 32:
            count = ctrl + 1
            out.extend(data[i : i + count])
            i += count
        else:
            length = ctrl >> 5
            ofs = (ctrl & 0x1F) << 8
            if length == 7:
                length += data[i]
                i += 1
            ofs += data[i]
            i += 1
            ref = len(out) - ofs - 1
            for _ in range(length + 2):
                out.append(out[ref])
                ref += 1

    if len(out) != expected_size:
        raise ParseError(
            f"{path}: LZF tile decompressed to {len(out)} bytes, expected {expected_size}"
        )
    return bytes(out)


def _delinearize(data: bytes, pixel_size: int) -> bytes:
    """Undo Krita's byte-plane reordering (RRRGGGBBBAAA -> RGBARGBARGBA)."""
    plane_len = len(data) // pixel_size
    out = bytearray(len(data))
    for channel in range(pixel_size):
        out[channel::pixel_size] = data[channel * plane_len : (channel + 1) * plane_len]
    return bytes(out)


def _read_header_lines(data: bytes, start: int) -> tuple[dict[str, str], int]:
    fields: dict[str, str] = {}
    cursor = start
    while True:
        newline = data.index(b"\n", cursor)
        key, _, value = data[cursor:newline].decode("ascii").partition(" ")
        fields[key] = value
        cursor = newline + 1
        if key == "DATA":
            return fields, cursor


def _parse_tile_store(data: bytes, path: str | Path) -> list[_Tile]:
    fields, cursor = _read_header_lines(data, 0)

    tile_width = int(fields.get("TILEWIDTH", "0"))
    tile_height = int(fields.get("TILEHEIGHT", "0"))
    if tile_width != _TILE_DIM or tile_height != _TILE_DIM:
        raise ParseError(f"{path}: unsupported tile size {tile_width}x{tile_height}, expected {_TILE_DIM}x{_TILE_DIM}")

    pixel_size = int(fields.get("PIXELSIZE", "0"))
    if pixel_size != _PIXEL_SIZE:
        raise ParseError(
            f"{path}: unsupported colorspace (pixel size {pixel_size} bytes); "
            "only 8-bit RGBA (4 bytes/pixel) is supported"
        )

    tile_count = int(fields.get("DATA", "0"))
    tile_data_size = pixel_size * tile_width * tile_height

    tiles: list[_Tile] = []
    for _ in range(tile_count):
        newline = data.index(b"\n", cursor)
        x_s, y_s, _compression, size_s = data[cursor:newline].decode("ascii").split(",")
        cursor = newline + 1

        x, y, payload_size = int(x_s), int(y_s), int(size_s)
        payload = data[cursor : cursor + payload_size]
        cursor += payload_size

        flag = payload[0]
        body = payload[1:]
        if flag == _RAW_FLAG:
            if len(body) != tile_data_size:
                raise ParseError(
                    f"{path}: tile at ({x},{y}) has {len(body)} byte(s) of raw pixel data, expected {tile_data_size}"
                )
            pixel_bytes = body
        elif flag == _COMPRESSED_FLAG:
            pixel_bytes = _delinearize(_lzf_decompress(body, tile_data_size, path), pixel_size)
        else:
            raise ParseError(f"{path}: tile at ({x},{y}) has unknown payload flag {flag}")

        tiles.append(_Tile(x, y, pixel_bytes))

    logger.debug("parsed tile store: %d tile(s), pixel_size=%d", tile_count, pixel_size)
    return tiles


def _tiles_to_cel(tiles: list[_Tile]) -> Cel | None:
    if not tiles:
        return None

    min_x = min(t.x for t in tiles)
    min_y = min(t.y for t in tiles)
    max_x = max(t.x + _TILE_DIM for t in tiles)
    max_y = max(t.y + _TILE_DIM for t in tiles)

    canvas = Image.new("RGBA", (max_x - min_x, max_y - min_y), (0, 0, 0, 0))
    for tile in tiles:
        bgra = Image.frombytes("RGBA", (_TILE_DIM, _TILE_DIM), tile.data)
        b, g, r, a = bgra.split()
        canvas.paste(Image.merge("RGBA", (r, g, b, a)), (tile.x - min_x, tile.y - min_y))

    return Cel(image=canvas, x=min_x, y=min_y)


def _find_layer_entry(namelist: list[str], filename: str) -> str | None:
    suffix = f"/layers/{filename}"
    for name in namelist:
        if name.endswith(suffix):
            return name
    return None


def _iter_paint_layers(
    layers_el: ElementTree.Element, opacity_scale: int, visible: bool
) -> Iterator[tuple[ElementTree.Element, int, bool]]:
    for layer_el in layers_el.findall("layer"):
        nodetype = layer_el.get("nodetype", "")
        layer_opacity = int(layer_el.get("opacity", "255"))
        layer_visible = layer_el.get("visible", "1") == "1"
        combined_opacity = (opacity_scale * layer_opacity) // 255
        combined_visible = visible and layer_visible

        if nodetype == _NODETYPE_GROUP:
            nested = layer_el.find("layers")
            if nested is not None:
                yield from _iter_paint_layers(nested, combined_opacity, combined_visible)
            continue

        if nodetype != _NODETYPE_PAINT:
            logger.debug("skipping unsupported nodetype %r (name=%r)", nodetype, layer_el.get("name"))
            continue

        yield layer_el, combined_opacity, combined_visible


def _read_raster_keyframes(
    zf: zipfile.ZipFile, keyframes_filename: str, path: str | Path
) -> list[_RasterKeyframe]:
    entry = _find_layer_entry(zf.namelist(), keyframes_filename)
    if entry is None:
        raise ParseError(f"{path}: keyframes file {keyframes_filename!r} referenced but not found in archive")

    root = ElementTree.fromstring(zf.read(entry))
    channel = None
    for candidate in root.findall("channel"):
        if candidate.get("name") == "content":
            channel = candidate
            break
    if channel is None:
        return []

    keyframes = []
    for kf_el in channel.findall("keyframe"):
        time = int(kf_el.get("time", "0"))
        frame_filename = kf_el.get("frame")
        if not frame_filename:
            continue
        frame_entry = _find_layer_entry(zf.namelist(), frame_filename)
        if frame_entry is None:
            logger.debug("keyframe at time=%d references missing frame file %r", time, frame_filename)
            continue
        cel = _tiles_to_cel(_parse_tile_store(zf.read(frame_entry), path))
        keyframes.append(_RasterKeyframe(time=time, cel=cel))

    keyframes.sort(key=lambda kf: kf.time)
    return keyframes


def _parse_fps(root: ElementTree.Element) -> int:
    animation_el = root.find("animation")
    if animation_el is not None:
        try:
            return int(animation_el.get("framerate", _DEFAULT_FPS))
        except ValueError:
            pass
    return _DEFAULT_FPS


@register("krita", extensions=[".kra"])
class KritaConverter(BaseConverter):
    def read(self, path: str | Path) -> Document:
        logger.debug("reading Krita file: %s", path)
        try:
            zf_context = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ParseError(f"{path}: not a valid zip archive, not a Krita file ({exc})") from None

        with zf_context as zf:
            try:
                xml_bytes = zf.read("maindoc.xml")
            except KeyError:
                raise ParseError(f"{path}: missing maindoc.xml, not a Krita file") from None

            try:
                root = ElementTree.fromstring(xml_bytes)
            except ElementTree.ParseError as exc:
                raise ParseError(f"{path}: bad maindoc.xml: {exc}") from None
            if root.get("mime") != _IMAGE_MIME:
                raise ParseError(f"{path}: unexpected <IMAGE mime> {root.get('mime')!r}, not a Krita file")

            width = int(root.get("width"))
            height = int(root.get("height"))
            fps = _parse_fps(root)
            logger.debug("canvas size: %dx%d, fps=%d", width, height, fps)

            doc = Document(width=width, height=height)

            layers_el = root.find("layers")
            paint_layers = list(_iter_paint_layers(layers_el, 255, True)) if layers_el is not None else []
            logger.debug("found %d paint layer(s) (after group flattening)", len(paint_layers))

            built_layers: list[Layer] = []
            layer_statics: list[Cel | None] = []
            layer_keyframes: list[list[_RasterKeyframe] | None] = []

            for layer_el, opacity, visible in paint_layers:
                name = layer_el.get("name", "")
                blend_mode = layer_el.get("compositeop", "normal")
                layer = Layer(name=name, visible=visible, opacity=opacity, blend_mode=blend_mode)

                keyframes_filename = layer_el.get("keyframes")
                keyframes = (
                    _read_raster_keyframes(zf, keyframes_filename, path) if keyframes_filename else None
                )

                filename = layer_el.get("filename")
                static_cel: Cel | None = None
                if filename and not keyframes:
                    entry = _find_layer_entry(zf.namelist(), filename)
                    if entry is None:
                        raise ParseError(f"{path}: layer {name!r} references missing pixel data file {filename!r}")
                    static_cel = _tiles_to_cel(_parse_tile_store(zf.read(entry), path))

                built_layers.append(layer)
                layer_statics.append(static_cel)
                layer_keyframes.append(keyframes if keyframes else None)

            max_time = 0
            for keyframes in layer_keyframes:
                if keyframes:
                    max_time = max(max_time, keyframes[-1].time)
            frame_count = max_time + 1 if any(layer_keyframes) else 1
            duration_ms = round(1000 / fps) if fps > 0 else round(1000 / _DEFAULT_FPS)
            doc.frames = [Frame(duration_ms=duration_ms) for _ in range(frame_count)]

            for layer, static_cel, keyframes in zip(built_layers, layer_statics, layer_keyframes):
                if keyframes:
                    times = [kf.time for kf in keyframes]
                    for frame_index in range(frame_count):
                        idx = bisect.bisect_right(times, frame_index) - 1
                        if idx < 0:
                            continue
                        cel = keyframes[idx].cel
                        if cel is not None:
                            layer.cels[frame_index] = cel
                elif static_cel is not None:
                    for frame_index in range(frame_count):
                        layer.cels[frame_index] = static_cel
                doc.layers.append(layer)

            logger.debug("finished reading: %d layer(s), %d frame(s)", len(doc.layers), len(doc.frames))
            return doc

    def write(self, document: Document, path: str | Path) -> None:
        logger.debug(
            "writing Krita file: %s (%dx%d, %d layer(s), %d frame(s))",
            path,
            document.width,
            document.height,
            len(document.layers),
            len(document.frames),
        )
        frame_count = len(document.frames) if document.frames else 1
        image_name = Path(path).stem or "Image"
        first_duration_ms = document.frames[0].duration_ms if document.frames else 0
        fps = round(1000 / first_duration_ms) if first_duration_ms > 0 else _DEFAULT_FPS
        fps = max(fps, 1)

        filenames = [f"layer{i}" for i in range(len(document.layers))]
        layer_xml_parts = []
        write_jobs: list[tuple[str, bytes]] = []

        for layer, filename in zip(document.layers, filenames):
            runs: list[tuple[int, Cel | None]] = []
            sentinel = object()
            prev = sentinel
            for i in range(frame_count):
                cel = layer.cel_at(i)
                if cel is not prev:
                    runs.append((i, cel))
                    prev = cel

            animated = len(runs) > 1
            base_cel = runs[0][1]

            base_data = _build_tile_store(
                base_cel.image if base_cel else None,
                base_cel.x if base_cel else 0,
                base_cel.y if base_cel else 0,
            )
            write_jobs.append((f"{image_name}/layers/{filename}", base_data))

            attrs = {
                "name": layer.name,
                "filename": filename,
                "nodetype": _NODETYPE_PAINT,
                "x": "0",
                "y": "0",
                "opacity": str(layer.opacity),
                "visible": "1" if layer.visible else "0",
                "compositeop": layer.blend_mode or "normal",
            }

            if animated:
                keyframes_filename = f"{filename}.keyframes.xml"
                attrs["keyframes"] = keyframes_filename

                keyframe_elems = []
                for time, cel in runs:
                    frame_filename = f"{filename}.f{time}"
                    frame_data = _build_tile_store(
                        cel.image if cel else None, cel.x if cel else 0, cel.y if cel else 0
                    )
                    write_jobs.append((f"{image_name}/layers/{frame_filename}", frame_data))
                    keyframe_elems.append(
                        f"      <keyframe time={_xml_quoteattr(str(time))} frame={_xml_quoteattr(frame_filename)}>"
                        '<offset x="0" y="0"/></keyframe>'
                    )

                keyframes_xml = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    "<keyframes>\n"
                    '  <channel name="content">\n' + "\n".join(keyframe_elems) + "\n"
                    "  </channel>\n"
                    "</keyframes>"
                ).encode("utf-8")
                write_jobs.append((f"{image_name}/layers/{keyframes_filename}", keyframes_xml))

            attr_str = " ".join(f"{key}={_xml_quoteattr(str(value))}" for key, value in attrs.items())
            layer_xml_parts.append(f"    <layer {attr_str} />")

            logger.debug(
                "layer %r: filename=%s animated=%s run_count=%d",
                layer.name,
                filename,
                animated,
                len(runs),
            )

        animation_xml = ""
        if document.is_animated():
            animation_xml = f'\n  <animation framerate="{fps}" from="0" to="{frame_count - 1}" currenttime="0" />'

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<IMAGE mime={_xml_quoteattr(_IMAGE_MIME)} name={_xml_quoteattr(image_name)} "
            f'width="{document.width}" height="{document.height}" colorspacename="RGBA" '
            'x-res="100" y-res="100" description="">\n'
            "  <layers>\n" + "\n".join(layer_xml_parts) + "\n  </layers>" + animation_xml + "\n"
            "</IMAGE>"
        ).encode("utf-8")

        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(zipfile.ZipInfo("mimetype"), _MIMETYPE_CONTENT, zipfile.ZIP_STORED)
            zf.writestr("maindoc.xml", xml)
            for entry_name, data in write_jobs:
                zf.writestr(entry_name, data)

        logger.debug("wrote %d layer data entr(y/ies)", len(write_jobs))
