"""Receive the same wheel in lean and full environments; only fixtures are synthetic."""
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rdf_vocabulary_conformance import check_vocabulary_conformance

DATASET, SHAPES, QUERY = "dataset.nq", "shapes.ttl", "query.rq"
RECORD, CONTEXT = "records/item.json", "context.json"
ASK = b"ASK { GRAPH <urn:test:data> { <urn:test:item> <urn:test:label> 'observed' } }"
QUAD = b'<urn:test:item> <urn:test:label> "observed" <urn:test:data> .\n'
SHAPES_TTL = b"""
    @prefix sh: <http://www.w3.org/ns/shacl#> .
    [] a sh:NodeShape ;
       sh:targetSubjectsOf <urn:test:label>, <urn:test:ref> ;
       sh:property [ sh:path <urn:test:label> ; sh:minCount 1 ] .
"""
DATASET_CONFIG = {"formatVersion": 1, "dataset": {
    "file": DATASET, "shapes": [SHAPES], "ask": [{"id": "local", "query": QUERY, "expect": True}],
}}
DATASET_FILES = {DATASET: QUAD, SHAPES: SHAPES_TTL, QUERY: ASK}
RECORD_CONFIG = {"formatVersion": 1, "records": {
    "glob": "records/*.json", "context": CONTEXT, "shapes": SHAPES,
    "baseIri": "https://example.invalid/id/", "unmappedPrefix": "urn:test:unmapped:",
}, "shapeTests": [
    {"id": "good", "record": RECORD, "mutation": [], "expect": "conforms", "reason": "label is present"},
    {"id": "bad", "record": RECORD, "mutation": [{"op": "remove", "path": "/label"}],
     "expect": "violates", "reason": "label is required"},
]}
RECORD_FILES = {
    RECORD: b'{"label":"observed","ref":"https://example.invalid/other"}',
    CONTEXT: b'{"@context":{"@vocab":"urn:test:unmapped:","label":"urn:test:label",'
             b'"ref":{"@id":"urn:test:ref","@type":"@id"}}}',
    SHAPES: SHAPES_TTL,
}


def check_map(report):
    return {check["id"]: check for check in report["checks"]}


def has_module(module):
    return importlib.util.find_spec(module) is not None


class ValidationInstall(unittest.TestCase):
    def test_install_mode_matches_the_test_environment(self):
        mode = os.environ.get("RDF_VOCABULARY_TEST_INSTALL")
        if mode is None:
            self.skipTest("expectation is set by the installed-wheel test invocation")
        self.assertIn(mode, ("lean", "full"))
        optional_modules = ("pyld", "pyshacl", "owlrl", "prettytable", "wcwidth",
                            "packaging", "lxml", "html5rdf", "frozendict", "cachetools")
        for name in optional_modules:
            with self.subTest(name=name):
                self.assertEqual(has_module(name), mode == "full")
        self.assertEqual(importlib.metadata.version("rdflib"), "7.6.0")
        self.assertEqual(importlib.metadata.version("pyparsing"), "3.3.3")
        imported = subprocess.run(
            [sys.executable, "-c",
             "import sys; from rdf_vocabulary_conformance import prepare_local_select; "
             "prepare_local_select(b'SELECT ?s WHERE { VALUES ?s { <urn:test:s> } }'); "
             "assert not {'pyld', 'pyshacl'} & sys.modules.keys()"],
            capture_output=True, check=False, text=True,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)

    def test_ask_is_judged_even_if_shacl_extra_is_missing(self):
        report = check_vocabulary_conformance(DATASET_CONFIG, DATASET_FILES)
        checks = check_map(report)
        self.assertTrue(checks["dataset.ask"]["ok"])
        if has_module("pyshacl"):
            self.assertTrue(checks["dataset.shacl"]["ok"], report)
            self.assertTrue(report["ok"])
        else:
            self.assertFalse(report["ok"])
            self.assertIn("SHACL validation (PySHACL) requires the 'conformance' extra",
                          checks["dataset.shacl"]["detail"])

    def test_record_conversion_and_declared_shape_tests_never_pass_without_engines(self):
        report = check_vocabulary_conformance(RECORD_CONFIG, RECORD_FILES)
        checks = check_map(report)
        tests = report["shapeTests"]
        if has_module("pyld") and has_module("pyshacl"):
            self.assertTrue(report["ok"], report)
            self.assertEqual([test["actual"] for test in tests], ["conforms", "violates"])
            self.assertIn("MinCountConstraintComponent", tests[1]["detail"])
        else:
            self.assertFalse(report["ok"])
            if not has_module("pyld"):
                self.assertIn("JSON-LD validation (PyLD) requires the 'conformance' extra",
                              checks["records.context-processes"]["detail"])
                self.assertFalse(checks["records.convert-with-no-unmapped-key"]["ok"])
            self.assertFalse(checks["records.conform-to-shapes"]["ok"])
            self.assertEqual([test["actual"] for test in tests], ["error", "error"])
            self.assertTrue(all(not test["ok"] and "'conformance' extra" in test["detail"] for test in tests))

    def test_full_jsonld_still_refuses_a_remote_context(self):
        if not has_module("pyld"):
            self.skipTest("requires full validation install")
        files = {**RECORD_FILES, CONTEXT: b'{"@context":"https://example.invalid/context"}'}
        checks = check_map(check_vocabulary_conformance(RECORD_CONFIG, files))
        self.assertFalse(checks["records.context-processes"]["ok"])
        self.assertIn("remote document 'https://example.invalid/context' refused: contexts must be inline",
                      checks["records.convert-with-no-unmapped-key"]["detail"])

    def test_validation_cli_keeps_json_report_and_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for key, value in {**DATASET_FILES, "conformance.json": json.dumps(DATASET_CONFIG).encode()}.items():
                (root / key).write_bytes(value)
            result = subprocess.run(
                [sys.executable, "-m", "rdf_vocabulary_conformance",
                 "--root", str(root), "--config", "conformance.json"],
                cwd=root, capture_output=True, check=False, text=True,
            )
        expected = has_module("pyshacl")
        self.assertEqual(result.returncode, 0 if expected else 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["ok"], expected)
        if not expected:
            self.assertIn("FAIL dataset.shacl:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
