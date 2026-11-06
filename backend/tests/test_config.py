import os
import unittest
from unittest import mock

from app.core import config


class ConfigTest(unittest.TestCase):
    def test_storage_paths_are_absolute_and_cwd_independent(self):
        self.assertTrue(os.path.isabs(config.settings.CHROMA_STORAGE_DIR))
        self.assertTrue(os.path.isabs(config.settings.REPO_STORAGE_DIR))
        self.assertIn(os.path.join(".data", "chroma"), config.settings.CHROMA_STORAGE_DIR)
        self.assertIn(os.path.join(".data", "repos"), config.settings.REPO_STORAGE_DIR)

    def test_backend_root_points_at_the_backend_package(self):
        self.assertTrue(config.BACKEND_ROOT.name.endswith("backend"))
        self.assertTrue(os.path.isfile(os.path.join(config.BACKEND_ROOT, "app", "main.py")))

    def test_env_file_is_resolved_from_the_backend_root(self):
        env_file = config.Settings.Config.env_file
        self.assertTrue(os.path.isabs(env_file))
        self.assertTrue(os.path.isfile(env_file))

    def test_ingestion_knobs_are_configurable(self):
        for name in (
            "EMBEDDING_BACKEND",
            "EMBEDDING_MODEL",
            "GEMINI_EMBEDDING_MODEL",
            "GEMINI_EMBEDDING_DIMENSIONS",
            "HF_INFERENCE_PROVIDER",
            "EMBEDDING_BATCH_SIZE",
            "CHUNK_SIZE_CHARS",
            "MAX_FILE_SIZE_BYTES",
            "LLM_PROVIDER",
            "LLM_TEMPERATURE",
        ):
            self.assertTrue(hasattr(config.settings, name), f"missing setting {name}")

    def test_batch_size_is_positive(self):
        self.assertGreater(config.settings.EMBEDDING_BATCH_SIZE, 0)
        self.assertGreater(config.settings.CHUNK_SIZE_CHARS, 0)
        self.assertGreater(config.settings.MAX_FILE_SIZE_BYTES, 0)


class LazyInitializationTest(unittest.TestCase):
    def test_importing_services_does_not_load_the_embedding_model(self):
        import sys

        from app.services import embedding_service

        self.assertIsNone(embedding_service._embedding_function)
        self.assertIsNone(embedding_service._chroma_client)
        self.assertNotIn("sentence_transformers", sys.modules)

    def test_llm_is_not_built_at_import_time(self):
        from app.services import rag_service

        self.assertIsNone(rag_service._llm)


class WarmUpTest(unittest.TestCase):
    def test_warm_up_loads_the_model_on_a_daemon_thread(self):
        from app.services import embedding_service

        with mock.patch.object(embedding_service, "get_embedding_function") as loader:
            thread = embedding_service.warm_embedding_model_in_background()
            thread.join(timeout=10)

        self.assertTrue(thread.daemon)
        self.assertFalse(thread.is_alive())
        loader.assert_called_once()

    def test_warm_up_does_not_block_the_caller(self):
        import time as time_module

        from app.services import embedding_service

        def slow_load():
            time_module.sleep(1.5)

        with mock.patch.object(embedding_service, "get_embedding_function", slow_load):
            started = time_module.perf_counter()
            thread = embedding_service.warm_embedding_model_in_background()
            elapsed = time_module.perf_counter() - started
            thread.join(timeout=10)

        self.assertLess(elapsed, 1.0)

    def test_warm_up_failure_does_not_break_startup(self):
        from app.services import embedding_service

        with mock.patch.object(
            embedding_service, "get_embedding_function", side_effect=RuntimeError("no model")
        ):
            thread = embedding_service.warm_embedding_model_in_background()
            thread.join(timeout=10)

        self.assertFalse(thread.is_alive())

    def test_model_is_reused_after_warm_up(self):
        from app.services import embedding_service

        sentinel = object()
        with mock.patch.object(embedding_service, "get_embedding_function", return_value=sentinel):
            thread = embedding_service.warm_embedding_model_in_background()
            thread.join(timeout=10)
            with mock.patch.object(embedding_service, "_embedding_function", sentinel):
                self.assertIs(embedding_service.get_embedding_function(), sentinel)


if __name__ == "__main__":
    unittest.main()
