"""Offline ASK checks over an explicitly supplied RDF dataset.

This is the publication-facing check profile for repository-owned queries,
not another general SPARQL endpoint. Keep named graphs separate, and refuse
nonlocal or nondeterministic algebra before RDFLib evaluates an expression.
"""
from __future__ import annotations

from pathlib import PurePosixPath

from pyparsing import ParseResults
from rdflib import Dataset
from rdflib.paths import Path as PropertyPath
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parserutils import CompValue

from .context import Context, FileProblem

FORMATS = {".nq": "nquads", ".trig": "trig"}
MAX_DATASET_BYTES = 8 * 1024 * 1024
MAX_QUERY_BYTES = 16 * 1024
MAX_ALGEBRA_NODES = 2048
# Structural allowlist; nested FILTER NOT EXISTS retains parsed graph syntax
# in RDFLib and must pass the same admission as the translated outer query.
ALLOWED = frozenset({
    "AskQuery", "BGP", "Join", "Project", "Graph", "Filter", "Extend",
    "GraphGraphPattern", "GroupGraphPatternSub", "TriplesBlock",
    "ConditionalAndExpression", "ConditionalOrExpression", "RelationalExpression",
    "Builtin_EXISTS", "Builtin_NOTEXISTS", "Builtin_BOUND", "Builtin_sameTerm",
    "Builtin_isIRI", "Builtin_isLITERAL", "Builtin_STR", "Builtin_REGEX",
    "Builtin_STRSTARTS", "Builtin_STRENDS", "Builtin_STRBEFORE", "Builtin_STRAFTER",
})


def _admit_query(text: str):
    prepared = prepareQuery(text)
    root = prepared.algebra
    if root.name != "AskQuery" or root["datasetClause"]:
        raise ValueError("requires ASK over the supplied dataset; no FROM or FROM NAMED")
    work = [root]
    count = 0
    seen = set()
    while work:
        value = work.pop()
        if isinstance(value, PropertyPath):
            raise ValueError("property paths are outside the local ASK profile")
        if isinstance(value, CompValue):
            if value.name not in ALLOWED:
                raise ValueError(f"{value.name} is outside the local ASK profile")
            if id(value) in seen:
                continue
            seen.add(id(value))
            count += 1
            if count > MAX_ALGEBRA_NODES:
                raise ValueError("ASK algebra exceeds the node limit")
            work.extend(value.values())
            evaluated = vars(value).get("graph")
            if evaluated is not None:
                work.append(evaluated)
        elif isinstance(value, (tuple, list, set, ParseResults)):
            work.extend(value)
    return prepared


def dataset_ask(ctx: Context) -> tuple[list[str], str]:
    section = ctx.config["dataset"]
    path = section["file"]
    data = ctx.read(path)
    if len(data) > MAX_DATASET_BYTES:
        raise FileProblem(f"{path}: dataset exceeds the input byte limit")
    dataset = Dataset(default_union=False)
    fmt = FORMATS[PurePosixPath(path).suffix.lower()]
    try:
        dataset.parse(data=data.decode("utf-8"), format=fmt, publicID=f"urn:x-conformance:{path}")
    except Exception as error:
        raise FileProblem(f"{path}: invalid UTF-8 or {fmt} ({type(error).__name__})") from None
    failures = []
    for item in section["ask"]:
        name, query_path = item["id"], item["query"]
        try:
            query = ctx.read(query_path)
            if len(query) > MAX_QUERY_BYTES:
                raise ValueError("query exceeds the input byte limit")
            prepared = _admit_query(query.decode("utf-8"))
            answer = dataset.query(prepared, use_store_provided=False).askAnswer
            if answer is not item["expect"]:
                failures.append(f"{name}: ASK returned {answer!r}, expected {item['expect']!r}")
        except FileProblem as error:
            failures.append(f"{name}: {error}")
        except Exception as error:
            first = (str(error).strip().splitlines() or [""])[0][:200]
            failures.append(f"{name}: {query_path}: {type(error).__name__}: {first}")
    return failures, f"{len(section['ask'])} local dataset ASK checks"
