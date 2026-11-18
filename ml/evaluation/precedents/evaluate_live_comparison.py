# evaluate_live_comparison.py
"""Run the production precedent search and comparison flow for three demo queries."""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

shared = Path(os.environ.get('ALPHALAWVA_EXPERIMENT_DATA_ROOT', ROOT)).expanduser()
load_dotenv(shared / '.env')
os.environ['ALPHALAWVA_LOCAL_DATA_DIR'] = str(shared / 'local_data')
os.environ['ALPHALAWVA_MODEL_CACHE_DIR'] = str(shared / 'data/statutes/models')
os.environ['RAG_STATUTE_RERANKER_DIR'] = str(shared / 'local_data/models/rerankers/kure-reranker-nano')

from api.models.precedents import PrecedentSearchRequest  # noqa: E402
from api.services.gemini_query_scope_guard import create_gemini_query_scope_guard_from_env  # noqa: E402
from api.services.gemini_similarity_explainer import create_gemini_similarity_explainer_from_env  # noqa: E402
from api.services.precedent_search_eligibility import create_precedent_search_retriever_from_env  # noqa: E402
from api.services.precedent_search_service import PrecedentSearchService  # noqa: E402

QUERIES = [
    '전세계약을 했는데 중개사가 집에 먼저 잡힌 빚이 있다는 사실을 알려주지 않아 보증금을 돌려받지 못했습니다.',
    '월세집에 누수와 곰팡이가 생겼는데 집주인이 수리를 해주지 않습니다.',
    '전세계약이 끝났는데 집주인이 보증금을 돌려주지 않습니다.',
]

explainer = create_gemini_similarity_explainer_from_env()
if explainer is None:
    raise RuntimeError('Gemini 비교 모델 설정 또는 API 키가 없습니다.')
service = PrecedentSearchService(
    retriever=create_precedent_search_retriever_from_env(),
    scope_guard=create_gemini_query_scope_guard_from_env(),
    similarity_explainer=explainer,
)
output_path = shared / 'local_data/precedents/evaluation/live_comparison_20261118.jsonl'
output_path.parent.mkdir(parents=True, exist_ok=True)
original_post = explainer._post_json
captured = {}

def capture(payload):
    captured['request'] = payload
    response = original_post(payload)
    captured['response'] = response
    return response

explainer._post_json = capture
with output_path.open('a', encoding='utf-8') as out:
    for query in QUERIES:
        search = service.search(PrecedentSearchRequest(query=query, limit=10))
        print(f'검색 {search.status}: {search.result_count}건', flush=True)
        for item in search.results[:3]:
            precedent_id = item.precedent.precedent_id
            captured.clear()
            started = time.perf_counter()
            result = service.generate_similarity_explanation(search.search_id, precedent_id)
            record = {'query': query, 'search_id': str(search.search_id), 'precedent_id': precedent_id,
                      'search_result': item.model_dump(mode='json'), 'explanation': result.model_dump(mode='json'),
                      'duration_seconds': round(time.perf_counter()-started, 2), **captured}
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
            out.flush()
            print(f"  {precedent_id}: {result.status} ({record['duration_seconds']}초)", flush=True)
