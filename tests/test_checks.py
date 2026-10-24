"""One unit test per check: break the tiny project in exactly one way and see which check fails."""

import json
import unittest

from rdf_vocabulary_conformance import check_vocabulary_conformance
from rdf_vocabulary_conformance.checks import REGISTRY

from . import fixtures as f


def run(edit=None, config_edit=None):
    files = f.files()
    config = f.config()
    if edit:
        edit(files)
    if config_edit:
        config_edit(config)
    return check_vocabulary_conformance(config, files)


def failed(report):
    return {c["id"] for c in report["checks"] if not c["ok"]}


class Baseline(unittest.TestCase):
    def test_the_tiny_project_passes_every_check(self):
        report = run()
        self.assertTrue(report["ok"], [c for c in report["checks"] if not c["ok"]])
        self.assertEqual([c["id"] for c in report["checks"]], [r[0] for r in REGISTRY])
        self.assertEqual(report["summary"]["shapeTests"], 4)
        self.assertTrue(all(t["ok"] for t in report["shapeTests"]))

    def test_an_unconfigured_check_is_absent_not_passed(self):
        report = check_vocabulary_conformance({"formatVersion": 1, "parse": {"rdf": {"glob": "onto/**/*.ttl"}}}, f.files())
        self.assertEqual([c["id"] for c in report["checks"]], ["parsing.rdf-files-parse"])
        self.assertEqual(report["shapeTests"], [])


class EachCheckBites(unittest.TestCase):
    def assertBreaks(self, check_id, edit=None, config_edit=None):
        report = run(edit, config_edit)
        self.assertFalse(report["ok"])
        self.assertIn(check_id, failed(report), [c["detail"] for c in report["checks"] if not c["ok"]])

    # Parsing
    def test_unparseable_turtle(self):
        self.assertBreaks("parsing.rdf-files-parse", lambda m: m.update({"onto/extra.ttl": b"this is { not turtle"}))

    def test_empty_graph(self):
        self.assertBreaks("parsing.rdf-files-parse", lambda m: m.update({"onto/extra.ttl": b"# nothing\n"}))

    def test_too_few_rdf_files(self):
        self.assertBreaks("parsing.rdf-files-parse", config_edit=lambda c: c["parse"]["rdf"].update(minimum=5))

    def test_json_that_is_not_an_object(self):
        self.assertBreaks("parsing.json-files-parse", lambda m: m.update({"onto/projection.jsonld": b"[1]"}))

    def test_unexpected_json_file(self):
        self.assertBreaks("parsing.json-files-parse", lambda m: m.update({"onto/extra.jsonld": b"{}"}))

    def test_expected_json_file_missing(self):
        self.assertBreaks("parsing.json-files-parse", lambda m: m.pop("onto/projection.jsonld"))

    # Rooting
    def test_version_info_disagrees_with_the_manifest(self):
        self.assertBreaks("rooting.ontology-metadata", lambda m: f.edit(m, "onto/vocab.ttl", 'owl:versionInfo "1.2.3"', 'owl:versionInfo "9.9.9"'))

    def test_version_iri_wrong(self):
        self.assertBreaks("rooting.ontology-metadata", lambda m: f.edit(m, "onto/vocab.ttl", "v/1.2.3", "v/1.2.4"))

    def test_import_missing(self):
        self.assertBreaks("rooting.ontology-metadata", lambda m: f.edit(m, "onto/vocab.ttl", "owl:imports <https://example.org/root/> ;", ""))

    def test_license_wrong(self):
        self.assertBreaks("rooting.ontology-metadata", lambda m: f.edit(m, "onto/vocab.ttl", "by/4.0", "by-nc/4.0"))

    def test_not_declared_an_ontology(self):
        self.assertBreaks("rooting.ontology-metadata", lambda m: f.edit(m, "onto/vocab.ttl", "<https://example.org/v> a owl:Ontology", "<https://example.org/v> a owl:Thing"))

    def test_manifest_without_a_version(self):
        self.assertBreaks("rooting.ontology-metadata", lambda m: m.update({"paper.yaml": b"title: Tiny\n"}))

    def test_root_does_not_declare_its_record_class(self):
        self.assertBreaks("rooting.root-declares-namespace", lambda m: f.edit(m, "onto/root.ttl", "<https://example.org/root/Record> a owl:Class", "<https://example.org/root/Record> a owl:Thing"))

    def test_vocabulary_breaks_the_root_shapes(self):
        self.assertBreaks("rooting.conforms-to-root-shapes", lambda m: f.edit(m, "onto/vocab.ttl", "  dcterms:license <https://creativecommons.org/licenses/by/4.0/> .", "  rdfs:label \"x\" ."))

    def test_record_kind_is_not_a_subclass_of_the_root_record(self):
        self.assertBreaks("rooting.record-kinds-subclass-root", lambda m: f.edit(m, "onto/vocab.ttl", "rdfs:subClassOf <https://example.org/root/Record> ;", ""))

    def test_undocumented_term(self):
        self.assertBreaks("rooting.terms-documented", lambda m: f.edit(m, "onto/vocab.ttl", 'rdfs:comment "How many."', ""))

    def test_too_few_declared_terms(self):
        self.assertBreaks("rooting.terms-documented", config_edit=lambda c: c["vocabulary"].update(declaredTermsAtLeast=50))

    # SKOS
    def test_scheme_missing(self):
        self.assertBreaks("skos.scheme", lambda m: f.edit(m, "onto/vocab.ttl", ":terms a skos:ConceptScheme", ":terms a owl:Thing"))

    def test_too_few_top_concepts(self):
        self.assertBreaks("skos.scheme", config_edit=lambda c: c["skos"].update(minimumTopConcepts=9))

    def test_concept_without_a_definition(self):
        self.assertBreaks("skos.concepts-complete", lambda m: f.edit(m, "onto/vocab.ttl", 'skos:definition "The second thing in the glossary, defined at some length." ;', ""))

    def test_concept_with_a_blank_definition(self):
        self.assertBreaks("skos.concepts-complete", lambda m: f.edit(m, "onto/vocab.ttl", "The second thing in the glossary, defined at some length.", "   "))

    def test_concept_without_a_label(self):
        self.assertBreaks("skos.concepts-complete", lambda m: f.edit(m, "onto/vocab.ttl", 'skos:prefLabel "two" ;', ""))

    def test_concept_outside_the_scheme(self):
        self.assertBreaks("skos.concepts-complete", lambda m: f.edit(m, "onto/vocab.ttl", "skos:inScheme :terms ; skos:related h:one", "skos:related h:one"))

    def test_concept_count_drifts(self):
        self.assertBreaks("skos.concepts-complete", config_edit=lambda c: c["skos"].update(conceptCount=3))

    def test_collection_too_small(self):
        self.assertBreaks("skos.collections", config_edit=lambda c: c["skos"].update(minimumCollectionMembers=3))

    def test_collection_without_a_label(self):
        self.assertBreaks("skos.collections", lambda m: f.edit(m, "onto/vocab.ttl", ':group a skos:Collection ; skos:prefLabel "group" ;', ":group a skos:Collection ;"))

    def test_concept_that_is_also_a_collection(self):
        self.assertBreaks("skos.concept-collection-disjoint", lambda m: f.edit(m, "onto/vocab.ttl", "h:one a skos:Concept ;", "h:one a skos:Concept, skos:Collection ;"))

    def test_glossary_source_not_pinned(self):
        self.assertBreaks("skos.glossary-sources", lambda m: f.edit(m, "onto/vocab.ttl", "blob/" + "a" * 40 + "/g/glossary/terms/one.md", "blob/main/g/glossary/terms/one.md"))

    def test_glossary_concept_with_two_sources(self):
        self.assertBreaks("skos.glossary-sources", lambda m: f.edit(m, "onto/vocab.ttl", "  skos:inScheme :terms ; skos:related h:two ;", "  skos:inScheme :terms ; skos:related h:two ; dcterms:source <https://example.org/other> ;"))

    def test_glossary_concept_without_derivation(self):
        self.assertBreaks("skos.glossary-sources", lambda m: f.edit(m, "onto/vocab.ttl", 'prov:wasDerivedFrom "swh:1:cnt:' + "c" * 40 + '"', 'prov:wasDerivedFrom "not-a-hash"'))

    def test_glossary_count_drifts(self):
        self.assertBreaks("skos.glossary-sources", config_edit=lambda c: c["skos"]["glossary"].update(count=3))

    def test_glossary_at_two_commits(self):
        self.assertBreaks("skos.glossary-single-commit", lambda m: f.edit(m, "onto/vocab.ttl", "blob/" + "a" * 40 + "/g/glossary/terms/two.md", "blob/" + "d" * 40 + "/g/glossary/terms/two.md"))

    def test_glossary_not_at_the_expected_commit(self):
        self.assertBreaks("skos.glossary-single-commit", config_edit=lambda c: c["skos"]["glossary"]["source"].update(expectedCommit="e" * 40))

    def test_projection_label_drifts(self):
        self.assertBreaks("skos.projection-agrees", lambda m: f.edit(m, "onto/projection.jsonld", '"skos:prefLabel": "one"', '"skos:prefLabel": "uno"'))

    def test_projection_definition_drifts(self):
        self.assertBreaks("skos.projection-agrees", lambda m: f.edit(m, "onto/projection.jsonld", "The first thing in the glossary", "Something else entirely about glossary"))

    def test_projection_misses_a_concept(self):
        def drop(m):
            projection = json.loads(m["onto/projection.jsonld"])
            projection["@graph"].pop()
            m["onto/projection.jsonld"] = f.dumps(projection)

        self.assertBreaks("skos.projection-agrees", drop)

    def test_projection_invents_a_relation(self):
        def invent(m):
            projection = json.loads(m["onto/projection.jsonld"])
            projection["@graph"][0]["skos:related"].append({"@id": "https://example.org/house/zzz"})
            m["onto/projection.jsonld"] = f.dumps(projection)

        self.assertBreaks("skos.projection-agrees", invent)

    def test_projection_count_drifts(self):
        self.assertBreaks("skos.projection-agrees", lambda m: f.edit(m, "onto/projection.jsonld", '"concepts": 2', '"concepts": 3'))

    def test_related_edge_not_symmetric(self):
        self.assertBreaks("skos.related-symmetric", lambda m: f.edit(m, "onto/vocab.ttl", "skos:related h:one ;", ""))

    # Closed sets
    def test_stray_member_of_a_closed_set(self):
        self.assertBreaks(
            "closed-sets.enumerations-match-instances",
            lambda m: m.update({"onto/vocab.ttl": m["onto/vocab.ttl"] + b'\n:stray a :Status ; skos:prefLabel "stray" .\n'}),
        )

    def test_enumerated_individual_not_typed(self):
        self.assertBreaks("closed-sets.enumerations-match-instances", lambda m: f.edit(m, "onto/vocab.ttl", ":closed a :Status ;", ":closed a owl:Thing ;"))

    def test_closed_set_size_drifts(self):
        self.assertBreaks("closed-sets.enumerations-match-instances", config_edit=lambda c: c["closedSets"][0].update(size=3))

    def test_label_set_drifts(self):
        self.assertBreaks("closed-sets.labels", lambda m: f.edit(m, "onto/vocab.ttl", 'skos:prefLabel "closed"', 'skos:prefLabel "shut"'))

    # Records
    def test_record_without_a_declared_kind(self):
        self.assertBreaks("records.kinds-declared", lambda m: m.update({"data/b.json": f.dumps(f.RECORD)}))

    def test_declared_kind_without_a_record(self):
        self.assertBreaks("records.kinds-declared", lambda m: m.pop("data/a.json"))

    def test_context_that_does_not_process(self):
        self.assertBreaks("records.context-processes", lambda m: m.update({"onto/context.jsonld": f.dumps({"@context": {"a": {"@id": 5}}})}))

    def test_unmapped_key(self):
        self.assertBreaks("records.convert-with-no-unmapped-key", lambda m: m.update({"data/a.json": f.dumps({**f.RECORD, "mystery": 1})}))

    def test_unmapped_kind(self):
        self.assertBreaks("records.convert-with-no-unmapped-key", lambda m: m.update({"data/a.json": f.dumps({**f.RECORD, "kind": "Nonesuch"})}))

    def test_top_node_has_the_wrong_kind(self):
        self.assertBreaks("records.convert-with-no-unmapped-key", config_edit=lambda c: c["records"]["kinds"].update({"data/a.json": "https://example.org/v#Status"}))

    def test_top_type_outside_the_namespace(self):
        self.assertBreaks("records.convert-with-no-unmapped-key", config_edit=lambda c: c["records"].update(topTypeNamespace="https://elsewhere.example/"))

    def test_remote_context_is_refused_not_fetched(self):
        report = run(lambda m: m.update({"data/a.json": f.dumps({"@context": "https://example.invalid/ctx", **f.RECORD})}))
        convert = next(c for c in report["checks"] if c["id"] == "records.convert-with-no-unmapped-key")
        self.assertFalse(convert["ok"])
        self.assertIn("loading remote context failed", " ".join(convert["failures"]))

    def test_record_that_is_not_json(self):
        self.assertBreaks("records.convert-with-no-unmapped-key", lambda m: m.update({"data/a.json": b"{"}))

    def test_record_breaking_a_shape(self):
        self.assertBreaks("records.conform-to-shapes", lambda m: m.update({"data/a.json": f.dumps({**f.RECORD, "count": -5})}))

    def test_graph_expectation_count_drifts(self):
        self.assertBreaks("records.graph-expectations", config_edit=lambda c: c["records"]["graphExpectations"][0].update(count=3))

    def test_graph_expectation_types_drift(self):
        self.assertBreaks("records.graph-expectations", config_edit=lambda c: c["records"]["graphExpectations"][0].update(objectTypes=["https://example.org/v#Doc"]))

    def test_missing_file_is_a_failed_check_not_a_crash(self):
        report = run(lambda m: m.pop("onto/shapes.ttl"))
        self.assertIn("records.conform-to-shapes", failed(report))
        self.assertIn("onto/shapes.ttl: file not supplied", json.dumps(report))


class ShapeTests(unittest.TestCase):
    def test_a_shape_that_does_not_bite_fails_the_run(self):
        def soften(m):
            f.edit(m, "onto/shapes.ttl", "sh:minInclusive 0", "sh:minInclusive -100")

        report = run(soften)
        self.assertFalse(report["ok"])
        negative = next(t for t in report["shapeTests"] if t["id"] == "negative-count")
        self.assertEqual((negative["ok"], negative["expect"], negative["actual"]), (False, "violates", "conforms"))

    def test_a_baseline_that_violates_fails_the_run(self):
        report = run(config_edit=lambda c: c["shapeTests"][0]["mutation"].append({"op": "replace", "path": "/count", "value": -1}))
        base = report["shapeTests"][0]
        self.assertEqual((base["ok"], base["actual"]), (False, "violates"))

    def test_a_mutation_that_cannot_apply_is_an_error_not_a_pass(self):
        report = run(config_edit=lambda c: c["shapeTests"][1].update(mutation=[{"op": "remove", "path": "/nonesuch"}]))
        broken = next(t for t in report["shapeTests"] if t["id"] == "negative-count")
        self.assertEqual((broken["ok"], broken["actual"]), (False, "error"))
        self.assertIn("/nonesuch", broken["detail"])

    def test_via_context_requires_an_unmapped_term_not_a_shacl_failure(self):
        report = run(config_edit=lambda c: c["shapeTests"][3].update(mutation=[{"op": "replace", "path": "/count", "value": -1}]))
        context = next(t for t in report["shapeTests"] if t["id"] == "unmapped-key")
        self.assertEqual((context["ok"], context["actual"]), (False, "conforms"))

    def test_the_source_record_is_never_mutated(self):
        files = f.files()
        before = dict(files)
        check_vocabulary_conformance(f.config(), files)
        self.assertEqual(files, before)


class Determinism(unittest.TestCase):
    def test_same_inputs_give_identical_bytes_whatever_the_file_order(self):
        from rdf_vocabulary_conformance.core import dumps

        files = f.files()
        reordered = dict(reversed(list(files.items())))
        self.assertEqual(
            dumps(check_vocabulary_conformance(f.config(), files)),
            dumps(check_vocabulary_conformance(f.config(), reordered)),
        )

    def test_a_failing_report_is_deterministic_too(self):
        from rdf_vocabulary_conformance.core import dumps

        def break_it(m):
            m["data/a.json"] = f.dumps({**f.RECORD, "count": -5, "mystery": 1})
            f.edit(m, "onto/vocab.ttl", 'skos:prefLabel "closed"', 'skos:prefLabel "shut"')

        self.assertEqual(dumps(run(break_it)), dumps(run(break_it)))

    def test_inputs_witness_names_exactly_the_bytes_read(self):
        report = run()
        paths = [x["path"] for x in report["inputs"]["files"]]
        self.assertEqual(paths, sorted(paths))
        self.assertIn("onto/vocab.ttl", paths)
        self.assertRegex(report["inputs"]["config"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
