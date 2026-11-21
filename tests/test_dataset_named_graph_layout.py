import unittest

from rdf_vocabulary_conformance import ConfigError, check_vocabulary_conformance

DATA = "build/records.nq"
DOCS, REVIEW, NAV = "urn:test:documents", "urn:test:reviewed", "urn:test:navigation"
TRIPLE = b"<urn:test:item> <urn:test:definition> <urn:test:value>"
OTHER = b"<urn:test:item> <urn:test:document> <urn:test:doc>"


def quad(graph, triple=TRIPLE):
    context = (" <" + graph + ">").encode("ascii") if graph else b""
    return triple + context + b" .\n"


def config(*, required=(DOCS, REVIEW), optional=(NAV,), disjoint=True):
    return {"formatVersion": 1, "dataset": {"file": DATA, "namedGraphLayout": {
        "required": list(required), "optional": list(optional), "disjoint": disjoint,
    }}}


def report(data, **opts):
    return check_vocabulary_conformance(config(**opts), {DATA: data})


class DatasetNamedGraphLayout(unittest.TestCase):
    base = quad(REVIEW) + quad(DOCS, OTHER)

    def test_closed_layout_allows_optional_graph_absence_or_distinct_membership(self):
        for data in (self.base, self.base + quad(NAV, b"<urn:test:x> <urn:test:y> <urn:test:z>")):
            with self.subTest(data=data):
                result = report(data)
                self.assertTrue(result["ok"])
                self.assertEqual([c["id"] for c in result["checks"]], ["dataset.namedGraphLayout"])

    def test_default_extra_missing_and_blank_named_graphs_fail(self):
        for data, message in (
            (self.base + quad(None), "default graph is not empty"),
            (self.base + quad("urn:test:other"), "unexpected named graph"),
            (quad(REVIEW), "required named graph is missing or empty"),
            (self.base + TRIPLE + b" _:context .\n", "do not have an IRI"),
        ):
            with self.subTest(data=data):
                result = report(data)
                self.assertFalse(result["ok"])
                self.assertIn(message, result["checks"][0]["detail"])

    def test_same_union_hidden_copy_fails_only_under_disjointness(self):
        data = self.base + quad(NAV)
        self.assertFalse(report(data)["ok"])
        self.assertTrue(report(data, disjoint=False)["ok"])

    def test_missing_bytes_are_a_failed_check_not_an_empty_dataset(self):
        self.assertFalse(check_vocabulary_conformance(config(), {})["ok"])

    def test_refuses_ambiguous_or_non_iri_policy(self):
        for options in (
            {"required": (DOCS, DOCS)},
            {"required": (DOCS, NAV)},
            {"optional": ("not-an-iri",)},
            {"required": ("urn:x-rdflib:default",)},
        ):
            with self.subTest(options=options), self.assertRaises(ConfigError):
                report(self.base, **options)


if __name__ == "__main__":
    unittest.main()
