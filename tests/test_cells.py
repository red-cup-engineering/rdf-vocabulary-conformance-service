"""The report as SignCell cells: signcell's wire shape, deterministic, FOUR-valued verdicts."""

import json
import tempfile
import unittest
from pathlib import Path

from rdf_vocabulary_conformance import cli
from rdf_vocabulary_conformance.cells import (
    EXTREMAL, KNOWLEDGE, canonical_json, cell, cells_text, is_cell, ni_of, report_cells, report_subject, verdict_of,
)
from rdf_vocabulary_conformance.core import check_vocabulary_conformance

from . import fixtures as f
from .test_faces import run_cli, write_project


class WireShapeTests(unittest.TestCase):
    def test_ni_of_is_the_house_name_of_bytes(self):
        self.assertEqual(ni_of(b""), "ni:///sha-256;47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU")

    def test_a_cell_has_exactly_signcells_keys_and_dedupes_its_sides(self):
        c = cell("s", "p", "o", [{"a": 1}, {"a": 1}, {"b": 2}], [])
        self.assertEqual(sorted(c), ["against", "for", "object", "predicate", "subject"])
        self.assertEqual(c["for"], [{"a": 1}, {"b": 2}])
        self.assertTrue(is_cell(c))
        self.assertFalse(is_cell({**c, "extra": 1}))
        self.assertFalse(is_cell({**c, "value": "held"}))  # a value is in the knowledge family
        self.assertTrue(is_cell({**c, "value": "stable"}))

    def test_the_verdict_is_the_presence_of_the_two_sides(self):
        self.assertEqual([verdict_of(cell("s", "p", "o", t, d)) for t, d in [([], []), (["x"], []), ([], ["x"]), (["x"], ["x"])]], list(EXTREMAL))
        self.assertEqual(len(KNOWLEDGE), 4)


class ReportCellTests(unittest.TestCase):
    def report(self, **overrides):
        config, files = f.config(), f.files()
        return check_vocabulary_conformance(config, files)

    def test_one_cell_per_check_and_shape_test_all_held_on_a_conforming_project(self):
        report = self.report()
        cells = report_cells(report)
        self.assertEqual(len(cells), len(report["checks"]) + len(report["shapeTests"]))
        self.assertTrue(all(is_cell(c) for c in cells))
        self.assertEqual({verdict_of(c) for c in cells}, {"held"})
        self.assertEqual({c["subject"] for c in cells}, {report_subject(report)})
        self.assertTrue(report_subject(report).startswith("ni:///sha-256;"))
        self.assertTrue(all(c["predicate"].startswith("urn:rdf-vocabulary-conformance:") and c["object"] == "conforms" for c in cells))
        self.assertEqual([c["predicate"] for c in cells], sorted(c["predicate"] for c in cells))

    def test_a_failing_check_is_denied_with_its_failures_as_witnesses_against(self):
        report = self.report()
        report["checks"][0] = {**report["checks"][0], "ok": False, "failures": ["one", "two"], "detail": "one; two"}
        denied = [c for c in report_cells(report) if verdict_of(c) == "denied"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["for"], [])
        self.assertEqual(denied[0]["against"], [{"kind": "check-failed", "failure": "one"}, {"kind": "check-failed", "failure": "two"}])

    def test_a_shape_test_that_could_not_run_is_unknown(self):
        report = self.report()
        report["shapeTests"][0] = {**report["shapeTests"][0], "ok": False, "actual": "error", "detail": "boom"}
        unknown = [c for c in report_cells(report) if verdict_of(c) == "unknown"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual((unknown[0]["for"], unknown[0]["against"]), ([], []))

    def test_the_same_inputs_give_the_same_bytes(self):
        self.assertEqual(cells_text(report_cells(self.report())), cells_text(report_cells(self.report())))
        self.assertTrue(cells_text(report_cells(self.report())).endswith("]\n"))


class CliTests(unittest.TestCase):
    def test_cells_flag_writes_the_cells_beside_the_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root)
            code, out, _ = run_cli("--root", str(root), "--config", "onto/conformance.json", "--cells", "build/conformance.cells.json")
            self.assertEqual(code, 0)
            cells = json.loads((root / "build/conformance.cells.json").read_text(encoding="utf-8"))
            self.assertTrue(all(is_cell(c) for c in cells))
            self.assertEqual(len(cells), json.loads(out)["summary"]["checks"] + json.loads(out)["summary"]["shapeTests"])
            self.assertEqual((root / "build/conformance.cells.json").read_text(encoding="utf-8"), cells_text(cells))
            self.assertEqual(canonical_json(cells) + "\n", cells_text(cells))


if __name__ == "__main__":
    unittest.main()
