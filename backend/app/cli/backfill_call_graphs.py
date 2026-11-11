"""
CLI to backfill deterministic call graphs for already-indexed repositories.

Reads only ``File`` / ``Chunk`` rows, so no clone and no re-embedding happens.
Safe to re-run: a repository's edges are replaced in place, never merged.

Repositories indexed before call-site extraction existed have no calls
recorded in their chunk metadata; the graph built for them is empty by
honesty (not by guesswork). Reprocess such a repository to populate it.

Usage:
    python -m app.cli.backfill_call_graphs
    python -m app.cli.backfill_call_graphs --repo NVIDIA/OpenShell
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.db.session import SessionLocal
# Import every model before touching the ORM, so the string-based relationships
# on Repository (owner, files) can be resolved by name.
from app.models import call_edge as _call_edge  # noqa: F401
from app.models import chat as _chat  # noqa: F401
from app.models import chunk as _chunk  # noqa: F401
from app.models import file as _file  # noqa: F401
from app.models import repo_manifest as _repo_manifest  # noqa: F401
from app.models.repository import Repository, RepoStatus
from app.models.user import User  # noqa: F401
from app.services.call_graph_service import build_and_persist_call_graph

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
logger = logging.getLogger("call_graph_backfill")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill repository call graphs.")
    parser.add_argument(
        "--repo",
        action="append",
        help="Only backfill this owner/name. Repeatable. Omit for all eligible repos.",
    )
    parser.add_argument(
        "--include-incomplete",
        action="store_true",
        help="Also process repos that are not COMPLETED (off by default: a "
             "partial index produces a misleading graph).",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        query = db.query(Repository)
        if args.repo:
            query = query.filter(Repository.full_name.in_(args.repo))
        if not args.include_incomplete:
            query = query.filter(Repository.status == RepoStatus.COMPLETED)

        repos = query.order_by(Repository.full_name).all()
        if not repos:
            print("No repositories matched.")
            return 0

        print(f"Backfilling call graphs for {len(repos)} repository(ies):\n")
        for repo in repos:
            if not repo.is_complete:
                # Only reachable with --include-incomplete; a partial index
                # produces a misleading graph.
                print(f"  SKIP {repo.full_name}: not finished indexing")
                continue
            if not repo.has_embeddings and (repo.chunk_count or 0) == 0:
                print(f"  SKIP {repo.full_name}: no index rows to resolve")
                continue
            try:
                result = build_and_persist_call_graph(db, repo)
                print(f"  OK   {repo.full_name}: {result.summary()}")
            except Exception as exc:  # keep going; one bad repo should not stop the rest
                db.rollback()
                print(f"  FAIL {repo.full_name}: {exc}")
                logger.exception("Call graph backfill failed for %s", repo.full_name)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
