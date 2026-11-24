"""Offline receiving checks over an explicitly supplied RDF dataset.

This is the publication-facing check profile for repository-owned queries,
not another general SPARQL endpoint. Keep named graphs separate, and refuse
nonlocal or nondeterministic algebra before RDFLib evaluates an expression.
"""
from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote, urlsplit

from pyparsing import ParseResults
from rdflib import Dataset, Graph, URIRef, Variable
from rdflib.compare import isomorphic
from rdflib.graph import DATASET_DEFAULT_GRAPH_ID
from rdflib.namespace import OWL, RDF
from rdflib.paths import Path as PropertyPath
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql.sparql import Query

from .context import Context, FileProblem, SH, merged

MAX_QUERY_BYTES = 16 * 1024
MAX_REFERENCE_BYTES = 8 * 1024 * 1024
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


# Saved SELECT projections use a separate, explicit local profile; additions
# here do not widen the older ASK admission.
SELECT_ALLOWED = frozenset({
    "SelectQuery", "BGP", "Join", "LeftJoin", "Union", "Project", "Graph", "Filter",
    "Extend", "Distinct", "OrderBy", "OrderCondition", "TrueFilter", "UnaryNot",
    "ConditionalAndExpression", "ConditionalOrExpression", "RelationalExpression",
    "Builtin_BOUND", "Builtin_sameTerm", "Builtin_COALESCE", "Builtin_IF",
    "ToMultiSet", "values", "Group", "AggregateJoin", "Aggregate_Count", "Aggregate_Sample",
})


def _prepare_select(text: str):
    syntax = parseQuery(text)
    if syntax[1].name != "SelectQuery" or "datasetClause" in syntax[1]:
        raise ValueError("requires SELECT over the supplied dataset; no FROM or FROM NAMED")
    # RDFLib rewrites grouped projections to SAMPLE internally. Inspect its
    # syntax before that translation so an authored SAMPLE cannot impersonate it.
    work = [syntax]
    count = 0
    while work:
        value = work.pop()
        if isinstance(value, CompValue):
            if value.name in {"SubSelect", "Aggregate_Sample", "ServiceGraphPattern"}:
                raise ValueError(f"{value.name} is outside the local SELECT profile")
            count += 1
            if count > MAX_ALGEBRA_NODES:
                raise ValueError("SELECT syntax exceeds the node limit")
            work.extend(value.values())
        elif isinstance(value, (tuple, list, set, ParseResults)):
            work.extend(value)
    return translateQuery(syntax)


def _admit_query(text: str, *, select: bool = False):
    prepared = _prepare_select(text) if select else prepareQuery(text)
    root = prepared.algebra
    operation = "SELECT" if select else "ASK"
    if root.name != ("SelectQuery" if select else "AskQuery") or root["datasetClause"]:
        raise ValueError(f"requires {operation} over the supplied dataset; no FROM or FROM NAMED")
    allowed = SELECT_ALLOWED if select else ALLOWED
    work = [(root, False)]
    count = 0
    seen = set()
    group_samples = set()
    while work:
        value, named = work.pop()
        if isinstance(value, PropertyPath):
            raise ValueError(f"property paths are outside the local {operation} profile")
        if isinstance(value, CompValue):
            if value.name not in allowed:
                raise ValueError(f"{value.name} is outside the local {operation} profile")
            key = id(value), named
            if key in seen:
                continue
            seen.add(key)
            count += 1
            if count > MAX_ALGEBRA_NODES:
                raise ValueError(f"{operation} algebra exceeds the node limit")
            if select:
                if value.name == "Graph" and (
                    not isinstance(value.term, URIRef) or not urlsplit(value.term).scheme
                    or value.term == DATASET_DEFAULT_GRAPH_ID
                ):
                    raise ValueError("local SELECT GRAPH requires a constant named graph IRI")
                if value.name == "BGP" and value.triples and not named:
                    raise ValueError("local SELECT triple patterns require a named GRAPH")
                if value.name == "ToMultiSet":
                    if not isinstance(value.p, CompValue) or value.p.name != "values":
                        raise ValueError("local SELECT multiset input must be VALUES")
                if value.name == "AggregateJoin":
                    group = value.p
                    keys = group.expr if isinstance(group, CompValue) and group.name == "Group" else None
                    group_keys = {expr for expr in keys or () if isinstance(expr, Variable)}
                    for aggregator in value.A:
                        if aggregator.name == "Aggregate_Sample":
                            if not isinstance(aggregator.vars, Variable) or aggregator.vars not in group_keys:
                                raise ValueError("local SELECT implicit SAMPLE must project a GROUP BY variable")
                            group_samples.add(id(aggregator))
                if value.name == "Aggregate_Sample" and id(value) not in group_samples:
                    raise ValueError("local SELECT SAMPLE requires its own Group")
            work.extend((child, named or (select and value.name == "Graph" and slot == "p"))
                        for slot, child in value.items())
            evaluated = vars(value).get("graph")
            if evaluated is not None:
                work.append((evaluated, named))
        elif isinstance(value, (tuple, list, set, ParseResults)):
            work.extend((child, named) for child in value)
        elif select and isinstance(value, dict):
            work.extend((child, named) for child in value.values())
    return prepared


def prepare_local_select(query: bytes) -> Query:
    """Compile a structurally bounded SELECT for an already supplied local dataset.

    No dataset is created, extended or evaluated here. Evaluate the result
    with RDFLib's local processor (``use_store_provided=False``) on the caller's
    trusted dataset. The syntax limits are not limits on result size or runtime.
    """
    if not isinstance(query, bytes):
        raise TypeError("local SELECT input must be UTF-8 bytes")
    if len(query) > MAX_QUERY_BYTES:
        raise ValueError("query exceeds the input byte limit")
    return _admit_query(query.decode("utf-8"), select=True)


def matches_named_graph(dataset: Dataset, graph_iri: URIRef, reference: Graph) -> bool:
    """Compare a nonempty named graph with a reference up to blank-node renaming.

    The default graph, missing named graphs and empty references never pass.
    Read only the identified graph, without registering an empty context.
    """
    if not isinstance(graph_iri, URIRef) or graph_iri == dataset.default_graph.identifier:
        return False
    actual = dataset.get_context(graph_iri)
    return bool(reference) and len(actual) == len(reference) and isomorphic(actual, reference)


def _named_graph_layout_failures(dataset: Dataset, *, required: Iterable[str],
                                optional: Iterable[str] = (), disjoint: bool = False) -> list[str]:
    """Judge the nonempty contexts of an already parsed RDF dataset.

    A dataset must have an empty default graph, all required IRI-named
    graphs, and no other nonempty graph besides the allowed optional ones.
    Optional disjointness compares actual RDF triples in those contexts.
    """
    mandatory = frozenset(URIRef(value) for value in required)
    allowed = mandatory | frozenset(URIRef(value) for value in optional)
    default = dataset.default_graph.identifier
    named = {graph.identifier: graph for graph in dataset.graphs()
             if graph.identifier != default and len(graph)}
    failures = []
    if len(dataset.default_graph):
        failures.append("default graph is not empty")
    failures.extend(f"required named graph is missing or empty: {iri}" for iri in sorted(mandatory - named.keys()))
    # Blank graph labels are parser-local and cannot appear in a reproducible error.
    blank_count = sum(1 for name in named if not isinstance(name, URIRef))
    if blank_count:
        failures.append(f"{blank_count} nonempty named graphs do not have an IRI")
    failures.extend(f"unexpected named graph: {iri}" for iri in sorted(
        name for name in named.keys() - allowed if isinstance(name, URIRef)))
    if disjoint and not failures:
        prior = set()
        for iri, graph in sorted(named.items()):
            if any(triple in prior for triple in graph):
                failures.append(f"named graph shares statements with an earlier graph: {iri}")
            prior.update(graph)
    return failures


def dataset_named_graph_layout(ctx: Context) -> tuple[list[str], str]:
    section = ctx.config["dataset"]
    layout = section["namedGraphLayout"]
    failures = _named_graph_layout_failures(
        ctx.dataset(section["file"]), required=layout["required"],
        optional=layout.get("optional", ()), disjoint=layout.get("disjoint", False),
    )
    return failures, "closed layout of nonempty named RDF graphs"


def dataset_graph_matches(ctx: Context) -> tuple[list[str], str]:
    """Bind selected named graphs to exact local Turtle references."""
    section = ctx.config["dataset"]
    dataset = ctx.dataset(section["file"])
    failures = []
    for item in section["graphMatches"]:
        name, graph_iri, path = item["id"], item["graph"], item["reference"]
        try:
            data = ctx.read(path)
            if len(data) > MAX_REFERENCE_BYTES:
                raise ValueError("Turtle reference exceeds the input byte limit")
            reference = Graph().parse(data=data.decode("utf-8"), format="turtle",
                                      publicID=item.get("baseIri", "https://rdf-reference.invalid/" + quote(path)))
            if not matches_named_graph(dataset, URIRef(graph_iri), reference):
                failures.append(f"{name}: named graph {graph_iri} differs from the nonempty Turtle reference {path}")
        except FileProblem as error:
            failures.append(f"{name}: {error}")
        except Exception as error:
            first = (str(error).strip().splitlines() or [""])[0][:200]
            failures.append(f"{name}: {path}: {type(error).__name__}: {first}")
    return failures, f"{len(section['graphMatches'])} named graph references"


def dataset_ask(ctx: Context) -> tuple[list[str], str]:
    section = ctx.config["dataset"]
    dataset = ctx.dataset(section["file"])
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


def dataset_shacl(ctx: Context) -> tuple[list[str], str]:
    """SHACL Core runs on a separate union; do not change the ASK dataset."""
    section = ctx.config["dataset"]
    dataset = ctx.dataset(section["file"])
    shape_paths = section["shapes"]
    # Explicit local Core shapes only: no executable extensions or imports.
    shapes = merged(*(ctx.graph(path) for path in shape_paths))
    if not shapes:
        raise FileProblem("dataset.shapes: expected nonempty local SHACL Core shapes")
    extra_types = {SH.SPARQLConstraint, SH.SPARQLConstraintComponent, SH.JSConstraint,
                   SH.JSConstraintComponent, SH.SPARQLTarget, SH.JSTarget, SH.SPARQLRule, SH.TripleRule,
                   SH.ConstraintComponent}
    extra_paths = {SH.sparql, SH.select, SH.ask, SH.js, SH.jsFunctionName, SH.target, SH.rule,
                   SH.validator, SH.nodeValidator, SH.propertyValidator, OWL.imports}
    for _, predicate, obj in shapes:
        if predicate in extra_paths or (predicate == RDF.type and obj in extra_types):
            raise FileProblem("dataset.shapes: executable shapes and ontology imports are outside the SHACL Core profile")
    if len(dataset.default_graph):
        raise FileProblem("dataset.shapes: SHACL Core expects named data; the default graph is not empty")
    union = Graph()
    for s, p, o, _ in dataset.quads():
        union.add((s, p, o))
    if not union:
        raise FileProblem("dataset.shapes: no named triples to validate")
    conforms, failures = ctx.shacl(union, shapes)
    if not conforms and not failures:
        failures = ["dataset does not conform"]
    return failures, f"SHACL Core over the dataset union with {len(shape_paths)} shape files"
