"""The faces: CLI (exit codes, filesystem edge), NDJSON, packaging metadata, and parity with the paper."""

import base64
import contextlib
import importlib
import io
import json
import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

import rdf_vocabulary_conformance as package
from rdf_vocabulary_conformance import cli, ndjson
from rdf_vocabulary_conformance.collect import collect_files
from rdf_vocabulary_conformance.core import check_vocabulary_conformance, dumps

from . import fixtures as f

REPO = Path(__file__).resolve().parent.parent


def write_project(root: Path, files=None, config=None, config_path="onto/conformance.json"):
    for path, content in (files or f.files()).items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (root / config_path).parent.mkdir(parents=True, exist_ok=True)
    (root / config_path).write_text(json.dumps(config or f.config()), encoding="utf-8")


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rvc-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_exit_0_on_a_conforming_project_and_the_report_is_the_cores(self):
        write_project(self.tmp)
        code, out, err = run_cli("--root", str(self.tmp), "--config", "onto/conformance.json")
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertTrue(report["ok"])
        self.assertEqual(report, check_vocabulary_conformance(f.config(), f.files()))
        self.assertIn("ok: 22/22 checks, 4/4 shape tests", err)

    def test_default_config_path(self):
        write_project(self.tmp, config_path="ontology/conformance.json")
        code, _, err = run_cli("--root", str(self.tmp))
        self.assertEqual(code, 0, err)

    def test_exit_1_and_a_stderr_line_per_failure(self):
        files = f.files()
        files["data/a.json"] = f.dumps({**f.RECORD, "count": -5})
        write_project(self.tmp, files)
        code, out, err = run_cli("--root", str(self.tmp), "--config", "onto/conformance.json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])
        self.assertIn("FAIL records.conform-to-shapes", err)
        self.assertIn("FAILED:", err)

    def test_exit_2_when_the_config_is_missing(self):
        code, out, _ = run_cli("--root", str(self.tmp))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["refusal"]["reason"], "ConfigNotFound")

    def test_exit_2_when_the_config_is_not_json(self):
        write_project(self.tmp)
        (self.tmp / "onto/conformance.json").write_text("{", encoding="utf-8")
        code, out, _ = run_cli("--root", str(self.tmp), "--config", "onto/conformance.json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["refusal"]["reason"], "ConfigUnreadable")

    def test_exit_2_when_the_config_is_not_admissible(self):
        config = f.config()
        config["surprise"] = 1
        write_project(self.tmp, config=config)
        code, out, err = run_cli("--root", str(self.tmp), "--config", "onto/conformance.json")
        self.assertEqual(code, 2)
        refusal = json.loads(out)["refusal"]
        self.assertEqual(refusal["reason"], "InvalidConfig")
        self.assertTrue(refusal["errors"])
        self.assertIn("bad configuration", err)

    def test_collect_reads_only_what_the_patterns_name_and_stays_inside_the_root(self):
        write_project(self.tmp)
        (self.tmp / "onto/unrelated.txt").write_text("x")
        outside = Path(tempfile.mkdtemp(prefix="rvc-out-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "secret.ttl").write_text("<a:a> <a:b> <a:c> .")
        try:
            os.symlink(outside / "secret.ttl", self.tmp / "onto/link.ttl")
        except OSError:
            self.skipTest("symlinks unavailable")
        collected = collect_files(self.tmp, ["onto/**/*.ttl", "paper.yaml", "missing.ttl"])
        self.assertEqual(sorted(collected), ["onto/root.shapes.ttl", "onto/root.ttl", "onto/shapes.ttl", "onto/vocab.ttl", "paper.yaml"])


class Ndjson(unittest.TestCase):
    def request(self, config=None, files=None):
        return json.dumps({"config": config or f.config(), "files": {k: v.decode() for k, v in (files or f.files()).items()}})

    def test_one_line_in_one_line_out(self):
        stdin = io.StringIO(self.request() + "\n\n" + self.request(files={**f.files(), "data/a.json": f.dumps({**f.RECORD, "count": -5})}) + "\n")
        stdout = io.StringIO()
        ndjson.run(stdin, stdout)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        first, second = json.loads(lines[0]), json.loads(lines[1])
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertNotIn("refusal", second)  # non-conformant is a report, not a refusal
        self.assertEqual(lines[0], dumps(check_vocabulary_conformance(f.config(), f.files())))

    def test_base64_files(self):
        files = {k: {"base64": base64.b64encode(v).decode()} for k, v in f.files().items()}
        result = ndjson.handle(json.dumps({"config": f.config(), "files": files}))
        self.assertTrue(result["ok"])

    def test_refusals(self):
        cases = {
            "not json": "InvalidRequest",
            json.dumps([1]): "InvalidRequest",
            json.dumps({"config": {}, "files": {}, "extra": 1}): "InvalidRequest",
            json.dumps({"config": {"formatVersion": 1}, "files": {}}): "InvalidConfig",
            json.dumps({"config": f.config(), "files": {"../x": "y"}}): "InvalidRequest",
            json.dumps({"config": f.config(), "files": {"a": 5}}): "InvalidRequest",
            json.dumps({"config": f.config(), "files": {"a": {"base64": "!!"}}}): "InvalidRequest",
        }
        for line, reason in cases.items():
            with self.subTest(line=line[:40]):
                result = ndjson.handle(line)
                self.assertFalse(result["ok"])
                self.assertEqual(result["refusal"]["reason"], reason)

    def test_the_command_streams_stdin_to_stdout(self):
        import subprocess
        import sys

        env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        done = subprocess.run(
            [sys.executable, "-m", "rdf_vocabulary_conformance.ndjson"],
            input=self.request() + "\n", capture_output=True, text=True, env=env, timeout=300,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["ok"])


class Packaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))

    def test_version_agrees_everywhere(self):
        self.assertEqual(self.meta["project"]["version"], package.__version__)
        self.assertEqual(package.__version__, "0.1.0")

    def test_every_dependency_is_exactly_pinned(self):
        deps = self.meta["project"]["dependencies"]
        self.assertTrue(all("==" in d for d in deps), deps)
        names = {d.split("==")[0].lower() for d in deps}
        self.assertTrue({"rdflib", "pyshacl", "pyld"} <= names)
        required = {line.strip() for line in (REPO / "requirements.txt").read_text().splitlines() if "==" in line}
        self.assertEqual(required, set(deps))

    def test_console_scripts_resolve(self):
        for name, target in self.meta["project"]["scripts"].items():
            module, function = target.split(":")
            self.assertTrue(callable(getattr(importlib.import_module(module), function)), name)
        self.assertEqual(set(self.meta["project"]["scripts"]), {"check-vocabulary-conformance", "check-vocabulary-conformance-ndjson"})

    def test_mit_license_and_schema_shipped(self):
        self.assertEqual(self.meta["project"]["license"], "MIT")
        self.assertTrue((REPO / "LICENSE").read_text().startswith("MIT License"))
        self.assertIn("conformance.schema.json", self.meta["tool"]["setuptools"]["package-data"]["rdf_vocabulary_conformance"])

    def test_offer_conforms_to_the_house_shape(self):
        offer = json.loads((REPO / "data/records/offers/check-vocabulary-conformance.json").read_text())
        self.assertEqual(offer["saleStatus"], "held")
        self.assertEqual(offer["seller"]["enterprise"], "engineering")
        self.assertEqual(len(offer["markets"]), 4)
        self.assertEqual(offer["price"], "$0.01")
        self.assertIn("Python", json.dumps(offer))
        # the offer's worked example is the real report for its body
        self.assertEqual(offer["output"]["example"], json.loads(dumps(check_vocabulary_conformance(offer["bodyExample"]["config"], {k: v.encode() for k, v in offer["bodyExample"]["files"].items()}))))


@unittest.skipUnless(os.environ.get("WHITE_REPOPULATION_ROOT"), "set WHITE_REPOPULATION_ROOT to a checkout of the paper repository")
class ParityWithThePaper(unittest.TestCase):
    """The paper's own tests/test_ontology.py, expressed as ontology/conformance.json, run through the CLI."""

    def test_every_check_and_all_shape_tests_behave_as_the_paper_tests_do(self):
        root = os.environ["WHITE_REPOPULATION_ROOT"]
        code, out, err = run_cli("--root", root)
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"], {"checks": 22, "checksFailed": 0, "shapeTests": 21, "shapeTestsFailed": 0})
        mutations = [t for t in report["shapeTests"] if t["expect"] == "violates"]
        self.assertEqual(len(mutations), 15)
        self.assertTrue(all(t["actual"] == "violates" for t in mutations))
        baselines = [t for t in report["shapeTests"] if t["expect"] == "conforms"]
        self.assertEqual(len(baselines), 6)
        self.assertTrue(all(t["actual"] == "conforms" for t in baselines))

    def test_the_run_is_reproducible(self):
        root = os.environ["WHITE_REPOPULATION_ROOT"]
        self.assertEqual(run_cli("--root", root)[1], run_cli("--root", root)[1])


if __name__ == "__main__":
    unittest.main()
