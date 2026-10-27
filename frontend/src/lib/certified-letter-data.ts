import precedentSource from "./certified-letter-precedent-full-texts.json";
import highlightSource from "./certified-letter-precedent-highlights.json";
import type { CaseBasis, ConfirmedContractSnapshot } from "./demo-types";

export const certifiedLetterSampleRequest = "전세 계약이 종료됐지만 아직 보증금을 돌려받지 못했습니다. 2028년 11월 7일까지 보증금 전액을 반환해 달라는 내용증명을 보내고 싶습니다.";

export const certifiedLetterContractSnapshot: ConfirmedContractSnapshot = {
  contractType: "전세",
  fields: [
    { id: "contract-type", label: "계약 유형", value: "전세", group: "문서·계약 기본정보", priority: "priority", status: "user-confirmed", required: true, source: { page: 1, label: "계약 종류" } },
    { id: "renewal-type", label: "계약 구분", value: "신규 계약", group: "문서·계약 기본정보", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "계약 구분" } },
    { id: "contract-date", label: "계약일", value: "2026.10.05", group: "문서·계약 기본정보", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "계약 체결일" } },
    { id: "property-address", label: "주소", value: "서울특별시 성동구 알파로 27", group: "목적물", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "소재지" } },
    { id: "property-detail", label: "상세주소", value: "알파아파트 101동 1203호", group: "목적물", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "임차할 부분" } },
    { id: "building-info", label: "건물 구조·용도·면적", value: "아파트 · 84.97㎡", group: "목적물", priority: "priority", status: "needs-confirmation", source: { page: 1, label: "건물" } },
    { id: "leased-part", label: "임차 부분", value: "101동 1203호 전부", group: "목적물", priority: "priority", status: "needs-confirmation", source: { page: 1, label: "임차할 부분" } },
    { id: "lessor", label: "임대인", value: "박도윤", group: "당사자", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 2, label: "임대인" } },
    { id: "lessee", label: "임차인", value: "김서연", group: "당사자", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 2, label: "임차인" } },
    { id: "deposit", label: "보증금", value: "250,000,000", group: "금액·지급일정", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "보증금" } },
    { id: "lease-period", label: "임대차 시작·종료일", value: "2026.10.31 - 2028.10.30", group: "기간·인도", priority: "needs-confirmation", status: "needs-confirmation", required: true, source: { page: 1, label: "임대차 기간" } },
    { id: "special-terms", label: "특약 원문", value: "1. 잔금 지급일 다음 날까지 새로운 권리를 설정하지 않는다.\n2. 전세보증금반환보증 가입에 필요한 서류 제출에 협조한다.\n3. 통상적인 사용으로 발생한 손모를 포함하여 원상복구한다.\n4. 신규 임차인이 입주한 때에 보증금을 반환한다.", group: "조항·특약", priority: "needs-confirmation", status: "needs-confirmation", required: true, multiline: true, source: { page: 2, label: "특약사항" } },
  ],
};

export const certifiedLetterResult = {
  title: "임대차보증금 반환 촉구의 건",
  recipient: { name: "박도윤", address: "서울특별시 성동구 베타로 15", detailAddress: "베타빌딩 502호" },
  sender: { name: "김서연", address: "서울특별시 성동구 알파로 27", detailAddress: "알파아파트 101동 1203호" },
  body: `1. 계약 체결 및 주요 내용

발신인 김서연과 수신인 박도윤은 2026년 10월 5일 서울특별시 성동구 알파로 27, 알파아파트 101동 1203호 전부에 관하여 임대차보증금 250,000,000원, 임대차기간 2026년 10월 31일부터 2028년 10월 30일까지로 정한 주택임대차계약을 체결하였습니다.

2. 계약 종료 및 보증금 미반환

위 임대차계약은 2028년 10월 30일 종료되었으나, 2028년 10월 31일 현재 발신인은 임대차보증금 250,000,000원을 반환받지 못하였습니다.

계약서 제3쪽 특약사항 제5항에는 “임대차 종료 후 신규 임차인이 입주한 때에 보증금을 반환한다.”라고 기재되어 있습니다. 그러나 발신인은 신규 임차인의 입주 여부만을 이유로 보증금 반환 시기가 정해지지 않은 채 계속 미뤄지는 것에는 동의하기 어렵다는 입장입니다.

3. 관련 법령 및 이행 협의

「주택임대차보호법」 제4조는 임대차기간이 끝난 경우에도 임차인이 보증금을 반환받을 때까지 임대차관계가 존속되는 것으로 정하고 있습니다.

또한 「민법」 제536조에 따른 동시이행 관계를 고려하여, 발신인은 임대차보증금 전액을 반환받는 것과 동시에 임대차목적물 및 열쇠의 인도 일정과 방법을 협의하고 필요한 절차를 이행할 의사가 있습니다.

4. 요구사항

발신인은 수신인에게 2028년 11월 7일까지 임대차보증금 250,000,000원 전액을 반환할 것을 요청합니다.

수신인이 보증금에서 공제하거나 별도로 정산할 금액이 있다고 주장하는 경우에는 같은 기한까지 그 구체적인 항목, 금액 및 근거자료를 서면으로 제시하고, 다툼이 없는 나머지 보증금은 우선 반환하여 주시기 바랍니다.

5. 협의 요청 및 후속 조치

발신인은 보증금 반환일, 반환 방법 및 임대차목적물 인도 일정을 원만하게 협의할 의사가 있으므로, 위 기한 내에 구체적인 반환 계획을 알려주시기 바랍니다.

위 기한까지 보증금 반환 또는 구체적인 이행 협의가 이루어지지 않을 경우, 발신인은 권리 보전을 위하여 임차권등기명령, 지급명령 또는 임대차보증금 반환청구 등 필요한 민사상 절차를 검토할 예정임을 알려드립니다.

2028년 10월 31일

발신인 김서연 (서명 또는 날인)`,
  strategy: "계약서에서 확인된 보증금, 계약기간, 목적물과 특약사항을 사용하고, 사용자가 확인한 보증금 미반환 사실과 반환기한을 분리해 기재했다. 신규 임차인 입주 특약의 효력을 단정하지 않으면서도 반환 시기를 무기한 미룰 수 없다는 발신인의 입장을 명확히 했다. 보증금 반환과 목적물 인도를 함께 협의하겠다는 의사를 표시하여 단순한 위협보다 실제 이행 가능성을 높이는 방향으로 작성했다.",
  nextSteps: [
    "발송 전 계약 종료 또는 갱신 거절 사실을 확인할 수 있는 문자, 통지서 등 자료를 다시 확인합니다.",
    "임대인의 실제 송달 주소와 임차인의 주소, 보증금 액수 및 반환기한을 원자료와 대조합니다.",
    "우체국 내용증명과 배달증명으로 발송하고 계약서, 발송본 및 배달자료를 함께 보관합니다.",
    "기한 내 반환이나 구체적인 협의가 없으면 임차권등기명령, 지급명령 또는 보증금 반환청구 가능성을 검토합니다.",
  ],
  notices: [
    "이 문서는 발표용 가상정보로 만든 법률 검토 전 초안입니다.",
    "박도윤과 김서연의 주소는 OCR 골드셋에 포함되지 않아 발표용 사용자 확인값으로 별도 추가했습니다.",
    "계약 종료 또는 갱신 거절이 적법하게 이루어졌는지는 실제 발송 전 관련 통지자료로 확인해야 합니다.",
    "법령은 2026년 10월 27일 확인 가능한 내용을 사용했으므로, 가상 발송일인 2028년 10월 31일 당시 시행 법령을 다시 확인해야 합니다.",
    "유사 판례는 참고용이며 개별 사건의 사실관계와 결론이 그대로 적용된다는 의미가 아닙니다.",
  ],
};

export const certifiedLetterStatutes = [
  { lawName: "주택임대차보호법", articleLabel: "제4조", articleTitle: "임대차기간 등", content: "① 기간을 정하지 아니하거나 2년 미만으로 정한 임대차는 그 기간을 2년으로 본다. 다만, 임차인은 2년 미만으로 정한 기간이 유효함을 주장할 수 있다.\n② 임대차기간이 끝난 경우에도 임차인이 보증금을 반환받을 때까지는 임대차관계가 존속되는 것으로 본다.", explanation: "계약기간이 끝났더라도 보증금이 반환되지 않은 동안에는 임대차관계가 법적으로 계속되는 것으로 본다는 근거입니다.", url: "https://www.law.go.kr/법령/주택임대차보호법/제4조" },
  { lawName: "민법", articleLabel: "제536조", articleTitle: "동시이행의 항변권", content: "① 쌍무계약의 당사자 일방은 상대방이 그 채무이행을 제공할 때까지 자기의 채무이행을 거절할 수 있다. 그러나 상대방의 채무가 변제기에 있지 아니하는 때에는 그러하지 아니하다.\n② 당사자 일방이 상대방에게 먼저 이행하여야 할 경우에 상대방의 이행이 곤란할 현저한 사유가 있는 때에는 제1항 본문과 같다.", explanation: "임대차가 끝난 뒤 보증금 반환과 목적물 인도 절차를 함께 이행·협의해야 하는 관계를 설명하는 근거입니다.", url: "https://www.law.go.kr/법령/민법/제536조" },
];

type CertifiedLetterPrecedent = CaseBasis & { matchedReason: string };

type PrecedentSource = typeof precedentSource.precedents[number];

function formatDate(value: string) {
  const [year, month, day] = value.split("-");
  return `${year}. ${Number(month)}. ${Number(day)}.`;
}

export const certifiedLetterPrecedents: CertifiedLetterPrecedent[] = precedentSource.precedents.map((precedent: PrecedentSource) => ({
  title: precedent.case_name,
  caseNumber: precedent.case_number,
  precedentId: `국가법령정보센터 판례일련번호 ${precedent.precedent_id}`,
  court: precedent.court_name,
  decisionDate: formatDate(precedent.decision_date),
  summary: precedent.precedent_summary,
  easySummary: precedent.easy_summary,
  matchedReason: (() => {
    const reasons: Record<string, string> = {
      "207564": "임대차기간이 끝난 뒤 보증금을 반환받지 못한 임차인이 반환을 청구한 사례라는 점에서 참고합니다.",
      "238851": "임대차 종료 후 반환할 보증금과 공제·정산 범위를 다룬 사례라는 점에서 참고합니다.",
      "615961": "신규 임차인 입주와 보증금 반환을 연결한 상황에서 계약 종료 후 보증금이 반환되지 않았다는 점을 참고합니다.",
    };
    return reasons[precedent.precedent_id];
  })(),
  evidenceChunks: [
    { section: "생성요약", content: precedent.precedent_summary },
    ...(highlightSource.highlights.find((item) => item.precedent_id === precedent.precedent_id)?.chunks.map((chunk) => ({ section: chunk.section, content: chunk.text })) ?? []),
  ],
  highlightChunks: highlightSource.highlights.find((item) => item.precedent_id === precedent.precedent_id)?.chunks.map((chunk) => ({ section: chunk.section, startOffset: chunk.start_offset, endOffset: chunk.end_offset })) ?? [],
  fullText: {
    order: precedent.full_text.order || undefined,
    claim: precedent.full_text.claim || undefined,
    lowerCourt: precedent.full_text.original_judgment || undefined,
    reason: precedent.full_text.reason || undefined,
  },
  url: precedent.source_url,
}));
