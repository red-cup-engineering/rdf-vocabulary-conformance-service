"""The pure core: `check_vocabulary_conformance(config, files)`."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from . import shape_tests
from .checks import REGISTRY, _capped
from .config import ConfigError, is_safe_path, validate_config
from .context import Context, FileProblem

TOOL = "rdf-vocabulary-conformance-service"


def _version() -> str:
    from . import __version__

    return __version__


def _admit_files(files: Any) -> None:
    if not isinstance(files, Mapping):
        raise TypeError("files must be a mapping of path to bytes")
    for path, content in files.items():
        if not isinstance(path, str) or not is_safe_path(path):
            raise TypeError(f"file path {path!r} must be a relative forward-slash path")
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError(f"file {path!r} must be bytes")


def config_digest(config: dict) -> str:
    canonical = json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_vocabulary_conformance(config: dict, files: Mapping[str, bytes]) -> dict:
    """Judge a vocabulary and its records. Pure: no filesystem, network, clock or randomness.

    Returns `{ok, tool, version, inputs, summary, checks, shapeTests}`; the same
    config and files give byte-identical JSON. Raises `ConfigError` when the
    configuration is not admissible and `TypeError` when `files` is malformed.
    """
    validate_config(config)
    _admit_files(files)
    ctx = Context(config, files)
    checks = []
    for check_id, wanted, function in REGISTRY:
        if not wanted(config):
            continue
        try:
            failures, summary = function(ctx)
        except FileProblem as error:
            failures, summary = [str(error)], "could not run"
        except Exception as error:  # a check must never take the report down
            failures, summary = [f"{type(error).__name__}: {str(error)[:300]}"], "could not run"
        failures = _capped(failures)
        checks.append({"id": check_id, "ok": not failures, "detail": summary if not failures else "; ".join(failures[:3]) + ("" if len(failures) <= 3 else f" (+{len(failures) - 3} more)"), "failures": failures})
    tests = shape_tests.run(ctx)
    failed_checks = sum(1 for c in checks if not c["ok"])
    failed_tests = sum(1 for t in tests if not t["ok"])
    return {
        "ok": failed_checks == 0 and failed_tests == 0,
        "tool": TOOL,
        "version": _version(),
        "inputs": {"config": config_digest(config), "files": ctx.digests()},
        "summary": {
            "checks": len(checks),
            "checksFailed": failed_checks,
            "shapeTests": len(tests),
            "shapeTestsFailed": failed_tests,
        },
        "checks": checks,
        "shapeTests": tests,
    }


__all__ = ["check_vocabulary_conformance", "ConfigError", "config_digest", "dumps"]


def dumps(value: Any, *, indent: int | None = None) -> str:
    """The one serialization every face uses: sorted keys, UTF-8 text, no trailing newline."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=indent)
