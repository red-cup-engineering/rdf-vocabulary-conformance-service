"""Deterministic conformance checks for a research vocabulary and its data records."""

__version__ = "0.2.0.dev4"

from .config import ConfigError, required_patterns, validate_config  # noqa: E402
from .core import check_vocabulary_conformance  # noqa: E402
from .dataset_checks import matches_named_graph, prepare_local_select  # noqa: E402

__all__ = ["__version__", "ConfigError", "check_vocabulary_conformance", "matches_named_graph", "prepare_local_select", "required_patterns", "validate_config"]
