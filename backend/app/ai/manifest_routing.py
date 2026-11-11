"""
Routing for architecture and call-graph questions.

Docs and config files no longer produce embedded chunks (see
``repo_processor``), so dense retrieval alone cannot answer questions like
"how is this repo organised?". Those questions need the deterministic manifest:
languages, entrypoints, directory rollups and the symbol index.

Call-graph questions ("who calls X?", "what does X call?") have the same
problem for a different reason: the answer is a relation between functions,
not a passage of text, so retrieval surfaces the caller or the callee but
rarely both with their file and line. Those questions get the stored call
edges (see ``call_graph_service``), labelled with how each target was found.

The router here is intentionally keyword-based and deterministic rather than an
LLM call. It runs before the chain is built, costs nothing, and is easy to test.
When a question looks architectural, the manifest markdown is prepended to the
retrieved context instead of replacing it, so a question that mixes "where is the
CLI entrypoint" with "what does it call" still gets both views.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.call_edge import CallEdge
from app.models.repo_manifest import RepoManifest as RepoManifestRow
from app.services.manifest_service import get_manifest

logger = logging.getLogger(__name__)

#: Ceiling on manifest characters injected into a prompt. The OpenShell manifest
#: renders to ~43 KB; most of that is the symbol list, which dense retrieval is
#: better at. The overview sections that routing actually needs are far smaller,
#: so the manifest is truncated to its orientation part.
MAX_MANIFEST_CONTEXT_CHARS = 12000

#: Terms that signal the question is about repository shape rather than a
#: specific implementation. Matched case-insensitively as whole words where
#: possible, so "class" does not fire on "classic".
_ARCHITECTURE_PATTERNS = [
    r"over(ar)?view\b",
    r"architecture\b",
    r"how (is|are) .* (organi[sz]ed|structured|laid out)",
    r"repo(sitory)? (structure|layout|organi[sz]ation)",
    r"code ?base (structure|layout|overview|organi[sz]ation)",
    r"project (structure|layout)",
    r"high[- ]level (structure|view|design)",
    r"what (languages|technolog(y|ies))\b",
    r"entry ?points?\b",
    r"entry ?files?\b",
    r"main (entry ?points?|modules?)\b",
    r"where does .* (start|begin)",
    r"directory (structure|layout|map|tree)",
    r"folder (structure|layout)\b",
    r"file (structure|layout)\b",
    r"what (is|are) in (this|the) repo",
    r"what (does|do) (this|the) repo(sitory)? (do|contain|include)",
    # "largest module" and "modules are the largest" are both natural phrasings,
    # so match the adjective on either side of the noun.
    r"\b(biggest|largest)\b[^?]{0,20}\b(module|director(y|ies)|package|component)",
    r"\b(module|director(y|ies)|package|component)s?\b[^?]{0,20}\b(biggest|largest)\b",
    r"tech stack\b",
    r"what are the (main|primary|core) (modules|components|packages|directories)",
    r"summari[sz]e (the|this) (repo|codebase|project|structure)",
    r"give me (an|a) (overview|tour|map) of",
    r"how many (files|modules|packages)",
    r"what (modules|packages|components) (are|exist)",
]

_ARCHITECTURE_RE = re.compile(
    r"|".join(_ARCHITECTURE_PATTERNS), re.IGNORECASE
)


def is_architecture_question(question: str) -> bool:
    """
    Return True when ``question`` is about repository shape.

    Conservative by design: a false positive only adds context, it never removes
    vector retrieval. A false negative is the real cost, since the docs/config
    text is no longer in the vector store.
    """
    if not question or not question.strip():
        return False
    return bool(_ARCHITECTURE_RE.search(question))


def _overview_markdown(markdown: str) -> str:
    """
    Return the orientation part of the rendered manifest.

    The full markdown ends with a per-language symbol dump. Symbol names are
    already retrievable through the vector store, and pasting 28,000 of them
    would crowd out the retrieved code. Keep everything from the top through the
    directory rollups, then stop.
    """
    for marker in ("\n## Symbols by language", "\n## Docs and config inventory"):
        index = markdown.find(marker)
        if index != -1:
            return markdown[:index].rstrip()
    return markdown


def load_manifest_context(
    db: Session, repo_id: str, max_chars: int = MAX_MANIFEST_CONTEXT_CHARS
) -> Optional[str]:
    """
    Return manifest context for ``repo_id``, or ``None`` if unavailable.

    Returns ``None`` rather than raising: a missing manifest degrades the answer
    to plain vector retrieval, which is what happened before manifests existed.
    """
    try:
        row: Optional[RepoManifestRow] = get_manifest(db, repo_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Manifest lookup failed for %s: %s", repo_id, exc)
        return None

    if row is None or not row.markdown:
        logger.info("No stored manifest for %s; skipping manifest routing", repo_id)
        return None

    context = _overview_markdown(row.markdown)
    if len(context) > max_chars:
        context = context[:max_chars].rstrip() + "\n\n(manifest truncated)"
    return context


# --------------------------------------------------------------------------
# Call-graph routing. "Who calls X?" needs the stored call edges, which dense
# retrieval over chunk text answers unreliably: the answer is a relation, not
# a passage. Same rules as architecture routing: keyword-based, deterministic,
# additive — routing can only add context, never replace vector retrieval.
# --------------------------------------------------------------------------

#: Ceiling on call-graph characters injected into a prompt. The stored edges
#: for a hot symbol can run long; the prompt needs the relation, not every
#: call site ever recorded.
MAX_CALLGRAPH_CONTEXT_CHARS = 6000

#: How many distinct symbols from one question get their edges rendered.
MAX_SYMBOLS_PER_QUESTION = 3

#: Edge rows rendered per symbol, per direction, before the section is cut.
MAX_EDGES_PER_SYMBOL = 20

_CALL_PATTERNS = [
    r"\bwhat calls\b",
    r"\bwho calls\b",
    r"\bwhat (does|do) [^.?]*\bcall\b",
    r"\bcalled by\b",
    r"\bcallers of\b",
    r"\bcallees of\b",
    r"\bcall (graph|chain|tree|path)s?\b",
    r"\bwho (invokes|executes)\b",
    r"\bwhat invokes\b",
    r"\b(function|method) (dependencies|dependenc(y|ies))\b",
    r"\btrace [^.?]*\b(call|execution)\b",
    r"\b(call|execution) (chain|path|flow)\b",
]

_CALL_RE = re.compile(r"|".join(_CALL_PATTERNS), re.IGNORECASE)

#: Words that match the identifier shapes or the "who calls X" phrase but are
#: question phrasing, not code.
_SYMBOL_STOPWORDS = frozenset({
    "call_graph", "call_chain", "call_tree", "call_paths", "call_path",
    "called_by", "callers_of", "callees_of",
    # Pronouns and question words that can follow "called by" / "calls".
    "whom", "who", "what", "it", "them", "this", "that", "these", "those",
    "everything", "anything", "something", "someone", "somebody", "each",
    "all", "any", "some", "here", "there",
})

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_SNAKE_CASE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_CAMEL_CASE_RE = re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]*)+\b")
_PASCAL_CASE_RE = re.compile(r"\b[A-Z][a-z0-9]*(?:[A-Z][a-z0-9]*)+\b")

#: "who calls parse?" names a plain lowercase word that no identifier-shape
#: regex would catch, so the phrase itself is parsed for the target.
_DIRECT_TARGET_RE = re.compile(
    r"(?:\bwho|\bwhat)\s+calls\s+([a-zA-Z_][\w.]*)"
    r"|\bcallers\s+of\s+([a-zA-Z_][\w.]*)"
    r"|\bcalled\s+by\s+([a-zA-Z_][\w.]*)"
    r"|\bcallees\s+of\s+([a-zA-Z_][\w.]*)",
    re.IGNORECASE,
)


def is_call_question(question: str) -> bool:
    """
    Return True when ``question`` is about who calls what.

    Conservative, like :func:`is_architecture_question`: a false positive
    only adds context (and costs one cheap indexed lookup that finds
    nothing); a false negative loses the only source that can answer.
    """
    if not question or not question.strip():
        return False
    return bool(_CALL_RE.search(question))


def extract_candidate_symbols(question: str) -> List[str]:
    """
    Guess which symbols the question is about, most specific first.

    Backticked or quoted spans win (the user marked them as code), then
    snake_case and camelCase/PascalCase identifiers in the raw text. The
    callee/caller names in the stored graph are bare names, so a dotted span
    like ``os.path.join`` contributes its last segment only.
    """
    candidates: List[str] = []

    def add(value: str) -> None:
        value = value.strip().strip("`'\"")
        # A dotted reference names its target in the last segment.
        value = value.split(".")[-1].strip()
        if (
            value
            and len(value) <= 100
            and value not in candidates
            and value.lower() not in _SYMBOL_STOPWORDS
        ):
            candidates.append(value)

    for match in _BACKTICK_RE.findall(question or ""):
        add(match)

    for groups in _DIRECT_TARGET_RE.findall(question or ""):
        for value in groups:
            if value:
                add(value)

    text = _BACKTICK_RE.sub(" ", question or "")
    for pattern in (_SNAKE_CASE_RE, _CAMEL_CASE_RE, _PASCAL_CASE_RE):
        for match in pattern.findall(text):
            add(match)

    return candidates[:MAX_SYMBOLS_PER_QUESTION]


def _render_callgraph_section(
    symbol: str, rows: List[CallEdge]
) -> Optional[str]:
    """Render one symbol's callers and callees as markdown, or None if empty."""
    callers = [row for row in rows if row.callee_symbol == symbol]
    callees = [row for row in rows if row.caller_symbol == symbol]
    if not callers and not callees:
        return None

    lines: List[str] = [f"### `{symbol}`"]

    if callers:
        lines.append(f"Callers of `{symbol}` ({len(callers)} call site(s)):")
        for row in callers[:MAX_EDGES_PER_SYMBOL]:
            lines.append(
                f"- `{row.caller_symbol}` at {row.caller_path}:{row.call_line} "
                f"[{row.resolution}]"
            )
        if len(callers) > MAX_EDGES_PER_SYMBOL:
            lines.append(f"- … and {len(callers) - MAX_EDGES_PER_SYMBOL} more")

    if callees:
        lines.append(f"Callees of `{symbol}` ({len(callees)} call site(s)):")
        for row in callees[:MAX_EDGES_PER_SYMBOL]:
            target = f"`{row.callee_symbol}`"
            if row.callee_path:
                target += f" at {row.callee_path}"
            lines.append(f"- called from line {row.call_line}: {target} [{row.resolution}]")
        if len(callees) > MAX_EDGES_PER_SYMBOL:
            lines.append(f"- … and {len(callees) - MAX_EDGES_PER_SYMBOL} more")

    return "\n".join(lines)


def load_callgraph_context(
    db: Session, repo_id: str, question: str, max_chars: int = MAX_CALLGRAPH_CONTEXT_CHARS
) -> Optional[str]:
    """
    Return stored call-graph context for the question, or ``None``.

    ``None`` rather than raising, mirroring :func:`load_manifest_context`: a
    missing graph (not yet migrated, or nothing resolved) degrades the answer
    to plain retrieval, which is what happened before the graph existed.
    """
    symbols = extract_candidate_symbols(question)
    if not symbols:
        return None

    try:
        rows = (
            db.query(CallEdge)
            .filter(
                CallEdge.repository_id == repo_id,
                or_(
                    CallEdge.callee_symbol.in_(symbols),
                    CallEdge.caller_symbol.in_(symbols),
                ),
            )
            .order_by(CallEdge.caller_path, CallEdge.call_line)
            .all()
        )
    except Exception as exc:  # e.g. the table does not exist yet on an old DB
        logger.warning("Call graph lookup failed for %s: %s", repo_id, exc)
        return None

    if not rows:
        logger.info("No stored call edges for %s; skipping call routing", repo_id)
        return None

    sections: List[str] = []
    for symbol in symbols:
        section = _render_callgraph_section(symbol, rows)
        if section:
            sections.append(section)

    if not sections:
        return None

    context = "\n\n".join(sections)
    if len(context) > max_chars:
        context = context[:max_chars].rstrip() + "\n\n(call graph truncated)"
    return context