import type { CaseBasis, ConfirmedContractSnapshot } from "@/lib/demo-types";

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

export const CERTIFIED_LETTER_FOLLOW_UP_TITLES = [
  "발송 전 확인",
  "발송 및 기록 보관",
  "기한 경과 후 대응",
] as const;

type ApiErrorPayload = {
  error?: {
    message?: string;
    field_errors?: Array<{ location?: Array<string | number>; message?: string }>;
  };
};

export class CertifiedLetterApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "CertifiedLetterApiError";
  }
}

export type CertifiedLetterContractInput = {
  contract_type: "jeonse" | "monthly_rent" | "sale";
  contract_date: string | null;
  property: {
    address: string;
    detail_address: string | null;
    building_type: string | null;
  };
  period: {
    start_date: string;
    end_date: string;
  } | null;
  payment: {
    deposit: number | null;
    monthly_rent: number | null;
    sale_price: number | null;
    down_payment: number | null;
    interim_payment: number | null;
    interim_payment_2: number | null;
    balance: number | null;
  };
  lessor: {
    name: string;
    address: string | null;
    detail_address: string | null;
    phone_number: string | null;
  } | null;
  lessee: {
    name: string;
    address: string | null;
    detail_address: string | null;
    phone_number: string | null;
  } | null;
  seller: {
    name: string;
    address: string | null;
    detail_address: string | null;
    phone_number: string | null;
  } | null;
  buyer: {
    name: string;
    address: string | null;
    detail_address: string | null;
    phone_number: string | null;
  } | null;
  handover_date: string | null;
  ownership_transfer_date: string | null;
  special_terms: string[];
};

export type CertifiedLetterDraftRequest = {
  sender_role: "lessee" | "lessor" | "buyer" | "seller";
  purpose: string;
  facts: string;
  requested_deadline: string | null;
  contract: CertifiedLetterContractInput;
};

export type CertifiedLetterResult = {
  document: {
    title: string;
    sender: { name: string; address: string; detail_address: string | null };
    recipient: { name: string; address: string; detail_address: string | null };
    body: string;
  };
  strategy_summary: string;
  follow_up_actions: Array<{ order: number; action: string }>;
  statute_evidence: Array<{
    statute_id: string | null;
    law_name: string;
    article_label: string;
    article_title: string | null;
    content: string;
    explanation: string;
    effective_date: string | null;
    source_node_id: string | null;
    source_url: string | null;
  }>;
  similar_precedents: Array<{
    precedent_id: string;
    case_number: string | null;
    case_name: string | null;
    court_name: string | null;
    decision_date: string | null;
    summary: string;
    precedent_summary: string | null;
    easy_summary: string | null;
    matched_reason: string | null;
    matched_chunks: Array<{
      rank: number;
      chunk_id: string;
      section: string | null;
      chunk_type: string | null;
      section_chunk_index: number | null;
      similarity_score: number;
      text: string;
    }>;
    full_text_sections: Array<{
      position: number;
      key: string;
      title: string;
      content: string;
    }>;
    source_url: string;
  }>;
  warnings: string[];
};

export type CertifiedLetterJobResponse = {
  draft_id: string;
  status: "queued" | "processing" | "completed" | "failed";
  outcome: "draft_ready" | "needs_information" | "out_of_scope" | "insufficient_evidence" | null;
  result: CertifiedLetterResult | null;
  missing_information: string[];
  message: string | null;
  error: { code: string; message: string } | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type SavedCertifiedLetterSummary = {
  result_id: string;
  draft_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type SavedCertifiedLetterDetail = SavedCertifiedLetterSummary & {
  result: CertifiedLetterResult;
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
    throw new CertifiedLetterApiError(
      "AlphaLawVA API 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인해주세요.",
      0,
    );
  }

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as ApiErrorPayload | null;
    const fieldMessage = payload?.error?.field_errors?.[0]?.message;
    throw new CertifiedLetterApiError(
      fieldMessage ?? payload?.error?.message ?? "내용증명 요청을 처리하지 못했습니다.",
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export function createCertifiedLetterDraft(
  payload: CertifiedLetterDraftRequest,
  idempotencyKey: string,
) {
  return apiRequest<{ draft_id: string; status: "queued"; created_at: string }>(
    "/api/v1/certified-letter-drafts",
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    },
  );
}

export function getCertifiedLetterDraft(draftId: string) {
  return apiRequest<CertifiedLetterJobResponse>(
    `/api/v1/certified-letter-drafts/${encodeURIComponent(draftId)}`,
  );
}

export function saveCertifiedLetterResult(draftId: string, accessToken: string) {
  return apiRequest<SavedCertifiedLetterDetail>("/api/v1/certified-letter-results", {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify({ draft_id: draftId }),
  });
}

export function listCertifiedLetterResults(accessToken: string) {
  return apiRequest<{ items: SavedCertifiedLetterSummary[] }>(
    "/api/v1/certified-letter-results",
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
}

export function getSavedCertifiedLetterResult(resultId: string, accessToken: string) {
  return apiRequest<SavedCertifiedLetterDetail>(
    `/api/v1/certified-letter-results/${encodeURIComponent(resultId)}`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
}

function requiredField(snapshot: ConfirmedContractSnapshot, id: string, label: string) {
  const value = snapshot.fields.find((field) => field.id === id)?.value.trim();
  if (!value) throw new Error(`계약정보 확인 화면에서 ${label}을(를) 확인해주세요.`);
  return value;
}

function optionalField(snapshot: ConfirmedContractSnapshot, id: string) {
  return snapshot.fields.find((field) => field.id === id)?.value.trim() || null;
}

function parseDate(value: string) {
  const match = value.match(/(\d{4})[.\/-](\d{1,2})[.\/-](\d{1,2})/);
  if (!match) throw new Error(`날짜 형식을 확인해주세요: ${value}`);
  return `${match[1]}-${match[2].padStart(2, "0")}-${match[3].padStart(2, "0")}`;
}

function parsePeriod(value: string) {
  const matches = [...value.matchAll(/(\d{4})[.\/-](\d{1,2})[.\/-](\d{1,2})/g)];
  if (matches.length < 2) throw new Error("계약 시작일과 종료일을 확인해주세요.");
  return {
    start_date: parseDate(matches[0][0]),
    end_date: parseDate(matches[1][0]),
  };
}

function parseMoney(value: string, label: string) {
  // 지급 일정 필드는 "금액 · 지급일" 형식이다. 날짜 숫자까지 합치면
  // 금액이 비정상적으로 커지므로, 첫 번째 금액 표기만 읽는다.
  const amountText = value.match(/[0-9][0-9,]*/)?.[0] ?? "";
  const digits = amountText.replace(/[^0-9]/g, "");
  if (!digits) throw new Error(`${label} 금액을 확인해주세요.`);
  const amount = Number(digits);
  if (!Number.isSafeInteger(amount)) throw new Error(`${label} 금액이 너무 큽니다.`);
  return amount;
}

function parseOptionalMoney(value: string | null, label: string) {
  if (!value || value === "없음") return null;
  return parseMoney(value, label);
}

function parseOptionalDate(value: string | null) {
  if (!value || value === "없음") return null;
  return parseDate(value);
}

function specialTerms(snapshot: ConfirmedContractSnapshot) {
  return (optionalField(snapshot, "special-terms") ?? "")
    .split(/\n+/)
    .map((term) => term.trim())
    .filter(Boolean);
}

export function buildCertifiedLetterContract(
  snapshot: ConfirmedContractSnapshot,
): CertifiedLetterContractInput {
  const isSale = snapshot.contractType === "매매";
  const contractDate = parseDate(requiredField(snapshot, "contract-date", "계약일"));
  const monthlyRentValue = optionalField(snapshot, "monthly-rent");
  const payment = {
    deposit: isSale ? null : parseMoney(requiredField(snapshot, "deposit", "보증금"), "보증금"),
    monthly_rent: snapshot.contractType === "월세"
      ? parseMoney(monthlyRentValue ?? "", "월세")
      : null,
    sale_price: isSale ? parseMoney(requiredField(snapshot, "deposit", "매매대금"), "매매대금") : null,
    down_payment: parseOptionalMoney(optionalField(snapshot, "down-payment"), "계약금"),
    interim_payment: parseOptionalMoney(optionalField(snapshot, "interim-payment"), "중도금"),
    interim_payment_2: parseOptionalMoney(optionalField(snapshot, "interim-payment-2"), "2차 중도금"),
    balance: parseOptionalMoney(optionalField(snapshot, "balance"), "잔금"),
  };
  const parties = {
    name: requiredField(snapshot, "lessor", isSale ? "매도인 이름" : "임대인 이름"),
    address: optionalField(snapshot, "lessor-address"),
    detail_address: optionalField(snapshot, "lessor-detail-address"),
    phone_number: optionalField(snapshot, "lessor-phone"),
  };
  const counterparty = {
    name: requiredField(snapshot, "lessee", isSale ? "매수인 이름" : "임차인 이름"),
    address: optionalField(snapshot, "lessee-address"),
    detail_address: optionalField(snapshot, "lessee-detail-address"),
    phone_number: optionalField(snapshot, "lessee-phone"),
  };
  return {
    contract_type: isSale ? "sale" : snapshot.contractType === "전세" ? "jeonse" : "monthly_rent",
    contract_date: contractDate,
    property: {
      address: requiredField(snapshot, "property-address", "목적물 주소"),
      detail_address: optionalField(snapshot, "property-detail"),
      building_type: optionalField(snapshot, "building-info")?.split("·")[0]?.trim() || null,
    },
    period: isSale ? null : parsePeriod(requiredField(snapshot, "lease-period", "임대차 기간")),
    payment,
    lessor: isSale ? null : parties,
    lessee: isSale ? null : counterparty,
    seller: isSale ? parties : null,
    buyer: isSale ? counterparty : null,
    handover_date: parseOptionalDate(optionalField(snapshot, isSale ? "handover-date" : "move-in-date")),
    ownership_transfer_date: parseOptionalDate(optionalField(snapshot, "ownership-transfer-date")),
    special_terms: specialTerms(snapshot),
  };
}

export function mapCertifiedLetterPrecedent(
  precedent: CertifiedLetterResult["similar_precedents"][number],
): CaseBasis {
  const sections = Object.fromEntries(
    precedent.full_text_sections.map((section) => [section.key, section.content]),
  );
  return {
    title: precedent.case_name ?? "판례",
    caseNumber: precedent.case_number ?? `판례 ${precedent.precedent_id}`,
    precedentId: precedent.precedent_id,
    court: precedent.court_name ?? undefined,
    decisionDate: precedent.decision_date ?? undefined,
    summary: precedent.precedent_summary ?? precedent.summary,
    easySummary: precedent.easy_summary ?? undefined,
    matchedReason: precedent.matched_reason ?? undefined,
    evidenceChunks: precedent.matched_chunks.map((chunk) => ({
      section: chunk.section ?? "검색 적중 부분",
      content: chunk.text,
      chunkType: chunk.chunk_type ?? undefined,
      sectionChunkIndex: chunk.section_chunk_index ?? undefined,
    })),
    fullText: {
      order: sections.order,
      claim: sections.claim,
      lowerCourt: sections.lower_court_decision,
      reason: sections.reason,
    },
    url: precedent.source_url,
  };
}
