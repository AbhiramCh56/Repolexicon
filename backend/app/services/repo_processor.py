import logging
import os
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import pathspec
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.repository import Repository
from app.models.file import File
from app.models.chunk import Chunk
from app.services import file_filter, languages

logger = logging.getLogger(__name__)

# A sensible default list of files/directories to ignore
DEFAULT_IGNORE_PATTERNS = [
    ".git/", "node_modules/", "venv/", "env/", "__pycache__/",
    "dist/", "build/", "out/", ".next/", "coverage/",
    "*.jpg", "*.jpeg", "*.png", "*.gif", "*.ico", "*.svg", "*.webp",
    "*.mp4", "*.mp3", "*.wav",
    "*.pdf", "*.zip", "*.tar", "*.gz", "*.rar",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml"
]

# How many files to buffer before committing a batch to Postgres
COMMIT_EVERY_FILES = 50

# A simple map to detect language based on extension
LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "React (JavaScript)",
    ".ts": "TypeScript",
    ".tsx": "React (TypeScript)",
    ".md": "Markdown",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".html": "HTML",
    ".css": "CSS",
    ".sh": "Shell",
    ".toml": "TOML",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C Header"
}


@dataclass
class FileProcessResult:
    files: int = 0
    chunks: int = 0
    errors: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    parse_states: dict = field(default_factory=dict)
    languages: dict = field(default_factory=dict)
    # Files whose representation lives only in the manifest, with no embedded chunks.
    manifest_only_files: int = 0

    def record_skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def record_parse_state(self, state: str) -> None:
        self.parse_states[state] = self.parse_states.get(state, 0) + 1

    def record_language(self, language: str) -> None:
        self.languages[language] = self.languages.get(language, 0) + 1

    def summary(self) -> str:
        text = f"Indexed {self.files} files into {self.chunks} chunks"
        if self.errors:
            text += f"; {len(self.errors)} file(s) skipped"
        if self.skipped:
            skipped = ", ".join(
                f"{count} {reason}" for reason, count in sorted(self.skipped.items())
            )
            text += f"; filtered out {skipped}"
        return text


def get_ignore_spec(repo_path: str) -> pathspec.PathSpec:
    """Reads .gitignore if it exists and combines it with our defaults."""
    patterns = list(DEFAULT_IGNORE_PATTERNS)

    gitignore_path = os.path.join(repo_path, ".gitignore")
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
            patterns.extend(f.readlines())

    return pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, patterns)


def detect_language(filename: str) -> str:
    """Guess the programming language from the extension."""
    if filename == "Dockerfile":
        return "Dockerfile"
    if filename == "Makefile":
        return "Makefile"

    _, ext = os.path.splitext(filename)
    return LANGUAGE_MAP.get(ext.lower(), "Unknown")


def naive_chunker(source_code: str, chunk_size: int | None = None):
    """Fallback chunker that splits raw text into fixed-size blocks by lines."""
    if chunk_size is None:
        chunk_size = settings.CHUNK_SIZE_CHARS

    chunks = []
    lines = source_code.splitlines()
    current_chunk = []
    current_length = 0
    start_line = 1

    for i, line in enumerate(lines, 1):
        current_chunk.append(line)
        current_length += len(line) + 1  # +1 for newline

        # If we hit the size limit or the end of the file, save the chunk
        if current_length >= chunk_size or i == len(lines):
            if current_chunk:
                chunks.append({
                    "chunk_type": "raw_text",
                    "name": f"Lines {start_line}-{i}",
                    "content": "\n".join(current_chunk),
                    "start_line": start_line,
                    "end_line": i
                })
            current_chunk = []
            current_length = 0
            start_line = i + 1

    return chunks


def _to_posix(rel_path: str) -> PurePosixPath:
    """
    Normalize an os.walk relative path to POSIX form.

    pathspec's GitWildMatchPattern only understands "/" separators, so feeding it
    os.path.join() output (backslashes on Windows) made every nested ignore rule
    such as "frontend/node_modules/" silently fail to match.
    """
    return PurePosixPath(rel_path.replace("\\", "/"))


def process_repository_files(repo_id: str, db: Session) -> FileProcessResult:
    """
    Walks the cloned repository directory, filters ignored files, and creates
    File + Chunk records for each valid file.
    """
    result = FileProcessResult()

    repo = db.query(Repository).filter(Repository.id == repo_id).first()
    if not repo or not repo.local_path:
        raise ValueError(f"Repository {repo_id} has no local checkout to process.")

    # Delete any existing files for this repo (useful if we re-sync later)
    db.query(File).filter(File.repository_id == repo.id).delete()
    db.commit()

    spec = get_ignore_spec(repo.local_path)
    pending = 0

    for root, dirs, files in os.walk(repo.local_path):
        rel_root = os.path.relpath(root, repo.local_path)
        if rel_root == ".":
            rel_root = ""
        posix_root = _to_posix(rel_root)

        # Prune ignored directories *in place* so os.walk doesn't traverse them
        dirs[:] = [
            d for d in dirs
            if not spec.match_file(f"{(posix_root / d).as_posix()}/")
            and d.lower() not in file_filter.BUILD_DIRS
        ]

        for filename in files:
            rel_file_path = (posix_root / filename).as_posix()
            full_path = os.path.join(root, filename)

            try:
                size = os.path.getsize(full_path)
            except OSError as exc:
                result.errors.append(f"{rel_file_path}: {exc}")
                continue

            reason = file_filter.skip_reason(
                rel_file_path,
                size,
                gitignore_spec=spec,
                max_file_size_bytes=settings.MAX_FILE_SIZE_BYTES,
            )
            if reason is not None:
                result.record_skip(reason)
                continue

            _, ext = os.path.splitext(filename)
            extension = ext.lower()

            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    source_code = f.read()

                if file_filter.looks_generated_bulk(source_code, size):
                    result.record_skip(file_filter.SkipReason.GENERATED_BULK)
                    continue

                with db.begin_nested():
                    parsed = languages.parse_symbols_for_file(
                        filename, extension, source_code
                    )
                    if not parsed.language:
                        # Payload that survived the extension checks but is not
                        # text; never index it as if it were code.
                        result.record_skip(file_filter.SkipReason.BINARY)
                        continue

                    result.record_parse_state(parsed.state)

                    new_file = File(
                        repository_id=repo.id,
                        file_path=rel_file_path,
                        extension=extension,
                        language=parsed.language,
                        parse_state=parsed.state,
                        size_bytes=size,
                        # The resolver reads these to scope called names to the
                        # file that defines them (see call_graph_service).
                        imports_json=parsed.imports or None,
                    )
                    db.add(new_file)
                    db.flush()
                    result.record_language(parsed.language)

                    is_structural = parsed.state == languages.PARSE_STATE_STRUCTURAL

                    if parsed.blocks:
                        extracted_blocks = parsed.blocks
                    elif is_structural:
                        # A structural parse that produced no symbols still gets a
                        # single chunk so the file remains searchable.
                        extracted_blocks = naive_chunker(source_code)
                    else:
                        # Docs and config are represented in the manifest instead
                        # of being embedded one chunk at a time. Emitting raw_text
                        # for these dominated the index on doc-heavy repositories
                        # (a single extensionless LICENSE file alone produced 555
                        # vectors), while contributing nothing to code questions.
                        extracted_blocks = []
                        result.manifest_only_files += 1

                    chunks_to_insert = [
                        Chunk(
                            file_id=new_file.id,
                            chunk_type=block["chunk_type"],
                            name=block["name"],
                            content=block["content"],
                            start_line=block["start_line"],
                            end_line=block["end_line"],
                            metadata_json=block.get("metadata_json"),
                        )
                        for block in extracted_blocks
                    ]
                    if chunks_to_insert:
                        db.add_all(chunks_to_insert)

                result.files += 1
                result.chunks += len(chunks_to_insert)
                pending += 1

            except Exception as exc:
                result.errors.append(f"{rel_file_path}: {exc}")
                logger.warning("Skipping %s: %s", rel_file_path, exc)
                continue

            if pending >= COMMIT_EVERY_FILES:
                db.commit()
                pending = 0

    db.commit()

    repo.chunk_count = result.chunks
    repo.embedded_count = 0
    db.commit()

    logger.info("%s: %s", repo.full_name, result.summary())
    if result.parse_states:
        logger.info(
            "%s: parse states %s",
            repo.full_name,
            ", ".join(f"{k}={v}" for k, v in sorted(result.parse_states.items())),
        )
    if result.languages:
        logger.info(
            "%s: languages %s",
            repo.full_name,
            ", ".join(f"{k}={v}" for k, v in sorted(result.languages.items())),
        )
    return result
