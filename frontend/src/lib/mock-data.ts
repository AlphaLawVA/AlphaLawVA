import type {
  CaseBasis,
  ClauseReview,
  ConfirmedContractSnapshot,
  ConfirmedRegistrySnapshot,
  RegistryReview,
} from "./demo-types";

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

export const lessorMaintenanceLaw = {
  law: "민법",
  article: "제623조",
  excerpt: "임대인은 계약이 존속하는 동안 임차인이 목적물을 사용·수익하는 데 필요한 상태를 유지할 의무를 부담합니다.",
  explanation: "시설의 유지·수리 책임을 정할 때 임차인의 원상회복 범위와 임대인의 유지의무를 함께 검토하기 위한 근거입니다.",
  url: "https://www.law.go.kr/법령/민법/제623조",
};

// UI demonstration data only. Retrieval records will replace these fields when the API is connected.
export const restorationCase: CaseBasis = {
  title: "토지인도",
  caseNumber: "2023다249661",
  precedentId: "국가법령정보센터 판례일련번호 237925",
  summary: "임차인은 원칙적으로 임차목적물을 반환할 때 원상회복의무를 부담하지만, 임대 당시 이미 존재하던 시설까지 당연히 복구해야 하는 것은 아니며 계약 경위와 당시 상태 등을 구체적으로 살펴야 한다고 본 판례입니다.",
  easySummary: "세입자가 들어오기 전부터 있던 시설까지 무조건 원래대로 돌려놓아야 하는 것은 아니며, 누가 무엇을 바꿨는지와 계약 내용을 따져봐야 한다는 뜻입니다.",
  court: "대법원",
  decisionDate: "2023. 11. 2.",
  judgmentType: "판결",
  evidenceChunks: [
    { section: "판례요약", content: "임대 당시 존재하던 시설까지 당연히 철거해야 하는 것은 아니며, 계약 경위와 당시 상태를 구체적으로 살펴야 합니다." },
    { section: "판결 이유", content: "원상회복 범위는 계약 당시 상태와 임차인이 실제로 변경한 부분을 구분하여 판단할 필요가 있습니다." },
  ],
  fullText: {
    order: "상고를 기각한다.\n상고비용은 원고가 부담한다.",
    claim: "원고는 임대차 종료 후 목적물 반환과 원상회복 범위에 관하여 청구하였다.",
    lowerCourt: "원심은 계약 당시의 상태와 당사자 사이의 약정을 살펴 원상회복 범위를 판단하였다.",
    reason: "임차인은 자신이 변경하거나 훼손한 부분에 관하여 원상회복의무를 부담할 수 있다. 다만 임대 당시부터 존재하던 시설까지 일률적으로 철거·복구 대상으로 볼 수 있는지는 계약 내용과 실제 사용 경위를 함께 살펴야 한다.",
  },
  url: "https://www.law.go.kr/LSW/precInfoP.do?precSeq=237925",
};

export const searchPrecedents: CaseBasis[] = [
  {
    title: "임대차보증금",
    caseNumber: "2021다210720",
    precedentId: "국가법령정보센터 판례일련번호 227209",
    court: "대법원",
    decisionDate: "2022. 3. 17.",
    judgmentType: "판결",
    summary: "대항요건을 갖춘 임차주택의 양수인은 임대인의 지위를 승계하며, 보증금 반환채무도 소유권과 결합하여 양수인에게 이전된다고 판단한 판례입니다.",
    easySummary: "세입자가 대항력을 갖춘 상태에서 집주인이 바뀌면, 새 집주인이 기존 임대차와 보증금 반환 책임을 이어받는다는 취지입니다.",
    evidenceChunks: [
      { section: "판례요약", content: "대항요건을 갖춘 임차주택의 양수인은 임대인의 지위를 승계하고, 보증금 반환채무도 함께 이전될 수 있습니다." },
      { section: "판결 이유", content: "임차인의 대항요건과 소유권 이전 시점을 함께 확인할 필요가 있습니다." },
    ],
    fullText: {
      order: "상고를 기각한다.\n상고비용은 원고가 부담한다.",
      claim: "원고는 주택 소유권 이전 뒤 보증금 반환의무의 귀속을 다투었다.",
      reason: "임차인이 법에서 정한 대항요건을 갖춘 경우에는 주택의 양수인이 임대인의 지위를 승계하는지가 문제된다. 구체적 책임 범위는 계약관계와 소유권 변동 경위를 함께 살펴 판단한다.",
    },
    url: "https://www.law.go.kr/LSW/precInfoP.do?precSeq=227209",
  },
  {
    title: "임대차보증금반환",
    caseNumber: "2012다84370",
    precedentId: "국가법령정보센터 판례일련번호 178111",
    court: "대법원",
    decisionDate: "2015. 5. 29.",
    judgmentType: "판결",
    summary: "매수인이 보증금 반환채무를 인수하고 매매대금에서 공제했더라도, 기존 임대인을 면책하려면 원칙적으로 임차인의 승낙이 필요하다고 본 판례입니다.",
    easySummary: "새 소유자가 보증금을 돌려주기로 했다는 사정만으로 기존 임대인의 책임이 자동으로 사라지는 것은 아니며, 세입자의 동의 여부를 신중히 봐야 한다는 취지입니다.",
    evidenceChunks: [
      { section: "판례요약", content: "새 소유자가 보증금 반환채무를 인수한 사정만으로 기존 임대인의 책임이 당연히 사라지는 것은 아닙니다." },
      { section: "쉬운요약", content: "누구에게 보증금을 요구할 수 있는지는 세입자의 동의 여부와 계약 내용을 함께 확인해야 합니다." },
    ],
    fullText: {
      order: "상고를 기각한다.\n상고비용은 원고가 부담한다.",
      claim: "원고는 소유권 이전 뒤 기존 임대인의 보증금 반환책임이 남는지를 다투었다.",
      lowerCourt: "원심은 임차인의 승낙 여부와 당사자 사이의 약정을 살펴 판단하였다.",
      reason: "보증금 반환채무의 인수와 기존 임대인의 면책 여부는 임차인의 의사와 계약 관계를 함께 고려하여 판단할 필요가 있다.",
    },
    url: "https://www.law.go.kr/LSW/precInfoP.do?precSeq=178111",
  },
  restorationCase,
];

export const depositReturnCase: CaseBasis = {
  title: "임대차보증금",
  caseNumber: "87다카98",
  precedentId: "국가법령정보센터 판례일련번호 103300",
  court: "대법원",
  decisionDate: "1987. 6. 23.",
  judgmentType: "판결",
  summary: "임대차가 끝난 뒤 목적물을 반환할 때까지 발생한 임차인의 채무를 공제하고 남은 보증금의 반환의무와 목적물 반환의무가 동시이행 관계에 있다고 본 판례입니다.",
  easySummary: "계약이 끝났다고 해도 집을 넘겨주는 문제와 보증금을 돌려받는 문제는 함께 확인해야 할 수 있다는 뜻입니다.",
  evidenceChunks: [
    { section: "판례요약", content: "임대차 종료 뒤 보증금 반환과 목적물 반환의 관계를 함께 살펴야 합니다." },
    { section: "판결 이유", content: "반환할 금액과 당사자의 이행 상황은 실제 계약 내용에 따라 구체적으로 확인할 필요가 있습니다." },
  ],
  fullText: {
    order: "상고를 기각한다.\n상고비용은 원고가 부담한다.",
    claim: "원고는 임대차 종료 뒤 임대차보증금의 반환을 청구하였다.",
    reason: "임대차 종료 뒤 보증금 반환의무와 목적물 반환의무는 당사자 사이의 약정과 실제 이행 상황을 함께 살펴 판단할 필요가 있다.",
  },
  url: "https://law.go.kr/LSW/precInfoP.do?precSeq=103300",
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
    statutes: [restorationLaw, lessorMaintenanceLaw],
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

export const confirmedContractSnapshot: ConfirmedContractSnapshot = {
  contractType: "전세",
  fields: [
    { id: "contract-type", label: "계약 유형", value: "전세", group: "문서·계약 기본정보", priority: "priority", status: "user-confirmed", required: true, source: { page: 1, label: "계약 종류" } },
    { id: "renewal-type", label: "갱신 구분", value: "", group: "문서·계약 기본정보", priority: "needs-confirmation", status: "needs-confirmation", source: { page: 1, label: "계약 갱신 여부" } },
    { id: "contract-date", label: "계약일", value: "2026.10.05", group: "문서·계약 기본정보", priority: "priority", status: "needs-confirmation", required: true, source: { page: 1, label: "계약 체결일" } },
    { id: "page-count", label: "문서 페이지 수", value: "2", group: "문서·계약 기본정보", priority: "automatic", status: "automatic", source: { page: 1, label: "문서 정보" } },
    { id: "property-address", label: "주소", value: "서울특별시 성동구 알파로 27", group: "목적물", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "소재지" } },
    { id: "property-detail", label: "상세주소", value: "알파아파트 101동 1203호", group: "목적물", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "임차할 부분" } },
    { id: "land-category", label: "토지 지목·면적", value: "대 · 38.12㎡", group: "목적물", priority: "automatic", status: "automatic", source: { page: 1, label: "토지" } },
    { id: "building-info", label: "건물 구조·용도·면적", value: "철근콘크리트조 · 아파트 · 84.97㎡", group: "목적물", priority: "priority", status: "automatic", source: { page: 1, label: "건물" } },
    { id: "leased-part", label: "임차 부분·면적", value: "101동 1203호 · 84.97㎡", group: "목적물", priority: "automatic", status: "automatic", source: { page: 1, label: "임차할 부분" } },
    { id: "land-right", label: "대지권 정보", value: "대지권의 목적인 토지 지분", group: "목적물", priority: "automatic", status: "automatic", source: { page: 1, label: "대지권" } },
    { id: "lessor", label: "임대인", value: "박도윤", group: "당사자·중개사", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 2, label: "임대인" } },
    { id: "lessee", label: "임차인", value: "김서연", group: "당사자·중개사", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 2, label: "임차인" } },
    { id: "representative", label: "대리인 여부·성명", value: "대리인 없음", group: "당사자·중개사", priority: "automatic", status: "automatic", source: { page: 2, label: "대리인" } },
    { id: "broker", label: "중개사무소·등록번호", value: "알파공인중개사사무소 · 12345-2026-00001", group: "당사자·중개사", priority: "automatic", status: "automatic", source: { page: 2, label: "개업공인중개사" } },
    { id: "broker-sign", label: "담당 공인중개사·서명 여부", value: "최중개 · 서명 확인", group: "당사자·중개사", priority: "automatic", status: "automatic", source: { page: 2, label: "중개대상물 확인·설명" } },
    { id: "deposit", label: "보증금", value: "250,000,000", group: "금액·지급일정", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "보증금" } },
    { id: "deposit-korean", label: "보증금 한글 원문", value: "금 이억오천만원정", group: "금액·지급일정", priority: "priority", status: "automatic", source: { page: 1, label: "보증금 한글" } },
    { id: "down-payment", label: "계약금·지급일", value: "25,000,000 · 2026.10.05", group: "금액·지급일정", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "계약금" } },
    { id: "interim-payment", label: "중도금·지급일", value: "25,000,000 · 2026.10.20", group: "금액·지급일정", priority: "priority", status: "needs-confirmation", source: { page: 1, label: "중도금" } },
    { id: "balance", label: "잔금·지급일", value: "200,000,000 · 2026.10.31", group: "금액·지급일정", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "잔금" } },
    { id: "maintenance-fee", label: "관리비", value: "월 120,000원", group: "금액·지급일정", priority: "automatic", status: "automatic", source: { page: 1, label: "관리비" } },
    { id: "lease-period", label: "임대차 시작·종료일", value: "2026.10.31 - 2028.10.30", group: "기간·인도", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "임대차 기간" } },
    { id: "move-in-date", label: "인도일", value: "2026.10.31", group: "기간·인도", priority: "priority", status: "needs-confirmation", source: { page: 1, label: "인도일" } },
    { id: "tax-arrears", label: "체납 여부", value: "미납 국세·지방세 열람 필요", group: "고지·권리 관련 사항", priority: "needs-confirmation", status: "needs-confirmation", source: { page: 2, label: "체납 관련 고지" } },
    { id: "senior-deposit", label: "선순위 보증금", value: "확인되지 않음", group: "고지·권리 관련 사항", priority: "priority", status: "automatic", source: { page: 2, label: "선순위 임차보증금" } },
    { id: "registry-checked", label: "등기 확인 여부", value: "등기사항증명서 첨부", group: "고지·권리 관련 사항", priority: "priority", status: "automatic", source: { page: 2, label: "등기사항증명서" } },
    { id: "right-restriction", label: "권리 설정 제한 특약", value: "잔금 지급일 다음 날까지 새로운 권리를 설정하지 않는다.", group: "고지·권리 관련 사항", priority: "needs-confirmation", status: "needs-confirmation", multiline: true, source: { page: 2, label: "특약 1" } },
    { id: "insurance", label: "보증보험 관련 문구", value: "전세보증금반환보증 가입에 필요한 서류 제출에 협조한다.", group: "고지·권리 관련 사항", priority: "priority", status: "automatic", multiline: true, source: { page: 2, label: "특약 2" } },
    { id: "special-terms", label: "특약 원문", value: "1. 잔금 지급일 다음 날까지 새로운 권리를 설정하지 않는다.\n2. 전세보증금반환보증 가입에 필요한 서류 제출에 협조한다.\n3. 통상적인 사용으로 발생한 손모를 포함하여 원상복구한다.\n4. 신규 임차인이 입주한 때에 보증금을 반환한다.", group: "조항·특약", priority: "needs-confirmation", status: "needs-confirmation", required: true, multiline: true, source: { page: 2, label: "특약사항" } },
    { id: "general-clause", label: "분석 대상 일반 조항", value: "계약 해제·손해배상 및 목적물 반환 조항", group: "조항·특약", priority: "automatic", status: "automatic", source: { page: 2, label: "일반 조항" } },
  ],
};

function copySnapshotWithChanges(
  contractType: ConfirmedContractSnapshot["contractType"],
  changes: Record<string, Partial<ConfirmedContractSnapshot["fields"][number]>>,
  omitIds: string[] = [],
): ConfirmedContractSnapshot {
  return {
    contractType,
    fields: confirmedContractSnapshot.fields
      .filter((field) => !omitIds.includes(field.id))
      .map((field) => ({ ...field, ...changes[field.id], source: { ...field.source } })),
  };
}

export const contractSnapshots: Record<ConfirmedContractSnapshot["contractType"], ConfirmedContractSnapshot> = {
  전세: confirmedContractSnapshot,
  월세: {
    ...copySnapshotWithChanges("월세", {
      "contract-type": { value: "월세", status: "user-confirmed" },
      deposit: { value: "50,000,000" },
      "deposit-korean": { value: "금 오천만원정" },
      "down-payment": { value: "5,000,000 · 2026.10.05" },
      "interim-payment": { value: "없음" },
      balance: { value: "45,000,000 · 2026.10.31" },
      "maintenance-fee": { value: "월 100,000원" },
    }, ["insurance"]),
    fields: [
      ...copySnapshotWithChanges("월세", {
        "contract-type": { value: "월세", status: "user-confirmed" },
        deposit: { value: "50,000,000" },
        "deposit-korean": { value: "금 오천만원정" },
        "down-payment": { value: "5,000,000 · 2026.10.05" },
        "interim-payment": { value: "없음" },
        balance: { value: "45,000,000 · 2026.10.31" },
        "maintenance-fee": { value: "월 100,000원" },
      }, ["insurance"]).fields,
      { id: "monthly-rent", label: "월 차임", value: "1,200,000", group: "금액·지급일정", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "차임" } },
      { id: "rent-payment-date", label: "차임 납부일", value: "매월 25일", group: "금액·지급일정", priority: "priority", status: "needs-confirmation", source: { page: 1, label: "차임 지급일" } },
      { id: "rent-payment-method", label: "선불·후불", value: "선불", group: "금액·지급일정", priority: "automatic", status: "automatic", source: { page: 1, label: "차임 지급" } },
    ],
  },
  매매: {
    ...copySnapshotWithChanges("매매", {
      "contract-type": { value: "매매", status: "user-confirmed" },
      lessor: { label: "매도인", value: "박도윤" },
      lessee: { label: "매수인", value: "김서연" },
      deposit: { label: "매매대금", value: "650,000,000" },
      "deposit-korean": { label: "매매대금 한글 원문", value: "금 육억오천만원정" },
      "down-payment": { value: "65,000,000 · 2026.10.05" },
      "interim-payment": { value: "130,000,000 · 2026.10.20" },
      balance: { value: "455,000,000 · 2026.10.31" },
    }, ["lease-period", "move-in-date", "insurance", "senior-deposit"]),
    fields: [
      ...copySnapshotWithChanges("매매", {
        "contract-type": { value: "매매", status: "user-confirmed" },
        lessor: { label: "매도인", value: "박도윤" },
        lessee: { label: "매수인", value: "김서연" },
        deposit: { label: "매매대금", value: "650,000,000" },
        "deposit-korean": { label: "매매대금 한글 원문", value: "금 육억오천만원정" },
        "down-payment": { value: "65,000,000 · 2026.10.05" },
        "interim-payment": { value: "130,000,000 · 2026.10.20" },
        balance: { value: "455,000,000 · 2026.10.31" },
      }, ["lease-period", "move-in-date", "insurance", "senior-deposit"]).fields,
      { id: "handover-date", label: "인도일", value: "2026.10.31", group: "기간·인도", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 2, label: "인도일" } },
      { id: "ownership-transfer-date", label: "소유권이전 예정일", value: "2026.10.31", group: "기간·인도", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 2, label: "소유권이전" } },
    ],
  },
};

export const confirmedRegistrySnapshot: ConfirmedRegistrySnapshot = {
  fields: [
    { id: "registry-issued-at", label: "발급일시", value: "2026.10.22 10:31", group: "발급·부동산 정보", priority: "automatic", status: "automatic", source: { page: 1, label: "발급 정보" } },
    { id: "registry-address", label: "소재지", value: "서울특별시 성동구 알파로 27", group: "발급·부동산 정보", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "표제부 소재지" } },
    { id: "registry-detail", label: "상세주소", value: "알파아파트 101동 1203호", group: "발급·부동산 정보", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "건물내역" } },
    { id: "registry-building-original", label: "표제부 건물 표시 원문", value: "철근콘크리트조 · 공동주택 · 전유부분 84.97㎡", group: "발급·부동산 정보", priority: "automatic", status: "automatic", multiline: true, source: { page: 1, label: "표제부" } },
    { id: "owner-name", label: "현재 소유자 성명", value: "박도윤", group: "현재 소유자", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "갑구 소유권이전" } },
    { id: "owner-share", label: "소유권 지분", value: "전부", group: "현재 소유자", priority: "priority", status: "automatic", source: { page: 1, label: "갑구 소유권이전" } },
    { id: "owner-cause", label: "등기원인·접수일", value: "2021.04.12 매매 · 2021.04.12 접수", group: "현재 소유자", priority: "priority", status: "automatic", source: { page: 1, label: "갑구 소유권이전" } },
    { id: "owner-status", label: "소유권 상태", value: "유효", group: "현재 소유자", priority: "priority", status: "automatic", source: { page: 1, label: "갑구 소유권이전" } },
  ],
  rights: [
    { id: "right-owner", section: "갑구", rank: "2", purpose: "소유권이전", receivedDate: "2021.04.12", holder: "박도윤", status: "active", source: { page: 1, label: "갑구 2번" } },
    { id: "right-mortgage", section: "을구", rank: "1", purpose: "근저당권설정", receivedDate: "2024.02.14", holder: "알파은행", debtor: "박도윤", maximumAmount: "180,000,000원", status: "active", source: { page: 1, label: "을구 1번" } },
    { id: "right-cancelled", section: "을구", rank: "2", purpose: "근저당권설정", receivedDate: "2020.08.21", holder: "베타은행", debtor: "박도윤", maximumAmount: "90,000,000원", status: "cancelled", source: { page: 1, label: "을구 2번" } },
  ],
};

// Content-proofing flow still consumes the compact field representation until its
// real OCR response contract is connected.
export const contractFields = confirmedContractSnapshot.fields
  .filter((field) => ["contract-type", "contract-date", "property-address", "building-info", "leased-part", "lessor", "lessee", "deposit", "down-payment", "interim-payment", "balance", "lease-period", "special-terms"].includes(field.id))
  .map((field) => ({ label: field.label, value: field.value, wide: field.multiline || field.value.length > 34 }));

export const registryFields = confirmedRegistrySnapshot.fields
  .map((field) => ({ label: field.label, value: field.value, wide: field.multiline || field.value.length > 34 }));

