import type { ConfirmedContractSnapshot, ConfirmedRegistrySnapshot } from "@/lib/demo-types";

const KEY = "alphalawva.contract-ocr-draft.v1";
const REGISTRY_KEY = "alphalawva.registry-ocr-draft.v1";

let draftVersion = 0;
const draftListeners = new Set<() => void>();

// 계약서와 등본 OCR 결과를 한 번에 저장해 확인 화면이 한 번만 새로 만들어지게 한다.
export function saveContractOcrDraft(
  snapshot: ConfirmedContractSnapshot,
  registrySnapshot?: ConfirmedRegistrySnapshot | null,
) {
  window.sessionStorage.setItem(KEY, JSON.stringify(snapshot));
  if (registrySnapshot) {
    window.sessionStorage.setItem(REGISTRY_KEY, JSON.stringify(registrySnapshot));
  } else {
    window.sessionStorage.removeItem(REGISTRY_KEY);
  }
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

function loadDraft<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  const value = window.sessionStorage.getItem(key);
  if (!value) return null;
  try {
    return JSON.parse(value) as T;
  } catch {
    window.sessionStorage.removeItem(key);
    return null;
  }
}

export function loadContractOcrDraft(): ConfirmedContractSnapshot | null {
  return loadDraft<ConfirmedContractSnapshot>(KEY);
}

export function loadRegistryOcrDraft(): ConfirmedRegistrySnapshot | null {
  return loadDraft<ConfirmedRegistrySnapshot>(REGISTRY_KEY);
}

export function clearContractOcrDraft() {
  if (typeof window !== "undefined") {
    window.sessionStorage.removeItem(KEY);
    window.sessionStorage.removeItem(REGISTRY_KEY);
  }
}
