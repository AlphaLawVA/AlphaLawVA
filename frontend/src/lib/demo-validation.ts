// 부동산 계약과의 관련성은 키워드로 판단하지 않는다. 서버의 LLM 범위 분류기가 판단하고,
// 관련 없는 요청은 서버가 out_of_scope로 돌려준다. 여기서는 빈 입력과 길이만 확인한다.
export function validateRealEstateInput(value: string, minimumLength: number) {
  const normalized = value.replace(/\s+/g, " ").trim();

  if (!normalized) return "내용을 입력해주세요.";
  if (normalized.length < minimumLength) return "상황과 원하는 내용을 조금 더 구체적으로 적어주세요.";

  return "";
}

export function hasContradictoryRequests(values: string[]) {
  const combined = values.join(" ");
  return (
    (/흡연.*허용|담배.*허용/.test(combined) && /흡연.*금지|담배.*금지/.test(combined)) ||
    (/반려.*허용|반려.*키우/.test(combined) && /반려.*금지|반려.*불가/.test(combined))
  );
}

export function validateDemoFile(file: File) {
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (!extension || !["pdf", "jpg", "jpeg", "png"].includes(extension)) {
    return "PDF, JPG, PNG 파일만 올릴 수 있습니다.";
  }
  if (file.size > 20 * 1024 * 1024) return "파일이 20MB를 넘습니다. 크기를 줄인 뒤 다시 올려주세요.";
  if (file.size === 0) return "내용이 없는 파일입니다. 다른 파일을 선택해주세요.";
  return "";
}
