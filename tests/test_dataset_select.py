import unittest
from unittest.mock import patch

from rdflib import Dataset, Literal, URIRef
from rdflib.graph import DATASET_DEFAULT_GRAPH_ID

from rdf_vocabulary_conformance import prepare_local_select
from rdf_vocabulary_conformance.dataset_checks import MAX_QUERY_BYTES, _admit_query

MAIN = URIRef("urn:test:main")
META = URIRef("urn:test:meta")
NAME = URIRef("urn:test:name")
CONTEXT = URIRef("urn:test:context")
A = URIRef("urn:test:a")
B = URIRef("urn:test:b")


class DatasetSelect(unittest.TestCase):
    def test_named_projections_join_order_and_conditions(self):
        dataset = Dataset(default_union=False)
        dataset.graph(MAIN).add((A, NAME, Literal("first")))
        dataset.graph(MAIN).add((B, NAME, Literal("second")))
        dataset.graph(META).add((B, CONTEXT, Literal("explicit")))
        dataset.graph(URIRef("urn:test:other")).add((A, NAME, Literal("not visible")))
        dataset.default_graph.add((B, NAME, Literal("not visible either")))
        query = b"""
            SELECT DISTINCT ?name ?context WHERE {
                VALUES ?s { <urn:test:a> <urn:test:b> }
                { GRAPH <urn:test:main> { ?s <urn:test:name> ?name } }
                UNION
                { GRAPH <urn:test:main> { ?s <urn:test:name> ?name } }
                OPTIONAL { GRAPH <urn:test:meta> { ?s <urn:test:context> ?raw } }
                FILTER(BOUND(?name) && (!BOUND(?raw) || sameTerm(?raw, "explicit")))
                BIND(COALESCE(?raw, IF(sameTerm(?s, <urn:test:a>), "fallback", "other")) AS ?context)
            } ORDER BY ?name
        """
        compiled = prepare_local_select(query)
        actual = list(dataset.query(compiled, use_store_provided=False))
        self.assertEqual([(str(row.name), str(row.context)) for row in actual],
                         [("first", "fallback"), ("second", "explicit")])

    def test_values_and_implicit_samples_of_group_keys(self):
        dataset = Dataset(default_union=False)
        dataset.graph(MAIN).add((A, NAME, Literal("group")))
        dataset.graph(MAIN).add((B, NAME, Literal("group")))
        query = b"""
            SELECT ?label (COUNT(DISTINCT ?s) AS ?count) WHERE {
              GRAPH <urn:test:main> { ?s <urn:test:name> ?label }
            } GROUP BY ?label ORDER BY ?label
        """
        result = list(dataset.query(prepare_local_select(query), use_store_provided=False))
        self.assertEqual([(str(row.label), int(row["count"])) for row in result], [("group", 2)])
        values = b"SELECT ?s WHERE { VALUES ?s { <urn:test:a> <urn:test:b> } } ORDER BY ?s"
        self.assertEqual([row.s for row in dataset.query(prepare_local_select(values), use_store_provided=False)], [A, B])

    def test_nonlocal_and_unapproved_algebra_is_refused_before_evaluation(self):
        cases = {
            "ASK": (b"ASK { }", "requires SELECT"),
            "dataset": (b"SELECT * FROM <urn:test:elsewhere> WHERE { }", "no FROM"),
            "named dataset": (b"SELECT * FROM NAMED <urn:test:elsewhere> WHERE { }", "no FROM"),
            "federated pattern": (b"SELECT * WHERE { SERVICE <urn:test:service> { ?s ?p ?o } }", "outside"),
            "property path": (b"SELECT * WHERE { GRAPH <urn:test:main> { ?s <urn:test:name>+ ?o } }", "property paths"),
            "arbitrary graph": (b"SELECT * WHERE { GRAPH ?graph { ?s ?p ?o } }", "constant named graph IRI"),
            "relative graph": (b"SELECT * WHERE { GRAPH <relative> { ?s ?p ?o } }", "constant named graph IRI"),
            "reserved default graph": (
                f"SELECT * WHERE {{ GRAPH <{DATASET_DEFAULT_GRAPH_ID}> {{ ?s ?p ?o }} }}".encode(),
                "constant named graph IRI"),
            "default graph": (b"SELECT * WHERE { ?s ?p ?o }", "require a named GRAPH"),
            "unsecured optional": (b"SELECT * WHERE { GRAPH <urn:test:main> { ?s ?p ?o } OPTIONAL { ?s ?q ?r } }", "require a named GRAPH"),
            "nested SELECT": (b"SELECT * WHERE { { SELECT ?s WHERE { VALUES ?s { <urn:test:a> } } } }", "SubSelect"),
            "authored SAMPLE of a group key": (
                b"SELECT (SAMPLE(?s) AS ?held) WHERE { GRAPH <urn:test:main> { ?s ?p ?o } } GROUP BY ?s",
                "Aggregate_Sample"),
            "implicit SAMPLE outside the group key": (
                b"SELECT ?s (COUNT(?o) AS ?n) WHERE { GRAPH <urn:test:main> { ?s ?p ?o } } GROUP BY ?o",
                "implicit SAMPLE"),
            "unstable function": (b"SELECT (RAND() AS ?n) WHERE { }", "outside"),
            "custom function": (b"SELECT (<urn:test:extension>(1) AS ?n) WHERE { }", "outside"),
            "arbitrary aggregate": (b"SELECT (SUM(?n) AS ?total) WHERE { VALUES ?n { 1 2 } }", "outside"),
        }
        with patch("rdflib.Dataset.query", side_effect=AssertionError("admission is not evaluation")) as evaluate:
            for label, (query, reason) in cases.items():
                with self.subTest(label=label), self.assertRaisesRegex(ValueError, reason):
                    prepare_local_select(query)
            evaluate.assert_not_called()

    def test_input_and_structure_are_bounded(self):
        with self.assertRaises(TypeError):
            prepare_local_select("SELECT * WHERE { }")
        with self.assertRaises(UnicodeDecodeError):
            prepare_local_select(b"\xff")
        with patch("rdf_vocabulary_conformance.dataset_checks.parseQuery") as parse:
            with self.assertRaisesRegex(ValueError, "input byte limit"):
                prepare_local_select(b"x" * (MAX_QUERY_BYTES + 1))
            parse.assert_not_called()
        with patch("rdf_vocabulary_conformance.dataset_checks.MAX_ALGEBRA_NODES", 1):
            with self.assertRaisesRegex(ValueError, "node limit"):
                prepare_local_select(b"SELECT ?s WHERE { VALUES ?s { <urn:test:a> } }")

    def test_ask_profile_does_not_gain_select_only_algebra(self):
        with self.assertRaisesRegex(ValueError, "outside the local ASK profile"):
            _admit_query("ASK { { GRAPH <urn:test:main> { ?s ?p ?o } } UNION { GRAPH <urn:test:meta> { ?s ?p ?o } } }")


if __name__ == "__main__":
    unittest.main()
