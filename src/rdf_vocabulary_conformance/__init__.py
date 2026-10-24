"""Deterministic conformance checks for a research vocabulary and its data records."""

__version__ = "0.1.0"

from .config import ConfigError, required_patterns, validate_config  # noqa: E402
from .core import check_vocabulary_conformance  # noqa: E402

__all__ = ["__version__", "ConfigError", "check_vocabulary_conformance", "required_patterns", "validate_config"]
