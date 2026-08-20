# Contributing

This is a small, focused tool. Contributions are welcome, particularly new format converters and corrections to the format-breakdown docs.

## Setup

```
git clone https://github.com/Quiet-Terminal-Interactive/ImageFormatHopper
cd ImageFormatHopper
pip install -e .
```

No additional bootstrapping is required.

## Ways to contribute

- **Add a format converter.** The architecture is designed for this; see below.
- **Fix a converter that produces incorrect output.** Attach or describe the input file that reproduces the problem.
- **Improve the format-breakdown docs** in `docs/format-breakdowns/`. These exist because at least one supported format (FireAlpaca) has no official specification, and the documentation there records what was determined through reverse engineering — what's confirmed, what's inferred, and what the converter deliberately doesn't handle. Corrections and additions to this record are valuable independent of any code change.
- **Scope creep is out of scope.** This tool reads and writes image files through a shared document model. A GUI, a plugin system, or network functionality would each be a different project. PRs adding them will be declined regardless of quality.

## Adding a new format

The intermediate representation exists specifically so that adding format N+1 does not require touching formats 1 through N. If a change to support a new format also modifies an existing converter, that's a sign the new converter is doing something it shouldn't — reconsider before submitting.

1. Add `src/imageformathopper/converters/yourformat_converter.py`.
2. Subclass `BaseConverter` if the format supports layers or animation, or `SingleImageConverter` if it's a flat image format. `SingleImageConverter` only requires `load_image`/`save_image`; it handles flattening a `Document` to a single image for you.
3. Decorate the class with `@register("yourformat", extensions=[".yf"])`.
4. No further registration step is needed, `converters/__init__.py` imports every module in the package on load, which triggers the decorator.

`read()` parses a file and returns a `Document`. `write()` takes a `Document` and produces a file. Raise `ParseError` on malformed input and `WriteError` when a `Document` cannot be represented in the target format. If the target format cannot represent everything the source held (layers into a flat format, animation into a static one), that reduction is expected; what a converter should not do is silently misreport what it read or wrote.

If the format you're implementing has no official specification, document what you learned in `docs/format-breakdowns/yourformat.md`, following the structure of the existing files: byte layout, what's confirmed against known-good samples versus assumed, and what the writer intentionally omits. Every other contributor, including future you, will appreciate present you writing this down.

## Style conventions

There is no linter configured yet, so these conventions are enforced by
review rather than tooling. Match the existing code:

- **Type hints on all public functions, methods, and dataclass fields.** Every module starts with `from __future__ import annotations` so hints can stay lazy.
- **`dataclass` for anything that is primarily data** — see `Document`, `Layer`, `Cel`, and `Frame` in `core/document.py`.
- **Raise from `core/exceptions.py`, not bare built-in exceptions.** The CLI catches `ImageFormatHopperError` at its boundary; an uncaught `ValueError` from inside a converter escapes that handling.
- **A converter's public surface is `read`/`write`** (or `load_image`/ `save_image` for `SingleImageConverter`). Format-specific parsing helpers should be private (leading underscore) and stay local to that converter's module.
- **No dependencies beyond Pillow without a specific justification.** Keeping the dependency surface minimal is a deliberate constraint, not an oversight.
- **Comments explain the format, not the code.** Most functions have no docstring, and that's intentional, names and types carry that information. Where a comment is warranted, it's typically explaining a field's byte offset, encoding, or a divergence from what the format appears to specify; see `aseprite_converter.py` for the expected style.

## Testing

There is no automated test suite yet. Fixture files in `test/` (`test.aseprite`, `test.mdp`, `test.png`) are provided for manual round-trip verification during development:

```
imageformathopper test/test.aseprite /tmp/out.png
imageformathopper test/test.mdp /tmp/out.png
```

Before submitting a change to a converter, round-trip a real file through it and inspect the output directly, the absence of an exception does not establish correctness, it just means nothing crashed loud enough for you to notice. Contributions that add automated tests (e.g., a `pytest` suite) for new or existing converters are welcome.

## Pull requests

- Keep each PR scoped to a single format, fix, or documentation change.
- Explain the reasoning behind format-parsing changes, particularly where the change diverges from documented behavior — note what evidence (sample files, hex dumps) supports the change.
- For substantial changes or new formats, open an issue first to confirm scope before implementing.
