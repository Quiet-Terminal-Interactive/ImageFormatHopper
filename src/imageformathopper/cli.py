from __future__ import annotations

import argparse
import logging
import sys

from . import converters
from .core import ImageFormatHopperError, get_converter, get_converter_for_path, list_formats

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="imageformathopper", description=__doc__)
    parser.add_argument("input", nargs="?", help="Path to the source file")
    parser.add_argument("output", nargs="?", help="Path to write the converted file to")
    parser.add_argument("--from", dest="from_format", help="Force the input format name (skip extension sniffing)")
    parser.add_argument("--to", dest="to_format", help="Force the output format name (skip extension sniffing)")
    parser.add_argument("--list-formats", action="store_true", help="List registered formats and exit")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging (per-converter diagnostics)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.verbose:
        logging.getLogger("PIL").setLevel(logging.INFO)

    if args.list_formats:
        for name, extensions in sorted(list_formats().items()):
            print(f"{name}: {', '.join(extensions)}")
        return 0

    if not args.input or not args.output:
        parser.error("input and output are required unless --list-formats is given")

    try:
        reader = get_converter(args.from_format) if args.from_format else get_converter_for_path(args.input)
        writer = get_converter(args.to_format) if args.to_format else get_converter_for_path(args.output)
        logger.debug("reader=%s writer=%s", reader.format_name, writer.format_name)

        document = reader.read(args.input)
        logger.debug(
            "read document: %dx%d, %d layer(s), %d frame(s)",
            document.width,
            document.height,
            len(document.layers),
            len(document.frames),
        )
        writer.write(document, args.output)
    except ImageFormatHopperError as exc:
        logger.debug("conversion failed", exc_info=True)
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{args.input} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
