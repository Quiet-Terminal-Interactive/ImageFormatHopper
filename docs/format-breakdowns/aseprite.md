# Aseprite (.aseprite / .ase) file format

Aseprite's file format is officially documented (looking at you, FireAlpaca), so this is a summary of the subset the converter actually reads and writes, not the full spec.

## File layout

```
file header    128 bytes, fixed size
frame 0        variable size, declared by its own header
frame 1        ...
...
```

### File header

```
file_size    u32<    total file size
magic        u16<    0xA5E0
frames       u16<    frame count
width        u16<    canvas width, px
height       u16<    canvas height, px
color_depth  u16<    8 (indexed), 16 (grayscale+alpha), or 32 (RGBA)
...          padded out to 128 bytes with fields the converter ignores
```

### Frame header

```
frame_size   u32<    size of this frame, header included
magic        u16<    0xF1FA
old_count    u16<    chunk count (legacy field, used when new_count = 0)
duration_ms  u16<    frame duration
reserved     2 bytes
new_count    u32<    chunk count (used when non-zero, supersedes old_count)
```

Each frame is then `chunk_count` chunks back to back, each starting with a `(chunk_size u32<, chunk_type u16<)` header. Layer chunks only appear in frame 0 (Aseprite defines layers once, for the whole document); cel and palette chunks can appear in any frame.

## Chunks

### Layer chunk (`0x2004`)

```
flags         u16<   bit 0 = visible
layer_type    u16<   ignored
child_level   u16<   ignored
default_w     u16<   ignored
default_h     u16<   ignored
blend_mode    u16<   ignored on read; always written as 0
opacity       u8     layer opacity, 0-255
reserved      3 bytes
name_len      u16<
name          name_len bytes, UTF-8
```

### Cel chunk (`0x2005`)

```
layer_index   u16<   index into the layers collected from frame 0
x, y          i16<   cel position
opacity       u8
cel_type      u16<   0 = raw image, 1 = linked, 2 = zlib-compressed image
z_index       i16<   ignored
reserved      5 bytes
```

For an image cel (`raw` or `compressed`), a `(width u16<, height u16<)` pair follows, then the pixel data — either uncompressed or a zlib stream — running to the end of the chunk. For a `linked` cel, a single `u16<` follows; the index of an earlier frame whose cel (same layer) should be reused as-is.

### Palette chunk (`0x2019`)

```
new_size       u32<   palette size after this chunk is applied
first_index    u32<   first index touched by this chunk
last_index     u32<   last index touched by this chunk
reserved       8 bytes
```

followed by `last_index - first_index + 1` entries:

```
flags   u16<   bit 0 = entry has a name string
r,g,b,a u8 each
[name_len u16< + name_len bytes, only if flags bit 0 is set]
```

Palette chunks are cumulative across frames (a later chunk can grow the palette or overwrite a range of entries); the converter carries the running palette forward frame to frame.

## Pixel format / color depth

- 32bpp: raw RGBA bytes, decoded directly.
- 16bpp: grayscale + alpha (`"LA"` in PIL terms), converted to RGBA.
- 8bpp: one palette index per pixel, expanded to RGBA via the most recent palette chunk. A `ParseError` is raised if an indexed cel appears before any palette chunk has been seen.

The writer always emits 32bpp RGBA compressed-image cels, it never writes `raw` (uncompressed) cels, `linked` cels, or a palette chunk, regardless of what the source document looked like.

## Implementation

See [`aseprite_converter.py`](../../src/imageformathopper/converters/aseprite_converter.py).
