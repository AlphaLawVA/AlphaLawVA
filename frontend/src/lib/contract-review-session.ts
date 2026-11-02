import type { ContractReviewResult } from "@/lib/contract-api";

const REVIEW_RESULT_KEY = "alphalawva.contract-review.result.v1";
let cachedResult: ContractReviewResult | null | undefined;

export function saveContractReviewResult(result: ContractReviewResult) {
  cachedResult = result;
  window.sessionStorage.setItem(REVIEW_RESULT_KEY, JSON.stringify(result));
}

export function loadContractReviewResult(): ContractReviewResult | null {
  if (cachedResult !== undefined) return cachedResult;
  if (typeof window === "undefined") return null;
  const value = window.sessionStorage.getItem(REVIEW_RESULT_KEY);
  if (!value) {
    cachedResult = null;
    return cachedResult;
  }
  try {
    cachedResult = JSON.parse(value) as ContractReviewResult;
    return cachedResult;
  } catch {
    window.sessionStorage.removeItem(REVIEW_RESULT_KEY);
    cachedResult = null;
    return cachedResult;
  }
}
