import type { ConfirmedContractSnapshot } from "@/lib/demo-types";

const KEY = "alphalawva.contract-ocr-draft.v1";

let draftVersion = 0;
const draftListeners = new Set<() => void>();

export function saveContractOcrDraft(snapshot: ConfirmedContractSnapshot) {
  window.sessionStorage.setItem(KEY, JSON.stringify(snapshot));
  draftVersion += 1;
  draftListeners.forEach((listener) => listener());
}

// 새 OCR 결과가 저장될 때마다 바뀌는 값. 보존된 확인 화면이 이전 결과를 들고 있지 않도록 key로 쓴다.
export function getContractOcrDraftVersion() {
  return draftVersion;
}

export function subscribeContractOcrDraft(listener: () => void) {
  draftListeners.add(listener);
  return () => {
    draftListeners.delete(listener);
  };
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
