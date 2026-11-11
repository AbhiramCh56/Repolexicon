"""
Tests for deterministic call-graph resolution and persistence.

The resolver's honesty rules are what these tests pin: every edge records
*how* its target was found, an ambiguous name is left unresolved rather than
guessed, and dynamic-dispatch receivers only resolve through same-file or
unique scoping.
"""

import unittest
import uuid
from unittest import mock

# Register the RepoManifest mapper before any File/Repository row is built:
# Repository's string-based relationships only resolve once every model
# module has been imported (same reason the backfill CLIs import them all).
from app.models import repo_manifest as _repo_manifest  # noqa: F401
from app.models.call_edge import CallEdge
from app.models.file import File
from app.models.repository import RepoStatus
from app.services import call_graph_service, github_service
from app.services.call_graph_service import (
    RESOLUTION_IMPORT,
    RESOLUTION_SAME_FILE,
    RESOLUTION_UNIQUE,
    RESOLUTION_UNRESOLVED,
    build_call_graph,
    persist_call_graph,
)
from support import FakeSession, make_repository


def _file(path, language, imports=None):
    return File(
        id=uuid.uuid4(),
        file_path=path,
        language=language,
        parse_state="structural",
        size_bytes=10,
        imports_json=imports,
    )


def _chunk(file_id, name, calls, chunk_type="function"):
    return (uuid.uuid4(), file_id, name, chunk_type, {"calls": calls})


def _repo_db(files, chunks):
    repo = make_repository(status="completed", chunk_count=10, embedded_count=10)
    db = FakeSession(repo, rows=files, execute_rows=chunks)
    return repo, db


def _edges_by(result, caller, callee):
    return [
        edge
        for edge in result.edges
        if edge.caller_symbol == caller and edge.callee_symbol == callee
    ]


class SameFileResolutionTests(unittest.TestCase):
    def test_bare_call_to_same_file_function(self):
        a = _file("app/a.py", "Python")
        repo, db = _repo_db(
            [a],
            [
                _chunk(a.id, "outer", [{"callee": "inner", "line": 2}]),
                _chunk(a.id, "inner", []),
            ],
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.call_sites, 1)
        edge = result.edges[0]
        self.assertEqual(edge.resolution, RESOLUTION_SAME_FILE)
        self.assertEqual(edge.caller_symbol, "outer")
        self.assertEqual(edge.callee_symbol, "inner")
        self.assertEqual(edge.callee_path, "app/a.py")
        self.assertEqual(edge.call_line, 2)

    def test_self_receiver_resolves_within_the_class_file(self):
        a = _file("app/a.py", "Python")
        repo, db = _repo_db(
            [a],
            [
                _chunk(a.id, "run", [{"callee": "self.step", "line": 3}]),
                _chunk(a.id, "step", []),
            ],
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.edges[0].resolution, RESOLUTION_SAME_FILE)
        self.assertEqual(result.edges[0].callee_symbol, "step")

    def test_self_call_is_recorded_not_hidden(self):
        a = _file("app/a.py", "Python")
        repo, db = _repo_db(
            [a], [_chunk(a.id, "recurse", [{"callee": "recurse", "line": 2}])]
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.call_sites, 1)
        self.assertEqual(result.edges[0].resolution, RESOLUTION_SAME_FILE)
        self.assertEqual(result.edges[0].callee_chunk_id, result.edges[0].caller_chunk_id)


class PythonImportResolutionTests(unittest.TestCase):
    def test_from_import_binds_bare_name(self):
        a = _file("app/a.py", "Python", ["from app.b import helper"])
        b = _file("app/b.py", "Python")
        repo, db = _repo_db(
            [a, b],
            [
                _chunk(a.id, "work", [{"callee": "helper", "line": 3}]),
                _chunk(b.id, "helper", []),
            ],
        )
        result = build_call_graph(db, repo)
        edge = result.edges[0]
        self.assertEqual(edge.resolution, RESOLUTION_IMPORT)
        self.assertEqual(edge.callee_symbol, "helper")
        self.assertEqual(edge.callee_path, "app/b.py")

    def test_module_alias_prefix_resolves_dotted_callee(self):
        a = _file("app/a.py", "Python", ["import app.services.c as cc"])
        c = _file("app/services/c.py", "Python")
        repo, db = _repo_db(
            [a, c],
            [
                _chunk(a.id, "work", [{"callee": "cc.tool", "line": 4}]),
                _chunk(c.id, "tool", []),
            ],
        )
        result = build_call_graph(db, repo)
        edge = result.edges[0]
        self.assertEqual(edge.resolution, RESOLUTION_IMPORT)
        self.assertEqual(edge.callee_symbol, "tool")
        self.assertEqual(edge.callee_path, "app/services/c.py")

    def test_relative_from_import_resolves(self):
        a = _file("app/a.py", "Python", ["from .sib import helper"])
        b = _file("app/sib.py", "Python")
        repo, db = _repo_db(
            [a, b],
            [
                _chunk(a.id, "work", [{"callee": "helper", "line": 2}]),
                _chunk(b.id, "helper", []),
            ],
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.edges[0].resolution, RESOLUTION_IMPORT)
        self.assertEqual(result.edges[0].callee_path, "app/sib.py")

    def test_from_import_used_as_namespace(self):
        a = _file("app/a.py", "Python", ["from app import b"])
        b = _file("app/b.py", "Python")
        repo, db = _repo_db(
            [a, b],
            [
                _chunk(a.id, "work", [{"callee": "b.helper", "line": 2}]),
                _chunk(b.id, "helper", []),
            ],
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.edges[0].resolution, RESOLUTION_IMPORT)
        self.assertEqual(result.edges[0].callee_path, "app/b.py")


class JavaScriptImportResolutionTests(unittest.TestCase):
    def test_relative_named_import_resolves(self):
        view = _file("src/view.js", "JavaScript", ['import { render } from "./render.js";'])
        render = _file("src/render.js", "JavaScript")
        repo, db = _repo_db(
            [view, render],
            [
                _chunk(view.id, "main", [{"callee": "render", "line": 5}]),
                _chunk(render.id, "render", []),
            ],
        )
        result = build_call_graph(db, repo)
        edge = result.edges[0]
        self.assertEqual(edge.resolution, RESOLUTION_IMPORT)
        self.assertEqual(edge.callee_symbol, "render")
        self.assertEqual(edge.callee_path, "src/render.js")

    def test_extensionless_specifier_and_index_files_resolve(self):
        view = _file("src/view.js", "JavaScript", ['import { render } from "./ui";'])
        index = _file("src/ui/index.js", "JavaScript")
        repo, db = _repo_db(
            [view, index],
            [
                _chunk(view.id, "main", [{"callee": "render", "line": 2}]),
                _chunk(index.id, "render", []),
            ],
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.edges[0].resolution, RESOLUTION_IMPORT)
        self.assertEqual(result.edges[0].callee_path, "src/ui/index.js")


class HonestyRulesTests(unittest.TestCase):
    def test_unique_name_resolves_project_wide(self):
        a = _file("app/a.py", "Python")
        b = _file("app/b.py", "Python")
        repo, db = _repo_db(
            [a, b],
            [
                _chunk(a.id, "work", [{"callee": "lonely_helper", "line": 2}]),
                _chunk(b.id, "lonely_helper", []),
            ],
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.edges[0].resolution, RESOLUTION_UNIQUE)
        self.assertEqual(result.edges[0].callee_path, "app/b.py")

    def test_ambiguous_name_without_import_stays_unresolved(self):
        a = _file("app/a.py", "Python")
        b = _file("app/b.py", "Python")
        c = _file("app/c.py", "Python")
        repo, db = _repo_db(
            [a, b, c],
            [
                _chunk(a.id, "work", [{"callee": "helper", "line": 2}]),
                _chunk(b.id, "helper", []),
                _chunk(c.id, "helper", []),
            ],
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.call_sites, 1)
        edge = result.edges[0]
        self.assertEqual(edge.resolution, RESOLUTION_UNRESOLVED)
        self.assertIsNone(edge.callee_path)
        self.assertIsNone(edge.callee_chunk_id)
        # The called name is still recorded, so the edge is visible, not lost.
        self.assertEqual(edge.callee_symbol, "helper")

    def test_ambiguous_name_resolves_when_import_scopes_it(self):
        a = _file("app/a.py", "Python", ["from app.b import helper"])
        b = _file("app/b.py", "Python")
        c = _file("app/c.py", "Python")
        repo, db = _repo_db(
            [a, b, c],
            [
                _chunk(a.id, "work", [{"callee": "helper", "line": 2}]),
                _chunk(b.id, "helper", []),
                _chunk(c.id, "helper", []),
            ],
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.edges[0].resolution, RESOLUTION_IMPORT)
        self.assertEqual(result.edges[0].callee_path, "app/b.py")

    def test_unknown_receiver_falls_back_to_same_file_then_unique(self):
        a = _file("app/a.py", "Python")
        repo, db = _repo_db(
            [a], [_chunk(a.id, "work", [{"callee": "obj.helper", "line": 2}])]
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.edges[0].resolution, RESOLUTION_UNRESOLVED)

        # The same receiver shape with a same-file definition resolves there.
        repo2, db2 = _repo_db(
            [a],
            [
                _chunk(a.id, "work", [{"callee": "obj.helper", "line": 2}]),
                _chunk(a.id, "helper", []),
            ],
        )
        result2 = build_call_graph(db2, repo2)
        self.assertEqual(result2.edges[0].resolution, RESOLUTION_SAME_FILE)

    def test_unresolvable_call_is_kept_and_counted(self):
        a = _file("app/a.py", "Python", ["import os"])
        repo, db = _repo_db(
            [a], [_chunk(a.id, "work", [{"callee": "os.path.join", "line": 2}])]
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.call_sites, 1)
        self.assertEqual(result.edges[0].resolution, RESOLUTION_UNRESOLVED)
        self.assertEqual(result.edges[0].callee_symbol, "join")
        counts = result.by_resolution()
        self.assertEqual(counts[RESOLUTION_UNRESOLVED], 1)
        self.assertIn("unresolved", result.summary())


class PersistCallGraphTests(unittest.TestCase):
    def test_persist_adds_one_row_per_call_site(self):
        repo = make_repository(status="completed", chunk_count=5, embedded_count=5)
        db = FakeSession(repo)
        result = call_graph_service.CallGraphResult(
            call_sites=2,
            edges=[
                call_graph_service.ResolvedEdge(
                    caller_chunk_id=uuid.uuid4(),
                    caller_symbol="work",
                    caller_path="app/a.py",
                    callee_chunk_id=uuid.uuid4(),
                    callee_symbol="helper",
                    callee_path="app/b.py",
                    call_line=3,
                    resolution=RESOLUTION_IMPORT,
                ),
                call_graph_service.ResolvedEdge(
                    caller_chunk_id=uuid.uuid4(),
                    caller_symbol="work",
                    caller_path="app/a.py",
                    callee_chunk_id=None,
                    callee_symbol="join",
                    callee_path=None,
                    call_line=4,
                    resolution=RESOLUTION_UNRESOLVED,
                ),
            ],
        )

        persist_call_graph(db, repo, result)

        added = [row for row in db.added if isinstance(row, CallEdge)]
        self.assertEqual(len(added), 2)
        self.assertTrue(all(row.repository_id == repo.id for row in added))
        resolved, unresolved = added
        self.assertEqual(resolved.resolution, RESOLUTION_IMPORT)
        self.assertEqual(resolved.callee_path, "app/b.py")
        self.assertIsNone(unresolved.callee_chunk_id)
        self.assertIsNone(unresolved.callee_path)
        self.assertGreaterEqual(db.commits, 1)

    def test_js_arrow_function_containers_are_valid_callers(self):
        # JS names functions via variable declarations, which chunk as
        # "module" containers (``const render = () => helper()``): the call
        # belongs to the named symbol, so the graph keeps the edge rather
        # than dropping it for not being a ``function`` chunk.
        a = _file("src/view.js", "JavaScript")
        repo, db = _repo_db(
            [a],
            [
                (uuid.uuid4(), a.id, "render", "module", {"calls": [{"callee": "run", "line": 2}]}),
                _chunk(a.id, "run", []),
            ],
        )
        result = build_call_graph(db, repo)
        self.assertEqual(result.call_sites, 1)
        self.assertEqual(result.edges[0].caller_symbol, "render")
        self.assertEqual(result.edges[0].callee_symbol, "run")
        self.assertEqual(result.edges[0].resolution, RESOLUTION_SAME_FILE)


class BackgroundTaskWiringTests(unittest.TestCase):
    """Step 8 of the import pipeline, with the manifest step's rules."""

    def _run_import(self, db, call_graph_mock):
        file_result = mock.Mock(
            chunks=5, manifest_only_files=0, files=5, errors=[]
        )
        embedding_result = mock.Mock(
            embedded=5, total=5, complete=True, errors=[], summary="Embedded 5/5"
        )
        with mock.patch.object(
            github_service, "process_repository_files", return_value=file_result
        ), mock.patch.object(
            github_service, "embed_repository_chunks", return_value=embedding_result
        ), mock.patch.object(
            github_service, "build_and_persist_manifest"
        ), call_graph_mock, mock.patch.object(
            github_service, "delete_repository_collection"
        ), mock.patch.object(
            github_service.git, "Repo"
        ) as clone:
            clone.clone_from.return_value.active_branch.name = "main"
            github_service.process_repository_background_task(
                str(db.repository.id), db
            )

    def test_call_graph_failure_does_not_fail_the_import(self):
        db = FakeSession(make_repository(status="completed"))
        failing = mock.patch.object(
            github_service,
            "build_and_persist_call_graph",
            side_effect=RuntimeError("boom"),
        )
        self._run_import(db, failing)

        self.assertEqual(db.repository.status, RepoStatus.COMPLETED)
        self.assertIsNone(db.repository.error_message)
        # The failure is contained: rolled back, import still completes.
        self.assertGreaterEqual(db.rollbacks, 1)

    def test_successful_import_builds_the_call_graph(self):
        db = FakeSession(make_repository(status="completed"))
        result = call_graph_service.CallGraphResult(
            call_sites=1,
            edges=[
                call_graph_service.ResolvedEdge(
                    caller_chunk_id=uuid.uuid4(),
                    caller_symbol="work",
                    caller_path="app/a.py",
                    callee_chunk_id=uuid.uuid4(),
                    callee_symbol="helper",
                    callee_path="app/b.py",
                    call_line=2,
                    resolution=RESOLUTION_IMPORT,
                )
            ],
        )
        building = mock.patch.object(
            github_service,
            "build_and_persist_call_graph",
            return_value=result,
        )
        self._run_import(db, building)

        self.assertEqual(db.repository.status, RepoStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
