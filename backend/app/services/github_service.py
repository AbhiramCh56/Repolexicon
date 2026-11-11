import logging
import shutil
import traceback
from pathlib import Path
from urllib.parse import urlparse

import git
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.repository import Repository, RepoStatus
from app.services.embedding_service import (
    delete_repository_collection,
    embed_repository_chunks,
)
from app.services.repo_processor import process_repository_files
from app.services.manifest_service import build_and_persist_manifest
from app.services.call_graph_service import build_and_persist_call_graph

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_CHARS = 2000


def extract_repo_info(github_url: str):
    """Extracts 'tiangolo' and 'fastapi' from 'https://github.com/tiangolo/fastapi'"""
    path = urlparse(github_url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2:
        owner, name = parts[0], parts[1]
        if name.endswith(".git"):
            name = name[:-4]
        return owner, name
    raise ValueError("Invalid GitHub URL format")


def cleanup_repository_artifacts(repo: Repository) -> None:
    """
    Remove the vector index and the on-disk checkout for a repository.

    Without this, deleting a repository orphaned both its Chroma collection and
    its cloned directory, permanently leaking disk space and vectors.
    """
    try:
        delete_repository_collection(str(repo.id))
    except Exception:
        logger.warning("Could not delete vector index for %s", repo.full_name, exc_info=True)

    if repo.local_path:
        local_path = Path(repo.local_path)
        if local_path.exists():
            shutil.rmtree(local_path, ignore_errors=True)
            logger.info("Removed checkout %s", local_path)


def process_repository_background_task(repo_id: str, db: Session):
    """
    Clones the repo, indexes it, and records honest progress/failure state.
    """
    repo = db.query(Repository).filter(Repository.id == repo_id).first()
    if not repo:
        logger.warning("Background task for unknown repository %s", repo_id)
        return

    full_name = repo.full_name

    try:
        repo.status = RepoStatus.CLONING
        repo.error_message = None
        repo.chunk_count = 0
        repo.embedded_count = 0
        db.commit()

        # 1. Prepare local storage path
        local_path = Path(settings.REPO_STORAGE_DIR) / str(repo.id)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.exists():
            shutil.rmtree(local_path, ignore_errors=True)

        # 2. Clone the repository. depth=1 is a shallow clone: we only ever
        #    need the latest snapshot of the code.
        logger.info("Cloning %s into %s", repo.github_url, local_path)
        cloned_repo = git.Repo.clone_from(repo.github_url, str(local_path), depth=1)

        # 3. Extract metadata (an empty repository has no active branch)
        try:
            default_branch = cloned_repo.active_branch.name
        except TypeError:
            default_branch = None

        repo.local_path = str(local_path)
        repo.default_branch = default_branch
        db.commit()

        # 4. Parse and chunk the files
        repo.status = RepoStatus.PROCESSING
        db.commit()
        file_result = process_repository_files(str(repo.id), db)
        if file_result.chunks == 0 and file_result.manifest_only_files == 0:
            raise ValueError(
                f"No indexable code found in {full_name} "
                f"({file_result.files} files kept, {len(file_result.errors)} skipped)."
            )

        # 5. Generate embeddings. A docs/config-only repository has no chunks by
        #    design; its manifest is the whole representation, so there is
        #    nothing to embed and the embedding step is skipped rather than
        #    reported as a failure.
        if file_result.chunks == 0:
            repo.embedded_count = 0
            repo.status = RepoStatus.COMPLETED
            db.commit()
            logger.info(
                "%s: no embeddable chunks (%d manifest-only files); "
                "completing with manifest-only index",
                full_name,
                file_result.manifest_only_files,
            )
        else:
            repo.status = RepoStatus.EMBEDDING
            db.commit()
            embedding_result = embed_repository_chunks(str(repo.id), db)
            if not embedding_result.complete:
                raise RuntimeError(
                    f"Indexing incomplete: {embedding_result.summary()}. "
                    f"First failure: {embedding_result.errors[0]}"
                )

            # 6. Only now is the repository genuinely ready
            repo.embedded_count = embedding_result.embedded
            repo.status = RepoStatus.COMPLETED
            db.commit()
            logger.info(
                "Indexed %s: %d/%d chunks embedded",
                full_name,
                embedding_result.embedded,
                embedding_result.total,
            )

        # 7. Build the deterministic architecture manifest. This reads the rows
        #    just written, so it needs no extra clone, embedding, or LLM call.
        #    A failure here must not fail an otherwise successful import.
        try:
            manifest_row = build_and_persist_manifest(db, repo)
            logger.info(
                "%s: manifest built (%d files, %d symbols)",
                full_name,
                manifest_row.file_count,
                manifest_row.symbol_count,
            )
        except Exception:
            logger.exception("Manifest generation failed for %s", full_name)
            db.rollback()

        # 8. Build the deterministic call graph from the same rows. Like the
        #    manifest, it is reproducible for a given index and non-fatal on
        #    failure: chat answers fall back to plain retrieval without it.
        try:
            call_graph_result = build_and_persist_call_graph(db, repo)
            logger.info(
                "%s: call graph built, %s",
                full_name,
                call_graph_result.summary(),
            )
        except Exception:
            logger.exception("Call graph generation failed for %s", full_name)
            db.rollback()

    except Exception:
        logger.exception("Failed to process repository %s", repo_id)
        db.rollback()

        # Drop any half-built vector index so it can never be queried
        delete_repository_collection(repo_id)

        failed_repo = db.query(Repository).filter(Repository.id == repo_id).first()
        if failed_repo:
            failed_repo.status = RepoStatus.FAILED
            failed_repo.error_message = traceback.format_exc()[-MAX_ERROR_MESSAGE_CHARS:]
            failed_repo.embedded_count = 0
            db.commit()
