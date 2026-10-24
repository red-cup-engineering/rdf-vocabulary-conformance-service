"""A small, deterministic JSON Schema (2020-12 subset) validator.

The conformance configuration is judged against `conformance.schema.json`.
The package keeps its dependency set to exactly what the RDF stack already
pins, so this module implements only the keywords that schema uses and
refuses (`UnsupportedKeyword`) any schema that uses another one, rather than
silently ignoring it. `assert_supported` walks a schema to prove that.
"""

from __future__ import annotations

import json
import re
from typing import Any

SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema", "$id", "$ref", "$defs", "$comment", "title", "description", "default", "examples",
        "type", "enum", "const", "required", "properties", "additionalProperties", "items",
        "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum",
        "maximum", "minProperties", "oneOf", "anyOf", "allOf", "if", "then", "else",
    }
)


class UnsupportedKeyword(ValueError):
    """The schema uses a keyword this validator does not implement."""


def assert_supported(schema: Any, where: str = "#") -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise UnsupportedKeyword(f"{where}: a schema must be an object or boolean")
    for key, value in schema.items():
        if key not in SUPPORTED_KEYWORDS:
            raise UnsupportedKeyword(f"{where}: keyword {key!r} is not supported")
        if key in ("properties", "$defs"):
            for name, sub in value.items():
                assert_supported(sub, f"{where}/{key}/{name}")
        elif key in ("items", "additionalProperties", "if", "then", "else"):
            assert_supported(value, f"{where}/{key}")
        elif key in ("oneOf", "anyOf", "allOf"):
            for index, sub in enumerate(value):
                assert_supported(sub, f"{where}/{key}/{index}")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _is_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise UnsupportedKeyword(f"type {name!r} is not supported")


def _resolve(ref: str, root: dict) -> Any:
    if not ref.startswith("#/"):
        raise UnsupportedKeyword(f"$ref {ref!r}: only local '#/...' references are supported")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _walk(value: Any, schema: Any, root: dict, path: str, out: list[str]) -> None:
    if schema is True or schema == {}:
        return
    if schema is False:
        out.append(f"{path}: no value is allowed here")
        return
    if "$ref" in schema:
        _walk(value, _resolve(schema["$ref"], root), root, path, out)
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, n) for n in names):
            out.append(f"{path}: expected {' or '.join(names)}")
            return
    if "const" in schema and _canonical(value) != _canonical(schema["const"]):
        out.append(f"{path}: must equal {_canonical(schema['const'])}")
    if "enum" in schema and _canonical(value) not in {_canonical(e) for e in schema["enum"]}:
        out.append(f"{path}: must be one of {_canonical(schema['enum'])}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen = [_canonical(v) for v in value]
            if len(set(seen)) != len(seen):
                out.append(f"{path}: items are not unique")
        if "items" in schema:
            for index, item in enumerate(value):
                _walk(item, schema["items"], root, f"{path}[{index}]", out)
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                out.append(f"{path}: missing required property {name!r}")
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            out.append(f"{path}: fewer than {schema['minProperties']} properties")
        properties = schema.get("properties", {})
        for name in sorted(value):
            child = f"{path}.{name}"
            if name in properties:
                _walk(value[name], properties[name], root, child, out)
            elif "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                if extra is False:
                    out.append(f"{path}: unexpected property {name!r}")
                else:
                    _walk(value[name], extra, root, child, out)
    for keyword in ("allOf",):
        for sub in schema.get(keyword, []):
            _walk(value, sub, root, path, out)
    if "anyOf" in schema:
        if not any(not _errors(value, sub, root, path) for sub in schema["anyOf"]):
            out.append(f"{path}: matches none of the allowed alternatives")
    if "oneOf" in schema:
        matches = sum(1 for sub in schema["oneOf"] if not _errors(value, sub, root, path))
        if matches != 1:
            out.append(f"{path}: must match exactly one alternative, matches {matches}")
    if "if" in schema:
        if not _errors(value, schema["if"], root, path):
            if "then" in schema:
                _walk(value, schema["then"], root, path, out)
        elif "else" in schema:
            _walk(value, schema["else"], root, path, out)


def _errors(value: Any, schema: Any, root: dict, path: str) -> list[str]:
    out: list[str] = []
    _walk(value, schema, root, path, out)
    return out


def validate(instance: Any, schema: dict) -> list[str]:
    """Sorted, de-duplicated list of `path: message` errors; empty when valid."""
    return sorted(set(_errors(instance, schema, schema, "$")))
