"""
Deterministic call-graph construction and persistence.

Bridges the parsed index (``File`` / ``Chunk`` rows) to a resolved,
function-level call graph:

* :func:`build_call_graph` reads the symbols, call sites and imports recorded
  during ingestion and resolves every call site to the chunk that defines the
  target — no clone, no embedding, no LLM, so the output is reproducible for
  a given input.
* :func:`persist_call_graph` stores the edges, replacing any previous graph
  for the repository.

Honesty rules, mirroring the manifest:

* Every edge carries a ``resolution`` label recording *how* the target was
  found: ``same_file``, ``import`` or ``unique``.
* A call that cannot be placed (stdlib, third-party, dynamic dispatch) is
  kept and labelled ``unresolved`` rather than guessed. A wrong edge costs a
  reader more than a missing one.
* Unresolvable-by-design cases are not silently dropped: they are visible in
  the summary counts so a sparse graph is never mistaken for a dense one.

Resolution is name-scoped, not type-aware: two files defining ``process()``
are distinguished by the caller's imports and same-file scoping, never by type
inference. Dynamic dispatch (callbacks, interfaces, reflection) resolves only
when the name is unique, and says which rule matched when it does.
"""

from __future__ import annotations

import ast
import logging
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.structure import STRUCTURAL_CHUNK_TYPES
from app.models.call_edge import CallEdge
from app.models.chunk import Chunk
from app.models.file import File
from app.models.repository import Repository

logger = logging.getLogger(__name__)

#: How each call target was found. Surfaced on every stored edge.
RESOLUTION_SAME_FILE = "same_file"
RESOLUTION_IMPORT = "import"
RESOLUTION_UNIQUE = "unique"
RESOLUTION_UNRESOLVED = "unresolved"

#: Chunks loaded per database round-trip while building the symbol index.
SYMBOL_BATCH_SIZE = 500

#: Edges persisted per database commit.
EDGE_COMMIT_BATCH = 500

#: Receivers that refer to the enclosing object/class itself. A call through
#: one of these is a same-file (or unique) lookup, never an import lookup.
_SELF_RECEIVERS = ("self", "cls", "this", "super")

_JS_SPECIFIER_EXTENSIONS = (
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts",
)
_JS_INDEX_FILES = (
    "index.ts", "index.tsx", "index.js", "index.jsx", "index.mjs",
)


@dataclass
class ImportMap:
    """How one file's imports bind names to the modules/files they came from."""

    # ``import a.b as c`` / ``import * as ns from "./m"``: binding -> target.
    aliases: Dict[str, str] = field(default_factory=dict)
    # ``from a import b [as c]``: binding -> (target module/file, original name).
    names: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    # C/C++ quoted includes, by basename: scoping only, no binding.
    include_basenames: Set[str] = field(default_factory=set)


@dataclass
class ResolvedEdge:
    """One call site after resolution. ``callee_*`` is None when unresolved."""

    caller_chunk_id: Any
    caller_symbol: str
    caller_path: str
    callee_chunk_id: Optional[Any]
    callee_symbol: str
    callee_path: Optional[str]
    call_line: int
    resolution: str


@dataclass
class CallGraphResult:
    call_sites: int
    edges: List[ResolvedEdge] = field(default_factory=list)

    def by_resolution(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for edge in self.edges:
            counts[edge.resolution] = counts.get(edge.resolution, 0) + 1
        return counts

    def summary(self) -> str:
        counts = self.by_resolution()
        resolved = self.call_sites - counts.get(RESOLUTION_UNRESOLVED, 0)
        detail = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
        return f"resolved {resolved}/{self.call_sites} call sites ({detail})"


# --------------------------------------------------------------------------
# Per-language import parsing. Each parser turns raw import statement text
# (already extracted during ingestion, see languages.parse_symbols) into an
# ImportMap. Languages with no useful import scoping (Go, Rust, Ruby, PHP)
# get an empty map and fall back to same-file and unique matching.
# --------------------------------------------------------------------------


def _python_relative_module(importer_path: str, module: str, level: int) -> str:
    """
    Resolve an import to a repo-relative path form.

    ``from .mod import x`` in ``app/a/b.py`` targets ``app/a/mod``; ``from ..
    import y`` targets ``app/``. An absolute import (``level == 0``) already
    names the full dotted path, so it is returned unchanged. The result keeps
    ``/`` separators; the module index normalises both forms.
    """
    if level == 0:
        return module
    parts = importer_path.split("/")[:-1]
    for _ in range(max(0, level - 1)):
        if parts:
            parts.pop()
    base = "/".join(parts)
    if module:
        return f"{base}/{module}" if base else module
    return base


def _python_import_map(import_lines: Sequence[str], importer_path: str) -> ImportMap:
    import_map = ImportMap()

    for text in import_lines or []:
        try:
            body = ast.parse(text).body
        except Exception:
            continue
        for node in body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_map.aliases[alias.name] = alias.name
                    if alias.asname:
                        import_map.aliases[alias.asname] = alias.name
                    else:
                        # ``import a.b`` binds ``a``; keep the full dotted form
                        # so ``a.b.f()`` matches the longest prefix first.
                        top = alias.name.split(".")[0]
                        import_map.aliases.setdefault(top, alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                base = _python_relative_module(
                    importer_path, module, node.level or 0
                )
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    import_map.names[alias.asname or alias.name] = (
                        base or module,
                        alias.name,
                    )
    return import_map


def _resolve_js_specifier(
    specifier: str, importer_path: str, path_index: Set[str]
) -> Optional[str]:
    """Map a relative JS/TS import specifier to a repo-relative file path."""
    if not specifier.startswith("."):
        # Bare specifier: an npm package, out of the repository's scope.
        return None
    base = posixpath.normpath(
        posixpath.join(posixpath.dirname(importer_path), specifier)
    )
    # The specifier may already carry its extension ("./view.js"), so try it
    # as-is before appending one.
    if base in path_index:
        return base
    for extension in _JS_SPECIFIER_EXTENSIONS:
        candidate = base + extension
        if candidate in path_index:
            return candidate
    for index_file in _JS_INDEX_FILES:
        candidate = f"{base}/{index_file}"
        if candidate in path_index:
            return candidate
    return None


def _js_import_map(
    import_lines: Sequence[str], importer_path: str, path_index: Set[str]
) -> ImportMap:
    import_map = ImportMap()

    for text in import_lines or []:
        from_match = re.search(r"from\s+['\"]([^'\"]+)['\"]", text)
        if from_match is None:
            # Side-effect import (``import "./side"``) binds no names.
            continue
        target = _resolve_js_specifier(from_match.group(1), importer_path, path_index)
        if target is None:
            continue

        default_match = re.match(r"import\s+([A-Za-z_$][\w$]*)\s+from", text)
        namespace_match = re.search(r"import\s+\*\s+as\s+([A-Za-z_$][\w$]*)\s+from", text)
        named_match = re.search(r"import\s*\{([^}]*)\}\s*from", text)

        if default_match:
            import_map.aliases[default_match.group(1)] = target
        elif namespace_match:
            import_map.aliases[namespace_match.group(1)] = target
        elif named_match:
            for piece in named_match.group(1).split(","):
                piece = piece.strip()
                if not piece:
                    continue
                if " as " in piece:
                    original, binding = [p.strip() for p in piece.split(" as ")]
                else:
                    original = binding = piece
                import_map.names[binding] = (target, original)
    return import_map


def _java_import_map(import_lines: Sequence[str]) -> ImportMap:
    import_map = ImportMap()

    for text in import_lines or []:
        match = re.match(r"import\s+(?:static\s+)?([\w.]+)\s*;", (text or "").strip())
        if match is None:
            continue
        dotted = match.group(1)
        if dotted.endswith(".*"):
            # Wildcard: no binding, fall back to unique matching.
            continue
        import_map.names[dotted.split(".")[-1]] = (
            dotted.replace(".", "/") + ".java",
            dotted.split(".")[-1],
        )
    return import_map


def _c_import_map(import_lines: Sequence[str]) -> ImportMap:
    import_map = ImportMap()

    for text in import_lines or []:
        match = re.search(r'#\s*include\s*"([^"]+)"', text or "")
        if match:
            import_map.include_basenames.add(match.group(1).split("/")[-1])
    return import_map


def _import_map_for(
    language: Optional[str], import_lines: Sequence[str], importer_path: str,
    path_index: Set[str],
) -> ImportMap:
    if language == "Python":
        return _python_import_map(import_lines, importer_path)
    if language in ("JavaScript", "TypeScript", "TSX"):
        return _js_import_map(import_lines, importer_path, path_index)
    if language == "Java":
        return _java_import_map(import_lines)
    if language in ("C", "C++"):
        return _c_import_map(import_lines)
    return ImportMap()


# --------------------------------------------------------------------------
# The resolver.
# --------------------------------------------------------------------------


class _CallGraphResolver:
    """In-memory symbol and import indexes over one repository's rows."""

    def __init__(self, files: Sequence[Any], chunk_rows: Sequence[Sequence[Any]]):
        # path -> language
        self.language_by_path: Dict[str, Optional[str]] = {}
        # path -> set of repo-relative paths (built first; JS imports need it)
        self.path_index: Set[str] = set()
        # path -> ImportMap
        self.import_maps: Dict[str, ImportMap] = {}
        # path -> {symbol name: chunk id} (first definition in file order wins)
        self.symbols_by_file: Dict[str, Dict[str, Any]] = {}
        # symbol name -> [(chunk id, path)]
        self.global_symbols: Dict[str, List[Tuple[Any, str]]] = {}
        # basename -> [paths] (C/C++ include scoping)
        self.files_by_basename: Dict[str, List[str]] = {}
        # dotted module key -> paths; a key maps only when unambiguous
        self.python_module_index: Dict[str, Set[str]] = {}
        # (chunk id, path, symbol name, call sites) for chunks that call things
        self.caller_chunks: List[Tuple[Any, str, str, List[Dict[str, Any]]]] = []
        self._java_path_cache: Dict[str, Set[str]] = {}

        file_path_by_id: Dict[Any, str] = {}
        for file in files:
            path = (file.file_path or "").replace("\\", "/")
            file_path_by_id[file.id] = path
            self.path_index.add(path)
            self.language_by_path[path] = file.language
            basename = path.split("/")[-1]
            self.files_by_basename.setdefault(basename, []).append(path)

        self._build_python_module_index()
        for file in files:
            path = file_path_by_id[file.id]
            self.import_maps[path] = _import_map_for(
                file.language,
                file.imports_json or [],
                path,
                self.path_index,
            )

        for chunk_id, file_id, name, chunk_type, metadata in chunk_rows:
            if chunk_type not in STRUCTURAL_CHUNK_TYPES or not name:
                continue
            path = file_path_by_id.get(file_id)
            if not path:
                continue
            self.symbols_by_file.setdefault(path, {}).setdefault(name, chunk_id)
            self.global_symbols.setdefault(name, []).append((chunk_id, path))
            calls = (metadata or {}).get("calls") or []
            if calls:
                self.caller_chunks.append((chunk_id, path, name, calls))

    # ------------------------------------------------------------------ index

    def _build_python_module_index(self) -> None:
        """
        Map dotted module names to file paths, including path suffixes.

        Suffix keys are what make ``import app.services.repo_processor`` work
        when the package root is not the repository root (src layouts,
        monorepos). A suffix that matches more than one file is ambiguous and
        stays unusable rather than picking a winner.
        """
        for path in self.path_index:
            if not path.endswith(".py"):
                continue
            parts = path[:-3].split("/")
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            for start in range(len(parts)):
                key = ".".join(parts[start:])
                self.python_module_index.setdefault(key, set()).add(path)

    def _module_paths(self, target: str) -> Set[str]:
        """Dotted (or path-form) module -> concrete repo-relative file paths."""
        key = target.replace("/", ".").strip(".")
        if not key:
            return set()
        return set(self.python_module_index.get(key, ()))

    def _java_paths(self, target: str) -> Set[str]:
        cached = self._java_path_cache.get(target)
        if cached is not None:
            return set(cached)
        paths: Set[str] = set()
        if target in self.path_index:
            paths = {target}
        else:
            for path in self.path_index:
                if path.endswith(f"/{target}"):
                    paths = {path}
                    break
        if not paths:
            basename = target.split("/")[-1]
            paths = set(self.files_by_basename.get(basename, ()))
        self._java_path_cache[target] = paths
        return set(paths)

    def _alias_paths(self, target: str, language: Optional[str]) -> Set[str]:
        if language == "Python":
            return self._module_paths(target)
        if language in ("JavaScript", "TypeScript", "TSX"):
            return {target} if target in self.path_index else set()
        if language == "Java":
            return self._java_paths(target)
        return set()

    # -------------------------------------------------------------- resolution

    def _unique(self, symbol: str):
        matches = self.global_symbols.get(symbol, [])
        if len(matches) == 1:
            return matches[0][0], matches[0][1], RESOLUTION_UNIQUE
        return None, None, RESOLUTION_UNRESOLVED

    def _resolve_one(
        self, caller_path: str, callee_text: str, language: Optional[str]
    ) -> Tuple[Optional[Any], Optional[str], str]:
        parts = [part for part in callee_text.split(".") if part]
        if not parts:
            return None, None, RESOLUTION_UNRESOLVED

        symbol = parts[-1]
        import_map = self.import_maps.get(caller_path) or ImportMap()
        caller_symbols = self.symbols_by_file.get(caller_path, {})

        if parts[:-1]:
            qualifier = ".".join(parts[:-1])

            if qualifier in _SELF_RECEIVERS:
                # self.method() / this.method(): the target is the caller's
                # own file (its class) or a unique name.
                target = caller_symbols.get(symbol)
                if target is not None:
                    return target, caller_path, RESOLUTION_SAME_FILE
                return self._unique(symbol)

            # 1. Module alias prefixes, longest first: file_filter.skip_reason
            #    after ``import app.services.file_filter`` / ``import * as ns``.
            for size in range(len(parts) - 1, 0, -1):
                prefix = ".".join(parts[:size])
                if prefix in import_map.aliases:
                    for path in self._alias_paths(
                        import_map.aliases[prefix], language
                    ):
                        target = self.symbols_by_file.get(path, {}).get(symbol)
                        if target is not None:
                            return target, path, RESOLUTION_IMPORT

            # 2. A from-imported binding used as a namespace:
            #    ``from a import b`` then ``b.f()``.
            if qualifier in import_map.names:
                target_module, original = import_map.names[qualifier]
                candidates = set(self._alias_paths(target_module, language))
                candidates |= self._alias_paths(
                    f"{target_module}/{original}", language
                )
                for path in candidates:
                    target = self.symbols_by_file.get(path, {}).get(symbol)
                    if target is not None:
                        return target, path, RESOLUTION_IMPORT

            # 3. Unknown receiver (``obj.method()``): the class usually lives
            #    in the same file, else a unique project-wide name.
            target = caller_symbols.get(symbol)
            if target is not None:
                return target, caller_path, RESOLUTION_SAME_FILE
            return self._unique(symbol)

        # Bare name: local definition wins, then an explicit from-import,
        # then a C/C++ quoted include, then a unique project-wide name.
        if symbol in caller_symbols:
            return caller_symbols[symbol], caller_path, RESOLUTION_SAME_FILE

        if symbol in import_map.names:
            target_module, original = import_map.names[symbol]
            for path in self._alias_paths(target_module, language):
                target = self.symbols_by_file.get(path, {}).get(original)
                if target is not None:
                    return target, path, RESOLUTION_IMPORT

        if import_map.include_basenames:
            hits: Set[Tuple[Any, str]] = set()
            for basename in import_map.include_basenames:
                for path in self.files_by_basename.get(basename, ()):
                    target = self.symbols_by_file.get(path, {}).get(symbol)
                    if target is not None:
                        hits.add((target, path))
            if len(hits) == 1:
                target, path = next(iter(hits))
                return target, path, RESOLUTION_IMPORT

        return self._unique(symbol)

    # -------------------------------------------------------------------- run

    def resolve_all(self) -> CallGraphResult:
        edges: List[ResolvedEdge] = []
        for chunk_id, path, name, calls in self.caller_chunks:
            language = self.language_by_path.get(path)
            for call in calls:
                text = str(call.get("callee") or "")
                try:
                    line = int(call.get("line") or 0)
                except (TypeError, ValueError):
                    line = 0
                callee_chunk_id, callee_path, resolution = self._resolve_one(
                    path, text, language
                )
                edges.append(
                    ResolvedEdge(
                        caller_chunk_id=chunk_id,
                        caller_symbol=name,
                        caller_path=path,
                        callee_chunk_id=callee_chunk_id,
                        callee_symbol=text.split(".")[-1] or text,
                        callee_path=callee_path,
                        call_line=line,
                        resolution=resolution,
                    )
                )
        return CallGraphResult(call_sites=len(edges), edges=edges)


def build_call_graph(db: Session, repo: Repository) -> CallGraphResult:
    """
    Resolve the call graph for ``repo`` from its existing index rows.

    Reads ``File`` / ``Chunk`` rows only: safe to run against a repository
    that finished embedding, and deterministic for a given set of rows.
    """
    files = (
        db.query(File)
        .filter(File.repository_id == repo.id)
        .order_by(File.file_path)
        .all()
    )
    file_ids = [str(file.id) for file in files]

    chunk_rows: List[Sequence[Any]] = []
    for start in range(0, len(file_ids), SYMBOL_BATCH_SIZE):
        batch = file_ids[start : start + SYMBOL_BATCH_SIZE]
        chunk_rows.extend(
            db.execute(
                select(
                    Chunk.id,
                    Chunk.file_id,
                    Chunk.name,
                    Chunk.chunk_type,
                    Chunk.metadata_json,
                )
                .where(Chunk.file_id.in_(batch))
                .order_by(Chunk.start_line, Chunk.id)
            ).all()
        )

    resolver = _CallGraphResolver(files, chunk_rows)
    return resolver.resolve_all()


def persist_call_graph(db: Session, repo: Repository, result: CallGraphResult) -> None:
    """
    Store ``result`` as the repository's call graph, replacing any previous one.

    A reprocess must never merge with (or duplicate) a previous graph, so the
    old edges are dropped first.
    """
    db.query(CallEdge).filter(CallEdge.repository_id == repo.id).delete()
    db.commit()

    pending = 0
    for edge in result.edges:
        db.add(
            CallEdge(
                repository_id=repo.id,
                caller_chunk_id=edge.caller_chunk_id,
                caller_symbol=edge.caller_symbol,
                caller_path=edge.caller_path,
                callee_chunk_id=edge.callee_chunk_id,
                callee_symbol=edge.callee_symbol,
                callee_path=edge.callee_path,
                call_line=edge.call_line,
                resolution=edge.resolution,
            )
        )
        pending += 1
        if pending >= EDGE_COMMIT_BATCH:
            db.commit()
            pending = 0
    db.commit()


def build_and_persist_call_graph(db: Session, repo: Repository) -> CallGraphResult:
    """Build and store the call graph in one call."""
    result = build_call_graph(db, repo)
    persist_call_graph(db, repo, result)
    logger.info("%s: call graph built, %s", repo.full_name, result.summary())
    return result
