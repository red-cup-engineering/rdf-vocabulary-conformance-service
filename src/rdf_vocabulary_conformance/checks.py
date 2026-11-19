"""The conformance checks.

Each check takes the run `Context` and returns `(failures, summary)`:
`failures` is a list of human-readable strings (empty when the check passes)
and `summary` describes what was examined. A check is registered with the
predicate that says whether the configuration asks for it; an unconfigured
check is absent from the report, not passed.

The checks generalize, one for one, the paper-specific tests they replace:
Parsing, Rooting, Concepts (SKOS), ClosedSets and Records. ShapesBite is
`shape_tests.py`.
"""

from __future__ import annotations

import re
from typing import Callable

from pathlib import PurePosixPath

from rdflib import Graph, URIRef
from rdflib.collection import Collection
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SKOS

from . import jsonpatch
from .context import Context, FileProblem, jsonld_message, merged

PROV_WAS_DERIVED_FROM = URIRef("http://www.w3.org/ns/prov#wasDerivedFrom")
CAP = 20  # failures kept per check, so a broken run stays readable

Outcome = tuple[list[str], str]


def _capped(failures: list[str]) -> list[str]:
    failures = sorted(failures)
    if len(failures) > CAP:
        return failures[:CAP] + [f"... and {len(failures) - CAP} more"]
    return failures


def _iri(value: str) -> URIRef:
    return URIRef(value)


def _show(term: object) -> str:
    return str(term)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parsing_rdf(ctx: Context) -> Outcome:
    spec = ctx.config["parse"]["rdf"]
    paths = ctx.match(spec["glob"])
    failures: list[str] = []
    if len(paths) < spec.get("minimum", 0):
        failures.append(f"{len(paths)} files match {spec['glob']}; at least {spec['minimum']} required")
    for path in paths:
        try:
            if len(ctx.graph(path)) == 0:
                failures.append(f"{path}: parses to an empty graph")
        except FileProblem as error:
            failures.append(str(error))
    return failures, f"{len(paths)} RDF files match {spec['glob']}"


def parsing_json(ctx: Context) -> Outcome:
    spec = ctx.config["parse"]["json"]
    paths = ctx.match(spec["glob"])
    failures: list[str] = []
    if len(paths) < spec.get("minimum", 0):
        failures.append(f"{len(paths)} files match {spec['glob']}; at least {spec['minimum']} required")
    if "exactly" in spec:
        expected = set(spec["exactly"])
        for path in sorted(expected - set(paths)):
            failures.append(f"{path}: expected but not present")
        for path in sorted(set(paths) - expected):
            failures.append(f"{path}: present but not expected")
    for path in paths:
        try:
            if not isinstance(ctx.json(path), dict):
                failures.append(f"{path}: not a JSON object")
        except FileProblem as error:
            failures.append(str(error))
    return failures, f"{len(paths)} JSON files match {spec['glob']}"


# ---------------------------------------------------------------------------
# Rooting
# ---------------------------------------------------------------------------


def rooting_ontology_metadata(ctx: Context) -> Outcome:
    vocabulary = ctx.config["vocabulary"]
    spec = vocabulary["ontology"]
    graph = ctx.vocabulary()
    iri = _iri(spec["iri"])
    failures: list[str] = []
    if (iri, RDF.type, OWL.Ontology) not in graph:
        failures.append(f"{iri} is not declared an owl:Ontology")
    version = None
    if "versionInfoFrom" in spec:
        source = spec["versionInfoFrom"]
        found = re.search(source["pattern"], ctx.text(source["file"]), re.M)
        if not found:
            failures.append(f"{source['file']}: no match for {source['pattern']}")
        else:
            version = found.group(1)
            actual = graph.value(iri, OWL.versionInfo)
            if actual is None or str(actual) != version:
                failures.append(f"owl:versionInfo is {_show(actual)}, {source['file']} says {version}")
    if "versionIri" in spec:
        expected = spec["versionIri"].replace("{iri}", spec["iri"]).replace("{version}", version or "")
        if (iri, OWL.versionIRI, _iri(expected)) not in graph:
            failures.append(f"missing owl:versionIRI {expected}")
    for imported in spec.get("imports", []):
        if (iri, OWL.imports, _iri(imported)) not in graph:
            failures.append(f"missing owl:imports {imported}")
    if "license" in spec and (iri, DCTERMS.license, _iri(spec["license"])) not in graph:
        failures.append(f"missing dcterms:license {spec['license']}")
    return failures, f"header of {spec['iri']}"


def rooting_root_declares(ctx: Context) -> Outcome:
    root = ctx.config["root"]
    graph = ctx.root_ontology()
    failures: list[str] = []
    if "iri" in root and (_iri(root["iri"]), RDF.type, OWL.Ontology) not in graph:
        failures.append(f"root does not declare {root['iri']} an owl:Ontology")
    if "recordClass" in root and (_iri(root["recordClass"]), RDF.type, OWL.Class) not in graph:
        failures.append(f"root does not declare {root['recordClass']} an owl:Class")
    return failures, f"root ontology {root['ontology']}"


def rooting_root_shapes(ctx: Context) -> Outcome:
    root = ctx.config["root"]
    shapes = ctx.graph(root["shapes"])
    base = ctx.graph(root["ontology"])
    failures: list[str] = []
    for path in root["validate"]:
        try:
            conforms, lines = ctx.shacl(merged(ctx.graph(path), base), shapes)
        except FileProblem as error:
            failures.append(str(error))
            continue
        if not conforms:
            failures.append(f"{path} does not conform to {root['shapes']}: " + "; ".join(lines[:5]))
    return failures, f"{len(root['validate'])} graphs judged by {root['shapes']}"


def rooting_kinds_subclass_root(ctx: Context) -> Outcome:
    root_class = _iri(ctx.config["root"]["recordClass"])
    graph = merged(ctx.vocabulary(), ctx.root_ontology())
    kinds = ctx.config["records"]["kinds"]
    failures = [
        f"{path}: {kind} is not a subclass of {root_class}"
        for path, kind in kinds.items()
        if root_class not in set(graph.transitive_objects(_iri(kind), RDFS.subClassOf))
    ]
    return failures, f"{len(set(kinds.values()))} record kinds"


def rooting_terms_documented(ctx: Context) -> Outcome:
    vocabulary = ctx.config["vocabulary"]
    graph = ctx.vocabulary()
    namespace = vocabulary["namespace"]
    declared = {
        s
        for t in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty)
        for s in graph.subjects(RDF.type, t)
        if str(s).startswith(namespace)
    }
    failures: list[str] = []
    if len(declared) < vocabulary.get("declaredTermsAtLeast", 0):
        failures.append(f"{len(declared)} terms declared in {namespace}; at least {vocabulary['declaredTermsAtLeast']} required")
    for term in declared:
        if not (graph.value(term, RDFS.comment) or graph.value(term, RDFS.label)):
            failures.append(f"{term}: no rdfs:comment or rdfs:label")
    return failures, f"{len(declared)} terms declared in {namespace}"


# ---------------------------------------------------------------------------
# SKOS
# ---------------------------------------------------------------------------


def _concepts(graph: Graph) -> list[URIRef]:
    return sorted(graph.subjects(RDF.type, SKOS.Concept))


def _glossary(graph: Graph, prefix: str) -> list[URIRef]:
    return [c for c in _concepts(graph) if str(c).startswith(prefix)]


def skos_scheme(ctx: Context) -> Outcome:
    spec = ctx.config["skos"]
    graph = ctx.vocabulary()
    scheme = _iri(spec["scheme"])
    failures: list[str] = []
    if (scheme, RDF.type, SKOS.ConceptScheme) not in graph:
        failures.append(f"{scheme} is not a skos:ConceptScheme")
    tops = list(graph.objects(scheme, SKOS.hasTopConcept))
    if len(tops) < spec.get("minimumTopConcepts", 0):
        failures.append(f"{len(tops)} top concepts; at least {spec['minimumTopConcepts']} required")
    return failures, f"scheme {scheme} has {len(tops)} top concepts"


def skos_concepts_complete(ctx: Context) -> Outcome:
    spec = ctx.config["skos"]
    graph = ctx.vocabulary()
    scheme = _iri(spec["scheme"])
    concepts = _concepts(graph)
    failures: list[str] = []
    if "conceptCount" in spec and len(concepts) != spec["conceptCount"]:
        failures.append(f"{len(concepts)} concepts; exactly {spec['conceptCount']} expected")
    for concept in concepts:
        if not list(graph.objects(concept, SKOS.prefLabel)):
            failures.append(f"{concept}: no skos:prefLabel")
        definitions = list(graph.objects(concept, SKOS.definition))
        if not (definitions and all(str(d).strip() for d in definitions)):
            failures.append(f"{concept}: no skos:definition")
        if (concept, SKOS.inScheme, scheme) not in graph:
            failures.append(f"{concept}: not skos:inScheme {scheme}")
    return failures, f"{len(concepts)} concepts"


def skos_collections(ctx: Context) -> Outcome:
    spec = ctx.config["skos"]
    graph = ctx.vocabulary()
    scheme = _iri(spec["scheme"])
    minimum = spec.get("minimumCollectionMembers", 0)
    collections = sorted(graph.subjects(RDF.type, SKOS.Collection))
    failures: list[str] = []
    for collection in collections:
        if not list(graph.objects(collection, SKOS.prefLabel)):
            failures.append(f"{collection}: no skos:prefLabel")
        if not list(graph.objects(collection, SKOS.definition)):
            failures.append(f"{collection}: no skos:definition")
        members = list(graph.objects(collection, SKOS.member))
        if len(members) < minimum:
            failures.append(f"{collection}: {len(members)} members; at least {minimum} required")
        for member in members:
            if (member, SKOS.inScheme, scheme) not in graph:
                failures.append(f"{collection}: member {member} is not skos:inScheme {scheme}")
    return failures, f"{len(collections)} collections"


def skos_disjoint(ctx: Context) -> Outcome:
    graph = ctx.vocabulary()
    both = set(graph.subjects(RDF.type, SKOS.Concept)) & set(graph.subjects(RDF.type, SKOS.Collection))
    return [f"{s}: is both a skos:Concept and a skos:Collection" for s in sorted(both)], "skos:Concept and skos:Collection are disjoint"


def skos_glossary_sources(ctx: Context) -> Outcome:
    spec = ctx.config["skos"]["glossary"]
    graph = ctx.vocabulary()
    glossary = _glossary(graph, spec["iriPrefix"])
    failures: list[str] = []
    if "count" in spec and len(glossary) != spec["count"]:
        failures.append(f"{len(glossary)} glossary concepts under {spec['iriPrefix']}; exactly {spec['count']} expected")
    source_pattern = spec.get("source", {}).get("pattern")
    derived_pattern = spec.get("derivedFrom", {}).get("pattern")
    for concept in glossary:
        if source_pattern is not None:
            sources = [str(o) for o in graph.objects(concept, DCTERMS.source)]
            if len(sources) != 1:
                failures.append(f"{concept}: {len(sources)} dcterms:source values; exactly 1 required")
            elif not re.search(source_pattern, sources[0]):
                failures.append(f"{concept}: dcterms:source {sources[0]} does not match {source_pattern}")
        if derived_pattern is not None:
            derived = [str(o) for o in graph.objects(concept, PROV_WAS_DERIVED_FROM)]
            if len(derived) != 1:
                failures.append(f"{concept}: {len(derived)} prov:wasDerivedFrom values; exactly 1 required")
            elif not re.search(derived_pattern, derived[0]):
                failures.append(f"{concept}: prov:wasDerivedFrom {derived[0]} does not match {derived_pattern}")
    return failures, f"{len(glossary)} glossary concepts under {spec['iriPrefix']}"


def skos_glossary_single_commit(ctx: Context) -> Outcome:
    spec = ctx.config["skos"]["glossary"]
    source = spec["source"]
    graph = ctx.vocabulary()
    commits: set[str] = set()
    failures: list[str] = []
    for concept in _glossary(graph, spec["iriPrefix"]):
        for value in graph.objects(concept, DCTERMS.source):
            found = re.search(source["commitPattern"], str(value))
            if found:
                commits.add(found.group(1))
            else:
                failures.append(f"{concept}: dcterms:source {value} carries no commit ({source['commitPattern']})")
    if source.get("singleCommit", False) and len(commits) != 1:
        failures.append(f"{len(commits)} distinct commits: {', '.join(sorted(commits)) or 'none'}; exactly 1 required")
    if "expectedCommit" in source and commits - {source["expectedCommit"]}:
        failures.append(f"commits {', '.join(sorted(commits))} differ from the expected {source['expectedCommit']}")
    return failures, f"commits: {', '.join(sorted(commits)) or 'none'}"


def skos_projection_agrees(ctx: Context) -> Outcome:
    spec = ctx.config["skos"]["projection"]
    graph = ctx.vocabulary()
    glossary = _glossary(graph, ctx.config["skos"]["glossary"]["iriPrefix"])
    projection = ctx.json(spec["file"])
    prefix_length = spec.get("definitionPrefixLength", 80)
    nodes = projection.get("@graph") if isinstance(projection, dict) else None
    if not isinstance(nodes, list):
        return [f"{spec['file']}: no @graph array"], "projection"
    by_id = {node["@id"]: node for node in nodes if isinstance(node, dict) and "@id" in node}
    failures: list[str] = []
    for concept in glossary:
        node = by_id.get(str(concept))
        if node is None:
            failures.append(f"{concept}: missing from the projection")
            continue
        for key, predicate in (("skos:prefLabel", SKOS.prefLabel), ("skos:notation", SKOS.notation)):
            expected = graph.value(concept, predicate)
            actual = node.get(key)
            if (None if expected is None else str(expected)) != actual:
                failures.append(f"{concept}: {key} is {actual!r} in the projection, {_show(expected)!r} in the vocabulary")
        opening = str(graph.value(concept, SKOS.definition))
        definition = node.get("skos:definition")
        if not (isinstance(definition, str) and definition.startswith(opening[:prefix_length])):
            failures.append(f"{concept}: definition drifted from the projection")
        related = {r["@id"] for r in node.get("skos:related", []) if isinstance(r, dict) and "@id" in r}
        missing = related - {str(o) for o in graph.objects(concept, SKOS.related)}
        for target in sorted(missing):
            failures.append(f"{concept}: projection relates {target}, the vocabulary does not")
    if "countPointer" in spec:
        try:
            count = jsonpatch.get(projection, spec["countPointer"])
        except jsonpatch.PatchError as error:
            failures.append(f"{spec['file']}: {error}")
        else:
            if count != len(glossary):
                failures.append(f"{spec['file']}: {spec['countPointer']} is {count!r}; {len(glossary)} glossary concepts")
    return failures, f"{len(glossary)} glossary concepts against {spec['file']}"


def skos_related_symmetric(ctx: Context) -> Outcome:
    graph = ctx.vocabulary()
    scheme = _iri(ctx.config["skos"]["scheme"])
    failures: list[str] = []
    pairs = sorted(graph.subject_objects(SKOS.related))
    for a, b in pairs:
        if (b, SKOS.related, a) not in graph:
            failures.append(f"{a} skos:related {b} is not symmetric")
        if (b, SKOS.inScheme, scheme) not in graph:
            failures.append(f"{a} skos:related {b}, which is not skos:inScheme {scheme}")
    return failures, f"{len(pairs)} skos:related edges"


# ---------------------------------------------------------------------------
# Closed sets
# ---------------------------------------------------------------------------


def closed_sets_match(ctx: Context) -> Outcome:
    graph = ctx.vocabulary()
    failures: list[str] = []
    for spec in ctx.config["closedSets"]:
        cls = _iri(spec["class"])
        enumerated: set = set()
        for equivalent in graph.objects(cls, OWL.equivalentClass):
            for one_of in graph.objects(equivalent, OWL.oneOf):
                enumerated |= set(Collection(graph, one_of))
        if len(enumerated) != spec["size"]:
            failures.append(f"{cls}: owl:oneOf lists {len(enumerated)} individuals; exactly {spec['size']} expected")
        instances = set(graph.subjects(RDF.type, cls))
        for extra in sorted(instances - enumerated):
            failures.append(f"{cls}: {extra} claims membership but is not in owl:oneOf")
        for absent in sorted(enumerated - instances):
            failures.append(f"{cls}: {absent} is in owl:oneOf but is not typed {cls}")
    return failures, f"{len(ctx.config['closedSets'])} closed sets"


def closed_sets_labels(ctx: Context) -> Outcome:
    graph = ctx.vocabulary()
    failures: list[str] = []
    for spec in ctx.config["labelSets"]:
        cls = _iri(spec["class"])
        labels = {str(graph.value(s, SKOS.prefLabel)) for s in graph.subjects(RDF.type, cls)}
        expected = set(spec["labels"])
        for label in sorted(expected - labels):
            failures.append(f"{cls}: label {label!r} expected but absent")
        for label in sorted(labels - expected):
            failures.append(f"{cls}: label {label!r} present but not expected")
    return failures, f"{len(ctx.config['labelSets'])} label sets"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def records_kinds(ctx: Context) -> Outcome:
    spec = ctx.config["records"]
    found = set(ctx.match(spec["glob"]))
    expected = set(spec["kinds"])
    failures = [f"{p}: record has no declared kind" for p in sorted(found - expected)]
    failures += [f"{p}: declared kind but no record file" for p in sorted(expected - found)]
    return failures, f"{len(found)} record files, {len(expected)} declared kinds"


def records_context_valid(ctx: Context) -> Outcome:
    """The context must itself process: expanding a probe document uses every term definition."""
    from pyld import jsonld

    from .context import _refuse_remote

    context = ctx.record_context()
    try:
        jsonld.expand(
            {"@context": context, "@id": "urn:x-conformance:probe"},
            {"documentLoader": _refuse_remote},
        )
    except jsonld.JsonLdError as error:
        return [f"{ctx.config['records']['context']}: {jsonld_message(error)}"], "context"
    return [], f"{ctx.config['records']['context']} processes"


def records_convert(ctx: Context) -> Outcome:
    spec = ctx.config["records"]
    namespace = spec.get("topTypeNamespace") or ctx.config.get("vocabulary", {}).get("namespace")
    paths = ctx.match(spec["glob"])
    failures: list[str] = []
    for path in paths:
        try:
            graph = ctx.record_graph(path, ctx.record_json(path))
        except FileProblem as error:
            failures.append(str(error))
            continue
        for term in ctx.unmapped(graph):
            failures.append(f"{path}: JSON key or type the context does not map: {term}")
        top = _iri(f"{spec['baseIri']}{PurePosixPath(path).stem}")
        if path in spec.get("kinds", {}) and (top, RDF.type, _iri(spec["kinds"][path])) not in graph:
            failures.append(f"{path}: top node {top} is not typed {spec['kinds'][path]}")
        if namespace:
            for kind in graph.objects(top, RDF.type):
                if not str(kind).startswith(namespace):
                    failures.append(f"{path}: top type {kind} is outside {namespace}")
    return failures, f"{len(paths)} records converted through {spec['context']}"


def records_conform(ctx: Context) -> Outcome:
    spec = ctx.config["records"]
    shapes = ctx.graph(spec["shapes"])
    ontology = ctx.ontology()
    paths = ctx.match(spec["glob"])
    failures: list[str] = []
    for path in paths:
        try:
            conforms, lines = ctx.shacl(ctx.record_graph(path, ctx.record_json(path)), shapes, ontology)
        except FileProblem as error:
            failures.append(str(error))
            continue
        if not conforms:
            failures.append(f"{path} does not conform to {spec['shapes']}: " + "; ".join(lines[:5]))
    return failures, f"{len(paths)} records judged by {spec['shapes']}"


def records_graph_expectations(ctx: Context) -> Outcome:
    failures: list[str] = []
    expectations = ctx.config["records"]["graphExpectations"]
    for item in expectations:
        try:
            graph = ctx.record_graph(item["record"], ctx.record_json(item["record"]))
        except FileProblem as error:
            failures.append(f"{item['id']}: {error}")
            continue
        predicate = _iri(item["predicate"])
        objects = set(graph.objects(None, predicate))
        if "count" in item and len(objects) != item["count"]:
            failures.append(f"{item['id']}: {len(objects)} objects of {predicate}; exactly {item['count']} expected")
        if "objectTypes" in item:
            kinds = {str(t) for o in objects for t in graph.objects(o, RDF.type)}
            expected = set(item["objectTypes"])
            for kind in sorted(expected - kinds):
                failures.append(f"{item['id']}: type {kind} expected but absent")
            for kind in sorted(kinds - expected):
                failures.append(f"{item['id']}: type {kind} present but not expected")
    return failures, f"{len(expectations)} graph expectations"


# ---------------------------------------------------------------------------
# Registry: (id, wanted(config), function), in report order.
# ---------------------------------------------------------------------------

Check = tuple[str, Callable[[dict], bool], Callable[[Context], Outcome]]

REGISTRY: list[Check] = [
    ("parsing.rdf-files-parse", lambda c: "rdf" in c.get("parse", {}), parsing_rdf),
    ("parsing.json-files-parse", lambda c: "json" in c.get("parse", {}), parsing_json),
    ("rooting.ontology-metadata", lambda c: "ontology" in c.get("vocabulary", {}), rooting_ontology_metadata),
    ("rooting.root-declares-namespace", lambda c: "iri" in c.get("root", {}) or "recordClass" in c.get("root", {}), rooting_root_declares),
    ("rooting.conforms-to-root-shapes", lambda c: bool(c.get("root", {}).get("validate")), rooting_root_shapes),
    ("rooting.record-kinds-subclass-root", lambda c: "recordClass" in c.get("root", {}) and bool(c.get("records", {}).get("kinds")), rooting_kinds_subclass_root),
    ("rooting.terms-documented", lambda c: "namespace" in c.get("vocabulary", {}), rooting_terms_documented),
    ("skos.scheme", lambda c: "skos" in c, skos_scheme),
    ("skos.concepts-complete", lambda c: "skos" in c, skos_concepts_complete),
    ("skos.collections", lambda c: "skos" in c, skos_collections),
    ("skos.concept-collection-disjoint", lambda c: "skos" in c, skos_disjoint),
    ("skos.glossary-sources", lambda c: "glossary" in c.get("skos", {}), skos_glossary_sources),
    ("skos.glossary-single-commit", lambda c: "commitPattern" in c.get("skos", {}).get("glossary", {}).get("source", {}), skos_glossary_single_commit),
    ("skos.projection-agrees", lambda c: "projection" in c.get("skos", {}), skos_projection_agrees),
    ("skos.related-symmetric", lambda c: "skos" in c, skos_related_symmetric),
    ("closed-sets.enumerations-match-instances", lambda c: bool(c.get("closedSets")), closed_sets_match),
    ("closed-sets.labels", lambda c: bool(c.get("labelSets")), closed_sets_labels),
    ("records.kinds-declared", lambda c: "kinds" in c.get("records", {}), records_kinds),
    ("records.context-processes", lambda c: "records" in c, records_context_valid),
    ("records.convert-with-no-unmapped-key", lambda c: "records" in c, records_convert),
    ("records.conform-to-shapes", lambda c: "records" in c, records_conform),
    ("records.graph-expectations", lambda c: bool(c.get("records", {}).get("graphExpectations")), records_graph_expectations),
]
