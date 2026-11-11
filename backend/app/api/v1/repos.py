from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List

from fastapi.responses import PlainTextResponse

from app.db.session import get_db
from app.models.user import User
from app.models.call_edge import CallEdge
from app.models.file import File
from app.models.repository import Repository, RepoStatus
from app.models.repo_manifest import RepoManifest
from app.schemas.repository import (
    CallGraphResponse,
    CallGraphSummaryResponse,
    CallEdgeOut,
    DiagramProgress,
    DiagramsResponse,
    RepositoryCreate,
    RepositoryResponse,
    StructureResponse,
)
from app.api.deps import get_current_user
from app.services import languages
from app.ai.diagrams import build_diagrams, build_graphs
from app.services.manifest_service import (
    build_and_persist_manifest,
    build_live_manifest,
    get_manifest,
)
from app.services.github_service import (
    cleanup_repository_artifacts,
    extract_repo_info,
    process_repository_background_task,
)
from app.services.call_graph_service import RESOLUTION_UNRESOLVED

router = APIRouter()


def _language_breakdown(db: Session, repo_id: str) -> dict:
    """Per-language file counts and how thoroughly each language was parsed."""
    rows = (
        db.query(File.language, File.parse_state, func.count(File.id))
        .filter(File.repository_id == repo_id)
        .group_by(File.language, File.parse_state)
        .all()
    )
    breakdown: dict = {}
    for language, parse_state, count in rows:
        entry = breakdown.setdefault(
            language or "Unknown", {"files": 0, "structural_files": 0, "raw_files": 0}
        )
        entry["files"] += count
        if parse_state == languages.PARSE_STATE_STRUCTURAL:
            entry["structural_files"] += count
        else:
            entry["raw_files"] += count
    return breakdown


def _serialize_repository(repo: Repository, db: Session | None = None) -> dict:
    """Single source of truth for the repository payload."""
    payload = {
        "id": repo.id,
        "github_url": repo.github_url,
        "name": repo.name,
        "full_name": repo.full_name,
        "status": repo.status,
        "error_message": repo.error_message,
        "created_at": repo.created_at,
        "has_embeddings": repo.has_embeddings,
        "is_queryable": repo.is_queryable,
        "chunk_count": repo.chunk_count or 0,
        "embedded_count": repo.embedded_count or 0,
        "languages": _language_breakdown(db, repo.id) if db is not None else {},
    }
    return payload


def _get_owned_repository(repo_id: str, db: Session, current_user: User) -> Repository:
    repo = db.query(Repository).filter(
        Repository.id == repo_id,
        Repository.owner_id == current_user.id
    ).first()

    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    return repo


@router.post("/", response_model=RepositoryResponse, status_code=status.HTTP_202_ACCEPTED)
def create_repository(
    repo_in: RepositoryCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Submit a GitHub URL to be tracked and cloned."""
    url_str = str(repo_in.github_url).rstrip("/")
    existing_repo = db.query(Repository).filter(
        Repository.owner_id == current_user.id,
        Repository.github_url == url_str
    ).first()

    if existing_repo:
        raise HTTPException(status_code=400, detail="You have already imported this repository.")

    try:
        owner, name = extract_repo_info(url_str)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    full_name = f"{owner}/{name}"

    db_repo = Repository(
        owner_id=current_user.id,
        github_url=url_str,
        name=name,
        full_name=full_name
    )
    db.add(db_repo)
    db.commit()
    db.refresh(db_repo)

    background_tasks.add_task(process_repository_background_task, str(db_repo.id), db)
    return _serialize_repository(db_repo, db)


@router.get("/", response_model=List[RepositoryResponse])
def get_user_repositories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all repositories for the current user."""
    return [_serialize_repository(repo, db) for repo in current_user.repositories]


@router.get("/{repo_id}", response_model=RepositoryResponse)
def get_repository(
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get status of a specific repository."""
    return _serialize_repository(_get_owned_repository(repo_id, db, current_user), db)


@router.get("/{repo_id}/structure", response_model=StructureResponse)
def get_repository_structure(
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Return the deterministic architecture manifest for a repository.

    Builds the manifest on first request from existing index rows, so no clone,
    re-embedding, or LLM call is involved. 409s while the repository is still
    indexing, because a partial index would produce a misleading manifest.
    """
    repo = _get_owned_repository(repo_id, db, current_user)

    # is_complete rather than has_embeddings: a docs/config-only repository has
    # no vectors but still has a complete, meaningful manifest.
    if not repo.is_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Repository is still being indexed. The structure manifest is "
                "only available once indexing has completed."
            ),
        )

    row = get_manifest(db, repo.id)
    if row is None:
        row = build_and_persist_manifest(db, repo)

    return StructureResponse(
        repository_id=repo.id,
        full_name=repo.full_name,
        file_count=row.file_count,
        symbol_count=row.symbol_count,
        manifest=row.manifest_json,
        markdown=row.markdown,
    )


@router.get(
    "/{repo_id}/structure.md",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/markdown": {}}}},
)
def get_repository_structure_markdown(
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Rendered manifest only, as text/markdown."""
    repo = _get_owned_repository(repo_id, db, current_user)

    if not repo.is_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository is still being indexed.",
        )

    row = get_manifest(db, repo.id) or build_and_persist_manifest(db, repo)
    return PlainTextResponse(row.markdown, media_type="text/markdown")


@router.get("/{repo_id}/diagrams", response_model=DiagramsResponse)
def get_repository_diagrams(
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Return Mermaid architecture diagrams for a repository.

    A completed repository is served from its stored manifest, exactly as
    before (no clone, no re-embedding, no LLM call). While indexing is still
    running, the response is a *live preview* (``partial: true``) built from a
    transient manifest over the files already represented in the index. That
    transient manifest is never persisted, so the canonical manifest and the
    ``is_complete`` gates around it are untouched.
    """
    repo = _get_owned_repository(repo_id, db, current_user)

    if repo.status == RepoStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Repository indexing failed, so its architecture cannot be "
                "shown. Reprocess the repository to try again."
            ),
        )

    if not repo.is_complete:
        live = build_live_manifest(db, repo)
        payload = live.manifest.to_payload()
        return DiagramsResponse(
            repository_id=repo.id,
            full_name=repo.full_name,
            diagrams=build_diagrams(payload),
            graphs=build_graphs(payload),
            partial=True,
            status=repo.status,
            progress=DiagramProgress(
                embedded=repo.embedded_count or 0,
                chunk_count=repo.chunk_count or 0,
                live_files=live.live_files,
                total_files=live.total_files,
            ),
        )

    row = get_manifest(db, repo.id)
    if row is None:
        row = build_and_persist_manifest(db, repo)

    return DiagramsResponse(
        repository_id=repo.id,
        full_name=repo.full_name,
        diagrams=build_diagrams(row.manifest_json),
        graphs=build_graphs(row.manifest_json),
        partial=False,
        status=repo.status,
        progress=DiagramProgress(
            embedded=repo.embedded_count or 0,
            chunk_count=repo.chunk_count or 0,
            live_files=row.file_count,
            total_files=row.file_count,
        ),
    )


@router.get("/{repo_id}/callgraph", response_model=CallGraphResponse)
def get_repository_call_graph(
    repo_id: str,
    symbol: str,
    direction: str = "callees",
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the callers or callees of one symbol, one row per call site.

    ``direction=callees`` answers "what does ``symbol`` call" (edges whose
    caller is ``symbol``); ``direction=callers`` answers "who calls
    ``symbol``" (edges whose callee name is ``symbol``). Unresolved edges are
    included and labelled rather than hidden, so a call that could not be
    placed (stdlib, third-party, dynamic dispatch) is visible in the answer.
    """
    repo = _get_owned_repository(repo_id, db, current_user)

    # is_complete rather than has_embeddings: a partial index would produce a
    # misleading graph, and the graph is rebuilt at the end of every import.
    if not repo.is_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Repository is still being indexed. The call graph is only "
                "available once indexing has completed."
            ),
        )

    if direction not in ("callers", "callees"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="direction must be 'callers' or 'callees'",
        )

    limit = max(1, min(limit, 500))

    query = db.query(CallEdge).filter(CallEdge.repository_id == repo.id)
    if direction == "callers":
        # callee_symbol stores the bare name even when unresolved, so a
        # callers lookup still finds unplaced call sites by name.
        query = query.filter(CallEdge.callee_symbol == symbol)
    else:
        query = query.filter(CallEdge.caller_symbol == symbol)

    rows = query.order_by(CallEdge.caller_path, CallEdge.call_line).all()
    return CallGraphResponse(
        repository_id=repo.id,
        full_name=repo.full_name,
        symbol=symbol,
        direction=direction,
        total=len(rows),
        edges=[
            CallEdgeOut(
                caller_symbol=row.caller_symbol,
                caller_path=row.caller_path,
                callee_symbol=row.callee_symbol,
                callee_path=row.callee_path,
                call_line=row.call_line,
                resolution=row.resolution,
            )
            for row in rows[:limit]
        ],
    )


@router.get("/{repo_id}/callgraph/summary", response_model=CallGraphSummaryResponse)
def get_repository_call_graph_summary(
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Honest counts for a repository's stored call graph.

    ``by_resolution`` includes ``unresolved``, so a sparse or noisy graph is
    never mistaken for a dense, verified one; ``top_callees`` are the most
    called symbols project-wide.
    """
    repo = _get_owned_repository(repo_id, db, current_user)

    if not repo.is_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Repository is still being indexed. The call graph is only "
                "available once indexing has completed."
            ),
        )

    resolution_rows = (
        db.query(CallEdge.resolution, func.count(CallEdge.id))
        .filter(CallEdge.repository_id == repo.id)
        .group_by(CallEdge.resolution)
        .all()
    )
    by_resolution = {resolution: count for resolution, count in resolution_rows}
    total = sum(by_resolution.values())
    resolved = total - by_resolution.get(RESOLUTION_UNRESOLVED, 0)

    callee_rows = (
        db.query(CallEdge.callee_symbol, func.count(CallEdge.id))
        .filter(CallEdge.repository_id == repo.id)
        .group_by(CallEdge.callee_symbol)
        .order_by(func.count(CallEdge.id).desc(), CallEdge.callee_symbol.asc())
        .limit(10)
        .all()
    )

    return CallGraphSummaryResponse(
        repository_id=repo.id,
        full_name=repo.full_name,
        call_sites=total,
        resolved=resolved,
        by_resolution=by_resolution,
        top_callees=[{"symbol": name, "count": count} for name, count in callee_rows],
    )


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_repository(
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a repository along with its vector index and on-disk checkout."""
    repo = _get_owned_repository(repo_id, db, current_user)

    cleanup_repository_artifacts(repo)
    db.delete(repo)
    db.commit()


@router.post("/{repo_id}/reprocess", response_model=RepositoryResponse)
def reprocess_repository(
    repo_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Re-trigger the processing and embedding for a repository."""
    repo = _get_owned_repository(repo_id, db, current_user)

    repo.status = RepoStatus.PENDING
    repo.error_message = None
    repo.chunk_count = 0
    repo.embedded_count = 0
    db.commit()

    background_tasks.add_task(process_repository_background_task, str(repo.id), db)
    return _serialize_repository(repo, db)
