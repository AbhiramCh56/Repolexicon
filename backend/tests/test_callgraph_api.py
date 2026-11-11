"""
Tests for the call-graph API endpoints.

The guards mirror the structure endpoints: another user's repository is 404,
a repository still indexing is 409 (a partial index would produce a
misleading graph), and both directions of the lookup are answerable once
indexing completes.
"""

import unittest
import uuid
from types import SimpleNamespace

from fastapi import HTTPException

from app.api.v1 import repos
from app.models.repository import RepoStatus
from support import FakeSession, make_repository


def _user():
    return SimpleNamespace(id=uuid.uuid4(), is_active=True)


def _edge(**overrides):
    values = {
        "caller_symbol": "main",
        "caller_path": "src/main.py",
        "callee_symbol": "render",
        "callee_path": "src/render.py",
        "call_line": 5,
        "resolution": "import",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _completed_repo():
    return make_repository(
        status=RepoStatus.COMPLETED, chunk_count=5, embedded_count=5
    )


class CallGraphGuardTests(unittest.TestCase):
    def test_other_users_repository_is_404(self):
        db = FakeSession(None)
        with self.assertRaises(HTTPException) as ctx:
            repos.get_repository_call_graph(
                str(uuid.uuid4()), "render", db=db, current_user=_user()
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_indexing_repository_is_409(self):
        repo = make_repository(
            status=RepoStatus.PROCESSING, chunk_count=10, embedded_count=3
        )
        db = FakeSession(repo)
        with self.assertRaises(HTTPException) as ctx:
            repos.get_repository_call_graph(
                str(repo.id), "render", db=db, current_user=_user()
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("still being indexed", ctx.exception.detail)

    def test_invalid_direction_is_422(self):
        repo = _completed_repo()
        db = FakeSession(repo)
        with self.assertRaises(HTTPException) as ctx:
            repos.get_repository_call_graph(
                str(repo.id), "render", direction="up", db=db, current_user=_user()
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_summary_for_indexing_repository_is_409(self):
        repo = make_repository(status=RepoStatus.EMBEDDING, chunk_count=5, embedded_count=1)
        db = FakeSession(repo)
        with self.assertRaises(HTTPException) as ctx:
            repos.get_repository_call_graph_summary(
                str(repo.id), db=db, current_user=_user()
            )
        self.assertEqual(ctx.exception.status_code, 409)


class CallGraphLookupTests(unittest.TestCase):
    def test_callees_of_symbol(self):
        repo = _completed_repo()
        # FakeSession ignores filters, so the rows passed in must already be
        # the "callees of main" result set.
        edges = [
            _edge(),
            _edge(callee_symbol="os.path.join", callee_path=None, call_line=7, resolution="unresolved"),
        ]
        db = FakeSession(repo, rows=edges)

        response = repos.get_repository_call_graph(
            str(repo.id), "main", db=db, current_user=_user()
        )

        self.assertEqual(response.symbol, "main")
        self.assertEqual(response.direction, "callees")
        self.assertEqual(response.total, 2)
        self.assertEqual(len(response.edges), 2)
        self.assertEqual(response.edges[0].callee_symbol, "render")
        self.assertEqual(response.edges[0].call_line, 5)
        self.assertEqual(response.edges[0].resolution, "import")

    def test_callers_lookup_includes_unresolved_edges_by_name(self):
        # callee_symbol keeps the bare name even when unresolved, so asking
        # who calls a symbol still surfaces call sites that could not be placed.
        repo = _completed_repo()
        edges = [
            _edge(),
            _edge(
                caller_symbol="helper",
                caller_path="src/util.py",
                callee_symbol="render",
                callee_path=None,
                call_line=3,
                resolution="unresolved",
            ),
        ]
        db = FakeSession(repo, rows=edges)

        response = repos.get_repository_call_graph(
            str(repo.id), "render", direction="callers", db=db, current_user=_user()
        )

        self.assertEqual(response.direction, "callers")
        self.assertEqual(response.total, 2)
        resolutions = {edge.resolution for edge in response.edges}
        self.assertEqual(resolutions, {"import", "unresolved"})

    def test_limit_caps_edges_without_hiding_the_total(self):
        repo = _completed_repo()
        edges = [_edge(call_line=line) for line in range(1, 11)]
        db = FakeSession(repo, rows=edges)

        response = repos.get_repository_call_graph(
            str(repo.id), "main", limit=3, db=db, current_user=_user()
        )

        self.assertEqual(response.total, 10)
        self.assertEqual(len(response.edges), 3)


class CallGraphSummaryTests(unittest.TestCase):
    def test_summary_counts_include_unresolved(self):
        repo = _completed_repo()
        resolution_rows = [("same_file", 3), ("import", 2), ("unresolved", 1)]
        callee_rows = [("render", 4), ("get", 2)]
        # The first query() call is the repository lookup, which consumes the
        # placeholder; each subsequent query pops the next entry.
        db = FakeSession(repo, query_results=[None, resolution_rows, callee_rows])

        response = repos.get_repository_call_graph_summary(
            str(repo.id), db=db, current_user=_user()
        )

        self.assertEqual(response.call_sites, 6)
        self.assertEqual(response.resolved, 5)
        self.assertEqual(response.by_resolution["same_file"], 3)
        self.assertEqual(response.by_resolution["unresolved"], 1)
        self.assertEqual(
            response.top_callees,
            [{"symbol": "render", "count": 4}, {"symbol": "get", "count": 2}],
        )

    def test_empty_graph_reports_zero_sites(self):
        repo = _completed_repo()
        db = FakeSession(repo, query_results=[None, [], []])

        response = repos.get_repository_call_graph_summary(
            str(repo.id), db=db, current_user=_user()
        )

        self.assertEqual(response.call_sites, 0)
        self.assertEqual(response.resolved, 0)
        self.assertEqual(response.by_resolution, {})
        self.assertEqual(response.top_callees, [])


if __name__ == "__main__":
    unittest.main()
