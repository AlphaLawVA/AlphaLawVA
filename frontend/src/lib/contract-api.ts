import type {
  ConfirmedContractSnapshot as DemoContractSnapshot,
  ConfirmedRegistrySnapshot as DemoRegistrySnapshot,
  SnapshotField,
} from "@/lib/demo-types";

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

type ApiErrorPayload = {
  error?: {
    message?: string;
    field_errors?: Array<{ message?: string }>;
  };
};

export class ContractApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ContractApiError";
  }
}

export type ContractReviewItem = {
  item_id: string;
  category: string;
  label: string;
  original_text: string | null;
  status: "pending_evidence" | "review_ready" | "needs_input";
  message: string;
};

export type ContractEvidence = {
  item_id: string;
  source_type: string;
  source_id: string;
  title: string;
  text: string;
  rank: number;
  similarity: number;
  metadata: Record<string, string | number | null>;
};

export type ContractReviewAnalysis = {
  item_id: string;
  summary: string;
  review_status: "safe" | "check";
  revision: string;
  negotiation: string;
  evidence_source_ids: string[];
};

export type ContractReviewResult = {
  review_id: string;
  status: string;
  contract_items: ContractReviewItem[];
  registry_items: ContractReviewItem[];
  precheck_items: ContractReviewItem[];
  evidence: ContractEvidence[];
  evidence_status: "pending_retrieval" | "retrieved" | "unavailable";
  analysis: ContractReviewAnalysis[];
  analysis_status: "pending" | "analyzed" | "unavailable";
  message: string;
  disclaimer: string;
};

export type SpecialClauseDraftItem = {
  request_index: number;
  request_text: string;
  status: "draft_ready" | "needs_input" | "pending_generation";
  draft_text: string | null;
  title: string | null;
  reason: string | null;
  strategy: string | null;
  needs_input: string[];
  evidence_source_ids: string[];
};

export type SpecialClauseDraftResult = {
  draft_id: string;
  status: string;
  items: SpecialClauseDraftItem[];
  evidence: ContractEvidence[];
  evidence_status: "pending_retrieval" | "retrieved" | "unavailable";
  generation_status: "pending" | "generated" | "unavailable";
  message: string;
  disclaimer: string;
};

async function requestApi<T>(path: string, init?: RequestInit): Promise<T> {
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
    throw new ContractApiError(
      "계약서 검토 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인해주세요.",
      0,
    );
  }

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as ApiErrorPayload | null;
    const fieldMessage = payload?.error?.field_errors?.[0]?.message;
    throw new ContractApiError(
      fieldMessage ?? payload?.error?.message ?? "요청을 처리하지 못했습니다.",
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

function fieldMap(fields: SnapshotField[]) {
  return new Map(fields.map((field) => [field.id, field]));
}

function valueOf(fields: Map<string, SnapshotField>, id: string, label: string) {
  const value = fields.get(id)?.value.trim();
  if (!value) throw new Error(`${label}을(를) 원문과 비교해 확인해주세요.`);
  return value;
}

function optionalValue(fields: Map<string, SnapshotField>, id: string) {
  return fields.get(id)?.value.trim() || null;
}

function parseMoney(value: string, label: string) {
  const amountText = value.match(/[0-9][0-9,]*/)?.[0] ?? "";
  const digits = amountText.replace(/[^0-9]/g, "");
  if (!digits) throw new Error(`${label} 금액을 확인해주세요.`);
  const amount = Number(digits);
  if (!Number.isSafeInteger(amount)) throw new Error(`${label} 금액이 너무 큽니다.`);
  return amount;
}

// 2026.10.05, 2026-10-5, 2026/10/05, 2026년 10월 5일, 20261005를 모두 허용한다.
const DATE_PATTERN =
  /(\d{4})\s*(?:[.\/-]|년)\s*(\d{1,2})\s*(?:[.\/-]|월)\s*(\d{1,2})\s*일?|(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)/g;

function findDates(value: string) {
  return [...value.matchAll(DATE_PATTERN)].map((match) => ({
    text: match[0],
    year: match[1] ?? match[4],
    month: match[2] ?? match[5],
    day: match[3] ?? match[6],
  }));
}

function parseDate(value: string, label: string) {
  const found = findDates(value)[0];
  const year = Number(found?.year);
  const month = Number(found?.month);
  const day = Number(found?.day);
  const calendar = new Date(year, month - 1, day);
  const isRealDate =
    found !== undefined &&
    calendar.getFullYear() === year &&
    calendar.getMonth() === month - 1 &&
    calendar.getDate() === day;
  if (!isRealDate) {
    throw new Error(`${label} 날짜 형식을 확인해주세요. (예: 2026.10.05)`);
  }
  return `${found.year}-${found.month.padStart(2, "0")}-${found.day.padStart(2, "0")}`;
}

function parsePeriod(value: string) {
  const matches = findDates(value);
  if (matches.length < 2) throw new Error("임대차 시작일과 종료일을 확인해주세요.");
  return {
    start_date: parseDate(matches[0].text, "임대차 시작일"),
    end_date: parseDate(matches[1].text, "임대차 종료일"),
  };
}

function parseInstallment(value: string | null, label: string) {
  if (!value || value === "없음") return null;
  const dateMatch = findDates(value)[0];
  return {
    label,
    amount: { amount: parseMoney(value, label), korean_text: null },
    due_date: dateMatch ? parseDate(dateMatch.text, `${label} 지급일`) : null,
  };
}

function sourceOf(field: SnapshotField) {
  return { page_number: field.source.page, bounding_box: null };
}

function confirmationFor(fields: SnapshotField[]) {
  return fields.map((field) => ({
    field_path: `fields.${field.id}`,
    raw_text: field.value || null,
    source: sourceOf(field),
    confidence: null,
    resolution: field.status === "confirmed-empty" ? "confirmed_blank" : "user_confirmed",
  }));
}

function makeConfirmation() {
  return {
    confirmed_by_user: true,
    revision: 1,
    confirmed_at: new Date().toISOString(),
  };
}

function specialTerms(field: SnapshotField) {
  return field.value
    .split(/\n+/)
    .map((text) => text.trim())
    .filter(Boolean)
    .map((original_text, index) => ({
      term_id: `special-term-${index + 1}`,
      order: index + 1,
      original_text,
      source: sourceOf(field),
    }));
}

function contractPayload(snapshot: DemoContractSnapshot) {
  const fields = fieldMap(snapshot.fields);
  const contractType = snapshot.contractType;
  const lessorLabel = contractType === "매매" ? "매도인" : "임대인";
  const lesseeLabel = contractType === "매매" ? "매수인" : "임차인";
  const depositLabel = contractType === "매매" ? "매매대금" : "보증금";
  const propertyAddress = valueOf(fields, "property-address", "목적물 주소");
  const specialTermsField = fields.get("special-terms");
  if (!specialTermsField) throw new Error("특약 원문을 확인해주세요.");
  const common = {
    document_title: contractType === "매매" ? "부동산 매매계약서" : "주택임대차 표준계약서",
    page_count: Number(optionalValue(fields, "page-count") ?? 2),
    contract_kind: optionalValue(fields, "renewal-type"),
    contract_date: parseDate(valueOf(fields, "contract-date", "계약일"), "계약일"),
    property: {
      address: propertyAddress,
      detail_address: optionalValue(fields, "property-detail"),
      property_type: optionalValue(fields, "building-info")?.split("·")[1]?.trim() ?? null,
      parcel_number: null,
      building_name_unit: optionalValue(fields, "leased-part"),
      land_category: optionalValue(fields, "land-category")?.split("·")[0]?.trim() ?? null,
      land_area_m2: null,
      building_structure: optionalValue(fields, "building-info")?.split("·")[0]?.trim() ?? null,
      building_use: optionalValue(fields, "building-info")?.split("·")[1]?.trim() ?? null,
      building_area_m2: null,
      leased_portion: optionalValue(fields, "leased-part"),
      leased_area_m2: null,
      land_right_type: optionalValue(fields, "land-right"),
      land_right_ratio: null,
    },
    parties: [
      { role: contractType === "매매" ? "seller" : "lessor", name: valueOf(fields, "lessor", lessorLabel), address: null, detail_address: null, agent_name: null },
      { role: contractType === "매매" ? "buyer" : "lessee", name: valueOf(fields, "lessee", lesseeLabel), address: null, detail_address: null, agent_name: null },
    ],
    brokers: [],
    clauses: [],
    special_terms: specialTerms(specialTermsField),
    disclosures: {
      tax_arrears: optionalValue(fields, "tax-arrears"),
      senior_deposit_status: optionalValue(fields, "senior-deposit"),
      registry_checked: optionalValue(fields, "registry-checked")?.includes("첨부") ?? false,
      rights_change_restriction: optionalValue(fields, "right-restriction"),
      guarantee_insurance_term: optionalValue(fields, "insurance"),
    },
    signatures: [],
    attachment_names: [],
  };
  const price = { amount: parseMoney(valueOf(fields, "deposit", depositLabel), depositLabel), korean_text: optionalValue(fields, "deposit-korean") };
  const downPayment = parseInstallment(optionalValue(fields, "down-payment"), "계약금");
  const intermediate = parseInstallment(optionalValue(fields, "interim-payment"), "중도금");
  const balance = parseInstallment(optionalValue(fields, "balance"), "잔금");

  if (contractType === "매매") {
    return {
      ...common,
      contract_type: "sale",
      sale_price: price,
      down_payment: downPayment?.amount ?? null,
      intermediate_payments: intermediate ? [intermediate] : [],
      remaining_balance: balance?.amount ?? null,
      remaining_balance_date: balance?.due_date ?? null,
      handover_date: optionalValue(fields, "handover-date") ? parseDate(optionalValue(fields, "handover-date")!, "인도일") : null,
      ownership_transfer_date: optionalValue(fields, "ownership-transfer-date") ? parseDate(optionalValue(fields, "ownership-transfer-date")!, "소유권이전 예정일") : null,
      encumbrance_handling: optionalValue(fields, "right-restriction"),
      loan_assumption: null,
      existing_occupancies: [],
    };
  }

  const period = parsePeriod(valueOf(fields, "lease-period", "임대차 기간"));
  const lease = {
    ...common,
    contract_type: contractType === "월세" ? "monthly_rent" : "jeonse",
    lease_deposit: price,
    down_payment: downPayment?.amount ?? null,
    intermediate_payments: intermediate ? [intermediate] : [],
    remaining_balance: balance?.amount ?? null,
    remaining_balance_date: balance?.due_date ?? null,
    start_date: period.start_date,
    end_date: period.end_date,
    handover_date: optionalValue(fields, "move-in-date") ? parseDate(optionalValue(fields, "move-in-date")!, "인도일") : null,
    management_fee: null,
  };
  if (contractType === "월세") {
    return {
      ...lease,
      monthly_rent: {
        amount: { amount: parseMoney(valueOf(fields, "monthly-rent", "월 차임"), "월 차임"), korean_text: null },
        payment_day: Number((optionalValue(fields, "rent-payment-date") ?? "").match(/\d+/)?.[0] ?? 1),
        timing: optionalValue(fields, "rent-payment-method")?.includes("후불") ? "postpaid" : "prepaid",
        account_masked: null,
      },
    };
  }
  return lease;
}

function registryPayload(snapshot: DemoRegistrySnapshot) {
  const fields = fieldMap(snapshot.fields);
  const issued = valueOf(fields, "registry-issued-at", "등본 발급일시");
  const issuedDate = parseDate(issued, "등본 발급일시");
  const issuedTime = issued.match(/(\d{1,2}):(\d{2})/)?.slice(1).join(":") ?? "00:00";
  const ownerField = fields.get("owner-name");
  if (!ownerField) throw new Error("현재 소유자 정보를 확인해주세요.");
  return {
    schema_version: "confirmed_registry_snapshot.v0.1",
    snapshot_id: crypto.randomUUID(),
    document_id: crypto.randomUUID(),
    confirmation: makeConfirmation(),
    issued_at: `${issuedDate}T${issuedTime}:00`,
    unique_property_number_masked: null,
    address: valueOf(fields, "registry-address", "등본 소재지"),
    detail_address: optionalValue(fields, "registry-detail"),
    property_description: [{ order: 1, original_text: valueOf(fields, "registry-building-original", "표제부 건물 표시"), source: sourceOf(fields.get("registry-building-original")!) }],
    owners: [{ name: ownerField.value, ownership_share: optionalValue(fields, "owner-share"), registration_date: null, registration_cause: optionalValue(fields, "owner-cause"), status: "active" }],
    encumbrances: snapshot.rights.map((right) => ({
      section: right.section === "갑구" ? "gapgu" : "eulgu",
      rank_number: right.rank,
      registration_purpose: right.purpose,
      registration_date: parseDate(right.receivedDate, "등기 접수일"),
      holder: right.holder,
      debtor: right.debtor ?? null,
      maximum_claim_amount: right.maximumAmount ? parseMoney(right.maximumAmount, "채권최고액") : null,
      raw_text: [right.rank, right.purpose, right.holder, right.maximumAmount].filter(Boolean).join(" · "),
      source: { page_number: right.source.page, bounding_box: null },
      status: right.status === "unclear" ? "unknown" : right.status,
    })),
    field_confirmations: confirmationFor(snapshot.fields),
    warnings: [],
  };
}

export async function createContractReview(
  contractSnapshot: DemoContractSnapshot,
  registrySnapshot: DemoRegistrySnapshot | null,
): Promise<ContractReviewResult> {
  const contract = {
    schema_version: "confirmed_contract_snapshot.v0.1",
    snapshot_id: crypto.randomUUID(),
    document_id: crypto.randomUUID(),
    confirmation: makeConfirmation(),
    contract: contractPayload(contractSnapshot),
    field_confirmations: confirmationFor(contractSnapshot.fields),
    warnings: [],
  };
  const savedContract = await requestApi<{ snapshot_id: string }>("/api/v1/contract-snapshots", {
    method: "POST",
    body: JSON.stringify(contract),
  });
  const savedRegistry = registrySnapshot
    ? await requestApi<{ snapshot_id: string }>("/api/v1/registry-snapshots", {
      method: "POST",
      body: JSON.stringify(registryPayload(registrySnapshot)),
    })
    : null;
  return requestApi<ContractReviewResult>("/api/v1/contract-reviews", {
    method: "POST",
    body: JSON.stringify({
      contract_snapshot_id: savedContract.snapshot_id,
      registry_snapshot_id: savedRegistry?.snapshot_id ?? null,
    }),
  });
}

export function createSpecialClauseDraft(requests: string[]) {
  return requestApi<SpecialClauseDraftResult>("/api/v1/special-clause-drafts", {
    method: "POST",
    body: JSON.stringify({ requests }),
  });
}
