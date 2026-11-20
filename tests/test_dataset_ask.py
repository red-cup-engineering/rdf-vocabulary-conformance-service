import hashlib
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


if __name__ == "__main__":
    unittest.main()
