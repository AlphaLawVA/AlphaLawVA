const offTopicPatterns = [
  /날씨|메뉴|레시피|요리|숙제|번역|코딩|주식|게임|여행|노래|운세/,
];

const realEstatePatterns = [
  /계약|임대|임차|전세|월세|보증금|집주인|세입자|부동산|주택|아파트|원룸|매매|등기|근저당|퇴거|수리|반려|흡연|담배|벽|못|타공|보일러|원상회복/,
];

export function validateRealEstateInput(value: string, minimumLength: number) {
  const normalized = value.replace(/\s+/g, " ").trim();

  if (!normalized) return "내용을 입력해주세요.";
  if (normalized.length < minimumLength) return "상황과 원하는 내용을 조금 더 구체적으로 적어주세요.";
  if (offTopicPatterns.some((pattern) => pattern.test(normalized))) {
    return "부동산 계약이나 주거 분쟁과 관련된 내용만 도와드릴 수 있습니다. 계약 종류, 현재 상황과 원하는 조치를 적어주세요.";
  }
  if (!realEstatePatterns.some((pattern) => pattern.test(normalized))) {
    return "부동산 계약과 관련된 요청인지 확인하기 어렵습니다. 계약 대상과 당사자, 원하는 조건을 함께 적어주세요.";
  }

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
