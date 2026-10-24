"""Shape tests: broken (or untouched) copies of real records, judged.

A record is patched with RFC 6902 operations and converted and validated by
the same path the records checks use. `expect: violates` proves a shape bites;
`expect: conforms` (usually with an empty mutation) proves the path accepts
good data, so a green run means something.
"""

from __future__ import annotations

from . import jsonpatch
from .context import Context, FileProblem


def _run_one(ctx: Context, item: dict, shapes, ontology) -> dict:
    expected = item["expect"]
    via = item.get("via", "shacl")
    result = {"id": item["id"], "record": item["record"], "expect": expected, "via": via if expected == "violates" else "any", "reason": item["reason"]}
    try:
        record = ctx.record_json(item["record"])
        patched = jsonpatch.apply_patch(record, item["mutation"])
        if not isinstance(patched, dict):
            raise FileProblem(f"{item['record']}: the mutation left a non-object")
        graph = ctx.record_graph(item["record"], patched)
    except (FileProblem, jsonpatch.PatchError) as error:
        result.update(ok=False, actual="error", detail=str(error))
        return result
    unmapped = ctx.unmapped(graph)
    try:
        conforms, lines = ctx.shacl(graph, shapes, ontology)
    except Exception as error:  # pyshacl raises its own runtime errors
        result.update(ok=False, actual="error", detail=f"SHACL validation failed ({type(error).__name__}: {str(error)[:200]})")
        return result
    if expected == "conforms":
        actual = "conforms" if conforms and not unmapped else "violates"
    elif via == "context":
        actual = "violates" if unmapped else "conforms"
    else:
        actual = "conforms" if conforms else "violates"
    evidence = []
    if unmapped:
        evidence.append("unmapped: " + ", ".join(unmapped[:5]))
    if lines:
        evidence.append(f"{len(lines)} SHACL result(s): " + "; ".join(lines[:5]))
    result.update(ok=actual == expected, actual=actual, detail="; ".join(evidence) if evidence else "no unmapped term, SHACL conforms")
    return result


def run(ctx: Context) -> list[dict]:
    items = ctx.config.get("shapeTests", [])
    if not items:
        return []
    spec = ctx.config["records"]
    try:
        shapes = ctx.graph(spec["shapes"])
        ontology = ctx.ontology()
    except FileProblem as error:
        return [
            {"id": i["id"], "record": i["record"], "expect": i["expect"], "via": i.get("via", "shacl"), "reason": i["reason"], "ok": False, "actual": "error", "detail": str(error)}
            for i in items
        ]
    return [_run_one(ctx, item, shapes, ontology) for item in items]
