import type { ConfirmedContractSnapshot } from "@/lib/demo-types";

const KEY = "alphalawva.contract-ocr-draft.v1";

export function saveContractOcrDraft(snapshot: ConfirmedContractSnapshot) {
  window.sessionStorage.setItem(KEY, JSON.stringify(snapshot));
}

export function loadContractOcrDraft(): ConfirmedContractSnapshot | null {
  if (typeof window === "undefined") return null;
  const value = window.sessionStorage.getItem(KEY);
  if (!value) return null;
  try {
    return JSON.parse(value) as ConfirmedContractSnapshot;
  } catch {
    window.sessionStorage.removeItem(KEY);
    return null;
  }
}

export function clearContractOcrDraft() {
  if (typeof window !== "undefined") {
    window.sessionStorage.removeItem(KEY);
  }
}
