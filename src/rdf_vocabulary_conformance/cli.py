"""`check-vocabulary-conformance --root <repo> [--config ontology/conformance.json]`.

Prints the JSON report on stdout and a short human summary on stderr.
Exit 0: every check and shape test passed. 1: something failed. 2: the
configuration could not be read or is not admissible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .collect import collect_files
from .config import ConfigError, required_patterns, validate_config
from .cells import cells_text, report_cells
from .core import check_vocabulary_conformance, dumps

DEFAULT_CONFIG = "ontology/conformance.json"


def _refusal(reason: str, message: str, errors: list[str] | None = None) -> dict:
    refusal: dict = {"reason": reason, "message": message}
    if errors:
        refusal["errors"] = errors
    return {"ok": False, "refusal": refusal}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check-vocabulary-conformance",
        description="Check a research vocabulary and its data records against a conformance.json configuration.",
    )
    parser.add_argument("--root", required=True, help="repository root; every path in the configuration is relative to it")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"configuration path relative to --root (default {DEFAULT_CONFIG})")
    parser.add_argument("--cells", metavar="PATH", help="also write the report as SignCell cells (canonical JSON, see cells.py) to PATH, relative to --root")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    root = Path(args.root)
    config_path = root / args.config
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        validate_config(config)
    except FileNotFoundError:
        print(dumps(_refusal("ConfigNotFound", f"{args.config}: no such file under {args.root}"), indent=2))
        print(f"bad configuration: {args.config} not found", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(dumps(_refusal("ConfigUnreadable", f"{args.config}: {error}"), indent=2))
        print(f"bad configuration: {args.config}: {error}", file=sys.stderr)
        return 2
    except ConfigError as error:
        print(dumps(_refusal("InvalidConfig", f"{args.config} is not admissible", error.errors), indent=2))
        for message in error.errors:
            print(f"bad configuration: {message}", file=sys.stderr)
        return 2

    files = collect_files(root, required_patterns(config))
    report = check_vocabulary_conformance(config, files)
    print(dumps(report, indent=2))
    if args.cells is not None:
        (root / args.cells).parent.mkdir(parents=True, exist_ok=True)
        (root / args.cells).write_text(cells_text(report_cells(report)), encoding="utf-8")
    for check in report["checks"]:
        if not check["ok"]:
            print(f"FAIL {check['id']}: {check['detail']}", file=sys.stderr)
    for test in report["shapeTests"]:
        if not test["ok"]:
            print(f"FAIL shape-test {test['id']}: expected {test['expect']}, got {test['actual']} ({test['detail']})", file=sys.stderr)
    summary = report["summary"]
    print(
        f"{'ok' if report['ok'] else 'FAILED'}: {summary['checks'] - summary['checksFailed']}/{summary['checks']} checks, "
        f"{summary['shapeTests'] - summary['shapeTestsFailed']}/{summary['shapeTests']} shape tests",
        file=sys.stderr,
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
