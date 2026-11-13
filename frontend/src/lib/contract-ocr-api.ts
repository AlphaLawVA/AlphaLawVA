import { contractSnapshots } from "@/lib/mock-data";
import type { ConfirmedContractSnapshot, SnapshotField } from "@/lib/demo-types";

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

type OcrStatus =
  | "extracted"
  | "missing"
  | "ambiguous"
  | "low_confidence"
  | "user_confirmed"
  | "user_corrected"
  | "not_applicable";

type OcrSource = { page_number: number; bounding_box?: Record<string, number> };

type OcrField<T> = {
  value: T | null;
  raw_text?: string | null;
  status: OcrStatus;
  source?: OcrSource | null;
};

type OcrTextBlock = {
  item_order: number;
  original_text: string;
  source: OcrSource;
};

type ContractOcrReviewItem = {
  path: string;
  status: string;
  reason: string;
  source?: OcrSource | null;
};

type ContractOcrExtraction = {
  contract_type: OcrField<"jeonse" | "monthly_rent" | "sale">;
  contract_date: OcrField<string>;
  start_date?: OcrField<string>;
  end_date?: OcrField<string>;
  delivery_date?: OcrField<string>;
  ownership_transfer_date?: OcrField<string>;
  property: {
    address: OcrField<string>;
    detail_address: OcrField<string>;
    leased_part_description?: OcrField<string>;
    land_category?: OcrField<string>;
    land_area_m2?: OcrField<number>;
    building_structure?: OcrField<string>;
    building_use?: OcrField<string>;
    building_area_m2?: OcrField<number>;
    leased_area_m2?: OcrField<number>;
  };
  parties: Array<{ role: string; name: OcrField<string> }>;
  payment: {
    sale_price?: OcrField<number>;
    deposit?: OcrField<number>;
    monthly_rent?: OcrField<number>;
    maintenance_fee?: OcrField<number>;
    down_payment?: OcrField<number>;
    intermediate_payments: Array<{ amount: OcrField<number>; due_date?: OcrField<string> }>;
    remaining_balance?: OcrField<number>;
    remaining_balance_date?: OcrField<string>;
    monthly_rent_payment_day?: OcrField<string>;
    payment_timing?: OcrField<"prepaid" | "postpaid" | "unspecified">;
  };
  special_terms: OcrTextBlock[];
  warnings: string[];
};

export type ContractOcrDraft = {
  status: "needs_review";
  provider: "Google Document AI";
  extraction: ContractOcrExtraction;
  review_items: ContractOcrReviewItem[];
  warnings: string[];
  processing_seconds: number;
  retention_notice: string;
};

export class ContractOcrApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ContractOcrApiError";
  }
}

export async function createContractOcrDraft(
  file: File,
  contractType: "jeonse" | "monthly_rent" | "sale",
): Promise<ContractOcrDraft> {
  const form = new FormData();
  form.append("file", file);
  form.append("contract_type", contractType);
  form.append("provider_consent", "true");
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/contract-ocr-drafts`, {
      method: "POST",
      body: form,
    });
  } catch {
    throw new ContractOcrApiError("OCR 서버에 연결할 수 없습니다. 서버 실행 상태를 확인해주세요.", 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: { message?: string } } | null;
    throw new ContractOcrApiError(payload?.error?.message ?? "OCR 초안을 만들지 못했습니다.", response.status);
  }
  return response.json() as Promise<ContractOcrDraft>;
}

const KOREAN_CONTRACT_TYPES: Record<ContractOcrExtraction["contract_type"]["value"] & string, ConfirmedContractSnapshot["contractType"]> = {
  jeonse: "전세",
  monthly_rent: "월세",
  sale: "매매",
};

function dateValue(value: string | null | undefined) {
  return value ? value.replaceAll("-", ".") : "";
}

function moneyValue(value: number | null | undefined) {
  return value === null || value === undefined ? "" : value.toLocaleString("ko-KR");
}

function sourceFor(field: SnapshotField, source: OcrSource | null | undefined, label: string) {
  return { page: source?.page_number ?? field.source.page, label };
}

function updateField(
  fields: SnapshotField[],
  id: string,
  value: string,
  source: OcrSource | null | undefined,
  label: string,
  forceReview = false,
) {
  return fields.map((field) => field.id === id
    ? {
        ...field,
        value,
        priority: value.trim() && !forceReview
          ? field.priority
          : "needs-confirmation" as const,
        status: value.trim() && !forceReview && field.priority === "automatic"
          ? "automatic" as const
          : "needs-confirmation" as const,
        source: sourceFor(field, source, label),
      }
    : { ...field, source: { ...field.source } });
}

const REVIEW_REQUIRED_STATUSES = new Set<OcrStatus>([
  "missing",
  "ambiguous",
  "low_confidence",
]);

function requiresReview(
  draft: ContractOcrDraft,
  paths: string[],
  fields: Array<OcrField<unknown> | undefined>,
) {
  return fields.some((field) => field && REVIEW_REQUIRED_STATUSES.has(field.status))
    || draft.review_items.some((item) => (
      paths.includes(item.path)
      && REVIEW_REQUIRED_STATUSES.has(item.status as OcrStatus)
    ));
}

function fieldValue(field: OcrField<string | number> | undefined) {
  return field?.value === null || field?.value === undefined ? "" : String(field.value);
}

function paymentWithDate(amount: OcrField<number> | undefined, dueDate?: OcrField<string>) {
  const amountText = moneyValue(amount?.value);
  const dateText = dateValue(dueDate?.value);
  return [amountText, dateText].filter(Boolean).join(" · ");
}

export function mapContractOcrDraftToSnapshot(draft: ContractOcrDraft): ConfirmedContractSnapshot {
  const extraction = draft.extraction;
  const contractType = KOREAN_CONTRACT_TYPES[extraction.contract_type.value ?? "jeonse"];
  const liveFieldIds = new Set([
    "contract-type",
    "contract-date",
    "page-count",
    "property-address",
    "property-detail",
    "land-category",
    "building-info",
    "leased-part",
    "lessor",
    "lessee",
    "deposit",
    "down-payment",
    "interim-payment",
    "interim-payment-2",
    "balance",
    "maintenance-fee",
    "special-terms",
    ...(contractType === "매매"
      ? ["handover-date", "ownership-transfer-date"]
      : ["lease-period", "move-in-date"]),
    ...(contractType === "월세"
      ? ["monthly-rent", "rent-payment-date", "rent-payment-method"]
      : []),
  ]);
  let fields: SnapshotField[] = contractSnapshots[contractType].fields
    .filter((field) => liveFieldIds.has(field.id))
    .map((field) => ({
      ...field,
      value: field.id === "contract-type" ? contractType : "",
      status: field.id === "contract-type" ? "user-confirmed" as const : "needs-confirmation" as const,
      source: { ...field.source },
    }));
  const price = extraction.payment.sale_price ?? extraction.payment.deposit;
  const seller = extraction.parties.find((party) => party.role === "seller" || party.role === "lessor");
  const buyer = extraction.parties.find((party) => party.role === "buyer" || party.role === "lessee");
  const terms = extraction.special_terms;

  type FieldUpdate = [string, string, OcrSource | null | undefined, string, boolean?];
  const review = (paths: string[], fields: Array<OcrField<unknown> | undefined>) => (
    requiresReview(draft, paths, fields)
  );
  const updates: FieldUpdate[] = [
    ["contract-date", dateValue(extraction.contract_date.value), extraction.contract_date.source, "계약일", review(["contract_date"], [extraction.contract_date])],
    ["page-count", "3", null, "문서 정보"],
    ["property-address", fieldValue(extraction.property.address), extraction.property.address.source, "소재지", review(["property.address"], [extraction.property.address])],
    ["property-detail", fieldValue(extraction.property.detail_address), extraction.property.detail_address.source, "상세 주소", review(["property.detail_address"], [extraction.property.detail_address])],
    ["land-category", [fieldValue(extraction.property.land_category), extraction.property.land_area_m2?.value ? `${extraction.property.land_area_m2.value}㎡` : ""].filter(Boolean).join(" · "), extraction.property.land_category?.source ?? extraction.property.land_area_m2?.source, "토지", review(["property.land_category", "property.land_area_m2"], [extraction.property.land_category, extraction.property.land_area_m2])],
    ["building-info", [fieldValue(extraction.property.building_structure), fieldValue(extraction.property.building_use), extraction.property.building_area_m2?.value ? `${extraction.property.building_area_m2.value}㎡` : ""].filter(Boolean).join(" · "), extraction.property.building_structure?.source ?? extraction.property.building_use?.source, "건물", review(["property.building_structure", "property.building_use", "property.building_area_m2"], [extraction.property.building_structure, extraction.property.building_use, extraction.property.building_area_m2])],
    ["leased-part", [fieldValue(extraction.property.leased_part_description), extraction.property.leased_area_m2?.value ? `${extraction.property.leased_area_m2.value}㎡` : ""].filter(Boolean).join(" · "), extraction.property.leased_part_description?.source ?? extraction.property.leased_area_m2?.source, "임차할 부분", review(["property.leased_part_description", "property.leased_area_m2"], [extraction.property.leased_part_description, extraction.property.leased_area_m2])],
    ["lessor", fieldValue(seller?.name), seller?.name.source, contractType === "매매" ? "매도인" : "임대인", review(seller ? [`parties.${extraction.parties.indexOf(seller)}.name`] : [], [seller?.name])],
    ["lessee", fieldValue(buyer?.name), buyer?.name.source, contractType === "매매" ? "매수인" : "임차인", review(buyer ? [`parties.${extraction.parties.indexOf(buyer)}.name`] : [], [buyer?.name])],
    ["deposit", moneyValue(price?.value), price?.source, contractType === "매매" ? "매매대금" : "보증금", review([extraction.payment.sale_price ? "payment.sale_price" : "payment.deposit"], [price])],
    ["down-payment", paymentWithDate(extraction.payment.down_payment), extraction.payment.down_payment?.source, "계약금", review(["payment.down_payment"], [extraction.payment.down_payment])],
    // 중도금이 1차·2차로 나뉜 계약은 두 번째 칸(interim-payment-2)에 2차중도금을 채운다.
    // 하나만 있으면 기존대로 "중도금" 한 칸만 쓰고 두 번째 칸은 "없음"으로 둔다.
    ["interim-payment", extraction.payment.intermediate_payments.length > 0 ? paymentWithDate(extraction.payment.intermediate_payments[0].amount, extraction.payment.intermediate_payments[0].due_date) : "없음", extraction.payment.intermediate_payments[0]?.amount.source, "중도금", review(["payment.intermediate_payments.0.amount", "payment.intermediate_payments.0.due_date"], [extraction.payment.intermediate_payments[0]?.amount, extraction.payment.intermediate_payments[0]?.due_date])],
    ["interim-payment-2", extraction.payment.intermediate_payments.length > 1 ? paymentWithDate(extraction.payment.intermediate_payments[1].amount, extraction.payment.intermediate_payments[1].due_date) : "없음", extraction.payment.intermediate_payments[1]?.amount.source, "2차중도금", review(["payment.intermediate_payments.1.amount", "payment.intermediate_payments.1.due_date"], [extraction.payment.intermediate_payments[1]?.amount, extraction.payment.intermediate_payments[1]?.due_date])],
    ["balance", paymentWithDate(extraction.payment.remaining_balance, extraction.payment.remaining_balance_date), extraction.payment.remaining_balance?.source ?? extraction.payment.remaining_balance_date?.source, "잔금", review(["payment.remaining_balance", "payment.remaining_balance_date"], [extraction.payment.remaining_balance, extraction.payment.remaining_balance_date])],
    ["maintenance-fee", moneyValue(extraction.payment.maintenance_fee?.value), extraction.payment.maintenance_fee?.source, "관리비", review(["payment.maintenance_fee"], [extraction.payment.maintenance_fee])],
    ["special-terms", terms.map((term) => `${term.item_order}. ${term.original_text}`).join("\n"), terms[0]?.source, "특약사항"],
  ];
  if (contractType === "매매") {
    updates.push(
      ["handover-date", dateValue(extraction.delivery_date?.value), extraction.delivery_date?.source, "인도일", review(["delivery_date"], [extraction.delivery_date])],
      ["ownership-transfer-date", dateValue(extraction.ownership_transfer_date?.value), extraction.ownership_transfer_date?.source, "소유권이전", review(["ownership_transfer_date"], [extraction.ownership_transfer_date])],
    );
  } else {
    updates.push(
      ["lease-period", [dateValue(extraction.start_date?.value), dateValue(extraction.end_date?.value)].filter(Boolean).join(" - "), extraction.start_date?.source ?? extraction.end_date?.source, "임대차 기간", review(["start_date", "end_date"], [extraction.start_date, extraction.end_date])],
      ["move-in-date", dateValue(extraction.delivery_date?.value), extraction.delivery_date?.source, "인도일", review(["delivery_date"], [extraction.delivery_date])],
    );
  }
  if (contractType === "월세") {
    updates.push(
      ["monthly-rent", moneyValue(extraction.payment.monthly_rent?.value), extraction.payment.monthly_rent?.source, "차임", review(["payment.monthly_rent"], [extraction.payment.monthly_rent])],
      ["rent-payment-date", extraction.payment.monthly_rent_payment_day?.value ? `매월 ${extraction.payment.monthly_rent_payment_day.value}일` : "", extraction.payment.monthly_rent_payment_day?.source, "차임 지급일", review(["payment.monthly_rent_payment_day"], [extraction.payment.monthly_rent_payment_day])],
      ["rent-payment-method", extraction.payment.payment_timing?.value === "postpaid" ? "후불" : extraction.payment.payment_timing?.value === "prepaid" ? "선불" : "", extraction.payment.payment_timing?.source, "차임 지급 방식", review(["payment.payment_timing"], [extraction.payment.payment_timing])],
    );
  }
  for (const [id, value, source, label, forceReview] of updates) {
    fields = updateField(fields, id, value, source, label, forceReview);
  }
  fields = fields.filter((field) => field.required || field.value.trim());
  return { contractType, fields };
}
