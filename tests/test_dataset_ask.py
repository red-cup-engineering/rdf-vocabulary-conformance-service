import hashlib
import importlib.util
import unittest
from unittest.mock import patch

from rdf_vocabulary_conformance import ConfigError, check_vocabulary_conformance, required_patterns

ASK = b"ASK { GRAPH <urn:test:authored> { <urn:test:act> <urn:test:maps-to> <urn:test:diagram> } }"
TRIPLE = b"<urn:test:act> <urn:test:maps-to> <urn:test:diagram>"
DATA_NAME = "derived/graph.nq"
QUERY_NAME = "source/query.rq"


def config(expect=True):
    return {"formatVersion": 1, "dataset": {
        "file": DATA_NAME, "ask": [{"id": "authored", "query": QUERY_NAME, "expect": expect}],
    }}


def report(data, query=ASK, expect=True):
    return check_vocabulary_conformance(config(expect), {DATA_NAME: data, QUERY_NAME: query})


class DatasetAsk(unittest.TestCase):
    def test_graph_identity_and_receipt_are_observed(self):
        good = TRIPLE + b" <urn:test:authored> .\n"
        wrong = TRIPLE + b" <urn:test:navigation> .\n"
        default = TRIPLE + b" .\n"
        self.assertTrue(report(good)["ok"])
        self.assertFalse(report(wrong)["ok"])
        self.assertFalse(report(default)["ok"])
        self.assertTrue(report(wrong, expect=False)["ok"])
        self.assertEqual(required_patterns(config()), [DATA_NAME, QUERY_NAME])
        self.assertEqual(report(good)["inputs"]["files"], [
            {"path": DATA_NAME, "sha256": hashlib.sha256(good).hexdigest()},
            {"path": QUERY_NAME, "sha256": hashlib.sha256(ASK).hexdigest()},
        ])
        self.assertEqual(report(good), report(good))

    def test_trig_keeps_the_same_named_graph(self):
        selected = config()
        selected["dataset"]["file"] = "derived/graph.trig"
        data = b"<urn:test:authored> { " + TRIPLE + b" . }\n"
        self.assertTrue(check_vocabulary_conformance(selected, {
            "derived/graph.trig": data, QUERY_NAME: ASK,
        })["ok"])

    def test_out_of_profile_queries_never_evaluate(self):
        candidates = [
            b"SELECT * WHERE { ?subject ?predicate ?object }",
            b"ASK FROM NAMED <urn:outside:dataset> WHERE { GRAPH ?name { ?s ?p ?o } }",
            b"ASK WHERE { FILTER NOT EXISTS { SERVICE <urn:outside:service> { ?s ?p ?o } } }",
            b"ASK WHERE { FILTER NOT EXISTS { ?s <urn:test:predicate>+ ?o } }",
            b"ASK WHERE { FILTER(RAND() > 0.5) }",
            b"ASK WHERE { FILTER(<urn:test:not-a-local-operator>(?value)) }",
            b"this is not a query",
        ]
        with patch("rdflib.Dataset.query", side_effect=AssertionError("must refuse before evaluation")) as evaluator:
            for raw in candidates:
                with self.subTest(query=raw):
                    self.assertFalse(report(TRIPLE+b" .\n", raw, expect=False)["ok"])
            evaluator.assert_not_called()

    def test_missing_input_is_not_a_false_answer(self):
        for files in ({}, {DATA_NAME: TRIPLE+b" .\n"}, {QUERY_NAME: ASK}):
            self.assertFalse(check_vocabulary_conformance(config(expect=False), files)["ok"])

    def test_no_glob_or_duplicate_case_ids(self):
        c = config()
        c["dataset"]["ask"][0]["query"] = "source/*.rq"
        with self.assertRaises(ConfigError):
            check_vocabulary_conformance(c, {})
        c = config()
        c["dataset"]["ask"] *= 2
        with self.assertRaises(ConfigError):
            check_vocabulary_conformance(c, {})



class DatasetShapes(unittest.TestCase):
    shapes_path = "data/shape.ttl"
    shape = b"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        <urn:test:shape> a sh:NodeShape ;
          sh:targetNode <urn:test:act> ;
          sh:property [ sh:path <urn:test:maps-to> ; sh:minCount 1 ; sh:class <urn:test:Diagram> ] .
    """
    type_quad = b"<urn:test:diagram> a <urn:test:Diagram>"

    def result(self, relation_graph, include_type=True, shape=None):
        config_with_shapes = config()
        config_with_shapes["dataset"]["shapes"] = [self.shapes_path]
        ttl = f"<urn:test:{relation_graph}> {{ ".encode() + TRIPLE + b" . }\n"
        if include_type:
            ttl += b"<urn:test:reviewed> { " + self.type_quad + b" . }\n"
        config_with_shapes["dataset"]["file"] = "derived/graph.trig"
        return check_vocabulary_conformance(config_with_shapes, {
            "derived/graph.trig": ttl, QUERY_NAME: ASK, self.shapes_path: self.shape if shape is None else shape,
        })

    @unittest.skipUnless(importlib.util.find_spec("pyshacl"), "requires full validation install")
    def test_independent_named_ask_and_union_shape_verdicts(self):
        verdict = lambda row: {c["id"]: c["ok"] for c in row["checks"]}
        confirms = self.result("authored")
        self.assertTrue(confirms["ok"])
        self.assertEqual(verdict(confirms), {"dataset.shacl": True, "dataset.ask": True})
        self.assertIn(self.shapes_path, [f["path"] for f in confirms["inputs"]["files"]])
        self.assertEqual(verdict(self.result("navigation")), {"dataset.shacl": True, "dataset.ask": False})
        self.assertEqual(verdict(self.result("authored", include_type=False)),
                         {"dataset.shacl": False, "dataset.ask": True})

    def test_custom_validator_never_reaches_shacl_engine(self):
        shape = self.shape + b'<urn:test:shape> <http://www.w3.org/ns/shacl#sparql> <urn:test:constraint> .'
        with patch("rdf_vocabulary_conformance.context.Context.shacl") as evaluate:
            result = self.result("authored", shape=shape)
            evaluate.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("outside the SHACL Core profile", result["checks"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
