# FireAlpaca (.mdp) file format

Reverse-engineered from sample files saved by FireAlpaca itself (no official spec exists). Structure, confirmed by hexdumping and round-tripping pixel data against a known-good PNG:

## File layout

```
b"mdipack\x00"         8 bytes, magic
reserved   u32<        4 bytes, always 0 in samples
xml_len    u32<        4 bytes
data_len   u32<        4 bytes, total size of the block section below
xml        xml_len bytes, UTF-8 "<Mdiapp>...</Mdiapp>" document
blocks     data_len bytes, `_PacBlock`s back to back
```

## Metadata XML

The XML declares canvas width/height and a `<Layer>` per layer (in bottom-to-top order), each with an `alpha` (0-255 layer opacity), a `mode` (blend mode), and a `bin` attribute naming the block that holds its pixel data. A `<Thumb bin="thumb">` element names the preview-image block the same way.

## Blocks

Each block (`"PAC "` + a 64-byte name field) is either:

- a flat zlib stream of `width*height*4` raw BGRA bytes (used for the thumbnail, whose dimensions come from `<Thumb width= height=>`), or
- a tiled layer image: `tile_count` (u32) + `tile_size` (u32, the shared edge length of every tile, always 128 so far) + that many `(tile_x, tile_y, reserved, compressed_len)` records, each followed by a zlib stream of `tile_size*tile_size*4` raw BGRA bytes (padded to a 4-byte boundary). `tile_x`/`tile_y` are tile grid indices, not pixel offsets — multiply by `tile_size` to get the paste position. Confirmed against a 256x256 four-tile file (tiles arranged in the expected 2x2 grid at indices (0,0), (1,0), (0,1), (1,1)) as well as the original 64x64 single-tile samples. A tile count of 0 means an empty layer, and the `tile_size` field is omitted entirely in that case; the payload is just those 4 zero bytes, nothing else.

## Pixel format

Pixel bytes throughout are BGRA, not RGBA; every raw stream decompresses correctly by channel count but comes out with red and blue swapped until you flip them.

## Implementation

See [`firealpaca_converter.py`](../../src/imageformathopper/converters/firealpaca_converter.py).
