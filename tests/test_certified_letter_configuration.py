# test_certified_letter_configuration.py
"""
Description: 환경변수 기반 내용증명 서비스가 모델·DB·리랭커를 지연 구성하는지 검증한다.
실제 모델이나 DB를 로드하지 않고 경로와 비활성화 동작을 확인한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 기본 앱의 내용증명 런타임 조립을 검증하는 테스트가 없는 상태.
After:
    - 키 유무, 경로 설정과 판례 리랭커 선택이 자동 검증됨.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.services.certified_letter_configuration import (
    PROJECT_ROOT,
    create_certified_letter_service_from_env,
)
from ml.agent.certified_letter.gemini import GeminiStructuredLetterModel
from ml.rag.reranker import CrossEncoderReranker, DenseSimilarityReranker


class CertifiedLetterConfigurationTests(unittest.TestCase):
    def test_missing_key_or_disable_switch_keeps_service_unconfigured(self):
        with (
            patch(
                "api.services.certified_letter_configuration.load_env_file"
            ),
            patch.dict(os.environ, {}, clear=True),
        ):
            self.assertIsNone(create_certified_letter_service_from_env().graph)

        with (
            patch(
                "api.services.certified_letter_configuration.load_env_file"
            ),
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key",
                    "CERTIFIED_LETTER_GENERATION_ENABLED": "false",
                },
                clear=True,
            ),
        ):
            self.assertIsNone(create_certified_letter_service_from_env().graph)

    def test_configures_lazy_retrievers_model_and_cross_encoder(self):
        graph = object()
        environment = {
            "GEMINI_API_KEY": "test-key",
            "CERTIFIED_LETTER_GEMINI_MODEL": "models/gemini-3.5-flash-lite",
            "ALPHALAWVA_LOCAL_DATA_DIR": "local-fixtures",
            "CERTIFIED_LETTER_STATUTE_DB_DIR": "statute-db",
            "CERTIFIED_LETTER_MODEL_CACHE_DIR": "model-cache",
        }
        with (
            patch(
                "api.services.certified_letter_configuration.load_env_file"
            ),
            patch.dict(os.environ, environment, clear=True),
            patch(
                "api.services.certified_letter_configuration."
                "create_certified_letter_workflow",
                return_value=graph,
            ) as create_workflow,
        ):
            service = create_certified_letter_service_from_env()

        self.assertIs(service.graph, graph)
        arguments = create_workflow.call_args.kwargs
        self.assertIsInstance(arguments["model"], GeminiStructuredLetterModel)
        self.assertIs(arguments["scope_classifier"], arguments["model"])
        self.assertEqual(arguments["model"].model_name, "gemini-3.5-flash-lite")
        self.assertEqual(
            arguments["statute_search"].retriever.db_dir,
            PROJECT_ROOT / "statute-db",
        )
        self.assertEqual(
            arguments["precedent_search"].retriever.db_dir,
            PROJECT_ROOT
            / "local-fixtures/precedents/vector_dbs/precedents_bge-m3_v1",
        )
        self.assertEqual(
            arguments["statute_search"].retriever.model_cache,
            PROJECT_ROOT / "model-cache",
        )
        self.assertFalse(arguments["statute_search"].retriever.is_loaded)
        self.assertFalse(arguments["precedent_search"].retriever.is_loaded)
        self.assertIs(
            arguments["statute_search"].retriever._model_loader,
            arguments["precedent_search"].retriever._model_loader,
        )
        self.assertIsInstance(
            arguments["statute_reranker"],
            DenseSimilarityReranker,
        )
        self.assertIsInstance(
            arguments["precedent_reranker"],
            CrossEncoderReranker,
        )
        self.assertFalse(arguments["precedent_reranker"].is_loaded)
        self.assertEqual(
            arguments["precedent_reranker"].model_path,
            PROJECT_ROOT
            / "local-fixtures/models/rerankers/bge-reranker-v2-m3",
        )
        self.assertIsInstance(
            arguments["model"].usage_manifest_path,
            Path,
        )

    def test_can_select_dense_precedent_baseline(self):
        with (
            patch(
                "api.services.certified_letter_configuration.load_env_file"
            ),
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key",
                    "CERTIFIED_LETTER_PRECEDENT_RERANKER": "dense",
                },
                clear=True,
            ),
            patch(
                "api.services.certified_letter_configuration."
                "create_certified_letter_workflow",
                return_value=object(),
            ) as create_workflow,
        ):
            create_certified_letter_service_from_env()

        self.assertIsInstance(
            create_workflow.call_args.kwargs["precedent_reranker"],
            DenseSimilarityReranker,
        )

    def test_can_plug_independent_local_cross_encoders_without_code_change(self):
        environment = {
            "GEMINI_API_KEY": "test-key",
            "ALPHALAWVA_LOCAL_DATA_DIR": "local-fixtures",
            "CERTIFIED_LETTER_STATUTE_RERANKER": "future-statute-model",
            "CERTIFIED_LETTER_PRECEDENT_RERANKER": "future-precedent-model",
            "CERTIFIED_LETTER_PRECEDENT_RERANKER_DIR": "custom/precedent-model",
            "CERTIFIED_LETTER_RERANKER_TRUST_REMOTE_CODE": "true",
        }
        with (
            patch(
                "api.services.certified_letter_configuration.load_env_file"
            ),
            patch.dict(os.environ, environment, clear=True),
            patch(
                "api.services.certified_letter_configuration."
                "create_certified_letter_workflow",
                return_value=object(),
            ) as create_workflow,
        ):
            create_certified_letter_service_from_env()

        arguments = create_workflow.call_args.kwargs
        statute = arguments["statute_reranker"]
        precedent = arguments["precedent_reranker"]
        self.assertIsInstance(statute, CrossEncoderReranker)
        self.assertIsInstance(precedent, CrossEncoderReranker)
        self.assertEqual(
            statute.model_path,
            PROJECT_ROOT
            / "local-fixtures/models/rerankers/future-statute-model",
        )
        self.assertEqual(
            precedent.model_path,
            PROJECT_ROOT / "custom/precedent-model",
        )
        self.assertTrue(statute.trust_remote_code)
        self.assertTrue(precedent.trust_remote_code)

    def test_rejects_unsafe_implicit_reranker_name(self):
        with (
            patch(
                "api.services.certified_letter_configuration.load_env_file"
            ),
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key",
                    "CERTIFIED_LETTER_STATUTE_RERANKER": "../outside",
                },
                clear=True,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "이름에는"):
                create_certified_letter_service_from_env()

    def test_external_env_file_becomes_relative_runtime_root(self):
        graph = object()
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_root = Path(temporary_directory).resolve()
            env_file = runtime_root / ".env"
            env_file.write_text(
                "GEMINI_API_KEY=test-key\n"
                "ALPHALAWVA_LOCAL_DATA_DIR=local_data\n"
                "CERTIFIED_LETTER_STATUTE_DB_DIR=data/statutes/vectorstores/bge_m3\n"
                "CERTIFIED_LETTER_MODEL_CACHE_DIR=data/statutes/models\n",
                encoding="utf-8",
            )
            with (
                patch.dict(
                    os.environ,
                    {"ALPHALAWVA_ENV_FILE": str(env_file)},
                    clear=True,
                ),
                patch(
                    "api.services.certified_letter_configuration."
                    "create_certified_letter_workflow",
                    return_value=graph,
                ) as create_workflow,
            ):
                service = create_certified_letter_service_from_env()

        self.assertIs(service.graph, graph)
        arguments = create_workflow.call_args.kwargs
        self.assertEqual(
            arguments["statute_search"].retriever.db_dir,
            runtime_root / "data/statutes/vectorstores/bge_m3",
        )
        self.assertEqual(
            arguments["precedent_search"].retriever.db_dir,
            runtime_root
            / "local_data/precedents/vector_dbs/precedents_bge-m3_v1",
        )
        self.assertEqual(
            arguments["statute_search"].retriever.model_cache,
            runtime_root / "data/statutes/models",
        )

    def test_rejects_missing_explicit_env_file(self):
        with patch.dict(
            os.environ,
            {"ALPHALAWVA_ENV_FILE": "/missing/alphalawva/.env"},
            clear=True,
        ):
            with self.assertRaisesRegex(FileNotFoundError, "ALPHALAWVA_ENV_FILE"):
                create_certified_letter_service_from_env()


if __name__ == "__main__":
    unittest.main()
