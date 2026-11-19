"""The per-run context: supplied files, parsed once, never written.

Nothing here reads a filesystem or the network. JSON-LD remote contexts are
refused; `owl:imports` are never followed.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import PurePosixPath
from typing import Any, Mapping

import pyshacl
from pyld import jsonld
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

from . import globs

SH = Namespace("http://www.w3.org/ns/shacl#")

RDF_FORMATS = {
    ".ttl": "turtle",
    ".nt": "nt",
    ".n3": "n3",
    ".rdf": "xml",
    ".xml": "xml",
    ".owl": "xml",
}


class FileProblem(Exception):
    """A supplied file is missing, unreadable, or does not parse. The message names it."""


def _refuse_remote(url: str, options: dict | None = None) -> Any:
    raise jsonld.JsonLdError(
        f"remote document {url!r} refused: contexts must be inline",
        "jsonld.LoadDocumentError",
        {"url": url},
        code="loading document failed",
    )


def jsonld_message(error: BaseException) -> str:
    """The whole PyLD cause chain, innermost last, as one bounded line."""
    parts = []
    node: BaseException | None = error
    while node is not None and len(parts) < 4:
        text = str(node.args[0]) if node.args else type(node).__name__
        code = getattr(node, "code", None)
        parts.append(f"{code}: {text}" if code else text)
        node = node.__cause__
    return " <- ".join(parts)[:400]


def merged(*graphs: Graph) -> Graph:
    out = Graph()
    for graph in graphs:
        for triple in graph:
            out.add(triple)
    return out


def violations(results: Graph) -> list[str]:
    """Deterministic one-line summaries of a SHACL report (blank nodes never named)."""
    lines: set[str] = set()
    for result in results.subjects(RDF.type, SH.ValidationResult):
        component = results.value(result, SH.sourceConstraintComponent)
        path = results.value(result, SH.resultPath)
        focus = results.value(result, SH.focusNode)
        lines.add(
            "{} path={} focus={}".format(
                str(component).rsplit("#", 1)[-1] if component is not None else "?",
                str(path) if isinstance(path, URIRef) else ("-" if path is None else "_:"),
                str(focus) if isinstance(focus, URIRef) else "_:",
            )
        )
    return sorted(lines)


class Context:
    def __init__(self, config: dict, files: Mapping[str, bytes]):
        self.config = config
        self.files = files
        self.reads: set[str] = set()
        self._graphs: dict[str, Graph] = {}
        self._json: dict[str, Any] = {}
        self._record_context: Any = None

    # -- files -------------------------------------------------------------

    def match(self, pattern: str) -> list[str]:
        return sorted(p for p in self.files if globs.matches(pattern, p))

    def read(self, path: str) -> bytes:
        if path not in self.files:
            raise FileProblem(f"{path}: file not supplied")
        self.reads.add(path)
        return bytes(self.files[path])

    def text(self, path: str) -> str:
        try:
            return self.read(path).decode("utf-8")
        except UnicodeDecodeError:
            raise FileProblem(f"{path}: not UTF-8") from None

    def json(self, path: str) -> Any:
        if path not in self._json:
            try:
                self._json[path] = json.loads(self.text(path))
            except json.JSONDecodeError as error:
                raise FileProblem(f"{path}: not valid JSON ({error.msg} at line {error.lineno})") from None
        return self._json[path]

    def graph(self, path: str) -> Graph:
        if path not in self._graphs:
            suffix = PurePosixPath(path).suffix.lower()
            fmt = RDF_FORMATS.get(suffix)
            if fmt is None:
                raise FileProblem(f"{path}: no RDF syntax is known for extension {suffix!r}")
            graph = Graph()
            try:
                graph.parse(data=self.text(path), format=fmt, publicID=f"urn:x-conformance:{path}")
            except FileProblem:
                raise
            except Exception as error:  # rdflib raises a different class per syntax
                first = (str(error).strip().splitlines() or [""])[0][:200]
                raise FileProblem(f"{path}: does not parse as {fmt} ({type(error).__name__}: {first})") from None
            self._graphs[path] = graph
        return self._graphs[path]

    # -- derived graphs ----------------------------------------------------

    def vocabulary(self) -> Graph:
        return merged(*(self.graph(p) for p in self.config["vocabulary"]["files"]))

    def root_ontology(self) -> Graph | None:
        root = self.config.get("root")
        return self.graph(root["ontology"]) if root else None

    def ontology(self) -> Graph:
        """Vocabulary merged with the root ontology: the SHACL ont_graph."""
        graphs = []
        if "vocabulary" in self.config:
            graphs.append(self.vocabulary())
        root = self.root_ontology()
        if root is not None:
            graphs.append(root)
        return merged(*graphs)

    # -- records -----------------------------------------------------------

    def record_json(self, path: str) -> dict:
        value = self.json(path)
        if not isinstance(value, dict):
            raise FileProblem(f"{path}: a record must be a JSON object")
        return value

    def record_context(self) -> Any:
        if self._record_context is None:
            path = self.config["records"]["context"]
            document = self.json(path)
            if not isinstance(document, dict) or "@context" not in document:
                raise FileProblem(f"{path}: has no top-level @context")
            self._record_context = document["@context"]
        return self._record_context

    def record_graph(self, path: str, record: dict) -> Graph:
        """A record as RDF through the JSON-LD 1.1 context (PyLD, spec-compliant)."""
        base = self.config["records"]["baseIri"]
        stem = PurePosixPath(path).stem
        document = {
            "@context": [self.record_context(), {"@base": f"{base}{stem}/"}],
            "@id": f"{base}{stem}",
            **record,
        }
        try:
            nquads = jsonld.to_rdf(document, {"format": "application/n-quads", "documentLoader": _refuse_remote})
        except jsonld.JsonLdError as error:
            raise FileProblem(f"{path}: JSON-LD conversion failed ({jsonld_message(error)})") from None
        graph = Graph()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # rdflib's own n-quads reader
            graph.parse(data=nquads, format="nquads")
        return graph

    def unmapped(self, graph: Graph) -> list[str]:
        """Predicates and types that fell through to the context's @vocab."""
        prefix = self.config["records"]["unmappedPrefix"]
        found = {str(p) for p in graph.predicates() if str(p).startswith(prefix)}
        found |= {str(o) for o in graph.objects(None, RDF.type) if isinstance(o, URIRef) and str(o).startswith(prefix)}
        return sorted(found)

    def shacl(self, data: Graph, shapes: Graph, ontology: Graph | None = None) -> tuple[bool, list[str]]:
        conforms, results, _text = pyshacl.validate(
            data, shacl_graph=shapes, ont_graph=ontology, inference="none"
        )
        return bool(conforms), ([] if conforms else violations(results))

    def digests(self) -> list[dict]:
        return [
            {"path": p, "sha256": hashlib.sha256(bytes(self.files[p])).hexdigest()}
            for p in sorted(self.reads)
        ]
