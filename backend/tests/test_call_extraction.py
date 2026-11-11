"""
Tests for call-site and import extraction during parsing.

The call graph is only as good as what was recorded at parse time, so these
tests pin the three invariants that matter downstream:

  1. Every call site keeps its qualifier (``file_filter.skip_reason``), which
     is what lets the resolver scope the call via the caller's imports.
  2. A call belongs to the innermost function containing it, so nested
     functions and class methods are not mis-attributed.
  3. Module-level calls are dropped, not attached to an invented caller.
"""

import unittest

from app.services import languages
from app.services.parser import PythonCodeParser


def _block(blocks, name):
    for block in blocks:
        if block["name"] == name:
            return block
    raise AssertionError(f"block {name!r} not found in {[b['name'] for b in blocks]}")


def _calls(blocks, name):
    return _block(blocks, name)["metadata_json"].get("calls") or []


PY_SOURCE = """import os
from app.services import file_filter


def outer():
    helper_local()

    def inner():
        file_filter.skip_reason("x")

    inner()


def standalone():
    outer()
    os.path.join("a", "b")


module_level()
"""


class PythonCallExtractionTests(unittest.TestCase):
    def test_bare_and_qualified_callees_are_kept(self):
        result = languages.parse_symbols(PY_SOURCE, ".py")
        self.assertEqual(
            _calls(result.blocks, "outer"),
            [{"callee": "helper_local", "line": 6}, {"callee": "inner", "line": 11}],
        )
        # The qualifier survives, which is what import-scoped resolution needs.
        self.assertEqual(
            _calls(result.blocks, "inner"),
            [{"callee": "file_filter.skip_reason", "line": 9}],
        )
        self.assertEqual(
            _calls(result.blocks, "standalone"),
            [{"callee": "outer", "line": 15}, {"callee": "os.path.join", "line": 16}],
        )

    def test_module_level_calls_are_dropped(self):
        result = languages.parse_symbols(PY_SOURCE, ".py")
        for block in result.blocks:
            callees = [c["callee"] for c in _calls(result.blocks, block["name"])]
            self.assertNotIn("module_level", callees)

    def test_class_method_calls_are_attributed_to_the_method(self):
        source = (
            "class Service:\n"
            "    def run(self):\n"
            "        self.step()\n"
            "\n"
            "    def step(self):\n"
            "        helper()\n"
        )
        result = languages.parse_symbols(source, ".py")
        self.assertEqual(_calls(result.blocks, "run"), [{"callee": "self.step", "line": 3}])
        self.assertEqual(_calls(result.blocks, "step"), [{"callee": "helper", "line": 6}])
        # The class block itself never receives call sites.
        self.assertEqual(_calls(result.blocks, "Service"), [])

    def test_imports_are_extracted_as_source_text(self):
        result = languages.parse_symbols(PY_SOURCE, ".py")
        self.assertEqual(
            result.imports,
            ["import os", "from app.services import file_filter"],
        )

    def test_syntax_error_yields_no_calls_and_no_imports(self):
        parser = PythonCodeParser("def broken(:\n    call_me()\n")
        self.assertEqual(parser.extract_calls(), [])
        self.assertEqual(parser.extract_imports(), [])

    def test_relative_and_star_imports_are_parsed_by_the_resolver_input(self):
        # The raw text is what gets stored; the resolver's import map parsing
        # is covered in test_call_graph.py. Here we only pin the shape.
        source = "from . import sibling\nfrom ..pkg import thing as aliased\n"
        result = languages.parse_symbols(source, ".py")
        self.assertEqual(
            result.imports,
            ["from . import sibling", "from ..pkg import thing as aliased"],
        )


JS_SOURCE = """import { render } from "./view.js";
import utils from "../lib/utils.js";

function main() {
  render();
  utils.log("x");
  unknown_thing();
}

run_at_load();
"""


class JavaScriptCallExtractionTests(unittest.TestCase):
    def test_calls_are_extracted_with_qualifiers_and_lines(self):
        result = languages.parse_symbols(JS_SOURCE, ".js")
        self.assertEqual(result.state, languages.PARSE_STATE_STRUCTURAL)
        self.assertEqual(
            _calls(result.blocks, "main"),
            [
                {"callee": "render", "line": 5},
                {"callee": "utils.log", "line": 6},
                {"callee": "unknown_thing", "line": 7},
            ],
        )

    def test_imports_are_extracted(self):
        result = languages.parse_symbols(JS_SOURCE, ".js")
        self.assertEqual(
            result.imports,
            [
                'import { render } from "./view.js";',
                'import utils from "../lib/utils.js";',
            ],
        )

    def test_module_scope_calls_are_dropped(self):
        result = languages.parse_symbols(JS_SOURCE, ".js")
        callees = [c["callee"] for c in _calls(result.blocks, "main")]
        self.assertNotIn("run_at_load", callees)


class JavaCallExtractionTests(unittest.TestCase):
    def test_method_invocations_report_the_bare_method_name(self):
        source = (
            "import com.example.Util;\n"
            "\n"
            "class C {\n"
            "  void foo() {\n"
            "    bar();\n"
            "    Util.help();\n"
            "  }\n"
            "}\n"
        )
        result = languages.parse_symbols(source, ".java")
        self.assertEqual(result.state, languages.PARSE_STATE_STRUCTURAL)
        self.assertEqual(
            _calls(result.blocks, "foo"),
            [{"callee": "bar", "line": 5}, {"callee": "help", "line": 6}],
        )


if __name__ == "__main__":
    unittest.main()
