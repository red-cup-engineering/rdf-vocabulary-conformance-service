"""The conformance report as SignCell cells, in signcell's wire shape.

`@red-cup-engineering/signcell/cell` (JavaScript) is the house definition of a cell. This service is
Python and cannot import it, so it mirrors the *wire shape* and nothing else:

    {"subject": ..., "predicate": ..., "object": ..., "for": [...], "against": [...], "value"?: ...}

exactly those keys (`isCell` in signcell's `cell.mjs` rejects any other), `for` and `against` the
witnesses told for and against the triple, deduplicated, and `value` a reading of FOUR in the
*knowledge* family ("absurd" | "potential" | "active" | "stable", the points (0,0) (1,0) (0,1) (1,1)
of told-for/told-against) written only when it differs from what the two sides show (a Measured
cell carries no `value` key). A JavaScript consumer re-admits these with `admitCell`, which
normalizes the sides and computes the cell's address (the ni: name of the triple's canonical CBOR).

What this module does not mirror is the witness order: signcell orders each side by canonical CBOR
bytes; here each side is ordered by canonical JSON text, and the cell set by (predicate, subject).
`admitCell` re-sorts, so the order is a presentation choice, not an identity.

One cell per check and per shape test:

    subject    ni:///sha-256;... of the canonical JSON of the report's `inputs` (the configuration
               digest and every input file's digest): the exact thing judged
    predicate  "urn:rdf-vocabulary-conformance:check:<id>" or "...:shape-test:<id>"
    object     "conforms"
    for        one witness {"kind": "check-passed", "detail": <the check's summary>} when it passed
    against    one witness {"kind": "check-failed", "failure": <text>} per failure
    verdict    the FOUR reading of the sides, also in the extremal family ("held" | "denied" |
               "unknown" | "contested"): held = passed, denied = failed, unknown = the check could
               not run (neither side told), contested = told both ways.

The verdict is the cell's presence, so it is not written into `value`; `verdict_of(cell)` reads it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Mapping

PREDICATE_BASE = "urn:rdf-vocabulary-conformance:"
OBJECT = "conforms"

# The reading families of signcell's FOUR, in side order (0,0) (1,0) (0,1) (1,1).
KNOWLEDGE = ("absurd", "potential", "active", "stable")
EXTREMAL = ("unknown", "held", "denied", "contested")


def canonical_json(value: Any) -> str:
    """Sorted keys, no insignificant whitespace, UTF-8 text (JCS for the strings and integers used here)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def ni_of(data: bytes) -> str:
    """The RFC 6920 name of bytes: ni:///sha-256;<base64url digest>, as the house makeNiUri writes it."""
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return f"ni:///sha-256;{digest}"


def _side(witnesses: list[Any]) -> list[Any]:
    unique = {canonical_json(witness): witness for witness in witnesses}
    return [unique[key] for key in sorted(unique)]


def cell(subject: Any, predicate: Any, obj: Any, told: list[Any] | None = None, denied: list[Any] | None = None) -> dict:
    """A cell over (subject, predicate, object) with the witnesses told for and against it."""
    for_side, against_side = _side(told or []), _side(denied or [])
    return {"subject": subject, "predicate": predicate, "object": obj, "for": for_side, "against": against_side}


def presence_index(value: Mapping[str, Any]) -> int:
    return (1 if value["for"] else 0) + (2 if value["against"] else 0)


def verdict_of(value: Mapping[str, Any]) -> str:
    """The cell's FOUR reading in the extremal family: unknown, held, denied or contested."""
    return EXTREMAL[presence_index(value)]


def knowledge_of(value: Mapping[str, Any]) -> str:
    """The same point in the knowledge family, the family a cell's `value` key is written in."""
    return KNOWLEDGE[presence_index(value)]


def is_cell(value: Any) -> bool:
    """Signcell's `isCell` by shape: exactly the five keys, or those and a knowledge-family `value`."""
    if not isinstance(value, dict):
        return False
    keys = set(value)
    base = {"subject", "predicate", "object", "for", "against"}
    valued = keys == base | {"value"} and value["value"] in KNOWLEDGE
    return (keys == base or valued) and isinstance(value["for"], list) and isinstance(value["against"], list)


def report_subject(report: Mapping[str, Any]) -> str:
    return ni_of(canonical_json(report["inputs"]).encode("utf-8"))


def report_cells(report: Mapping[str, Any]) -> list[dict]:
    """One cell per check and shape test of a report, ordered by predicate."""
    subject = report_subject(report)
    cells = []
    for check in report["checks"]:
        failures = list(check["failures"])
        cells.append(cell(
            subject, f"{PREDICATE_BASE}check:{check['id']}", OBJECT,
            [{"kind": "check-passed", "detail": check["detail"]}] if check["ok"] else [],
            [{"kind": "check-failed", "failure": text} for text in failures] if not check["ok"] else [],
        ))
    for test in report["shapeTests"]:
        cells.append(cell(
            subject, f"{PREDICATE_BASE}shape-test:{test['id']}", OBJECT,
            [{"kind": "check-passed", "detail": test["detail"]}] if test["ok"] else [],
            [] if test["ok"] or test["actual"] == "error" else [{"kind": "check-failed", "failure": f"expected {test['expect']}, got {test['actual']}: {test['detail']}"}],
        ))
    return sorted(cells, key=lambda value: (value["predicate"], canonical_json(value["subject"])))


def cells_text(cells: list[dict]) -> str:
    """The cells as deterministic text: canonical JSON of the array and a final newline."""
    return canonical_json(cells) + "\n"
