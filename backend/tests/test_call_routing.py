"""
Tests for call-question routing (app.ai.manifest_routing).

"Who calls X?" is answered by the stored call edges, not by dense retrieval:
the answer is a relation between functions, so the router extracts the symbol
from the question and renders its callers/callees. These tests pin the routing
decisions, symbol extraction and the degradation paths (no symbols, no edges,
lookup failure).
"""

import unittest
import uuid
from types import SimpleNamespace

from app.ai.manifest_routing import (
    extract_candidate_symbols,
    is_call_question,
    load_callgraph_context,
)
from support import FakeSession


class CallQuestionDetectionTests(unittest.TestCase):
    def test_call_questions_route(self):
        for question in (
            "who calls parse_symbols?",
            "what calls the manifest builder?",
            "what does process_repository_files call?",
            "show me the callers of build_manifest",
            "which functions are called by the parser?",
            "what are the callees of skip_reason?",
            "show me the call graph for the ingestion step",
            "trace the call chain from the API to the database",
            "who invokes the embedding service?",
            "what are the function dependencies of the resolver?",
        ):
            with self.subTest(question=question):
                self.assertTrue(is_call_question(question))

    def test_other_questions_do_not_route(self):
        for question in (
            "where is the retry logic implemented",
            "what does UserService do",
            "how is this repository organized?",
            "where does the application start?",
            "explain the JWT middleware",
            "how many files are there?",
            "what does the readme say about setup",
        ):
            with self.subTest(question=question):
                self.assertFalse(is_call_question(question))

    def test_empty_question_does_not_route(self):
        self.assertFalse(is_call_question(""))
        self.assertFalse(is_call_question("   "))
        self.assertFalse(is_call_question(None))


class SymbolExtractionTests(unittest.TestCase):
    def test_backticked_symbols_win(self):
        self.assertEqual(
            extract_candidate_symbols("who calls `parse_symbols`?"),
            ["parse_symbols"],
        )

    def test_dotted_backticked_symbol_uses_last_segment(self):
        self.assertEqual(
            extract_candidate_symbols("who calls `app.services.parse`?"),
            ["parse"],
        )

    def test_snake_case_identifiers_are_extracted(self):
        self.assertEqual(
            extract_candidate_symbols("what does process_repository_files call?"),
            ["process_repository_files"],
        )

    def test_camel_and_pascal_case_identifiers_are_extracted(self):
        self.assertEqual(
            extract_candidate_symbols("who calls repoProcessor?"),
            ["repoProcessor"],
        )
        self.assertEqual(
            extract_candidate_symbols("who calls RepoProcessor?"),
            ["RepoProcessor"],
        )

    def test_direct_phrase_target_is_extracted(self):
        # "parse" alone matches no identifier-shape regex; the phrase itself
        # names the target.
        self.assertEqual(
            extract_candidate_symbols("who calls parse?"),
            ["parse"],
        )
        self.assertEqual(
            extract_candidate_symbols("callers of build"),
            ["build"],
        )

    def test_question_phrasing_is_not_a_symbol(self):
        self.assertEqual(
            extract_candidate_symbols("show me the call graph for this repo"),
            [],
        )
        self.assertEqual(
            extract_candidate_symbols("what is called by whom here?"),
            [],
        )

    def test_duplicates_collapse_and_candidates_are_capped(self):
        question = "who calls `helper` and helper_a, helper_b, helper_c, helper_d?"
        candidates = extract_candidate_symbols(question)
        self.assertEqual(len(candidates), len(set(candidates)))
        self.assertLessEqual(len(candidates), 3)


def _edge(caller, callee, resolution="import", path="src/app.py", line=3):
    return SimpleNamespace(
        caller_symbol=caller,
        caller_path=path,
        callee_symbol=callee,
        callee_path=f"src/{callee}.py" if resolution != "unresolved" else None,
        call_line=line,
        resolution=resolution,
    )


class LoadCallGraphContextTests(unittest.TestCase):
    def _db(self, rows):
        return FakeSession(None, rows=rows)

    def test_callers_and_callees_are_rendered(self):
        rows = [
            _edge("main", "parse_symbols"),
            _edge("worker", "parse_symbols"),
            _edge("parse_symbols", "skip_reason", path="src/parse.py", line=12),
            _edge("parse_symbols", "join", resolution="unresolved", path="src/parse.py", line=14),
        ]
        context = load_callgraph_context(
            self._db(rows), str(uuid.uuid4()), "who calls `parse_symbols`?"
        )
        self.assertIsNotNone(context)
        self.assertIn("### `parse_symbols`", context)
        self.assertIn("Callers of `parse_symbols` (2 call site(s))", context)
        self.assertIn("`main` at src/app.py:3 [import]", context)
        self.assertIn("`worker` at src/app.py:3 [import]", context)
        self.assertIn("Callees of `parse_symbols`", context)
        self.assertIn("`skip_reason` at src/skip_reason.py", context)
        self.assertIn("[unresolved]", context)

    def test_question_without_symbols_returns_none(self):
        self.assertIsNone(
            load_callgraph_context(self._db([]), str(uuid.uuid4()), "who calls it?")
        )

    def test_no_stored_edges_returns_none(self):
        self.assertIsNone(
            load_callgraph_context(
                self._db([]),
                str(uuid.uuid4()),
                "who calls `parse_symbols`?",
            )
        )

    def test_lookup_failure_returns_none_not_an_error(self):
        class ExplodingQuery:
            def query(self, *args):
                raise RuntimeError("call_edges table missing")

        self.assertIsNone(
            load_callgraph_context(
                ExplodingQuery(), str(uuid.uuid4()), "who calls `parse_symbols`?"
            )
        )


if __name__ == "__main__":
    unittest.main()
