"""Units under the checks: JSON Patch, globs, the schema validator, configuration admission."""

import json
import unittest
from importlib import resources

from rdf_vocabulary_conformance import ConfigError, _jsonschema, globs, jsonpatch, required_patterns, validate_config

from . import fixtures as f


class JsonPatch(unittest.TestCase):
    def apply(self, doc, *ops):
        return jsonpatch.apply_patch(doc, list(ops))

    def test_rfc6902_appendix_a_examples(self):
        self.assertEqual(self.apply({"foo": "bar"}, {"op": "add", "path": "/baz", "value": "qux"}), {"foo": "bar", "baz": "qux"})  # A.1
        self.assertEqual(self.apply({"foo": ["bar", "baz"]}, {"op": "add", "path": "/foo/1", "value": "qux"}), {"foo": ["bar", "qux", "baz"]})  # A.2
        self.assertEqual(self.apply({"baz": "qux", "foo": "bar"}, {"op": "remove", "path": "/baz"}), {"foo": "bar"})  # A.3
        self.assertEqual(self.apply({"foo": ["bar", "qux", "baz"]}, {"op": "remove", "path": "/foo/1"}), {"foo": ["bar", "baz"]})  # A.4
        self.assertEqual(self.apply({"baz": "qux", "foo": "bar"}, {"op": "replace", "path": "/baz", "value": "boo"}), {"baz": "boo", "foo": "bar"})  # A.5
        self.assertEqual(
            self.apply({"foo": {"bar": "baz", "waldo": "fred"}, "qux": {"corge": "grault"}}, {"op": "move", "from": "/foo/waldo", "path": "/qux/thud"}),
            {"foo": {"bar": "baz"}, "qux": {"corge": "grault", "thud": "fred"}},
        )  # A.6
        self.assertEqual(self.apply({"foo": ["all", "grass", "cows", "eat"]}, {"op": "move", "from": "/foo/1", "path": "/foo/3"}), {"foo": ["all", "cows", "eat", "grass"]})  # A.7
        self.assertEqual(self.apply({"baz": "qux", "foo": ["a", 2, "c"]}, {"op": "test", "path": "/baz", "value": "qux"}, {"op": "test", "path": "/foo/1", "value": 2}), {"baz": "qux", "foo": ["a", 2, "c"]})  # A.8
        with self.assertRaises(jsonpatch.PatchError):
            self.apply({"baz": "qux"}, {"op": "test", "path": "/baz", "value": "bar"})  # A.9
        self.assertEqual(self.apply({"foo": "bar"}, {"op": "add", "path": "/child", "value": {"grandchild": {}}}), {"foo": "bar", "child": {"grandchild": {}}})  # A.10
        with self.assertRaises(jsonpatch.PatchError):
            self.apply({"foo": "bar"}, {"op": "add", "path": "/baz/bat", "value": "qux"})  # A.12
        self.assertEqual(self.apply({"foo": {"bar": 1}}, {"op": "copy", "from": "/foo", "path": "/baz"}), {"foo": {"bar": 1}, "baz": {"bar": 1}})
        self.assertEqual(self.apply({"foo": ["bar"]}, {"op": "add", "path": "/foo/-", "value": "baz"}), {"foo": ["bar", "baz"]})  # A.16

    def test_escapes(self):
        self.assertEqual(self.apply({"a/b": 1, "m~n": 2}, {"op": "replace", "path": "/a~1b", "value": 3}, {"op": "replace", "path": "/m~0n", "value": 4}), {"a/b": 3, "m~n": 4})

    def test_replace_and_remove_need_an_existing_target(self):
        for op in ({"op": "replace", "path": "/nope", "value": 1}, {"op": "remove", "path": "/nope"}, {"op": "remove", "path": "/list/9"}, {"op": "replace", "path": "/list/x", "value": 1}):
            with self.subTest(op=op), self.assertRaises(jsonpatch.PatchError):
                self.apply({"list": [1]}, op)

    def test_the_input_is_not_mutated_and_the_output_does_not_alias_values(self):
        doc = {"a": {"b": [1]}}
        value = {"x": [1]}
        out = self.apply(doc, {"op": "add", "path": "/c", "value": value}, {"op": "remove", "path": "/a/b/0"})
        self.assertEqual(doc, {"a": {"b": [1]}})
        out["c"]["x"].append(2)
        self.assertEqual(value, {"x": [1]})

    def test_test_is_strict_about_booleans_and_numbers(self):
        with self.assertRaises(jsonpatch.PatchError):
            self.apply({"a": True}, {"op": "test", "path": "/a", "value": 1})
        self.assertEqual(self.apply({"a": 1}, {"op": "test", "path": "/a", "value": 1.0}), {"a": 1})

    def test_move_into_own_child_is_refused(self):
        with self.assertRaises(jsonpatch.PatchError):
            self.apply({"a": {"b": 1}}, {"op": "move", "from": "/a", "path": "/a/c"})


class Globs(unittest.TestCase):
    def test_matching(self):
        cases = [
            ("ontology/**/*.ttl", "ontology/a.ttl", True),
            ("ontology/**/*.ttl", "ontology/vendor/a.ttl", True),
            ("ontology/**/*.ttl", "ontology/vendor/deep/a.ttl", True),
            ("ontology/**/*.ttl", "other/a.ttl", False),
            ("ontology/*.jsonld", "ontology/a.jsonld", True),
            ("ontology/*.jsonld", "ontology/sub/a.jsonld", False),
            ("data/records/*.json", "data/records/x.json", True),
            ("data/records/*.json", "data/records/x.jsonl", False),
            ("**", "a/b/c", True),
            ("a/?.txt", "a/b.txt", True),
            ("a/?.txt", "a/bb.txt", False),
            ("a.b", "aXb", False),
        ]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(globs.matches(pattern, path), expected)

    def test_static_directory(self):
        self.assertEqual(globs.static_directory("ontology/**/*.ttl"), "ontology")
        self.assertEqual(globs.static_directory("a/b/c/*.json"), "a/b/c")
        self.assertEqual(globs.static_directory("*.json"), "")
        self.assertEqual(globs.static_directory("a/*/b.json"), "a")


class SchemaValidator(unittest.TestCase):
    def test_the_shipped_schema_uses_only_supported_keywords(self):
        schema = json.loads(resources.files("rdf_vocabulary_conformance").joinpath("conformance.schema.json").read_text())
        _jsonschema.assert_supported(schema)

    def test_unsupported_keyword_is_refused_not_ignored(self):
        with self.assertRaises(_jsonschema.UnsupportedKeyword):
            _jsonschema.assert_supported({"type": "object", "dependentRequired": {}})

    def test_keywords(self):
        schema = {
            "type": "object",
            "required": ["a"],
            "additionalProperties": False,
            "properties": {
                "a": {"type": "integer", "minimum": 1, "maximum": 5},
                "b": {"type": "array", "items": {"type": "string", "pattern": "^x"}, "uniqueItems": True, "minItems": 1},
                "c": {"enum": ["p", "q"]},
                "d": {"const": 1},
                "e": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                "g": {"if": {"properties": {"k": {"const": 1}}, "required": ["k"]}, "then": {"required": ["v"]}},
            },
        }
        ok = lambda v: _jsonschema.validate(v, schema)
        self.assertEqual(ok({"a": 2, "b": ["x1", "x2"], "c": "p", "d": 1, "e": 3}), [])
        self.assertTrue(ok({}))
        self.assertTrue(ok({"a": True}))  # a boolean is not an integer
        self.assertTrue(ok({"a": 0}))
        self.assertTrue(ok({"a": 6}))
        self.assertTrue(ok({"a": 1, "z": 1}))
        self.assertTrue(ok({"a": 1, "b": ["y"]}))
        self.assertTrue(ok({"a": 1, "b": ["x", "x"]}))
        self.assertTrue(ok({"a": 1, "b": []}))
        self.assertTrue(ok({"a": 1, "c": "r"}))
        self.assertTrue(ok({"a": 1, "d": True}))
        self.assertTrue(ok({"a": 1, "e": 1.5}))
        self.assertTrue(ok({"a": 1, "g": {"k": 1}}))
        self.assertEqual(ok({"a": 1, "g": {"k": 1, "v": 0}}), [])

    def test_errors_are_sorted(self):
        errors = _jsonschema.validate({"x": 1, "y": 2}, {"type": "object", "additionalProperties": False})
        self.assertEqual(errors, sorted(errors))


class ConfigAdmission(unittest.TestCase):
    def refused(self, edit):
        config = f.config()
        edit(config)
        with self.assertRaises(ConfigError) as caught:
            validate_config(config)
        return caught.exception.errors

    def test_the_tiny_configuration_is_admissible(self):
        validate_config(f.config())

    def test_not_an_object(self):
        with self.assertRaises(ConfigError):
            validate_config([])

    def test_a_configuration_must_ask_for_something(self):
        with self.assertRaises(ConfigError):
            validate_config({"formatVersion": 1})

    def test_wrong_format_version(self):
        self.assertTrue(self.refused(lambda c: c.update(formatVersion=2)))

    def test_unknown_member(self):
        self.assertTrue(any("unexpected property" in e for e in self.refused(lambda c: c.update(surprise=1))))

    def test_paths_must_be_relative_and_contained(self):
        for bad in ("/etc/passwd", "../outside.ttl", "a/../b.ttl", "a//b.ttl", "a\\b.ttl"):
            with self.subTest(path=bad):
                self.assertTrue(self.refused(lambda c: c["vocabulary"].update(files=[bad])))

    def test_iris_must_be_absolute(self):
        self.assertTrue(self.refused(lambda c: c["root"].update(iri="relative/iri")))

    def test_regex_must_compile_and_have_its_groups(self):
        self.assertTrue(any("regular expression" in e for e in self.refused(lambda c: c["skos"]["glossary"]["source"].update(pattern="("))))
        self.assertTrue(any("capture group" in e for e in self.refused(lambda c: c["skos"]["glossary"]["source"].update(commitPattern="/blob/[0-9a-f]{40}/"))))

    def test_duplicate_shape_test_ids(self):
        self.assertTrue(any("duplicate id" in e for e in self.refused(lambda c: c["shapeTests"].append(dict(c["shapeTests"][0])))))

    def test_patch_operations_are_checked(self):
        self.assertTrue(self.refused(lambda c: c["shapeTests"][0].update(mutation=[{"op": "add", "path": "/x"}])))  # add needs value
        self.assertTrue(self.refused(lambda c: c["shapeTests"][0].update(mutation=[{"op": "move", "path": "/x"}])))  # move needs from
        self.assertTrue(self.refused(lambda c: c["shapeTests"][0].update(mutation=[{"op": "frob", "path": "/x"}])))
        self.assertTrue(self.refused(lambda c: c["shapeTests"][0].update(mutation=[{"op": "remove", "path": "x"}])))

    def test_expect_is_closed(self):
        self.assertTrue(self.refused(lambda c: c["shapeTests"][0].update(expect="maybe")))

    def test_sections_that_need_other_sections(self):
        self.assertTrue(any("needs a 'records'" in e for e in self.refused(lambda c: c.pop("records"))))
        self.assertTrue(any("needs a 'vocabulary'" in e for e in self.refused(lambda c: c.pop("vocabulary"))))
        self.assertTrue(any("needs root.shapes" in e for e in self.refused(lambda c: c["root"].pop("shapes"))))
        self.assertTrue(any("needs skos.glossary" in e for e in self.refused(lambda c: c["skos"].pop("glossary"))))
        self.assertTrue(any("versionInfoFrom" in e for e in self.refused(lambda c: c["vocabulary"]["ontology"].pop("versionInfoFrom"))))

    def test_record_kinds_must_lie_inside_the_glob(self):
        self.assertTrue(any("does not match" in e for e in self.refused(lambda c: c["records"]["kinds"].update({"elsewhere/x.json": "https://example.org/v#Doc"}))))

    def test_base_iri_ends_in_slash(self):
        self.assertTrue(any("must end" in e for e in self.refused(lambda c: c["records"].update(baseIri="https://example.org/record"))))

    def test_errors_are_sorted_and_deterministic(self):
        errors = self.refused(lambda c: c.update(formatVersion=2, surprise=1))
        self.assertEqual(errors, sorted(errors))

    def test_required_patterns_cover_every_file_the_checks_read(self):
        patterns = required_patterns(f.config())
        for path in f.files():
            self.assertTrue(any(globs.matches(p, path) for p in patterns), path)
        self.assertEqual(patterns, sorted(set(patterns)))


if __name__ == "__main__":
    unittest.main()
