import hashlib
import unittest

from rdflib import Dataset, Graph, URIRef

from rdf_vocabulary_conformance import (
    ConfigError, check_vocabulary_conformance, matches_named_graph, required_patterns,
)

NQ = "build/graph.nq"
TTL = "data/reviewed.ttl"
IRI = "urn:test:reviewed"
BASE = "https://example.invalid/papers/"
TURTLE = b'<urn:test:term> <urn:test:source> [ <urn:test:document> <source> ] .'
QUADS = b"""_:actual <urn:test:document> <https://example.invalid/papers/source> <urn:test:reviewed> .
<urn:test:term> <urn:test:source> _:actual <urn:test:reviewed> .
"""


def config():
    return {"formatVersion": 1, "dataset": {"file": NQ, "graphMatches": [
        {"id": "reviewed", "graph": IRI, "reference": TTL, "baseIri": BASE},
    ]}}


def report(nq=QUADS, ttl=TURTLE, cfg=None):
    files = {NQ: nq}
    if ttl is not None:
        files[TTL] = ttl
    return check_vocabulary_conformance(config() if cfg is None else cfg, files)


class GraphReferenceReceiving(unittest.TestCase):
    def test_compares_named_graph_and_records_exact_inputs(self):
        received = report()
        self.assertTrue(received["ok"])
        self.assertEqual([c["id"] for c in received["checks"]], ["dataset.graphMatches"])
        self.assertEqual(required_patterns(config()), [NQ, TTL])
        self.assertEqual(received["inputs"]["files"], [
            {"path": NQ, "sha256": hashlib.sha256(QUADS).hexdigest()},
            {"path": TTL, "sha256": hashlib.sha256(TURTLE).hexdigest()},
        ])

    def test_same_union_wrong_graph_and_same_size_changed_graph_fail(self):
        for nq in (QUADS.replace(IRI.encode(), b"urn:test:navigation"),
                   QUADS.replace(b"source>", b"not-source>", 1),
                   QUADS.replace(b" <urn:test:reviewed>", b""),
                   b""):
            with self.subTest(nq=nq):
                self.assertFalse(report(nq=nq)["ok"])

    def test_missing_ref_invalid_turtle_and_mutually_empty_graphs_fail(self):
        for ttl in (None, b"not Turtle", b"", b"\xff"):
            with self.subTest(ttl=ttl):
                self.assertFalse(report(nq=b"", ttl=ttl)["ok"])

    def test_rejects_reference_globs_traversal_relative_graph_and_duplicate_ids(self):
        for field, value in (("reference", "data/*.ttl"),
                             ("reference", "../reviewed.ttl"),
                             ("graph", "graphs/reviewed"),
                             ("reference", "https://example.invalid/reviewed.ttl")):
            with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                selected = config()
                selected["dataset"]["graphMatches"][0][field] = value
                report(cfg=selected)
        bad = config()
        bad["dataset"]["graphMatches"] *= 2
        with self.assertRaises(ConfigError):
            report(cfg=bad)

    def test_default_source_base_is_deterministic(self):
        selected = config()
        selected["dataset"]["graphMatches"][0].pop("baseIri")
        nq = QUADS.replace(BASE.encode(), b"https://rdf-reference.invalid/data/")
        self.assertTrue(report(nq=nq, cfg=selected)["ok"])
        self.assertFalse(report(cfg=selected)["ok"])

    def test_public_comparison_does_not_register_a_missing_context(self):
        data = Dataset(default_union=False).parse(data=QUADS, format="nquads")
        source = Graph().parse(data=TURTLE, format="turtle", publicID=BASE)
        before = set(g.identifier for g in data.graphs())
        self.assertTrue(matches_named_graph(data, URIRef(IRI), source))
        self.assertFalse(matches_named_graph(data, URIRef("urn:test:missing"), source))
        self.assertFalse(matches_named_graph(data, data.default_graph.identifier, source))
        self.assertEqual(set(g.identifier for g in data.graphs()), before)


if __name__ == "__main__":
    unittest.main()
