import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import chromadb
from google import genai
from google.genai import types
from langchain_core.embeddings import Embeddings
from langchain_huggingface import (
    HuggingFaceEmbeddings,
    HuggingFaceEndpointEmbeddings,
)
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chunk import Chunk
from app.models.file import File
from app.models.repository import Repository

logger = logging.getLogger(__name__)

_embedding_function = None
_chroma_client = None

# Gemini's embedding endpoint accepts at most 100 inputs per request. The
# token estimate keeps a request comfortably below the per-request limit.
_GEMINI_MAX_BATCH_ITEMS = 100
_GEMINI_MAX_BATCH_TOKENS = 20_000

# Rate-limited requests get a handful of slow retries with exponential backoff.
# Non-transient failures still get one quick retry, matching the previous
# behaviour without adding long sleeps to genuine errors.
_RETRYABLE_ATTEMPTS = 5
_NON_RETRYABLE_ATTEMPTS = 2
_RETRY_BASE_SECONDS = 2.0
_RETRY_MAX_SECONDS = 60.0

_RETRYABLE_MARKERS = (
    "429",
    "resource_exhausted",
    "rate limit",
    "quota",
    "503",
    "service unavailable",
    "overloaded",
    "timeout",
    "timed out",
    "connection",
    "temporarily",
)


def _is_retryable_error(exc: Exception) -> bool:
    """Whether an embedding failure looks transient enough to wait out."""
    message = str(exc).lower()
    return any(marker in message for marker in _RETRYABLE_MARKERS)


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    """
    Delay before the next retry.

    Gemini reports a server-side ``retryDelay`` (and a human-readable "Please
    retry in Ns"). Honouring it avoids retrying before the rate-limit window
    has actually reset. Otherwise fall back to capped exponential backoff.
    """
    message = str(exc)
    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", message)
    if not match:
        match = re.search(r"Please retry in (\d+(?:\.\d+)?)s", message)

    if match:
        # Small cushion so the reset has definitely landed.
        return min(float(match.group(1)) + 1.0, _RETRY_MAX_SECONDS)

    return min(_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), _RETRY_MAX_SECONDS)


def _normalize_embedding(vector: list[float]) -> list[float]:
    """L2-normalize a vector so L2 distance behaves like cosine similarity."""
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return list(vector)
    return [value / norm for value in vector]


def _estimate_tokens(text: str) -> int:
    """Conservative token estimate (roughly four characters per token)."""
    return max(1, len(text) // 4)


def _gemini_batches(texts: list[str]) -> list[list[str]]:
    """Split texts into request-sized batches."""
    batches: list[list[str]] = []
    current: list[str] = []
    tokens = 0

    for text in texts:
        text_tokens = _estimate_tokens(text)
        if current and (
            len(current) >= _GEMINI_MAX_BATCH_ITEMS
            or tokens + text_tokens > _GEMINI_MAX_BATCH_TOKENS
        ):
            batches.append(current)
            current = []
            tokens = 0
        current.append(text)
        tokens += text_tokens

    if current:
        batches.append(current)
    return batches


class _GeminiEmbeddings(Embeddings):
    """
    Gemini embeddings with batching, task formatting and normalization.

    Embedding 2 rejects ``task_type`` and expects the task to be part of the
    text instead. Wrapping every input in its own ``Content`` is what makes the
    API return one vector per chunk rather than aggregating them.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        output_dimensionality: int,
    ) -> None:
        self.model = model
        self.output_dimensionality = output_dimensionality
        self._client = genai.Client(api_key=api_key)
        self._uses_instruction_prefix = "embedding-2" in model

    def _config(self, task_type: str) -> types.EmbedContentConfig:
        values: dict = {"output_dimensionality": self.output_dimensionality}
        if not self._uses_instruction_prefix:
            values["task_type"] = task_type
        return types.EmbedContentConfig(**values)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        prepared = [
            f"title: none | text: {text}"
            if self._uses_instruction_prefix
            else text
            for text in texts
        ]
        config = self._config("RETRIEVAL_DOCUMENT")
        vectors: list[list[float]] = []

        for batch in _gemini_batches(prepared):
            result = self._client.models.embed_content(
                model=self.model,
                contents=[types.Content(parts=[types.Part(text=text)]) for text in batch],
                config=config,
            )
            vectors.extend(
                _normalize_embedding(list(embedding.values))
                for embedding in result.embeddings
            )

        return vectors

    def embed_query(self, text: str) -> list[float]:
        prepared = (
            f"task: code retrieval | query: {text}"
            if self._uses_instruction_prefix
            else text
        )
        result = self._client.models.embed_content(
            model=self.model,
            contents=prepared,
            config=self._config("CODE_RETRIEVAL_QUERY"),
        )
        return _normalize_embedding(list(result.embeddings[0].values))


def _build_embedding_function() -> Embeddings:
    """Build the configured embedding backend (cloud API or local model)."""
    backend = (settings.EMBEDDING_BACKEND or "gemini").strip().lower()

    if backend in {"gemini", "google"}:
        if not settings.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not set. Add it to backend/.env to use "
                "Gemini embeddings, or set EMBEDDING_BACKEND=huggingface/local."
            )
        logger.info(
            "Using Gemini embeddings: %s (%s dimensions)",
            settings.GEMINI_EMBEDDING_MODEL,
            settings.GEMINI_EMBEDDING_DIMENSIONS,
        )
        return _GeminiEmbeddings(
            model=settings.GEMINI_EMBEDDING_MODEL,
            api_key=settings.GEMINI_API_KEY,
            output_dimensionality=settings.GEMINI_EMBEDDING_DIMENSIONS,
        )

    if backend in {"huggingface", "remote", "api"}:
        if not settings.HF_TOKEN:
            raise ValueError(
                "HF_TOKEN is not set. Add it to backend/.env to use the Hugging "
                "Face Inference API, or set EMBEDDING_BACKEND=local."
            )
        logger.info(
            "Using Hugging Face Inference API for embeddings: %s (provider=%s)",
            settings.EMBEDDING_MODEL,
            settings.HF_INFERENCE_PROVIDER or "auto",
        )
        return HuggingFaceEndpointEmbeddings(
            model=settings.EMBEDDING_MODEL,
            task="feature-extraction",
            provider=settings.HF_INFERENCE_PROVIDER or None,
            huggingfacehub_api_token=settings.HF_TOKEN,
            # Chunks can exceed MiniLM's 256-token limit. Local inference
            # truncates automatically, so ask the API to do the same, and
            # normalize to match the vectors already stored in Chroma.
            model_kwargs={"truncate": True, "normalize": True},
        )

    if backend == "local":
        logger.info("Loading local embedding model %s", settings.EMBEDDING_MODEL)
        return HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)

    raise ValueError(
        f"Unsupported EMBEDDING_BACKEND '{backend}'. "
        "Use 'gemini', 'huggingface' or 'local'."
    )


def get_embedding_function() -> Embeddings:
    """Lazily build the shared embedding backend (client or ~90MB local model)."""
    global _embedding_function
    if _embedding_function is None:
        _embedding_function = _build_embedding_function()
    return _embedding_function


def get_chroma_client() -> chromadb.ClientAPI:
    """Lazily build the shared persistent Chroma client."""
    global _chroma_client
    if _chroma_client is None:
        Path(settings.CHROMA_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=settings.CHROMA_STORAGE_DIR)
    return _chroma_client


def get_collection_name(repo_id: str) -> str:
    """Canonical Chroma collection name for a repository."""
    return f"repo_{str(repo_id).replace('-', '')}"


def warm_embedding_model_in_background() -> threading.Thread:
    """
    Start building the embedding backend on a background thread.

    A local model costs several seconds to load (a cloud client is immediate),
    so doing it inline in the startup handler would delay the server becoming
    ready, while doing it lazily would make the user's first question wait.
    Warming in the background overlaps this with login and page load, and a
    failure here is harmless because get_embedding_function() retries on first
    use.
    """
    def _load() -> None:
        started = time.perf_counter()
        try:
            get_embedding_function()
            logger.info(
                "Embedding model ready in %.1fs", time.perf_counter() - started
            )
        except Exception:
            logger.exception(
                "Embedding model warm-up failed; it will be retried on first use"
            )

    thread = threading.Thread(target=_load, name="embedding-warmup", daemon=True)
    thread.start()
    return thread


def delete_repository_collection(repo_id: str) -> bool:
    """Drop a repository's vector collection. Returns True if one was removed."""
    collection_name = get_collection_name(repo_id)
    try:
        get_chroma_client().delete_collection(name=collection_name)
    except Exception:
        logger.debug("Collection %s not present; nothing to delete", collection_name)
        return False
    logger.info("Deleted Chroma collection %s", collection_name)
    return True


@dataclass
class EmbeddingResult:
    total: int
    embedded: int
    errors: list = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.embedded == self.total

    def summary(self) -> str:
        text = f"Embedded {self.embedded}/{self.total} chunks"
        if self.errors:
            text += f"; {len(self.errors)} batch(es) failed"
        return text


def embed_repository_chunks(repo_id: str, db: Session) -> EmbeddingResult:
    """
    Embeds every chunk of a repository into its own Chroma collection.

    The collection is deleted first so a reprocess never merges with (or
    duplicates) a previous index. Raises ValueError when there is nothing
    legitimate to embed; partial failures are reported in the result so the
    caller can fail the repository instead of silently marking it complete.
    """
    repo = db.query(Repository).filter(Repository.id == repo_id).first()
    if not repo:
        raise ValueError(f"Repository {repo_id} not found for embedding.")

    # Deterministic order: file path, then start line. Chunks of a file are
    # therefore embedded contiguously, walking the tree top-down, which is what
    # lets the live architecture preview map embedded_count -> whole files
    # (see manifest_service.compute_live_file_ids). Do not drop this ORDER BY:
    # without it the prefix math is only a guess.
    chunks_with_files = (
        db.query(Chunk, File)
        .join(File, Chunk.file_id == File.id)
        .filter(File.repository_id == repo_id)
        .order_by(File.file_path, Chunk.start_line, Chunk.id)
        .all()
    )
    total = len(chunks_with_files)
    if total == 0:
        raise ValueError(f"No chunks found for {repo.full_name}; nothing to embed.")

    delete_repository_collection(repo_id)

    # A reprocess must never show stale progress from the previous run while
    # the new batches are landing.
    repo.embedded_count = 0
    db.commit()

    collection_name = get_collection_name(repo_id)
    collection = get_chroma_client().get_or_create_collection(
        name=collection_name,
        metadata={"description": f"Embeddings for {repo.full_name}"},
    )

    embedding_function = get_embedding_function()

    documents = []
    metadatas = []
    ids = []
    for chunk, file in chunks_with_files:
        # The file path is part of the embedded text so the LLM knows where the code lives
        documents.append(f"File: {file.file_path}\nCode:\n{chunk.content}")
        ids.append(str(chunk.id))
        metadatas.append(
            {
                "file_path": file.file_path,
                "language": file.language,
                "chunk_type": chunk.chunk_type,
                "name": chunk.name or "unknown",
                "start_line": chunk.start_line or 0,
            }
        )

    batch_size = max(1, settings.EMBEDDING_BATCH_SIZE)
    total_batches = (total + batch_size - 1) // batch_size
    embedded = 0
    errors: list = []

    for index in range(0, total, batch_size):
        batch_docs = documents[index : index + batch_size]
        batch_number = index // batch_size + 1
        attempt = 1
        while True:
            try:
                batch_embeddings = embedding_function.embed_documents(batch_docs)
                collection.add(
                    documents=batch_docs,
                    embeddings=batch_embeddings,
                    metadatas=metadatas[index : index + batch_size],
                    ids=ids[index : index + batch_size],
                )
            except Exception as exc:
                retryable = _is_retryable_error(exc)
                max_attempts = (
                    _RETRYABLE_ATTEMPTS if retryable else _NON_RETRYABLE_ATTEMPTS
                )
                if attempt < max_attempts:
                    delay = (
                        _retry_delay_seconds(exc, attempt) if retryable else 0.0
                    )
                    logger.warning(
                        "Batch %s/%s failed (attempt %s/%s), retrying in %.1fs: %s",
                        batch_number,
                        total_batches,
                        attempt,
                        max_attempts,
                        delay,
                        exc,
                    )
                    if delay:
                        time.sleep(delay)
                    attempt += 1
                    continue
                logger.error(
                    "Batch %s/%s failed permanently after %s attempt(s): %s",
                    batch_number,
                    total_batches,
                    attempt,
                    exc,
                )
                errors.append(f"batch {batch_number}/{total_batches}: {exc}")
                break
            embedded += len(batch_docs)
            # Commit progress per batch so the API can report an honest
            # "embedded N of M" while the import is still running. This is what
            # drives the live architecture preview.
            repo.embedded_count = embedded
            db.commit()
            logger.info("Embedded batch %s/%s", batch_number, total_batches)
            break

    return EmbeddingResult(total=total, embedded=embedded, errors=errors)
