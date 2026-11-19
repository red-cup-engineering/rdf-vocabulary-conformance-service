"""RFC 6901 JSON Pointer and RFC 6902 JSON Patch over plain JSON values.

`apply_patch` never mutates its input; it returns a patched deep copy or
raises `PatchError` naming the operation that failed.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any


class PatchError(ValueError):
    """An operation could not be applied."""


_INDEX = re.compile(r"^(0|[1-9][0-9]*)$")


def parse_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"pointer {pointer!r} must be empty or start with '/'")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _same(a: Any, b: Any) -> bool:
    """JSON equality: booleans are not numbers, and 1 equals 1.0."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _index(container: list, token: str, pointer: str, *, allow_end: bool) -> int:
    if allow_end and token == "-":
        return len(container)
    if not _INDEX.match(token):
        raise PatchError(f"{pointer}: {token!r} is not an array index")
    index = int(token)
    limit = len(container) if allow_end else len(container) - 1
    if index > limit:
        raise PatchError(f"{pointer}: index {index} is out of range")
    return index


def get(document: Any, pointer: str) -> Any:
    node = document
    for token in parse_pointer(pointer):
        if isinstance(node, dict):
            if token not in node:
                raise PatchError(f"{pointer}: no member {token!r}")
            node = node[token]
        elif isinstance(node, list):
            node = node[_index(node, token, pointer, allow_end=False)]
        else:
            raise PatchError(f"{pointer}: cannot descend into a scalar at {token!r}")
    return node


def _parent(document: Any, pointer: str) -> tuple[Any, str]:
    tokens = parse_pointer(pointer)
    if not tokens:
        raise PatchError("the document root has no parent")
    parent = get(document, "/" + "/".join(t.replace("~", "~0").replace("/", "~1") for t in tokens[:-1])) if len(tokens) > 1 else document
    return parent, tokens[-1]


def _add(document: Any, pointer: str, value: Any) -> Any:
    if pointer == "":
        return copy.deepcopy(value)
    parent, token = _parent(document, pointer)
    if isinstance(parent, dict):
        parent[token] = copy.deepcopy(value)
    elif isinstance(parent, list):
        parent.insert(_index(parent, token, pointer, allow_end=True), copy.deepcopy(value))
    else:
        raise PatchError(f"{pointer}: cannot add into a scalar")
    return document


def _remove(document: Any, pointer: str) -> Any:
    parent, token = _parent(document, pointer)
    if isinstance(parent, dict):
        if token not in parent:
            raise PatchError(f"{pointer}: no member {token!r} to remove")
        return parent.pop(token)
    if isinstance(parent, list):
        return parent.pop(_index(parent, token, pointer, allow_end=False))
    raise PatchError(f"{pointer}: cannot remove from a scalar")


def apply_patch(document: Any, operations: list[dict]) -> Any:
    result = copy.deepcopy(document)
    for number, operation in enumerate(operations):
        op = operation.get("op")
        path = operation.get("path")
        label = f"operation {number} ({op} {path})"
        try:
            if op == "add":
                result = _add(result, path, operation["value"])
            elif op == "remove":
                if path == "":
                    raise PatchError("cannot remove the document root")
                _remove(result, path)
            elif op == "replace":
                if path == "":
                    result = copy.deepcopy(operation["value"])
                else:
                    get(result, path)  # must exist
                    parent, token = _parent(result, path)
                    if isinstance(parent, dict):
                        parent[token] = copy.deepcopy(operation["value"])
                    else:
                        parent[_index(parent, token, path, allow_end=False)] = copy.deepcopy(operation["value"])
            elif op == "move":
                source = operation["from"]
                if path == source:
                    continue
                if path.startswith(source + "/"):
                    raise PatchError("cannot move a value into one of its own children")
                value = _remove(result, source)
                result = _add(result, path, value)
            elif op == "copy":
                result = _add(result, path, get(result, operation["from"]))
            elif op == "test":
                if not _same(get(result, path), operation["value"]):
                    raise PatchError("test failed: the value differs")
            else:
                raise PatchError(f"unknown op {op!r}")
        except PatchError as error:
            raise PatchError(f"{label}: {error}") from None
        except KeyError as error:
            raise PatchError(f"{label}: missing {error}") from None
    return result


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)
