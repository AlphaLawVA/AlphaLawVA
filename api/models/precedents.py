# precedents.py
"""
Description: 판례 검색·상세·내부 원문 API의 요청과 응답 스키마를 정의한다.
검색 결과와 원본 데이터의 선택 필드 및 LLM 설명 상태를 명시적으로 검증한다.
Author: choeminju
Date: 2026-10-13
Before:
    - 판례 검색 화면과 백엔드 사이에 합의된 공개 I/O 스키마가 없는 상태.
After:
    - 판례 검색 기능의 목록·상세·내부 원문 응답 계약을 Pydantic으로 검증할 수 있음.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class SearchStatus(str, Enum):
    COMPLETED = "completed"
    NO_RESULTS = "no_results"
    OUT_OF_SCOPE = "out_of_scope"


class SummarySource(str, Enum):
    EASY_SUMMARY = "easy_summary"
    PRECEDENT_SUMMARY = "precedent_summary"


class SimilarityExplanationStatus(str, Enum):
    GENERATED = "generated"
    NOT_GENERATED = "not_generated"
    FAILED = "failed"


class SimilarityExplanationMethod(str, Enum):
    LOCAL_LLM = "local_llm"
    MATCHED_CHUNKS_ONLY = "matched_chunks_only"


class PrecedentSearchRequest(ApiModel):
    query: str = Field(
        min_length=2,
        max_length=2000,
        description="사용자가 자연어로 입력한 부동산 법률 상황이나 질문",
        examples=["개인회생 면책 후에도 임대인에게 보증금을 돌려받지 못했습니다."],
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=20,
        description="판례 단위로 반환할 최대 검색 결과 수",
    )


class SearchQuery(ApiModel):
    text: str = Field(
        min_length=2,
        max_length=2000,
        description="사용자가 입력한 원문 질문",
    )


class PrecedentMetadata(ApiModel):
    precedent_id: str = Field(
        min_length=1,
        description="국가법령정보센터 판례일련번호",
    )
    case_number: str | None = Field(
        default=None,
        min_length=1,
        description="대표 사건번호 또는 원본 사건번호 문자열",
    )
    case_numbers: list[str] = Field(
        default_factory=list,
        description="복수 사건번호를 분리한 목록",
    )
    case_name: str | None = Field(default=None, min_length=1, description="사건명")
    court_name: str | None = Field(default=None, min_length=1, description="법원명")
    decision_date: str | None = Field(
        default=None,
        min_length=1,
        description="원본 데이터의 선고일자",
    )
    case_type: str | None = Field(
        default=None,
        min_length=1,
        description="사건종류명",
    )
    judgment_type: str | None = Field(
        default=None,
        min_length=1,
        description="판결유형",
    )


class SummaryPreview(ApiModel):
    summary: str | None = Field(
        default=None,
        min_length=1,
        description="목록 화면에서 짧게 표시할 쉬운요약 또는 생성요약 일부",
    )
    source: SummarySource | None = Field(
        default=None,
        description="미리보기에 사용한 요약 필드",
    )

    @model_validator(mode="after")
    def validate_summary_source_pair(self) -> SummaryPreview:
        if (self.summary is None) != (self.source is None):
            raise ValueError("summary와 source는 함께 제공하거나 함께 생략해야 합니다.")
        return self


class PrecedentLinks(ApiModel):
    detail: str | None = Field(
        default=None,
        min_length=1,
        description="검색 맥락이 포함된 AlphaLawVA 판례 상세 API 경로",
    )
    full_text: str = Field(
        min_length=1,
        description="AlphaLawVA 내부 판례 원문 API 경로",
    )
    official_source: str = Field(
        min_length=1,
        description="국가법령정보센터의 공식 판례 원문 URL"
    )


class PrecedentSearchItem(ApiModel):
    rank: int = Field(ge=1, description="검색 결과 내 판례 순위")
    precedent: PrecedentMetadata
    similarity_score: float = Field(
        description="현재 검색기가 반환한 판례의 최고 청크 유사도"
    )
    matched_chunk_count: int = Field(
        ge=1,
        description="해당 판례에 묶인 검색 적중 청크 수",
    )
    preview: SummaryPreview
    is_favorited: bool = Field(
        default=False,
        description="현재 사용자의 즐겨찾기 여부; 저장 기능 연결 전에는 false",
    )
    links: PrecedentLinks

    @model_validator(mode="after")
    def require_detail_link(self) -> PrecedentSearchItem:
        if self.links.detail is None:
            raise ValueError("검색 결과에는 상세보기 링크가 필요합니다.")
        return self


class PrecedentSearchResponse(ApiModel):
    search_id: UUID = Field(description="검색 세션과 서버 로그를 연결하는 식별자")
    status: SearchStatus
    query: SearchQuery
    result_count: int = Field(ge=0, description="반환된 판례 수")
    results: list[PrecedentSearchItem]
    message: str | None = Field(
        default=None,
        min_length=1,
        description="검색 결과가 없거나 검색 범위를 벗어난 경우의 안내 문구",
    )

    @model_validator(mode="after")
    def validate_result_state(self) -> PrecedentSearchResponse:
        if self.result_count != len(self.results):
            raise ValueError("result_count는 results 길이와 같아야 합니다.")
        if self.status is SearchStatus.COMPLETED and not self.results:
            raise ValueError("completed 상태에는 하나 이상의 검색 결과가 필요합니다.")
        if self.status is not SearchStatus.COMPLETED and self.results:
            raise ValueError("완료 상태가 아니면 검색 결과를 포함할 수 없습니다.")
        if self.status is not SearchStatus.COMPLETED and not self.message:
            raise ValueError("검색 완료 상태가 아니면 안내 message가 필요합니다.")
        return self


class PrecedentSummaries(ApiModel):
    precedent_summary: str | None = Field(
        default=None,
        min_length=1,
        description="final_cases의 생성요약",
    )
    easy_summary: str | None = Field(
        default=None,
        min_length=1,
        description="final_cases의 쉬운요약",
    )


class PrecedentOfficialFields(ApiModel):
    issue: str | None = Field(
        default=None,
        min_length=1,
        description="원본 판례의 판시사항; 비어 있으면 null",
    )
    holding: str | None = Field(
        default=None,
        min_length=1,
        description="원본 판례의 판결요지; 비어 있으면 null",
    )
    referenced_statutes: str | None = Field(
        default=None,
        min_length=1,
        description="원본 판례의 참조조문 문자열",
    )
    referenced_precedents: str | None = Field(
        default=None,
        min_length=1,
        description="원본 판례의 참조판례 문자열",
    )


class MatchedChunk(ApiModel):
    rank: int = Field(ge=1, description="전체 청크 검색 결과에서의 순위")
    chunk_id: str
    section: str | None = None
    chunk_type: str | None = None
    section_chunk_index: int | None = Field(default=None, ge=0)
    similarity_score: float
    text: str = Field(min_length=1, description="검색에 사용된 판례 청크 원문")


class SimilarityExplanation(ApiModel):
    status: SimilarityExplanationStatus
    method: SimilarityExplanationMethod
    model: str | None = Field(
        default=None,
        min_length=1,
        description="설명을 생성한 로컬 LLM 식별자",
    )
    matched_reason: str | None = Field(
        default=None,
        min_length=1,
        description="사용자 상황과 판례가 유사한 이유를 설명한 생성 문장",
    )
    matched_chunks: list[MatchedChunk] = Field(
        min_length=1,
        description="유사성 판단의 근거가 된 검색 청크"
    )

    @model_validator(mode="after")
    def validate_generation_state(self) -> SimilarityExplanation:
        if self.status is SimilarityExplanationStatus.GENERATED:
            if self.method is not SimilarityExplanationMethod.LOCAL_LLM:
                raise ValueError("generated 상태의 method는 local_llm이어야 합니다.")
            if not self.model or not self.matched_reason:
                raise ValueError(
                    "generated 상태에는 model과 matched_reason이 필요합니다."
                )
        elif self.status is SimilarityExplanationStatus.NOT_GENERATED:
            if self.method is not SimilarityExplanationMethod.MATCHED_CHUNKS_ONLY:
                raise ValueError(
                    "not_generated 상태의 method는 matched_chunks_only여야 합니다."
                )
            if self.model is not None or self.matched_reason is not None:
                raise ValueError(
                    "not_generated 상태의 model과 matched_reason은 null이어야 합니다."
                )
        else:
            if self.method is not SimilarityExplanationMethod.LOCAL_LLM:
                raise ValueError("failed 상태의 method는 local_llm이어야 합니다.")
            if self.matched_reason is not None:
                raise ValueError("failed 상태의 matched_reason은 null이어야 합니다.")
        return self


class PrecedentDetailResponse(ApiModel):
    search_id: UUID
    query: SearchQuery
    precedent: PrecedentMetadata
    summaries: PrecedentSummaries
    official_fields: PrecedentOfficialFields
    similarity_explanation: SimilarityExplanation
    is_favorited: bool = False
    links: PrecedentLinks


class FullTextSection(ApiModel):
    position: int = Field(ge=1, description="내부 원문 화면에서의 표시 순서")
    key: str = Field(
        min_length=1,
        description="final_cases 원본 필드에 대응하는 안정적인 영문 키",
    )
    title: str = Field(min_length=1, description="화면에 표시할 한글 섹션명")
    content: str = Field(min_length=1, description="빈 값을 제외한 판례 원문 내용")


class PrecedentFullTextResponse(ApiModel):
    precedent: PrecedentMetadata
    summaries: PrecedentSummaries
    official_fields: PrecedentOfficialFields
    sections: list[FullTextSection] = Field(
        description="주문·청구취지·원심판결·이유 등 표시 가능한 원문 섹션"
    )
    links: PrecedentLinks
