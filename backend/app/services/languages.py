"""
Language registry and structural symbol extraction.

The registry is data, not code: adding a language means adding a
:class:`LanguageSpec` entry and installing its grammar wheel. Nothing else in
the pipeline needs to change, and a language whose grammar is not installed
degrades to generic text chunking rather than being dropped.

A language is only ever *reported* as structurally parsed when a grammar was
actually available and the source parsed without errors, so a partially
understood file is never mistaken for a fully understood one.
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.parser import PythonCodeParser

# Parse outcomes surfaced on every file.
PARSE_STATE_STRUCTURAL = "structural"
PARSE_STATE_NO_GRAMMAR = "raw_no_grammar"
PARSE_STATE_UNMAPPED = "raw_unmapped"
PARSE_STATE_UNPARSEABLE = "raw_unparseable"

ALL_PARSE_STATES = (
    PARSE_STATE_STRUCTURAL,
    PARSE_STATE_NO_GRAMMAR,
    PARSE_STATE_UNMAPPED,
    PARSE_STATE_UNPARSEABLE,
)


@dataclass(frozen=True)
class LanguageSpec:
    """How to obtain a grammar for a language and what to look for in it."""

    name: str
    module: str
    attribute: str
    class_nodes: Tuple[str, ...] = ()
    function_nodes: Tuple[str, ...] = ()
    container_nodes: Tuple[str, ...] = ()
    #: Container node types that are a namespace/scope rather than a class.
    namespace_nodes: Tuple[str, ...] = ()
    import_nodes: Tuple[str, ...] = ()
    #: Node types that represent a function/method call. The callee text
    #: (``foo`` or ``obj.method``) is captured for the call-graph resolver.
    call_nodes: Tuple[str, ...] = ()
    extensions: Tuple[str, ...] = ()

    def kind_for(self, node_type: str) -> Optional[str]:
        if node_type in self.class_nodes:
            return "class"
        if node_type in self.function_nodes:
            return "function"
        if node_type in self.container_nodes:
            return "module"
        return None


_REGISTRY: Tuple[LanguageSpec, ...] = (
    LanguageSpec(
        name="Python",
        module="tree_sitter_python",
        attribute="language",
        class_nodes=("class_definition",),
        function_nodes=("function_definition",),
        namespace_nodes=(),
        import_nodes=("import_statement", "import_from_statement"),
        # Python normally goes through the stdlib-ast fast path, which extracts
        # calls itself; this entry keeps the registry honest for direct use.
        call_nodes=("call",),
        extensions=(".py", ".pyi"),
    ),
    LanguageSpec(
        name="JavaScript",
        module="tree_sitter_javascript",
        attribute="language",
        class_nodes=("class_declaration", "class"),
        function_nodes=(
            "function_declaration",
            "function_expression",
            "generator_function_declaration",
            "method_definition",
        ),
        container_nodes=("lexical_declaration", "variable_declaration"),
        import_nodes=("import_statement",),
        call_nodes=("call_expression",),
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
    ),
    LanguageSpec(
        name="TypeScript",
        module="tree_sitter_typescript",
        attribute="language_typescript",
        class_nodes=(
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
            "enum_declaration",
        ),
        function_nodes=(
            "function_declaration",
            "function_signature",
            "method_definition",
            "method_signature",
        ),
        container_nodes=("lexical_declaration", "variable_declaration", "type_alias_declaration"),
        import_nodes=("import_statement", "import_alias"),
        call_nodes=("call_expression",),
        extensions=(".ts", ".mts", ".cts"),
    ),
    LanguageSpec(
        name="TSX",
        module="tree_sitter_typescript",
        attribute="language_tsx",
        class_nodes=(
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
            "enum_declaration",
        ),
        function_nodes=(
            "function_declaration",
            "function_signature",
            "method_definition",
            "method_signature",
        ),
        container_nodes=("lexical_declaration", "variable_declaration", "type_alias_declaration"),
        import_nodes=("import_statement", "import_alias"),
        call_nodes=("call_expression",),
        extensions=(".tsx",),
    ),
    LanguageSpec(
        name="Go",
        module="tree_sitter_go",
        attribute="language",
        class_nodes=("type_declaration",),
        function_nodes=("function_declaration", "method_declaration"),
        container_nodes=("type_spec",),
        import_nodes=("import_declaration", "import_spec"),
        call_nodes=("call_expression",),
        extensions=(".go",),
    ),
    LanguageSpec(
        name="Rust",
        module="tree_sitter_rust",
        attribute="language",
        class_nodes=("struct_item", "enum_item", "union_item", "trait_item"),
        function_nodes=("function_item", "function_signature_item"),
        container_nodes=("impl_item", "mod_item"),
        namespace_nodes=("mod_item",),
        import_nodes=("use_declaration", "extern_crate_declaration"),
        call_nodes=("call_expression",),
        extensions=(".rs",),
    ),
    LanguageSpec(
        name="Java",
        module="tree_sitter_java",
        attribute="language",
        class_nodes=(
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
            "annotation_type_declaration",
        ),
        function_nodes=("method_declaration", "constructor_declaration"),
        import_nodes=("import_declaration",),
        # Java's call node reports only the bare method name; the receiver
        # expression is not part of the ``name`` field.
        call_nodes=("method_invocation",),
        extensions=(".java",),
    ),
    LanguageSpec(
        name="C",
        module="tree_sitter_c",
        attribute="language",
        class_nodes=("struct_specifier", "union_specifier", "enum_specifier", "type_definition"),
        function_nodes=("function_definition",),
        import_nodes=("preproc_include",),
        call_nodes=("call_expression",),
        extensions=(".c", ".h"),
    ),
    LanguageSpec(
        name="C++",
        module="tree_sitter_cpp",
        attribute="language",
        class_nodes=(
            "class_specifier",
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
            "namespace_definition",
        ),
        function_nodes=("function_definition", "template_declaration"),
        import_nodes=("preproc_include",),
        call_nodes=("call_expression",),
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".ipp"),
    ),
    LanguageSpec(
        name="Ruby",
        module="tree_sitter_ruby",
        attribute="language",
        class_nodes=("class", "module", "singleton_class"),
        function_nodes=("method", "singleton_method"),
        namespace_nodes=("module",),
        import_nodes=(),
        call_nodes=("call",),
        extensions=(".rb", ".rake"),
    ),
    LanguageSpec(
        name="PHP",
        module="tree_sitter_php",
        attribute="language_php",
        class_nodes=("class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"),
        function_nodes=("function_definition", "method_declaration"),
        import_nodes=("namespace_use_declaration",),
        call_nodes=("function_call_expression", "member_call_expression"),
        extensions=(".php", ".phtml"),
    ),
)

#: Extensions deliberately mapped to a human label but no grammar, so the file is
#: still indexed as text while reporting an honest parse state.
TEXT_ONLY: Dict[str, str] = {
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".rst": "reStructuredText",
    ".txt": "Text",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".ini": "INI",
    ".cfg": "INI",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".less": "Less",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".graphql": "GraphQL",
    ".gql": "GraphQL",
    ".proto": "Protobuf",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".tf": "Terraform",
    ".dockerfile": "Dockerfile",
    ".env": "Dotenv",
    ".sample": "Dotenv",
    ".example": "Dotenv",
    ".template": "Dotenv",
    ".xml": "XML",
    ".csv": "CSV",
    ".ipynb": "Jupyter Notebook",
}

BY_EXTENSION: Dict[str, LanguageSpec] = {}
for _spec in _REGISTRY:
    for _ext in _spec.extensions:
        BY_EXTENSION[_ext] = _spec

GRAMMAR_CACHE: Dict[str, Any] = {}
_GRAMMAR_LOCK = threading.RLock()
_UNAVAILABLE: set = set()


def spec_for_extension(extension: str) -> Optional[LanguageSpec]:
    """Return the grammar spec for a file extension, or ``None`` if unmapped."""
    if not extension:
        return None
    return BY_EXTENSION.get(extension.lower())


def text_only_language(extension: str) -> Optional[str]:
    """Return a display label for a text format that has no grammar."""
    if not extension:
        return None
    return TEXT_ONLY.get(extension.lower())


def all_supported_extensions() -> Tuple[str, ...]:
    return tuple(sorted(set(BY_EXTENSION) | set(TEXT_ONLY)))


def language_coverage() -> Dict[str, bool]:
    """Map every registered language to whether its grammar is importable."""
    coverage: Dict[str, bool] = {}
    with _GRAMMAR_LOCK:
        for spec in _REGISTRY:
            coverage[spec.name] = _load_grammar(spec) is not None
    return coverage


def _load_grammar(spec: LanguageSpec):
    """Return a compiled tree-sitter Parser, or ``None`` if unavailable.

    A grammar that cannot be imported or compiled is remembered as unavailable
    so a missing wheel costs one failed import rather than one per file.
    """
    key = f"{spec.module}:{spec.attribute}"
    with _GRAMMAR_LOCK:
        if key in GRAMMAR_CACHE:
            return GRAMMAR_CACHE[key]
        if key in _UNAVAILABLE:
            return None
        try:
            import tree_sitter

            module = importlib.import_module(spec.module)
            language = tree_sitter.Language(getattr(module, spec.attribute)())
            parser = tree_sitter.Parser(language)
        except Exception:
            _UNAVAILABLE.add(key)
            return None
        GRAMMAR_CACHE[key] = parser
        return parser


_IDENTIFIER_TYPES = (
    "identifier",
    "type_identifier",
    "field_identifier",
    "property_identifier",
    "constant",
    "simple_identifier",
)

#: Containers whose own name lives one level down, so a direct child scan misses it.
_NAME_HOLDER_NODES = (
    "variable_declarator",
    "type_spec",
    "declarator",
    "init_declarator",
    "function_declarator",
    "pointer_declarator",
    "array_declarator",
    "reference_declarator",
)


def _find_identifier(node) -> Optional[str]:
    for child in node.named_children:
        if child.type in _IDENTIFIER_TYPES:
            return child.text.decode("utf-8", errors="ignore")
        found = _find_identifier(child)
        if found:
            return found
    return None


def _node_name(node) -> Optional[str]:
    named = node.child_by_field_name("name")
    if named is not None:
        return named.text.decode("utf-8", errors="ignore")
    for child in node.named_children:
        if child.type in _IDENTIFIER_TYPES:
            return child.text.decode("utf-8", errors="ignore")
        if child.type in _NAME_HOLDER_NODES:
            found = _find_identifier(child)
            if found:
                return found
    return None


def _enclosing_prefix(node) -> str:
    parts: List[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type in ("class", "class_definition", "class_declaration", "impl_item",
                           "module", "mod_item", "namespace_definition", "struct_specifier"):
            name = _node_name(parent)
            if name:
                parts.append(name)
        parent = parent.parent
    return ".".join(reversed(parts))


@dataclass
class ParsedFile:
    """Structural result for a single source file."""

    state: str
    language: str
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)

    @property
    def is_structural(self) -> bool:
        return self.state == PARSE_STATE_STRUCTURAL


#: Files with no useful extension that are still worth indexing as text.
FILENAME_LANGUAGES = {
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "cmakelists.txt": "CMake",
    "jenkinsfile": "Groovy",
    "rakefile": "Ruby",
    "gemfile": "Ruby",
    "procfile": "Procfile",
    "license": "License",
    "notice": "Notice",
    "readme": "Text",
}


def looks_binary(source_code: str) -> bool:
    """A NUL byte in the first few KB is the standard binary sniffer heuristic."""
    return "\x00" in source_code[:8192]


def parse_symbols_for_file(
    filename: str, extension: str, source_code: str
) -> ParsedFile:
    """
    Content-aware entry point used by the ingestion walk.

    An empty ``language`` means the payload is not text and must not be indexed.
    """
    if looks_binary(source_code):
        return ParsedFile(state=PARSE_STATE_UNPARSEABLE, language="")

    stem = filename.lower()
    if stem in FILENAME_LANGUAGES:
        return ParsedFile(state=PARSE_STATE_UNMAPPED, language=FILENAME_LANGUAGES[stem])
    if stem.startswith("dockerfile."):
        return ParsedFile(state=PARSE_STATE_UNMAPPED, language="Dockerfile")
    if stem.startswith(".env"):
        return ParsedFile(state=PARSE_STATE_UNMAPPED, language="Dotenv")

    parsed = parse_symbols(source_code, extension)
    if parsed.language == "Unknown":
        # An unmapped extension is still text worth indexing; "Text" is a more
        # honest label than "Unknown" once we have already ruled out binary.
        return ParsedFile(state=PARSE_STATE_UNMAPPED, language="Text")
    return parsed


def parse_symbols(source_code: str, extension: str) -> ParsedFile:
    """
    Extract structural chunks from a source file.

    Never raises and never returns nothing: a file that cannot be understood
    structurally comes back as one of the ``raw_*`` states with no blocks, and
    the caller applies generic chunking. This upholds the rule that a missing
    grammar degrades the index rather than shrinking it.
    """
    spec = spec_for_extension(extension)

    if spec is None:
        return ParsedFile(
            state=PARSE_STATE_UNMAPPED,
            language=text_only_language(extension) or "Unknown",
        )

    if spec.name == "Python":
        return _parse_python(source_code, spec)

    parser = _load_grammar(spec)
    if parser is None:
        return ParsedFile(state=PARSE_STATE_NO_GRAMMAR, language=spec.name)

    try:
        tree = parser.parse(source_code.encode("utf-8", errors="ignore"))
    except Exception:
        return ParsedFile(state=PARSE_STATE_NO_GRAMMAR, language=spec.name)

    root = tree.root_node
    if root.has_error:
        return ParsedFile(state=PARSE_STATE_UNPARSEABLE, language=spec.name)

    lines = source_code.splitlines()
    blocks: List[Dict[str, Any]] = []
    imports: List[str] = []
    calls: List[Tuple[str, int]] = []

    def visit(node) -> None:
        kind = spec.kind_for(node.type)
        if kind is not None:
            start = node.start_point[0]
            end = node.end_point[0]
            blocks.append(
                {
                    "chunk_type": kind,
                    "name": _node_name(node) or "anonymous",
                    "content": "\n".join(lines[start:end]),
                    "start_line": start + 1,
                    "end_line": end + 1,
                    "metadata_json": {"qualified_name": _qualified(spec, node, kind)},
                }
            )
        elif node.type in spec.import_nodes:
            text = node.text.decode("utf-8", errors="ignore").strip()
            if text:
                imports.append(text)
        elif node.type in spec.call_nodes:
            callee = _callee_text(node)
            if callee:
                calls.append((callee, node.start_point[0] + 1))
        for child in node.named_children:
            visit(child)

    visit(root)
    _attach_calls_to_blocks(blocks, calls)

    return ParsedFile(
        state=PARSE_STATE_STRUCTURAL,
        language=spec.name,
        blocks=blocks,
        imports=imports,
    )


#: Cap on call sites recorded per symbol. A pathological generated file can
#: contain thousands of calls in one function; the cap keeps its chunk metadata
#: bounded. Calls beyond the cap are dropped, not truncated mid-list.
MAX_CALLS_PER_SYMBOL = 500

#: A callee text longer than this is almost certainly a generated expression,
#: not something a reader would trace, so it is not recorded.
MAX_CALLEE_TEXT_CHARS = 200


def _callee_text(node) -> Optional[str]:
    """
    The callee of a call node, as text (``foo`` or ``obj.method``).

    Grammars differ on which field carries the callee: the C-family puts it in
    ``function``, Java in ``name`` and Ruby in ``method``. The full text is
    kept (including the receiver) because that qualifier is what the
    call-graph resolver uses to find the defining file.
    """
    for field_name in ("function", "name", "method"):
        part = node.child_by_field_name(field_name)
        if part is not None:
            text = part.text.decode("utf-8", errors="ignore").strip()
            if text and len(text) <= MAX_CALLEE_TEXT_CHARS:
                return text
    return None


def _attach_calls_to_blocks(
    blocks: List[Dict[str, Any]], calls: List[Tuple[str, int]]
) -> None:
    """
    Attach call sites to the innermost block whose line range contains them.

    Line-range attribution is what keeps a call inside a nested function with
    that function's chunk instead of the outer one. A call outside any block
    (module-level code) is dropped: the call graph is function-to-function, and
    inventing a "module" caller would be a guess, not a fact.
    """
    if not calls or not blocks:
        return

    for callee, line in calls:
        best = None
        best_span = -1
        for block in blocks:
            start = block.get("start_line") or 0
            end = block.get("end_line") or 0
            if start <= line <= end:
                span = end - start
                # Innermost block wins: smallest range, then latest start.
                if best is None or span < best_span or (
                    span == best_span and start > (best.get("start_line") or 0)
                ):
                    best = block
                    best_span = span
        if best is None:
            continue

        # NB: an existing ``metadata_json`` is an empty dict at this point,
        # which is falsy — ``setdefault(...) or {}`` would hand back a
        # throwaway dict and silently drop every recorded call.
        metadata = best.setdefault("metadata_json", {})
        if metadata is None:
            metadata = best["metadata_json"] = {}
        recorded = metadata.setdefault("calls", [])
        if len(recorded) < MAX_CALLS_PER_SYMBOL:
            recorded.append({"callee": callee, "line": line})


def _qualified(spec: LanguageSpec, node, kind: str) -> str:
    name = _node_name(node) or "anonymous"
    if spec.name == "Python":
        return name
    prefix = _enclosing_prefix(node)
    return f"{prefix}.{name}" if prefix else name


def _parse_python(source_code: str, spec: LanguageSpec) -> ParsedFile:
    """Use the existing stdlib ``ast`` parser as the Python fast path."""
    try:
        python_parser = PythonCodeParser(source_code)
        blocks = python_parser.parse()
        calls = python_parser.extract_calls()
        imports = python_parser.extract_imports()
    except Exception:
        return ParsedFile(state=PARSE_STATE_UNPARSEABLE, language=spec.name)

    if blocks and all(block.get("chunk_type") == "raw" for block in blocks):
        return ParsedFile(state=PARSE_STATE_UNPARSEABLE, language=spec.name)

    _attach_calls_to_blocks(blocks, calls)

    for block in blocks:
        metadata = block.setdefault("metadata_json", {})
        if metadata is None:
            metadata = block["metadata_json"] = {}
        metadata.setdefault("qualified_name", block.get("name") or "anonymous")
    return ParsedFile(
        state=PARSE_STATE_STRUCTURAL,
        language=spec.name,
        blocks=blocks,
        imports=imports,
    )
