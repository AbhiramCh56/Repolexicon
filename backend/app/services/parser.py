import ast
from typing import List, Dict, Any, Optional, Tuple


class PythonCodeParser:
    def __init__(self, source_code: str):
        self.source_code = source_code
        self.lines = source_code.splitlines()
        self._tree: Optional[ast.Module] = None

    def parse(self) -> List[Dict[str, Any]]:
        """Parses the source code and returns a list of structural chunks."""
        chunks = []
        try:
            tree = self._parse()
            if tree is None:
                return chunks

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    chunks.append(self._extract_node(node, "class"))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Only grab top-level functions (methods are grabbed when we walk the class)
                    # For simplicity in this prototype, we'll grab all and rely on name matching
                    chunks.append(self._extract_node(node, "function"))

        except SyntaxError:
            # Fallback for invalid Python code (e.g., Python 2 syntax in a Python 3 parser)
            print("Syntax error while parsing Python file, treating as raw text.")
            return [{"chunk_type": "raw", "name": "raw_block", "content": self.source_code, "start_line": 1, "end_line": len(self.lines)}]

        return chunks

    def _parse(self) -> Optional[ast.Module]:
        """Parse once and cache the tree for the extract_* helpers."""
        if self._tree is None:
            try:
                self._tree = ast.parse(self.source_code)
            except SyntaxError:
                raise
        return self._tree

    def extract_calls(self) -> List[Tuple[str, int]]:
        """
        Every call site in the file as ``(callee_text, line)``.

        The callee text keeps its qualifier (``file_filter.skip_reason``,
        ``self.method``), which is what lets the call-graph resolver scope the
        call to the defining file via the caller's imports. Attribution to the
        enclosing function happens later, by line range, so calls inside a
        nested function are not double-counted against the outer one.
        """
        calls: List[Tuple[str, int]] = []
        try:
            tree = self._parse()
        except SyntaxError:
            return calls
        if tree is None:
            return calls

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = self._callee_name(node.func)
                if name:
                    calls.append((name, node.lineno))
        return calls

    def extract_imports(self) -> List[str]:
        """The source text of every import statement, one per entry."""
        imports: List[str] = []
        try:
            tree = self._parse()
        except SyntaxError:
            return imports
        if tree is None:
            return imports

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                segment = ast.get_source_segment(self.source_code, node)
                if segment:
                    imports.append(segment.strip())
        return imports

    @staticmethod
    def _callee_name(func: ast.expr) -> Optional[str]:
        """Render a call target as text: ``foo``, ``self.method``, ``a.b.c``."""
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            base = PythonCodeParser._callee_name(func.value)
            return f"{base}.{func.attr}" if base else func.attr
        return None

    def _extract_node(self, node: ast.AST, chunk_type: str) -> Dict[str, Any]:
        """Extracts the source text and metadata for a specific AST node."""
        start_line = node.lineno
        # node.end_lineno is available in Python 3.8+
        end_line = getattr(node, 'end_lineno', start_line)

        # Reconstruct the code block
        content = "\n".join(self.lines[start_line - 1:end_line])

        return {
            "chunk_type": chunk_type,
            "name": getattr(node, "name", "anonymous"),
            "content": content,
            "start_line": start_line,
            "end_line": end_line,
            "metadata_json": {}
        }
