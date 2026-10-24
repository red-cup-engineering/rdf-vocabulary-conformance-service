# rdf-vocabulary-conformance-service

<!-- 561-group-standards:badges:start -->
[![Red Cup Engineering](https://img.shields.io/badge/561%20Group-Red%20Cup%20Engineering-c0392b)](https://github.com/red-cup-engineering)
[![License: MIT](https://img.shields.io/badge/license-MIT-c0392b)](LICENSE)
[![Tips welcome](https://img.shields.io/badge/tips-welcome-d4a017)](https://github.com/561-group/.github/blob/main/TIPS.md)
<!-- 561-group-standards:badges:end -->

Validate your ontology and data. A generic, deterministic checker for a research
vocabulary (OWL, SKOS) and the JSON data records that use it (JSON-LD 1.1
context, SHACL shapes). It generalizes the paper-specific Python tests a Science
repository cannot hold: the Science repository keeps **only data** — one
`ontology/conformance.json` — and calls this package from CI as a dev tool.

Python 3.12, MIT. Dependencies are pinned exactly (`rdflib==7.6.0`,
`pyshacl==0.40.1`, `PyLD==3.3.0`, and their transitive pins).

## Operations

### checkVocabularyConformance

~~~python
def check_vocabulary_conformance(config: dict, files: Mapping[str, bytes]) -> dict
~~~

Judges the vocabulary and records named by `config`, reading only the bytes in
`files` (repository-relative forward-slash path to content).

- Returns a report: `{ok, tool, version, inputs, summary, checks, shapeTests}`.
  `checks` is `[{id, ok, detail, failures}]` in a fixed order; `shapeTests` is
  `[{id, record, expect, via, actual, ok, detail, reason}]` in configuration
  order; `inputs` is `{config, files: [{path, sha256}]}`; `ok` is true only when
  every check and every shape test is.
- Refusals: `ConfigError` (a `ValueError` whose `errors` lists every reason:
  schema violation, uncompilable regular expression, duplicate id, a section
  that needs another, a path that is absolute or contains `..`); `TypeError`
  when `files` is not a mapping of safe relative paths to bytes. A missing or
  unparseable *file* is not a refusal: it fails the checks that need it.
- Precondition: `config` conforms to `conformance.schema.json` and the rules
  the schema cannot say; `files` holds the files the checks should read
  (`required_patterns(config)` lists them).
- Postcondition: one entry per *configured* check (an unconfigured check is
  absent, not passed); every `expect: violates` shape test either violates the
  way it says (`via`) or is reported failed; the same `config` and `files`
  yield byte-identical JSON, whatever the file mapping's order.
- Preserved frame: `config` and `files` are never mutated; mutations are applied
  to deep copies.
- Effect class: pure. No filesystem, network, clock or randomness. JSON-LD remote
  contexts are refused, `owl:imports` is never dereferenced, SHACL-JS is off.
- Witness: `inputs` — the sha256 of the canonical configuration and of every
  file actually read — so a report names the bytes it judged.
- Commands: `check-vocabulary-conformance` (filesystem face) and
  `check-vocabulary-conformance-ndjson` (stream face), below.

## Faces

### CLI

~~~sh
pip install -r requirements.txt && pip install .
check-vocabulary-conformance --root path/to/science-repo [--config ontology/conformance.json]
~~~

Prints the JSON report on stdout and one `FAIL ...` line per failure plus a
summary on stderr. Exit **0** all checks and shape tests passed; **1** something
failed; **2** the configuration is missing, unreadable or not admissible (stdout
then holds `{"ok": false, "refusal": {reason, message, errors?}}`). Only the
files the configuration names are read; globs walk only their static directory,
never leave `--root` through a symlink, and skip `.git`.

From a Science repository's CI:

~~~sh
pip install "rdf-vocabulary-conformance-service @ git+https://github.com/red-cup-engineering/rdf-vocabulary-conformance-service@v0.1.0"
check-vocabulary-conformance --root .
~~~

### NDJSON

`check-vocabulary-conformance-ndjson` reads one JSON request per stdin line and
writes one JSON result per stdout line; nothing touches a filesystem.

~~~json
{"config": {"formatVersion": 1, "...": "..."}, "files": {"ontology/v.ttl": "<utf-8 text>", "data/x.bin": {"base64": "..."}}}
~~~

The result is the report, or `{"ok": false, "refusal": {reason, message, errors?}}`
with reason `InvalidRequest`, `InvalidConfig` or `RequestTooLarge` (16 MiB per
line). A non-conformant report has `ok: false` and no `refusal`. This is the face
behind the held market offer (`data/records/offers/check-vocabulary-conformance.json`).

## Configuration

`ontology/conformance.json` (schema: `src/rdf_vocabulary_conformance/conformance.schema.json`,
`formatVersion: 1`). Paths are relative to the repository root; IRIs are
absolute. Every section is optional; a section's absence switches off the checks
that need it.

| section | what it says | checks it enables |
|---|---|---|
| `parse` | `rdf`/`json` globs, minimum counts, an exact JSON file set | `parsing.rdf-files-parse`, `parsing.json-files-parse` |
| `vocabulary` | vocabulary files, owned namespace, expected `owl:Ontology` header (IRI, version IRI with `{iri}`/`{version}`, imports, license, version read from a manifest by regex) | `rooting.ontology-metadata`, `rooting.terms-documented` |
| `root` | vendored root ontology and shapes, expected IRI and record class, graphs to validate against the root shapes | `rooting.root-declares-namespace`, `rooting.conforms-to-root-shapes`, `rooting.record-kinds-subclass-root` |
| `skos` | scheme, counts, glossary rule (IRI prefix, source pattern, commit pattern, single commit, derivation pattern), generated SKOS JSON-LD projection | `skos.scheme`, `skos.concepts-complete`, `skos.collections`, `skos.concept-collection-disjoint`, `skos.glossary-sources`, `skos.glossary-single-commit`, `skos.projection-agrees`, `skos.related-symmetric` |
| `closedSets`, `labelSets` | classes that equal their `owl:oneOf`; exact `skos:prefLabel` sets | `closed-sets.enumerations-match-instances`, `closed-sets.labels` |
| `records` | records glob, JSON-LD context, SHACL shapes, record base IRI, the `@vocab` prefix that marks an unmapped key, record kinds, graph expectations | `records.kinds-declared`, `records.context-processes`, `records.convert-with-no-unmapped-key`, `records.conform-to-shapes`, `records.graph-expectations` |
| `shapeTests` | `{id, record, mutation, expect, via?, reason}` | one result each |

A shape test applies an RFC 6902 JSON Patch (`add`, `remove`, `replace`, `move`,
`copy`, `test`) to a copy of a record, converts it through the context and
validates it. `expect: "violates"` with `via: "shacl"` (default) needs a SHACL
report that does not conform; `via: "context"` needs a key or type the context
leaves unmapped; `expect: "conforms"` (usually an empty mutation, as a baseline)
needs both clean. A patch that cannot apply is an `error`, never a pass.

The paper `white-repopulation` holds its configuration at
`ontology/conformance.json`. It carries all 37 tests of its former
`tests/test_ontology.py`: the 21 non-negative tests become 22 checks (one is
new: the records context must itself process), and the 16 `ShapesBite` tests
become 21 shape tests (15 broken copies plus the untouched-record baseline for
each of the six records). The paper's old version test compared `owl:versionInfo`
with a quoted YAML scalar (`version: "0.2.0"`) and failed; the configuration's
pattern strips the quotes.

## Tests

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -t .
WHITE_REPOPULATION_ROOT=/path/to/white-repopulation PYTHONPATH=src python -m unittest tests.test_faces   # parity
~~~

## Limitations

- Regular expressions in a configuration are Python `re` and unbounded in time; a
  hosted deployment must bound request time externally.
- The SKOS projection check expects an `@graph` of nodes with compact `skos:`
  keys, as produced by the house glossary projection.
- Checks are structural; they do not judge the meaning of a vocabulary or the
  truth of a record's numbers.
- Blank-node SHACL focus nodes are reported as `_:` so that output stays
  deterministic.
- Built and tested with Python 3.12 only; the package metadata declares
  `>=3.12`.
