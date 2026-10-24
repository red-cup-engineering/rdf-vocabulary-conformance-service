"""Configuration admission: schema, then the rules a schema cannot say."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

from . import _jsonschema
from .globs import matches


class ConfigError(ValueError):
    """The configuration is not admissible. `errors` is a sorted list of messages."""

    def __init__(self, errors: list[str]):
        self.errors = sorted(set(errors))
        shown = "; ".join(self.errors[:5])
        more = f" (+{len(self.errors) - 5} more)" if len(self.errors) > 5 else ""
        super().__init__(shown + more)


@lru_cache(maxsize=1)
def load_schema() -> dict:
    text = resources.files(__package__).joinpath("conformance.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


def _regexes(config: dict) -> list[tuple[str, str, int]]:
    """(location, pattern, minimum capture groups) for every regular expression."""
    found: list[tuple[str, str, int]] = []
    info = config.get("vocabulary", {}).get("ontology", {}).get("versionInfoFrom")
    if info:
        found.append(("vocabulary.ontology.versionInfoFrom.pattern", info["pattern"], 1))
    glossary = config.get("skos", {}).get("glossary", {})
    source = glossary.get("source", {})
    if "pattern" in source:
        found.append(("skos.glossary.source.pattern", source["pattern"], 0))
    if "commitPattern" in source:
        found.append(("skos.glossary.source.commitPattern", source["commitPattern"], 1))
    if "pattern" in glossary.get("derivedFrom", {}):
        found.append(("skos.glossary.derivedFrom.pattern", glossary["derivedFrom"]["pattern"], 0))
    return found


def _semantic_errors(config: dict) -> list[str]:
    errors: list[str] = []
    for location, pattern, groups in _regexes(config):
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            errors.append(f"{location}: not a regular expression ({error})")
            continue
        if compiled.groups < groups:
            errors.append(f"{location}: needs at least {groups} capture group(s)")

    vocabulary = config.get("vocabulary")
    root = config.get("root")
    records = config.get("records")

    version_iri = (vocabulary or {}).get("ontology", {}).get("versionIri", "")
    if "{version}" in version_iri and "versionInfoFrom" not in vocabulary["ontology"]:
        errors.append("vocabulary.ontology.versionIri uses {version} but versionInfoFrom is not set")
    for section in ("skos", "closedSets", "labelSets"):
        if section in config and vocabulary is None:
            errors.append(f"{section}: needs a 'vocabulary' section")
    if "validate" in (root or {}) and "shapes" not in root:
        errors.append("root.validate: needs root.shapes")
    if "shapeTests" in config and records is None:
        errors.append("shapeTests: needs a 'records' section")
    if records is not None:
        if not records["baseIri"].endswith("/"):
            errors.append("records.baseIri: must end in '/'")
        for path in records.get("kinds", {}):
            if not is_safe_path(path):
                errors.append(f"records.kinds: {path!r} is not a relative repository path")
            elif not matches(records["glob"], path):
                errors.append(f"records.kinds: {path!r} does not match records.glob")
    projection = config.get("skos", {}).get("projection")
    if projection is not None and "glossary" not in config["skos"]:
        errors.append("skos.projection: needs skos.glossary")
    for section, items in (("shapeTests", config.get("shapeTests", [])), ("records.graphExpectations", (records or {}).get("graphExpectations", []))):
        ids = [item["id"] for item in items]
        for duplicate in sorted({i for i in ids if ids.count(i) > 1}):
            errors.append(f"{section}: duplicate id {duplicate!r}")
    return errors


def is_safe_path(path: str) -> bool:
    """A relative forward-slash path with no empty, '.' or '..' segment and no control character."""
    if not path or path.startswith("/") or "\\" in path or any(ord(c) < 32 for c in path):
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def validate_config(config: Any) -> None:
    """Raise `ConfigError` unless `config` is admissible."""
    if not isinstance(config, dict):
        raise ConfigError(["$: the configuration must be a JSON object"])
    errors = _jsonschema.validate(config, load_schema())
    if not errors:
        errors = _semantic_errors(config)
    if errors:
        raise ConfigError(errors)


def required_patterns(config: dict) -> list[str]:
    """Every path or glob the checks may read, sorted. A face reads exactly these."""
    found: set[str] = set()

    def add(value: str | None) -> None:
        if value:
            found.add(value)

    parse = config.get("parse", {})
    for key in ("rdf", "json"):
        add(parse.get(key, {}).get("glob"))
    for path in parse.get("json", {}).get("exactly", []):
        add(path)
    vocabulary = config.get("vocabulary", {})
    for path in vocabulary.get("files", []):
        add(path)
    add(vocabulary.get("ontology", {}).get("versionInfoFrom", {}).get("file"))
    root = config.get("root", {})
    add(root.get("ontology"))
    add(root.get("shapes"))
    for path in root.get("validate", []):
        add(path)
    add(config.get("skos", {}).get("projection", {}).get("file"))
    records = config.get("records", {})
    for key in ("glob", "context", "shapes"):
        add(records.get(key))
    for path in records.get("kinds", {}):
        add(path)
    for item in records.get("graphExpectations", []):
        add(item["record"])
    for item in config.get("shapeTests", []):
        add(item["record"])
    return sorted(found)
