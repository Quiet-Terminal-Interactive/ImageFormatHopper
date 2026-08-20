# ImageFormatHopper

A tool for converting between image and sprite file formats — PNG, JPEG, GIF, WebP, Aseprite, and FireAlpaca — through a single shared intermediate representation, rather than through a tangle of direct format-to-format conversion routines. Removes the effort of exporting sprites by hand every time an art tool changed its mind about what a `.mdp` file is.

```
imageformathopper sprite.aseprite sprite.gif
sprite.aseprite -> sprite.gif
```

## Why an intermediate representation

A converter that translates directly between every pair of supported formats needs on the order of N² conversion routines, and every one of them has to independently encode the same knowledge about what a layered, animated image actually is. That knowledge drifts apart across routines over time, and adding a new format means writing conversions to and from every existing one.

ImageFormatHopper instead defines a single in-memory model (`Document`) of what a layered, animated image is: a set of layers, each with per-frame cels (pixel data, position, opacity), plus frame timing and an optional palette. Every format implements exactly two operations against that model: read a file into a `Document`, and write a `Document` to a file. Converting format A to format B is `B.write(A.read(path), out_path)`. Adding format N+1 means implementing those two operations once, against the model, not against the N formats already supported.

This is the same shape of problem DataFixerUpper solves for schema migration — decouple every format from every other format by routing all conversions through one shared, versionless model in the middle. We just apply it to sprites instead of a decade of Minecraft chunk formats.

## Supported formats

| Format     | Extensions          | Layers | Animation |
| ---------- | ------------------- | :----: | :-------: |
| PNG        | `.png`              |        |           |
| JPEG       | `.jpg`, `.jpeg`     |        |           |
| GIF        | `.gif`              |        |     ✓     |
| WebP       | `.webp`             |        |     ✓     |
| Aseprite   | `.aseprite`, `.ase` |   ✓    |     ✓     |
| FireAlpaca | `.mdp`              |   ✓    |           |

Converting into a format that can't represent everything the source had is lossy and one-directional: layers are flattened, animations are reduced to their first frame. Nothing is fabricated to compensate, data is preserved wherever the target format allows it, and dropped explicitly where it doesn't.

Aseprite and FireAlpaca support is implemented against reverse-engineered and partial format documentation rather than an official SDK. Aseprite's format is at least documented upstream; FireAlpaca's was recovered by hexdumping sample files and a bit of guesswork. See [`docs/format-breakdowns/`](docs/format-breakdowns) for exactly what each converter reads, what it writes, and what's known versus assumed about the underlying format.

## Installation

Requires Python 3.10+.

```
git clone https://github.com/Quiet-Terminal-Interactive/ImageFormatHopper
cd ImageFormatHopper
pip install -e .
```

## Usage

```
imageformathopper <input> <output>
```

Format is determined from file extension on both ends; see the table above for what's registered.

```
imageformathopper sprite.aseprite sprite.png     # flatten to a static PNG
imageformathopper sprite.aseprite sprite.gif     # preserve the animation
imageformathopper art.mdp art.png                # FireAlpaca -> PNG
imageformathopper frames.gif frames.webp         # re-encode an animation
```

To bypass extension sniffing, specify the format explicitly:

```
imageformathopper --from aseprite --to png weird_filename.dat out.png
```

To list every registered format and its extensions:

```
imageformathopper --list-formats
```

The package is also runnable as `python -m imageformathopper ...`.

## Building a single executable

To distribute ImageFormatHopper as a standalone binary rather than a Python package, build one with [PyInstaller](https://pyinstaller.org/):

```
pip install -e .
pip install pyinstaller
pyinstaller --onefile --name imageformathopper -m imageformathopper
```

The resulting binary is written to `dist/imageformathopper` (`dist\imageformathopper.exe` on Windows). It bundles the Python interpreter, Pillow, and all other runtime dependencies, so it runs on a machine without Python installed.

PyInstaller does not cross-compile: build on the same OS and architecture you're targeting. To produce binaries for multiple platforms, run this build step in a CI matrix with one job per target OS.

## Project layout

```
src/imageformathopper/
├── cli.py              argument parsing, the imageformathopper entry point
├── core/
│   ├── document.py     Document/Layer/Cel/Frame, the shared intermediate model
│   ├── base.py         BaseConverter / SingleImageConverter
│   ├── registry.py     @register, format lookup by name or extension
│   └── exceptions.py   ImageFormatHopperError and subclasses
└── converters/         one module per format, self-registering on import
```

Adding a new format means adding one module to `converters/`; it requires no changes elsewhere. See [CONTRIBUTING.md](CONTRIBUTING.md) for the procedure and the project's style conventions.

## License

MIT — see [LICENSE](LICENSE).