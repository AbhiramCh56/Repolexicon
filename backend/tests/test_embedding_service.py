import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from app.models.chunk import Chunk
from app.models.file import File
from app.services import embedding_service, rag_service
from support import FakeChromaClient, FakeEmbedder, FakeSession, make_repository


def _rows(count: int):
    rows = []
    for index in range(count):
        file = File(id=uuid.uuid4(), file_path=f"src/mod{index}.py", language="Python")
        chunk = Chunk(
            id=uuid.uuid4(),
            chunk_type="function",
            name=f"fn_{index}",
            content=f"def fn_{index}():\n    return {index}\n",
            start_line=1,
            end_line=2,
        )
        rows.append((chunk, file))
    return rows


class CollectionNamingTest(unittest.TestCase):
    def test_collection_name_is_canonical(self):
        self.assertEqual(
            embedding_service.get_collection_name("d5d87f22-c430-4379-a237-72455ed735fd"),
            "repo_d5d87f22c4304379a23772455ed735fd",
        )

    def test_rag_service_shares_the_single_implementation(self):
        self.assertIs(rag_service.get_collection_name, embedding_service.get_collection_name)


class FakeGeminiModels:
    def __init__(self):
        self.calls = []

    def embed_content(self, *, model, contents, config):
        self.calls.append(
            SimpleNamespace(model=model, contents=contents, config=config)
        )
        count = len(contents) if isinstance(contents, list) else 1
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[3.0, 4.0]) for _ in range(count)
            ]
        )


class FakeGeminiClient:
    def __init__(self):
        self.models = FakeGeminiModels()


class GeminiEmbeddingsTest(unittest.TestCase):
    def _make(self, model):
        client = FakeGeminiClient()
        with mock.patch.object(
            embedding_service.genai, "Client", return_value=client
        ):
            embedder = embedding_service._GeminiEmbeddings(
                model=model,
                api_key="test-key",
                output_dimensionality=2,
            )
        return embedder, client

    def test_embedding_2_prefixes_documents_and_returns_one_vector_each(self):
        embedder, client = self._make("gemini-embedding-2")

        vectors = embedder.embed_documents(["alpha", "beta"])

        self.assertEqual(vectors, [[0.6, 0.8], [0.6, 0.8]])
        call = client.models.calls[0]
        self.assertEqual(len(call.contents), 2)
        self.assertEqual(call.contents[0].parts[0].text, "title: none | text: alpha")
        self.assertIsNone(call.config.task_type)
        self.assertEqual(call.config.output_dimensionality, 2)

    def test_embedding_2_query_uses_the_retrieval_instruction(self):
        embedder, client = self._make("gemini-embedding-2")

        embedder.embed_query("where is auth")

        self.assertEqual(
            client.models.calls[0].contents,
            "task: code retrieval | query: where is auth",
        )
        self.assertIsNone(client.models.calls[0].config.task_type)

    def test_embedding_001_uses_the_task_type_fields(self):
        embedder, client = self._make("gemini-embedding-001")

        embedder.embed_documents(["alpha"])
        embedder.embed_query("where is auth")

        self.assertEqual(
            client.models.calls[0].contents[0].parts[0].text, "alpha"
        )
        self.assertEqual(
            client.models.calls[0].config.task_type, "RETRIEVAL_DOCUMENT"
        )
        self.assertEqual(client.models.calls[1].contents, "where is auth")
        self.assertEqual(
            client.models.calls[1].config.task_type, "CODE_RETRIEVAL_QUERY"
        )

    def test_empty_document_list_makes_no_request(self):
        embedder, client = self._make("gemini-embedding-2")

        self.assertEqual(embedder.embed_documents([]), [])
        self.assertEqual(client.models.calls, [])

    def test_gemini_batches_respect_the_item_limit(self):
        batches = embedding_service._gemini_batches(["chunk"] * 101)

        self.assertEqual([len(batch) for batch in batches], [100, 1])


class EmbeddingBackendTest(unittest.TestCase):
    def test_gemini_backend_builds_gemini_embeddings(self):
        with mock.patch.object(
            embedding_service.settings, "EMBEDDING_BACKEND", "gemini"
        ), mock.patch.object(
            embedding_service.settings, "GEMINI_API_KEY", "test-key"
        ), mock.patch.object(
            embedding_service.settings, "GEMINI_EMBEDDING_MODEL", "gemini-embedding-2"
        ), mock.patch.object(
            embedding_service.settings, "GEMINI_EMBEDDING_DIMENSIONS", 1536
        ), mock.patch.object(
            embedding_service, "_GeminiEmbeddings"
        ) as gemini_cls:
            sentinel = object()
            gemini_cls.return_value = sentinel

            result = embedding_service._build_embedding_function()

        self.assertIs(result, sentinel)
        gemini_cls.assert_called_once_with(
            model="gemini-embedding-2",
            api_key="test-key",
            output_dimensionality=1536,
        )

    def test_gemini_backend_requires_a_key(self):
        with mock.patch.object(
            embedding_service.settings, "EMBEDDING_BACKEND", "gemini"
        ), mock.patch.object(
            embedding_service.settings, "GEMINI_API_KEY", None
        ):
            with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
                embedding_service._build_embedding_function()

    def test_huggingface_backend_uses_the_inference_api(self):
        with mock.patch.object(embedding_service.settings, "EMBEDDING_BACKEND", "huggingface"), \
             mock.patch.object(embedding_service.settings, "HF_TOKEN", "hf_test_token"), \
             mock.patch.object(
                 embedding_service.settings,
                 "EMBEDDING_MODEL",
                 "sentence-transformers/all-MiniLM-L6-v2",
             ), \
             mock.patch.object(
                 embedding_service.settings, "HF_INFERENCE_PROVIDER", "hf-inference"
             ), \
             mock.patch.object(
                 embedding_service, "HuggingFaceEndpointEmbeddings"
             ) as endpoint_cls:
            sentinel = object()
            endpoint_cls.return_value = sentinel

            result = embedding_service._build_embedding_function()

        self.assertIs(result, sentinel)
        endpoint_cls.assert_called_once_with(
            model="sentence-transformers/all-MiniLM-L6-v2",
            task="feature-extraction",
            provider="hf-inference",
            huggingfacehub_api_token="hf_test_token",
            model_kwargs={"truncate": True, "normalize": True},
        )

    def test_huggingface_backend_requires_a_token(self):
        with mock.patch.object(embedding_service.settings, "EMBEDDING_BACKEND", "huggingface"), \
             mock.patch.object(embedding_service.settings, "HF_TOKEN", None):
            with self.assertRaisesRegex(ValueError, "HF_TOKEN"):
                embedding_service._build_embedding_function()

    def test_local_backend_loads_the_model_in_process(self):
        with mock.patch.object(embedding_service.settings, "EMBEDDING_BACKEND", "local"), \
             mock.patch.object(
                 embedding_service.settings, "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
             ), \
             mock.patch.object(embedding_service, "HuggingFaceEmbeddings") as local_cls:
            sentinel = object()
            local_cls.return_value = sentinel

            result = embedding_service._build_embedding_function()

        self.assertIs(result, sentinel)
        local_cls.assert_called_once_with(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )

    def test_unknown_backend_is_rejected(self):
        with mock.patch.object(embedding_service.settings, "EMBEDDING_BACKEND", "wat"):
            with self.assertRaisesRegex(ValueError, "EMBEDDING_BACKEND"):
                embedding_service._build_embedding_function()


class DeleteCollectionTest(unittest.TestCase):
    def test_delete_removes_the_collection(self):
        client = FakeChromaClient()
        client.get_or_create_collection(name="repo_abc")

        with mock.patch.object(embedding_service, "get_chroma_client", return_value=client):
            removed = embedding_service.delete_repository_collection("abc")

        self.assertTrue(removed)
        self.assertEqual(client.deleted, ["repo_abc"])
        self.assertEqual(client.collections, {})

    def test_delete_is_idempotent(self):
        client = FakeChromaClient()

        with mock.patch.object(embedding_service, "get_chroma_client", return_value=client):
            self.assertFalse(embedding_service.delete_repository_collection("abc"))
            self.assertFalse(embedding_service.delete_repository_collection("abc"))


class EmbeddingResultTest(unittest.TestCase):
    def test_complete_requires_every_chunk(self):
        self.assertTrue(embedding_service.EmbeddingResult(total=3, embedded=3).complete)
        self.assertFalse(embedding_service.EmbeddingResult(total=3, embedded=2).complete)
        self.assertIn("3/3", embedding_service.EmbeddingResult(total=3, embedded=3).summary())


class EmbedRepositoryChunksTest(unittest.TestCase):
    def _run(self, rows, client=None, embedder=None):
        client = client or FakeChromaClient()
        embedder = embedder or FakeEmbedder()
        repo = make_repository()
        db = FakeSession(repo, rows=rows)
        with mock.patch.object(embedding_service, "get_chroma_client", return_value=client), \
             mock.patch.object(embedding_service, "get_embedding_function", return_value=embedder):
            result = embedding_service.embed_repository_chunks(str(repo.id), db)
        return result, client, embedder

    def test_successful_run_embeds_every_chunk(self):
        result, client, _ = self._run(_rows(3))

        self.assertTrue(result.complete)
        self.assertEqual(result.embedded, 3)
        self.assertEqual(result.errors, [])
        collection = list(client.collections.values())[0]
        self.assertEqual(len(collection.documents), 3)
        self.assertIn("File: src/mod0.py", collection.documents[0])

    def test_metadata_is_attached_to_every_vector(self):
        _, client, _ = self._run(_rows(2))

        collection = list(client.collections.values())[0]
        for metadata in collection.metadatas:
            self.assertEqual(
                set(metadata),
                {"file_path", "language", "chunk_type", "name", "start_line"},
            )

    def test_reprocessing_does_not_duplicate_vectors(self):
        rows = _rows(3)
        repo_id = str(make_repository().id)

        client = FakeChromaClient()
        for _ in range(3):
            repo = make_repository(id=uuid.UUID(repo_id))
            db = FakeSession(repo, rows=rows)
            with mock.patch.object(embedding_service, "get_chroma_client", return_value=client), \
                 mock.patch.object(embedding_service, "get_embedding_function", return_value=FakeEmbedder()):
                embedding_service.embed_repository_chunks(repo_id, db)

        collection = list(client.collections.values())[0]
        self.assertEqual(len(collection.ids), 3)
        self.assertEqual(len(set(collection.ids)), 3)
        collection_name = f"repo_{repo_id.replace('-', '')}"
        self.assertEqual(client.deleted, [collection_name, collection_name])

    def test_transient_batch_failure_is_retried(self):
        embedder = FakeEmbedder(fail_on_call=1)
        with mock.patch.object(embedding_service.settings, "EMBEDDING_BATCH_SIZE", 10):
            result, _, _ = self._run(_rows(2), embedder=embedder)

        self.assertTrue(result.complete)
        self.assertEqual(result.embedded, 2)
        self.assertEqual(embedder.calls, 2)

    def test_retry_delay_is_parsed_from_the_gemini_error(self):
        error = RuntimeError(
            "429 RESOURCE_EXHAUSTED ... 'retryDelay': '12s' ... Please retry in 11.8s."
        )

        self.assertEqual(embedding_service._retry_delay_seconds(error, 1), 13.0)

    def test_retry_delay_falls_back_to_capped_exponential_backoff(self):
        error = RuntimeError("temporary failure")

        self.assertEqual(embedding_service._retry_delay_seconds(error, 1), 2.0)
        self.assertEqual(embedding_service._retry_delay_seconds(error, 3), 8.0)
        self.assertEqual(embedding_service._retry_delay_seconds(error, 10), 60.0)

    def test_rate_limited_batch_waits_for_the_server_and_eventually_succeeds(self):
        class RateLimitedThenOk(FakeEmbedder):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            def embed_documents(self, documents):
                self.attempts += 1
                if self.attempts < 3:
                    raise RuntimeError(
                        "429 RESOURCE_EXHAUSTED 'retryDelay': '12s'"
                    )
                return super().embed_documents(documents)

        embedder = RateLimitedThenOk()
        with mock.patch.object(embedding_service.settings, "EMBEDDING_BATCH_SIZE", 10), \
             mock.patch.object(embedding_service.time, "sleep") as sleep:
            result, _, _ = self._run(_rows(2), embedder=embedder)

        self.assertTrue(result.complete)
        self.assertEqual(embedder.attempts, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [13.0, 13.0])

    def test_retryable_batches_use_more_attempts_than_plain_errors(self):
        class AlwaysRateLimited(FakeEmbedder):
            def embed_documents(self, documents):
                self.calls += 1
                raise RuntimeError("429 RESOURCE_EXHAUSTED 'retryDelay': '1s'")

        embedder = AlwaysRateLimited()
        with mock.patch.object(embedding_service.settings, "EMBEDDING_BATCH_SIZE", 10), \
             mock.patch.object(embedding_service.time, "sleep"):
            result, _, _ = self._run(_rows(2), embedder=embedder)

        self.assertFalse(result.complete)
        self.assertEqual(embedder.calls, embedding_service._RETRYABLE_ATTEMPTS)

    def test_permanent_failure_fails_the_run(self):
        class AlwaysFails(FakeEmbedder):
            def embed_documents(self, documents):
                raise RuntimeError("model exploded")

        with mock.patch.object(embedding_service.settings, "EMBEDDING_BATCH_SIZE", 10):
            result, _, _ = self._run(_rows(2), embedder=AlwaysFails())

        self.assertFalse(result.complete)
        self.assertEqual(result.embedded, 0)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("model exploded", result.errors[0])

    def test_repository_without_chunks_raises(self):
        with self.assertRaises(ValueError):
            self._run([])

    def test_unknown_repository_raises(self):
        db = FakeSession(None, rows=[])
        with mock.patch.object(embedding_service, "get_chroma_client", return_value=FakeChromaClient()):
            with self.assertRaises(ValueError):
                embedding_service.embed_repository_chunks(str(uuid.uuid4()), db)

    def test_batches_respect_the_configured_size(self):
        with mock.patch.object(embedding_service.settings, "EMBEDDING_BATCH_SIZE", 2):
            _, client, embedder = self._run(_rows(5))

        self.assertEqual(embedder.calls, 3)
        self.assertEqual(len(list(client.collections.values())[0].ids), 5)


if __name__ == "__main__":
    unittest.main()
