from pydantic import BaseModel, HttpUrl, UUID4, field_validator
from datetime import datetime
from app.models.repository import RepoStatus
import re

class RepositoryCreate(BaseModel):
    github_url: HttpUrl

    @field_validator('github_url')
    @classmethod
    def validate_github_url(cls, v):
        url_str = str(v)
        # Basic regex to ensure it's a GitHub repo URL
        pattern = r"^https?://(www\.)?github\.com/[\w.-]+/[\w.-]+/?$"
        if not re.match(pattern, url_str):
            raise ValueError("Must be a valid GitHub repository URL (e.g., https://github.com/user/repo)")
        return v

class RepositoryResponse(BaseModel):
    id: UUID4
    github_url: str
    name: str
    full_name: str
    status: RepoStatus
    error_message: str | None
    created_at: datetime
    has_embeddings: bool = False
    # True when the repo can answer questions at all: either it has vectors, or
    # it is a docs/config-only repo represented entirely by its manifest.
    is_queryable: bool = False
    # Honest indexing progress, surfaced so the UI can show how far a live
    # import has got instead of a bare status label.
    chunk_count: int = 0
    embedded_count: int = 0
    languages: dict = {}

    class Config:
        from_attributes = True


class StructureResponse(BaseModel):
    """
    Deterministic architecture summary for a repository.

    ``manifest`` carries the full payload (languages, directories, entrypoints,
    symbol index, doc inventory). ``markdown`` is the rendered view used for
    display and for architecture-oriented retrieval.
    """

    repository_id: UUID4
    full_name: str
    file_count: int
    symbol_count: int
    manifest: dict
    markdown: str

    class Config:
        from_attributes = True


class DiagramProgress(BaseModel):
    """How much of a repository is represented in the diagrams right now."""

    embedded: int = 0
    chunk_count: int = 0
    live_files: int = 0
    total_files: int = 0


class CallEdgeOut(BaseModel):
    """
    One resolved call site.

    ``resolution`` records how the target was found, so an edge is never
    mistaken for a verified link: ``same_file`` (defined in the caller's own
    file), ``import`` (the caller's imports pointed at the defining file),
    ``unique`` (exactly one project-wide symbol with that name) or
    ``unresolved`` (stdlib, third-party or dynamic dispatch; the call site is
    kept and labelled rather than guessed).
    """

    caller_symbol: str
    caller_path: str
    callee_symbol: str
    callee_path: str | None = None
    call_line: int
    resolution: str


class CallGraphResponse(BaseModel):
    """Callers or callees of one symbol, one row per call site."""

    repository_id: UUID4
    full_name: str
    symbol: str
    direction: str
    total: int
    edges: list[CallEdgeOut]


class CallGraphSummaryResponse(BaseModel):
    """Honest counts for a repository's call graph, including unresolved."""

    repository_id: UUID4
    full_name: str
    call_sites: int
    resolved: int
    by_resolution: dict[str, int]
    top_callees: list[dict]


class DiagramsResponse(BaseModel):
    """
    Architecture diagrams for a repository, as Mermaid source.

    ``diagrams`` maps a view name (``languages``, ``directories``,
    ``entrypoints``) to a Mermaid string. ``graphs`` carries the same views as
    structured nodes/edges with stable ids, which the frontend renders as SVG so
    it can animate files joining the diagram as an import progresses. Both are
    derived from the stored manifest, so the output is deterministic and needs no
    clone or LLM call.

    While a repository is still indexing, ``partial`` is true and the views are a
    live preview built from the files already represented in the index (a
    transient manifest, never persisted). ``progress`` describes how far the
    import has got so the frontend can poll and animate files joining in.
    """

    repository_id: UUID4
    full_name: str
    diagrams: dict
    graphs: dict = {}
    partial: bool = False
    status: RepoStatus
    progress: DiagramProgress | None = None

    class Config:
        from_attributes = True