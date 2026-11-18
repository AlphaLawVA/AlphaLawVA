# test_openai_similarity_explainer.py
"""판례 비교에 실제 전달되는 GPT 입력과 응답을 검증한다."""

import json
import unittest
from unittest.mock import patch

from api.models.precedents import SimilarityExplanationMethod
from api.services.openai_similarity_explainer import OpenAISimilarityExplainer, SYSTEM_PROMPT
from tests.test_gemini_similarity_explainer import make_chunk


class OpenAISimilarityExplainerTests(unittest.TestCase):
    def test_request_uses_summary_and_matched_chunks_without_easy_summary(self):
        explainer = OpenAISimilarityExplainer(api_key="test")
        output = {"similarities": [{"user_point": "보증금 미반환", "case_point": "보증금 반환", "explanation": "보증금 반환 문제"}], "differences": [], "matched_reason": "이 판례에서도 보증금 반환 문제가 다뤄졌습니다."}
        response = {"candidates": [{"content": {"parts": [{"text": json.dumps(output, ensure_ascii=False)}]}}]}
        with patch.object(explainer.transport, "_post_json", return_value=response) as post:
            result = explainer.explain(query="보증금을 돌려받지 못했어요", precedent={"생성요약": "판례요약", "쉬운요약": "쉬운요약"}, matched_chunks=[make_chunk(1)])
        request = post.call_args.args[0]
        payload = json.loads(request["contents"][0]["parts"][0]["text"])
        self.assertEqual(request["systemInstruction"]["parts"][0]["text"], SYSTEM_PROMPT)
        self.assertEqual(payload["precedent"]["precedent_summary"], "판례요약")
        self.assertNotIn("easy_summary", payload["precedent"])
        self.assertEqual(len(payload["matched_evidence"]), 1)
        self.assertEqual(result, output["matched_reason"])
        self.assertEqual(explainer.method, SimilarityExplanationMethod.OPENAI)


if __name__ == "__main__":
    unittest.main()
