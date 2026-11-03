import type {
  ConfirmedRegistrySnapshot,
  RegistryRight,
  SnapshotField,
} from "@/lib/demo-types";
import { ContractOcrApiError } from "@/lib/contract-ocr-api";

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

type OcrSource = { page_number: number };

type OcrField<T> = {
  value: T | null;
  raw_text?: string | null;
  status: string;
  source?: OcrSource | null;
};

type RegistryOcrOwner = {
  name: OcrField<string>;
  ownership_share?: OcrField<string> | null;
  registration_date?: OcrField<string> | null;
  registration_cause?: OcrField<string> | null;
};

type RegistryOcrEncumbrance = {
  section: "gapgu" | "eulgu";
  rank_number: OcrField<string>;
  registration_purpose: OcrField<string>;
  registration_date?: OcrField<string> | null;
  holder?: OcrField<string> | null;
  debtor?: OcrField<string> | null;
  maximum_claim_amount?: OcrField<number> | null;
  source: OcrSource;
};

export type RegistryOcrDraft = {
  status: "needs_review";
  provider: "Google Document AI";
  page_count: number;
  extraction: {
    issued_at: OcrField<string>;
    address: OcrField<string>;
    detail_address: OcrField<string>;
    property_description: Array<{ original_text: string; source: OcrSource }>;
    owners: RegistryOcrOwner[];
    encumbrances: RegistryOcrEncumbrance[];
    warnings: string[];
  };
  warnings: string[];
  processing_seconds: number;
  retention_notice: string;
};

export async function createRegistryOcrDraft(file: File): Promise<RegistryOcrDraft> {
  const form = new FormData();
  form.append("file", file);
  form.append("provider_consent", "true");
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/registry-ocr-drafts`, {
      method: "POST",
      body: form,
    });
  } catch {
    throw new ContractOcrApiError("OCR 서버에 연결할 수 없습니다. 서버 실행 상태를 확인해주세요.", 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: { message?: string } } | null;
    throw new ContractOcrApiError(payload?.error?.message ?? "등기사항증명서 OCR 초안을 만들지 못했습니다.", response.status);
  }
  return response.json() as Promise<RegistryOcrDraft>;
}

function dateText(value: string | null | undefined) {
  return value ? value.slice(0, 10).replaceAll("-", ".") : "";
}

function issuedAtText(value: string | null | undefined) {
  return value ? `${dateText(value)} ${value.slice(11, 16)}` : "";
}

function text(field: OcrField<string> | null | undefined) {
  return field?.value ?? "";
}

type FieldSeed = Omit<SnapshotField, "status" | "priority"> & {
  review?: boolean;
};

function seedField({ review = true, ...field }: FieldSeed): SnapshotField {
  const needsReview = review || !field.value.trim();
  return {
    ...field,
    priority: needsReview ? "needs-confirmation" : "automatic",
    status: needsReview ? "needs-confirmation" : "automatic",
  };
}

function rightFor(item: RegistryOcrEncumbrance): RegistryRight {
  const section = item.section === "gapgu" ? "갑구" : "을구";
  const rank = text(item.rank_number);
  const amount = item.maximum_claim_amount?.value;
  return {
    id: `right-${item.section}-${rank}`,
    section,
    rank,
    purpose: text(item.registration_purpose),
    receivedDate: dateText(item.registration_date?.value),
    holder: text(item.holder),
    debtor: item.debtor?.value ?? undefined,
    maximumAmount: amount === null || amount === undefined
      ? undefined
      : `${amount.toLocaleString("ko-KR")}원`,
    // OCR은 말소선을 구분하지 못하므로 상태를 확정하지 않고 사용자 확인으로 넘긴다.
    status: "unclear",
    source: { page: item.source.page_number, label: `${section} ${rank}번` },
  };
}

export function mapRegistryOcrDraftToSnapshot(draft: RegistryOcrDraft): ConfirmedRegistrySnapshot {
  const extraction = draft.extraction;
  const owner = extraction.owners[0];
  const ownerNames = extraction.owners.map((item) => text(item.name)).filter(Boolean).join(", ");
  const ownerCause = owner
    ? [
        text(owner.registration_cause),
        owner.registration_date?.value ? `${dateText(owner.registration_date.value)} 접수` : "",
      ].filter(Boolean).join(" · ")
    : "";
  const ownerSource = { page: owner?.name.source?.page_number ?? 1, label: "갑구 소유권" };
  const titleText = extraction.property_description.map((item) => item.original_text).join("\n");

  const fields = [
    seedField({ id: "registry-issued-at", label: "발급일시", value: issuedAtText(extraction.issued_at.value), group: "발급·부동산 정보", source: { page: extraction.issued_at.source?.page_number ?? 1, label: "발급 정보" }, review: false }),
    seedField({ id: "registry-address", label: "소재지", value: text(extraction.address), group: "발급·부동산 정보", required: true, source: { page: extraction.address.source?.page_number ?? 1, label: "표제부 소재지" } }),
    seedField({ id: "registry-detail", label: "상세주소", value: text(extraction.detail_address), group: "발급·부동산 정보", required: true, source: { page: extraction.detail_address.source?.page_number ?? 1, label: "건물내역" } }),
    seedField({ id: "registry-building-original", label: "표제부 건물 표시 원문", value: titleText, group: "발급·부동산 정보", multiline: true, source: { page: extraction.property_description[0]?.source.page_number ?? 1, label: "표제부" } }),
    seedField({ id: "owner-name", label: "현재 소유자 성명", value: ownerNames, group: "현재 소유자", required: true, source: ownerSource }),
    seedField({ id: "owner-share", label: "소유권 지분", value: owner?.ownership_share?.value ?? "", group: "현재 소유자", source: ownerSource }),
    seedField({ id: "owner-cause", label: "등기원인·접수일", value: ownerCause, group: "현재 소유자", source: ownerSource }),
    // 말소 여부는 실선으로만 표시되어 OCR로 알 수 없으므로 사용자가 원문에서 확인한다.
    seedField({ id: "owner-status", label: "소유권 상태", value: owner ? "유효" : "", group: "현재 소유자", source: ownerSource }),
  ].filter((field) => field.required || field.value.trim());

  return { fields, rights: extraction.encumbrances.map(rightFor) };
}
