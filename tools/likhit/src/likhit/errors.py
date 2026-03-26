"""Project-specific exceptions."""


class LikhitError(Exception):
    """Base exception for extraction failures."""


class ValidationError(LikhitError):
    """Raised when user input or extracted content is invalid."""


class ExtractionError(LikhitError):
    """Raised when document extraction fails."""
