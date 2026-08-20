"""Exception types shared by every converter."""


class ImageFormatHopperError(Exception):
    """Base class for all errors raised by this package."""


class UnsupportedFormatError(ImageFormatHopperError):
    """Raised when no converter is registered for a requested format/extension."""


class ParseError(ImageFormatHopperError):
    """Raised by a converter's read() when the source file can't be parsed."""


class WriteError(ImageFormatHopperError):
    """Raised by a converter's write() when a document can't be serialized."""
