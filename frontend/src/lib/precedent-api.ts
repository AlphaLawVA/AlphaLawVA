import type { CaseBasis } from "@/lib/demo-types";

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

type ApiErrorPayload = {
  error?: {
    message?: string;
  };
};

export class PrecedentApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "PrecedentApiError";
  }
}

export type PrecedentMetadata = {
  precedent_id: string;
  case_number: string | null;
  case_numbers: string[];
  case_name: string | null;
  court_name: string | null;
  decision_date: string | null;
  case_type: string | null;
  judgment_type: string | null;
};

export type MatchedChunk = {
  rank: number;
  chunk_id: string;
  section: string | null;
  chunk_type: string | null;
  section_chunk_index: number | null;
  similarity_score: number;
  text: string;
};

type PrecedentLinks = {
  detail: string | null;
  full_text: string;
  official_source: string;
};

export type PrecedentSearchItem = {
  rank: number;
  precedent: PrecedentMetadata;
  similarity_score: number;
  matched_chunk_count: number;
  matched_chunks: MatchedChunk[];
  preview: {
    summary: string | null;
    source: "easy_summary" | "precedent_summary" | null;
  };
  is_favorited: boolean;
  links: PrecedentLinks;
};

export type PrecedentSearchResponse = {
  search_id: string;
  status: "completed" | "no_results" | "out_of_scope";
  query: { text: string };
  result_count: number;
  results: PrecedentSearchItem[];
  message: string | null;
};

export type SimilarityExplanation = {
  status: "generated" | "generating" | "not_generated" | "failed";
  method: "local_llm" | "gemini" | "matched_chunks_only";
  model: string | null;
  matched_reason: string | null;
  matched_chunks: MatchedChunk[];
};

export type PrecedentDetailResponse = {
  search_id: string;
  query: { text: string };
  precedent: PrecedentMetadata;
  summaries: {
    precedent_summary: string | null;
    easy_summary: string | null;
  };
  official_fields: {
    issue: string | null;
    holding: string | null;
    referenced_statutes: string | null;
    referenced_precedents: string | null;
  };
  similarity_explanation: SimilarityExplanation;
  is_favorited: boolean;
  links: PrecedentLinks;
};

export type PrecedentFullTextResponse = {
  precedent: PrecedentMetadata;
  summaries: {
    precedent_summary: string | null;
    easy_summary: string | null;
  };
  official_fields: {
    issue: string | null;
    holding: string | null;
    referenced_statutes: string | null;
    referenced_precedents: string | null;
  };
  sections: Array<{
    position: number;
    key: string;
    title: string;
    content: string;
  }>;
  links: PrecedentLinks;
};

export type PrecedentFavoriteResponse = {
  favorite_id: string;
  created_at: string;
  search_id: string;
  query: { text: string };
  precedent: PrecedentMetadata;
  summaries: PrecedentDetailResponse["summaries"];
  official_fields: PrecedentDetailResponse["official_fields"];
  similarity_explanation: SimilarityExplanation;
  is_favorited: boolean;
  links: PrecedentLinks;
};

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
  } catch {
    throw new PrecedentApiError(
      "판례 검색 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인해주세요.",
      0,
    );
  }

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as ApiErrorPayload | null;
    throw new PrecedentApiError(
      payload?.error?.message ?? "판례 정보를 불러오지 못했습니다.",
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function searchPrecedents(query: string, limit = 10) {
  return apiRequest<PrecedentSearchResponse>("/api/v1/precedent-searches", {
    method: "POST",
    body: JSON.stringify({ query, limit }),
  });
}

export function getPrecedentDetail(searchId: string, precedentId: string) {
  return apiRequest<PrecedentDetailResponse>(
    `/api/v1/precedent-searches/${encodeURIComponent(searchId)}/results/${encodeURIComponent(precedentId)}`,
  );
}

export function getPrecedentFullText(precedentId: string) {
  return apiRequest<PrecedentFullTextResponse>(
    `/api/v1/precedents/${encodeURIComponent(precedentId)}/full-text`,
  );
}

export function generateSimilarityExplanation(searchId: string, precedentId: string) {
  return apiRequest<SimilarityExplanation>(
    `/api/v1/precedent-searches/${encodeURIComponent(searchId)}/results/${encodeURIComponent(precedentId)}/similarity-explanation`,
    { method: "POST" },
  );
}

export function createPrecedentFavorite(
  searchId: string,
  precedentId: string,
  accessToken: string,
) {
  return apiRequest<PrecedentFavoriteResponse>("/api/v1/precedent-favorites", {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify({ search_id: searchId, precedent_id: precedentId }),
  });
}

export function deletePrecedentFavorite(favoriteId: string, accessToken: string) {
  return apiRequest<void>(
    `/api/v1/precedent-favorites/${encodeURIComponent(favoriteId)}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  );
}

export function listPrecedentFavorites(accessToken: string) {
  return apiRequest<{ favorite_count: number; favorites: PrecedentFavoriteResponse[] }>(
    "/api/v1/precedent-favorites",
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
}

export function getPrecedentFavorite(favoriteId: string, accessToken: string) {
  return apiRequest<PrecedentFavoriteResponse>(
    `/api/v1/precedent-favorites/${encodeURIComponent(favoriteId)}`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
}

export function generateFavoriteSimilarityExplanation(
  favoriteId: string,
  accessToken: string,
) {
  return apiRequest<SimilarityExplanation>(
    `/api/v1/precedent-favorites/${encodeURIComponent(favoriteId)}/similarity-explanation`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  );
}

function displayCaseNumber(metadata: PrecedentMetadata) {
  return metadata.case_number ?? (metadata.case_numbers.join(", ") || `판례 ${metadata.precedent_id}`);
}

function evidenceChunks(chunks: MatchedChunk[]) {
  return chunks.map((chunk) => ({
    section: chunk.section ?? "검색 적중 부분",
    content: chunk.text,
    chunkType: chunk.chunk_type ?? undefined,
    sectionChunkIndex: chunk.section_chunk_index ?? undefined,
  }));
}

export function mapSearchItem(item: PrecedentSearchItem): CaseBasis {
  const fallbackSummary = item.matched_chunks[0]?.text ?? "요약 정보가 없습니다.";
  return {
    title: item.precedent.case_name ?? "판례",
    caseNumber: displayCaseNumber(item.precedent),
    precedentId: item.precedent.precedent_id,
    summary: item.preview.summary ?? fallbackSummary,
    easySummary: item.preview.source === "easy_summary" ? item.preview.summary ?? undefined : undefined,
    court: item.precedent.court_name ?? undefined,
    decisionDate: item.precedent.decision_date ?? undefined,
    judgmentType: item.precedent.judgment_type ?? undefined,
    evidenceChunks: evidenceChunks(item.matched_chunks),
    url: item.links.official_source,
  };
}

export function mergePrecedentDetail(
  precedent: CaseBasis,
  detail: PrecedentDetailResponse,
): CaseBasis {
  return {
    ...precedent,
    title: detail.precedent.case_name ?? precedent.title,
    caseNumber: displayCaseNumber(detail.precedent),
    summary: detail.summaries.precedent_summary ?? precedent.summary,
    easySummary: detail.summaries.easy_summary ?? undefined,
    court: detail.precedent.court_name ?? undefined,
    decisionDate: detail.precedent.decision_date ?? undefined,
    judgmentType: detail.precedent.judgment_type ?? undefined,
    matchedReason: detail.similarity_explanation.matched_reason ?? undefined,
    evidenceChunks: evidenceChunks(detail.similarity_explanation.matched_chunks),
    url: detail.links.official_source,
  };
}

export function mapFavoriteResponse(favorite: PrecedentFavoriteResponse): CaseBasis {
  return {
    title: favorite.precedent.case_name ?? "판례",
    caseNumber: displayCaseNumber(favorite.precedent),
    precedentId: favorite.precedent.precedent_id,
    summary:
      favorite.summaries.precedent_summary
      ?? favorite.summaries.easy_summary
      ?? favorite.similarity_explanation.matched_chunks[0]?.text
      ?? "요약 정보가 없습니다.",
    easySummary: favorite.summaries.easy_summary ?? undefined,
    matchedReason: favorite.similarity_explanation.matched_reason ?? undefined,
    court: favorite.precedent.court_name ?? undefined,
    decisionDate: favorite.precedent.decision_date ?? undefined,
    judgmentType: favorite.precedent.judgment_type ?? undefined,
    evidenceChunks: evidenceChunks(favorite.similarity_explanation.matched_chunks),
    url: favorite.links.official_source,
  };
}

export function mergeSimilarityExplanation(
  precedent: CaseBasis,
  explanation: SimilarityExplanation,
): CaseBasis {
  return {
    ...precedent,
    matchedReason: explanation.matched_reason ?? undefined,
    evidenceChunks: evidenceChunks(explanation.matched_chunks),
  };
}

export function mergePrecedentFullText(
  precedent: CaseBasis,
  response: PrecedentFullTextResponse,
): CaseBasis {
  const sections = Object.fromEntries(response.sections.map((section) => [section.key, section.content]));
  return {
    ...precedent,
    summary: response.summaries.precedent_summary ?? precedent.summary,
    easySummary: response.summaries.easy_summary ?? precedent.easySummary,
    fullText: {
      order: sections.order,
      claim: sections.claim,
      lowerCourt: sections.lower_court_decision,
      reason: sections.reason,
    },
    url: response.links.official_source,
  };
}
