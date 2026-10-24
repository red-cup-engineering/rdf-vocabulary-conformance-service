"""`check-vocabulary-conformance-ndjson`: one JSON request per stdin line, one JSON result per stdout line.

Request:  {"config": {...}, "files": {"<path>": "<utf-8 text>" | {"base64": "<bytes>"}}}
Result:   the conformance report ({"ok", "checks", "shapeTests", ...}), or
          {"ok": false, "refusal": {"reason", "message", "errors"?}} when the
          request is not admitted. A refusal has a `refusal` member; a
          non-conformant report does not.

No filesystem is read: the caller supplies every file.
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
from typing import IO, Any, Sequence

from .config import ConfigError
from .core import check_vocabulary_conformance, dumps

MAX_LINE_BYTES = 16 * 1024 * 1024


def _refusal(reason: str, message: str, errors: list[str] | None = None) -> dict:
    refusal: dict = {"reason": reason, "message": message}
    if errors:
        refusal["errors"] = errors
    return {"ok": False, "refusal": refusal}


def _decode_files(files: Any) -> dict[str, bytes]:
    if not isinstance(files, dict):
        raise ValueError("files must be an object of path to text or {base64}")
    decoded: dict[str, bytes] = {}
    for path, value in files.items():
        if isinstance(value, str):
            decoded[path] = value.encode("utf-8")
        elif isinstance(value, dict) and set(value) == {"base64"} and isinstance(value["base64"], str):
            try:
                decoded[path] = base64.b64decode(value["base64"], validate=True)
            except (binascii.Error, ValueError):
                raise ValueError(f"files[{path!r}]: invalid base64") from None
        else:
            raise ValueError(f"files[{path!r}] must be a string or {{base64}}")
    return decoded


def handle(line: str) -> dict:
    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        return _refusal("RequestTooLarge", f"a request line may not exceed {MAX_LINE_BYTES} bytes")
    try:
        request = json.loads(line)
    except json.JSONDecodeError as error:
        return _refusal("InvalidRequest", f"not valid JSON ({error.msg})")
    if not isinstance(request, dict) or "config" not in request or "files" not in request or set(request) - {"config", "files"}:
        return _refusal("InvalidRequest", "a request is {config, files}")
    try:
        files = _decode_files(request["files"])
        return check_vocabulary_conformance(request["config"], files)
    except ConfigError as error:
        return _refusal("InvalidConfig", "the configuration is not admissible", error.errors)
    except (ValueError, TypeError) as error:
        return _refusal("InvalidRequest", str(error))


def run(stdin: IO[str], stdout: IO[str]) -> None:
    for line in stdin:
        if not line.strip():
            continue
        stdout.write(dumps(handle(line)) + "\n")
        stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    run(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
