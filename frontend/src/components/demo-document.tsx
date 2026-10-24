import styles from "@/app/contract-review/contract-review.module.css";

type DemoDocumentProps = {
  kind: "contract" | "registry";
};

export function DemoDocument({ kind }: DemoDocumentProps) {
  if (kind === "registry") {
    return (
      <div className={styles.registrySheet} aria-label="합성 등기사항전부증명서 미리보기">
        <div className={styles.sampleStamp}>데모용 합성 문서</div>
        <h2>등기사항전부증명서</h2>
        <p className={styles.documentSubtitle}>집합건물 · 현재 유효사항</p>
        <h3>표제부</h3>
        <table><tbody>
          <tr><th>소재지번</th><td>서울특별시 성동구 알파로 27</td></tr>
          <tr><th>건물명칭</th><td>알파아파트 101동 1203호</td></tr>
          <tr><th>건물내역</th><td>철근콘크리트조 · 공동주택 · 84.97㎡</td></tr>
        </tbody></table>
        <h3>갑구 · 소유권에 관한 사항</h3>
        <table><tbody>
          <tr><th>순위번호</th><th>등기목적</th><th>권리자 및 기타사항</th></tr>
          <tr><td>2</td><td>소유권이전</td><td>2021년 4월 12일 매매 · 소유자 박도윤</td></tr>
        </tbody></table>
        <h3>을구 · 소유권 이외의 권리</h3>
        <table><tbody>
          <tr><th>순위번호</th><th>등기목적</th><th>권리자 및 기타사항</th></tr>
          <tr><td>1</td><td>근저당권설정</td><td>채권최고액 180,000,000원 · 알파은행</td></tr>
        </tbody></table>
      </div>
    );
  }

  return (
    <div className={styles.contractSheet} aria-label="전세 계약서 합성 샘플 미리보기">
      <div className={styles.sampleStamp}>골드셋 기반 합성 문서</div>
      <h2>주택임대차 표준계약서</h2>
      <p className={styles.documentSubtitle}>전세 · 계약일 2026년 10월 5일</p>
      <h3>1. 임대 목적물</h3>
      <table><tbody>
        <tr><th>소재지</th><td colSpan={3}>서울특별시 성동구 알파로 27, 알파아파트 101동 1203호</td></tr>
        <tr><th>용도</th><td>아파트</td><th>면적</th><td>84.97㎡</td></tr>
      </tbody></table>
      <h3>2. 계약 내용</h3>
      <table><tbody>
        <tr><th>보증금</th><td>금 250,000,000원</td></tr>
        <tr><th>계약금</th><td>금 25,000,000원</td></tr>
        <tr><th>잔금</th><td>금 200,000,000원 · 2026년 10월 31일</td></tr>
        <tr><th>기간</th><td>2026년 10월 31일부터 2028년 10월 30일까지</td></tr>
      </tbody></table>
      <h3>3. 특약사항</h3>
      <ol className={styles.documentTerms}>
        <li>잔금 지급일 다음 날까지 새로운 권리를 설정하지 않는다.</li>
        <li>전세보증금반환보증 가입에 필요한 서류 제출에 협조한다.</li>
        <li>통상적인 사용으로 발생한 손모를 포함하여 원상복구한다.</li>
        <li>신규 임차인이 입주한 때에 보증금을 반환한다.</li>
      </ol>
      <div className={styles.documentSignatures}><span>임대인 박도윤</span><span>임차인 김서연</span></div>
    </div>
  );
}
