# Krita (.kra) file format

Krita is open source, so unlike others (still looking at you, FireAlpaca) this is grounded in Krita's own source rather than a hexdump-and-guess process.

## Container

A `.kra` file is a ZIP archive:

```
mimetype             uncompressed entry, literal bytes "application/x-krita" (ODF/EPUB-style)
maindoc.xml          UTF-8 XML, the document tree (see below)
documentinfo.xml     optional, not read/written by this converter
preview.png          optional thumbnail, never read back by Krita's own loader
mergedimage.png      optional flattened preview, never read back either
<name>/layers/...    per-layer pixel data and sidecar files, see below
```

Krita's own loader doesn't check the `mimetype` entry's content at all when opening a `.kra` file, and never reads `preview.png`/`mergedimage.png` back; this converter writes none of the three. What Krita does require: a `maindoc.xml` entry, and every `<layer filename="...">` it references resolving to a real `<name>/layers/<filename>` entry.

The `<name>/` directory prefix isn't derived from anything in `maindoc.xml` itself; this converter uses the output filename's stem and writes/reads that consistently. Reading doesn't depend on getting the prefix right either way: instead of reconstructing it, the converter searches the zip's name list for an entry ending in `/layers/<filename>`.

## maindoc.xml

```xml
<IMAGE mime="application/x-kra" name="..." width="w" height="h" colorspacename="RGBA" ...>
  <layers>
    <layer name="..." filename="layer0" nodetype="paintlayer"
           opacity="0-255" visible="0|1" compositeop="normal|multiply|..."
           keyframes="layer0.keyframes.xml" />   <!-- only if animated -->
    <layer nodetype="grouplayer" ...>
      <layers> ... nested children ... </layers>
    </layer>
  </layers>
  <animation framerate="24" from="0" to="11" currenttime="0" />  <!-- only if animated -->
</IMAGE>
```

- Sibling order in a `<layers>` block is bottom-of-stack first, top-of-stack last.
- `nodetype` values Krita defines include `paintlayer`, `grouplayer`, `adjustmentlayer`, `shapelayer`, `generatorlayer`, `clonelayer`, `filelayer`, and several mask types. Only `paintlayer` converts to a layer; everything else is flattened away or dropped. `grouplayer` is recursed into — its opacity and visibility are folded multiplicatively/logically into its descendants — but produces no layer of its own. Every other node type is skipped with a debug log line.
- `<layer>` also carries generic `x`/`y` integer-offset attributes. This converter always writes `x="0" y="0"` and on read ignores them in favor of the absolute coordinates already embedded in each tile record — a paint layer's pixel data is self-positioned via its tiled data manager's coordinate space, so `x`/`y` matters for node types this converter doesn't support anyway (shape layers, transform masks, ...) rather than paint layers. A real-world `.kra` file with a paint layer carrying a nonzero `x`/`y` not already reflected in its tile coordinates would be read at the wrong position.
- `compositeop` is passed straight through as the layer's blend mode on read and back out unchanged on write.
- Only `framerate` is actually read; `from`/`to`/`currenttime` are written but ignored on read — the frame range instead comes from the union of every layer's own keyframe `time` values. Real Krita files carry frame range information in a separate `<name>/animation/index.xml` file that this converter doesn't read or write. `framerate` defaults to 24 if absent or unparsable.

## Per-layer pixel data (tiled, not a flat blob)

Each paint layer's content is a separate zip entry named by its `filename` attribute, in Krita's `KisTiledDataManager` serialization: a 64x64-pixel tile grid over an unbounded, zero-centered coordinate space (tile origins are multiples of 64 and can be negative). Only tiles that aren't fully default/empty are stored.

```
"VERSION 2\n"             ASCII, literal
"TILEWIDTH 64\n"          ASCII, literal
"TILEHEIGHT 64\n"         ASCII, literal
"PIXELSIZE <n>\n"         ASCII, bytes/pixel (4 for the only colorspace this converter supports)
"DATA <count>\n"          ASCII, number of tile records that follow
```

followed by `count` tile records:

```
header    ASCII text   "<x>,<y>,LZF,<payloadSize>\n"   x,y = pixel-space tile origin, signed, multiples of 64
payload   payloadSize bytes:
  flag      u8           0 = raw, 1 = LZF-compressed
  body      payloadSize-1 bytes:
    flag==0: pixelSize*64*64 bytes, interleaved (BGRA for the RGBA colorspace), Krita's native order
    flag==1: an LZF stream inflating to the same size, but in PLANE order (all byte-0 of
             every pixel, then all byte-1, etc.) — must be de-planarized after decompression
```

The compression-name field in the header is always the literal string `"LZF"`, even when a given tile's payload is raw (the flag distinguishes that, not the header text) — this trips up anyone going by the older 2010 community-wiki page, which describes a `"NONE"` token that current Krita doesn't emit.

A `<filename>.defaultpixel` sidecar (the color of the infinite area outside any stored tile) can also exist; Krita's own loader treats it as optional and falls back to fully transparent when absent, which is the only behavior this converter implements — it never reads or writes `.defaultpixel`.

### Pixel format

The only colorspace this converter handles is 8-bit `"RGBA"`, which (despite the name) is stored BGRA. Alpha is not premultiplied. A file using any other colorspace (16-bit, floating point, CMYK, indexed, ...) is rejected with a `ParseError` naming the pixel size Krita reported, rather than silently misinterpreting the bytes.

### LZF compression

Krita's `"LZF"` tag is its own embedded codec (`lzff_compress`/`lzff_decompress`, FastLZ-lineage, not a linked copy of Marc Lehmann's `liblzf`), but the control-byte format is the standard LZF/FastLZ "level 1" stream shape. This converter implements the decompressor only (needed to read real Krita files, which compress tiles by default) — the writer always emits `flag=0` (raw) tiles. That's spec-compliant; Krita reads raw and LZF-compressed tiles identically, per-tile, so the only cost is larger output files, not a compatibility gap.

## Animation

An animated layer gets a `keyframes="<filename>.keyframes.xml"` attribute (keyed off the numeric `filename`, not the display name) pointing at a sidecar in the same `layers/` directory:

```xml
<keyframes>
  <channel name="content">
    <keyframe time="0" frame="layer0.f0"><offset x="0" y="0"/></keyframe>
    <keyframe time="5" frame="layer0.f5"><offset x="0" y="0"/></keyframe>
  </channel>
</keyframes>
```

- `time` is a frame-number integer, not milliseconds.
- Each `frame` attribute names another tile-store file (identical format to the main per-layer file above) holding that keyframe's content; a keyframe holds until the next one, there's no interpolation for raster content in Krita (interpolation only applies to scalar channels like opacity/transform, which this converter doesn't read or write at all).
- The `<offset>` child element exists in real Krita files (its raster keyframe saver writes a `QPoint` under that tag), but this converter's reader ignores it — the authoritative position for a frame's content comes from the tile coordinates already embedded in that frame's own tile-store file — and the writer emits `x="0" y="0"` as a placeholder.
- Krita keys each layer's animation independently, but this project uses one global frame list shared by every layer. On read, the frame range becomes `0..max(every animated layer's highest keyframe time)`, and every layer (animated or not) gets a frame populated at every index in that range — static layers by repeating the same frame, animated ones by picking whichever keyframe's content is "held" at that index. On write, the inverse: each layer's frame-by-frame sequence is collapsed into runs of identical frames, and only layers with more than one run get a `keyframes` attribute and sidecar at all — a layer that happens to be static across an animated document's whole frame range is written as an ordinary non-animated layer.
- Frame rate: this converter derives an fps for `<animation framerate="...">` from the first frame's duration, defaulting to 24 if the document has no frames. Per-frame variable fps within a single animation isn't supported.

## What's not implemented

Groups (beyond flattening through them), masks of any kind, adjustment/filter/generator/clone/file/shape layers, vector content, layer styles, scalar animation channels (opacity/transform keyframes), onion skinning, ICC profiles, storyboards, audio tracks, and painting assistants. All of these are either silently dropped (logged at debug level) or, for anything that would otherwise be silently misinterpreted (a non-RGBA-8 colorspace), rejected with a `ParseError`.

## Implementation

See [`krita_converter.py`](../../src/imageformathopper/converters/krita_converter.py).
