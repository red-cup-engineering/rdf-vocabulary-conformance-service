"""A tiny, complete, conforming vocabulary-and-records project, as a config and a path-to-bytes map."""

from __future__ import annotations

import copy
import json

PREFIXES = """@prefix : <https://example.org/v#> .
@prefix h: <https://example.org/house/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""

SRC = "https://github.com/o/r/blob/{}/g/glossary/terms/{}.md"
COMMIT = "a" * 40

VOCAB = PREFIXES + f"""
<https://example.org/v> a owl:Ontology ;
  owl:versionIRI <https://example.org/v/1.2.3> ;
  owl:versionInfo "1.2.3" ;
  owl:imports <https://example.org/root/> ;
  dcterms:license <https://creativecommons.org/licenses/by/4.0/> .

:Doc a owl:Class ; rdfs:subClassOf <https://example.org/root/Record> ; rdfs:label "Doc" .
:count a owl:DatatypeProperty ; rdfs:comment "How many." .
:Status a owl:Class ; rdfs:label "Status" ;
  owl:equivalentClass [ a owl:Class ; owl:oneOf ( :open :closed ) ] .
:open a :Status ; skos:prefLabel "open" .
:closed a :Status ; skos:prefLabel "closed" .

:terms a skos:ConceptScheme ; skos:hasTopConcept h:one , h:two .
h:one a skos:Concept ; skos:prefLabel "one" ; skos:notation "one" ;
  skos:definition "The first thing in the glossary, defined at some length." ;
  skos:inScheme :terms ; skos:related h:two ;
  dcterms:source <{SRC.format(COMMIT, "one")}> ;
  prov:wasDerivedFrom "swh:1:cnt:{'b' * 40}" .
h:two a skos:Concept ; skos:prefLabel "two" ; skos:notation "two" ;
  skos:definition "The second thing in the glossary, defined at some length." ;
  skos:inScheme :terms ; skos:related h:one ;
  dcterms:source <{SRC.format(COMMIT, "two")}> ;
  prov:wasDerivedFrom "swh:1:cnt:{'c' * 40}" .
:group a skos:Collection ; skos:prefLabel "group" ; skos:definition "Both." ;
  skos:member h:one , h:two .
"""

ROOT = PREFIXES + """
<https://example.org/root/> a owl:Ontology ; dcterms:license <https://creativecommons.org/licenses/by/4.0/> .
<https://example.org/root/Record> a owl:Class .
"""

ROOT_SHAPES = PREFIXES + """
<https://example.org/root/OntologyShape> a sh:NodeShape ; sh:targetClass owl:Ontology ;
  sh:property [ sh:path dcterms:license ; sh:minCount 1 ] .
"""

SHAPES = PREFIXES + """
<https://example.org/shapes/DocShape> a sh:NodeShape ; sh:targetClass :Doc ;
  sh:property [ sh:path :count ; sh:minCount 1 ; sh:datatype xsd:integer ; sh:minInclusive 0 ] .
"""

CONTEXT = {
    "@context": {
        "@version": 1.1,
        "@vocab": "https://example.org/field#",
        "ex": "https://example.org/v#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "kind": "@type",
        "Doc": "ex:Doc",
        "Item": "ex:Item",
        "count": {"@id": "ex:count", "@type": "xsd:integer"},
        "items": "ex:hasItem",
    }
}

PROJECTION = {
    "accounting": {"concepts": 2},
    "@graph": [
        {
            "@id": "https://example.org/house/one",
            "skos:prefLabel": "one",
            "skos:notation": "one",
            "skos:definition": "The first thing in the glossary, defined at some length. And more besides.",
            "skos:related": [{"@id": "https://example.org/house/two"}],
        },
        {
            "@id": "https://example.org/house/two",
            "skos:prefLabel": "two",
            "skos:notation": "two",
            "skos:definition": "The second thing in the glossary, defined at some length. And more besides.",
            "skos:related": [{"@id": "https://example.org/house/one"}],
        },
    ],
}

RECORD = {"kind": "Doc", "count": 3, "items": [{"kind": "Item"}, {"kind": "Item"}]}

CONFIG = {
    "formatVersion": 1,
    "parse": {
        "rdf": {"glob": "onto/**/*.ttl", "minimum": 4},
        "json": {"glob": "onto/*.jsonld", "exactly": ["onto/context.jsonld", "onto/projection.jsonld"]},
    },
    "vocabulary": {
        "files": ["onto/vocab.ttl"],
        "namespace": "https://example.org/v#",
        "declaredTermsAtLeast": 3,
        "ontology": {
            "iri": "https://example.org/v",
            "versionIri": "{iri}/{version}",
            "imports": ["https://example.org/root/"],
            "license": "https://creativecommons.org/licenses/by/4.0/",
            "versionInfoFrom": {"file": "paper.yaml", "pattern": "^version:\\s*[\"']?([^\"'\\s]+)"},
        },
    },
    "root": {
        "ontology": "onto/root.ttl",
        "shapes": "onto/root.shapes.ttl",
        "iri": "https://example.org/root/",
        "recordClass": "https://example.org/root/Record",
        "validate": ["onto/vocab.ttl", "onto/root.ttl", "onto/shapes.ttl"],
    },
    "skos": {
        "scheme": "https://example.org/v#terms",
        "minimumTopConcepts": 2,
        "conceptCount": 2,
        "minimumCollectionMembers": 2,
        "glossary": {
            "iriPrefix": "https://example.org/house/",
            "count": 2,
            "source": {
                "pattern": "^https://github\\.com/o/r/blob/[0-9a-f]{40}/g/glossary/terms/[a-z]+\\.md$",
                "commitPattern": "/blob/([0-9a-f]{40})/",
                "singleCommit": True,
            },
            "derivedFrom": {"pattern": "^swh:1:cnt:[0-9a-f]{40}$"},
        },
        "projection": {"file": "onto/projection.jsonld", "definitionPrefixLength": 40, "countPointer": "/accounting/concepts"},
    },
    "closedSets": [{"class": "https://example.org/v#Status", "size": 2}],
    "labelSets": [{"class": "https://example.org/v#Status", "labels": ["open", "closed"]}],
    "records": {
        "glob": "data/*.json",
        "context": "onto/context.jsonld",
        "shapes": "onto/shapes.ttl",
        "baseIri": "https://example.org/record/",
        "unmappedPrefix": "https://example.org/field#",
        "kinds": {"data/a.json": "https://example.org/v#Doc"},
        "graphExpectations": [
            {
                "id": "a-has-two-items",
                "record": "data/a.json",
                "predicate": "https://example.org/v#hasItem",
                "count": 2,
                "objectTypes": ["https://example.org/v#Item"],
            }
        ],
    },
    "shapeTests": [
        {"id": "unmodified-a", "record": "data/a.json", "mutation": [], "expect": "conforms", "reason": "The untouched record passes."},
        {
            "id": "negative-count",
            "record": "data/a.json",
            "mutation": [{"op": "replace", "path": "/count", "value": -1}],
            "expect": "violates",
            "reason": "A count may not be negative.",
        },
        {
            "id": "missing-count",
            "record": "data/a.json",
            "mutation": [{"op": "remove", "path": "/count"}],
            "expect": "violates",
            "reason": "A count is required.",
        },
        {
            "id": "unmapped-key",
            "record": "data/a.json",
            "mutation": [{"op": "add", "path": "/mystery", "value": 1}],
            "expect": "violates",
            "via": "context",
            "reason": "A key the context does not map is caught by the conversion check.",
        },
    ],
}


def dumps(value) -> bytes:
    return json.dumps(value, indent=2).encode("utf-8")


def files() -> dict[str, bytes]:
    return {
        "paper.yaml": b'title: Tiny\nversion: "1.2.3"\n',
        "onto/vocab.ttl": VOCAB.encode(),
        "onto/root.ttl": ROOT.encode(),
        "onto/root.shapes.ttl": ROOT_SHAPES.encode(),
        "onto/shapes.ttl": SHAPES.encode(),
        "onto/context.jsonld": dumps(CONTEXT),
        "onto/projection.jsonld": dumps(PROJECTION),
        "data/a.json": dumps(RECORD),
    }


def config() -> dict:
    return copy.deepcopy(CONFIG)


def edit(mapping: dict[str, bytes], path: str, old: str, new: str) -> None:
    text = mapping[path].decode("utf-8")
    assert old in text, f"{old!r} not in {path}"
    mapping[path] = text.replace(old, new, 1).encode("utf-8")
