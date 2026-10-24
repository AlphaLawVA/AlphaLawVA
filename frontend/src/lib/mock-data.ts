import type { ClauseReview, RegistryReview } from "./demo-types";

export const leaseSuccessionLaw = {
  law: "주택임대차보호법",
  article: "제3조 제4항",
  excerpt: "임차주택의 양수인은 임대인의 지위를 승계한 것으로 봅니다.",
  explanation: "계약 중 소유자가 바뀌는 경우 임대인의 지위와 보증금 반환 책임이 누구에게 이어지는지 판단할 때 확인하는 조문입니다.",
  url: "https://www.law.go.kr/법령/주택임대차보호법/제3조",
};

export const restorationLaw = {
  law: "민법",
  article: "제654조·제615조",
  excerpt: "임대차가 끝나 목적물을 반환할 때에는 원상회복의무에 관한 규정이 적용됩니다.",
  explanation: "원상회복 범위를 정할 때 계약 당시 상태와 임차인이 실제로 변경하거나 훼손한 부분을 구분하기 위한 근거입니다.",
  url: "https://www.law.go.kr/법령/민법/제654조",
};

export const restorationCase = {
  title: "토지인도",
  caseNumber: "대법원 2023. 11. 2. 선고 2023다249661 판결",
  precedentId: "국가법령정보센터 판례일련번호 237925",
  summary: "임차인은 원칙적으로 임차목적물을 반환할 때 원상회복의무를 부담하지만, 임대 당시 이미 존재하던 시설까지 당연히 복구해야 하는 것은 아니며 계약 경위와 당시 상태 등을 구체적으로 살펴야 한다고 본 판례입니다.",
  url: "https://www.law.go.kr/LSW/precInfoP.do?precSeq=237925",
};

export const clauseReviews: ClauseReview[] = [
  {
    id: "clause-1",
    title: "특약 1 · 권리관계 유지",
    original: "임대인은 잔금 지급일 다음 날까지 근저당권 등 새로운 권리를 설정하지 않는다.",
    status: "safe",
    reason: "잔금 지급 다음 날까지 권리변동을 제한하는 시점이 명시되어 있고, 임차인의 대항력 취득 시점 전후의 공백을 줄이는 방향으로 작성되어 있습니다.",
    revision: "현재 문구에서 별도로 수정할 부분을 찾지 못했습니다.",
    negotiation: "잔금 지급 직전에 최신 등기사항증명서를 다시 발급해 비교하고, 위반 시 계약 해제와 지급금 반환 방법도 함께 정할 수 있습니다.",
    statutes: [leaseSuccessionLaw],
    precedents: [],
  },
  {
    id: "clause-2",
    title: "특약 2 · 보증 가입 협조",
    original: "임대인은 임차인의 전세보증금반환보증 가입에 필요한 서류 제출에 협조한다.",
    status: "safe",
    reason: "보증 가입에 필요한 임대인의 협조 의무가 문장으로 명확하게 적혀 있습니다.",
    revision: "현재 문구에서 별도로 수정할 부분을 찾지 못했습니다.",
    negotiation: "보증기관의 심사 기한을 고려해 서류 제공 기한과 가입이 거절될 때의 처리 방법을 함께 협의하세요.",
    statutes: [leaseSuccessionLaw],
    precedents: [],
  },
  {
    id: "clause-3",
    title: "특약 3 · 원상회복",
    original: "임차인은 통상적인 사용으로 발생한 손모를 포함하여 퇴거 시 모든 시설을 원상복구한다.",
    status: "check",
    reason: "통상적인 사용으로 생긴 노후나 마모까지 임차인이 부담하는 것으로 읽힐 수 있어 책임 범위를 확인해야 합니다.",
    revision:
      "통상적인 사용으로 발생한 노후·마모를 제외하고 임차인의 고의 또는 과실로 생긴 손상만 원상회복한다.",
    negotiation:
      "입주 전 사진을 남기고 기존 하자의 위치와 수리 책임을 별지에 기록하는 방안을 협의하세요.",
    statutes: [restorationLaw],
    precedents: [restorationCase],
  },
  {
    id: "clause-4",
    title: "특약 4 · 보증금 반환 시기",
    original: "임대차 종료 후 신규 임차인이 입주한 때에 보증금을 반환한다.",
    status: "check",
    reason: "보증금 반환을 신규 임차인의 입주와 연결하면 계약 종료 뒤에도 반환 시기가 불확실해질 수 있습니다.",
    revision: "임대인은 임대차 종료와 목적물 인도가 완료되는 즉시 임차인에게 보증금을 반환한다.",
    negotiation: "신규 임차인 모집 여부와 관계없이 반환일을 계약 종료일과 연결해 명확히 정하고, 지급 계좌도 확인하세요.",
    statutes: [leaseSuccessionLaw],
    precedents: [],
  },
];

export const registryReviews: RegistryReview[] = [
  {
    id: "registry-1",
    title: "건물 용도",
    value: "공동주택(아파트)",
    status: "safe",
    summary: "등기 표제부에서 주거용 공동주택으로 확인됩니다.",
  },
  {
    id: "registry-2",
    title: "소유자",
    value: "박도윤",
    status: "safe",
    summary: "계약서에 적힌 임대인과 등기 명의인이 일치합니다.",
  },
  {
    id: "registry-3",
    title: "근저당권",
    value: "채권최고액 180,000,000원",
    status: "check",
    summary: "을구에서 근저당권 설정 내역이 확인됩니다.",
    action: "실제 채무액과 주택 시세, 보증금의 우선순위를 함께 확인하세요.",
  },
  {
    id: "registry-4",
    title: "압류·가압류",
    value: "확인되지 않음",
    status: "safe",
    summary: "제공된 등기사항증명서에서는 압류나 가압류 내역이 확인되지 않습니다.",
  },
];

export const contractFields = [
  { label: "계약 유형", value: "전세" },
  { label: "계약일", value: "2026.10.05" },
  { label: "소재지", value: "서울특별시 성동구 알파로 27, 알파아파트 101동 1203호", wide: true },
  { label: "건물 용도", value: "아파트" },
  { label: "임차 면적", value: "84.97㎡" },
  { label: "임대인", value: "박도윤" },
  { label: "임차인", value: "김서연" },
  { label: "보증금", value: "250,000,000원" },
  { label: "계약금", value: "25,000,000원" },
  { label: "중도금·지급일", value: "25,000,000원 · 2026.10.20", wide: true },
  { label: "잔금·지급일", value: "200,000,000원 · 2026.10.31", wide: true },
  { label: "계약 기간", value: "2026.10.31 - 2028.10.30", wide: true },
  { label: "특약사항", value: "권리관계 유지, 보증 가입 협조, 원상회복, 보증금 반환 시기", wide: true },
];

export const registryFields = [
  { label: "발급일", value: "2026.10.22" },
  { label: "소재지", value: "서울특별시 성동구 알파로 27, 알파아파트 101동 1203호", wide: true },
  { label: "건물 용도", value: "공동주택(아파트)" },
  { label: "전유면적", value: "84.97㎡" },
  { label: "현재 소유자", value: "박도윤" },
  { label: "소유권 변동", value: "2021.04.12 매매로 소유권 이전", wide: true },
  { label: "압류·가압류·가등기", value: "확인되지 않음", wide: true },
  { label: "근저당권", value: "채권최고액 180,000,000원 · 알파은행", wide: true },
];

