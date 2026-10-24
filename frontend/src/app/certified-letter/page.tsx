"use client";

import { useEffect, useState } from "react";
import { AlertCircle, Check, CheckCircle2, Download, FileText, ScanText, Upload } from "lucide-react";
import { DemoDocument } from "@/components/demo-document";
import { contractFields } from "@/lib/mock-data";
import styles from "../feature-demo.module.css";
import reviewStyles from "../contract-review/contract-review.module.css";

type Stage = "upload" | "processing" | "verify" | "request" | "result";
const sampleRequest = "전세 계약이 끝났는데 임대인이 보증금 2억 5천만 원을 돌려주지 않고 있습니다. 2028년 11월 7일까지 반환해 달라는 내용증명을 보내고 싶습니다.";

export default function CertifiedLetterPage() {
  const [stage, setStage] = useState<Stage>("upload");
  const [loaded, setLoaded] = useState(false);
  const [progress, setProgress] = useState(8);
  const [fields, setFields] = useState(contractFields);
  const [confirmed, setConfirmed] = useState(false);
  const [request, setRequest] = useState(sampleRequest);
  const [error, setError] = useState("");

  useEffect(() => {
    if (stage !== "processing") return;
    const timer = window.setInterval(() => setProgress((value) => Math.min(100, value + 5)), 120);
    return () => window.clearInterval(timer);
  }, [stage]);

  useEffect(() => {
    if (stage !== "processing" || progress < 100) return;
    const timer = window.setTimeout(() => setStage("verify"), 350);
    return () => window.clearTimeout(timer);
  }, [progress, stage]);

  function generateLetter() {
    if (request.trim().length < 35) {
      setError("무슨 일이 있었는지, 상대방에게 무엇을 언제까지 요구하는지 조금 더 구체적으로 적어주세요.");
      return;
    }
    setError("");
    setStage("result");
  }

  const stepIndex = stage === "upload" || stage === "processing" ? 0 : stage === "verify" ? 1 : stage === "request" ? 2 : 3;

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>계약정보를 바탕으로 전달 내용을 문서화</p>
        <h1>내용증명 생성</h1>
        <p>계약서에서 핵심 정보를 확인한 뒤, 상대방에게 전달할 요구를 내용증명 초안으로 정리합니다.</p>
      </header>
      <div className={styles.steps} aria-label="내용증명 생성 단계">
        {["계약서 업로드", "정보 확인", "요청 입력", "생성 결과"].map((label, index) => <span className={stepIndex === index ? styles.activeStep : ""} key={label}>{String(index + 1).padStart(2, "0")} {label}</span>)}
      </div>

      {stage === "upload" && (
        <div className={styles.split}>
          <section className={styles.panel}>
            <h2>계약서를 올려주세요</h2>
            <p className={styles.panelLead}>내용증명의 당사자, 목적물, 금액과 계약기간을 확인하는 데 사용합니다.</p>
            <div className={styles.letterDropzone}>
              <span><Upload size={24} /></span><strong>PDF 또는 이미지 파일</strong><p>PDF, JPG, PNG · 최대 20MB</p>
              <button className={styles.secondaryButton} type="button" onClick={() => setLoaded(true)}><FileText size={17} />샘플 계약서 불러오기</button>
            </div>
            {loaded && <div className={styles.uploadedFile}><div><FileText size={17} /><span>전세_손글씨_샘플.pdf</span></div><CheckCircle2 size={18} /></div>}
            <div className={styles.actions}><button className={styles.primaryButton} type="button" disabled={!loaded} onClick={() => { setProgress(8); setStage("processing"); }}><ScanText size={17} />계약정보 읽기</button></div>
          </section>
          <aside className={styles.sidePanel}><h2>업로드 전에 확인하세요</h2><div className={styles.summaryList}>{["계약서 전체 페이지가 포함되어 있는지", "당사자·금액·날짜가 선명하게 보이는지", "주민등록번호 등 불필요한 정보는 가렸는지"].map((item) => <div className={styles.summaryItem} key={item}><p><CheckCircle2 size={15} /> {item}</p></div>)}</div></aside>
        </div>
      )}

      {stage === "processing" && (
        <section className={styles.processingCard}><span><ScanText size={28} /></span><h2>계약정보를 읽고 있습니다</h2><p>당사자, 목적물, 금액과 주요 날짜를 구조화하고 있습니다.</p><div className={styles.progressHeader}><span>계약서 OCR 및 필드 추출</span><strong>{progress}%</strong></div><div className={styles.progressTrack}><i style={{ width: `${progress}%` }} /></div></section>
      )}

      {stage === "verify" && (
        <>
          <div className={styles.notice}><AlertCircle size={18} /><span>자동으로 읽은 값에는 오류가 있을 수 있습니다. 원문과 비교해 당사자, 주소, 날짜와 금액을 확인해주세요.</span></div>
          <div className={reviewStyles.verifyLayout}>
            <aside className={reviewStyles.documentPreview}><DemoDocument kind="contract" /></aside>
            <section className={reviewStyles.formPanel}><div className={reviewStyles.formHeading}><span>계약서</span><h2>내용증명에 사용할 계약정보</h2><p>틀린 값은 직접 수정할 수 있습니다.</p></div><div className={reviewStyles.formGrid}>{fields.map((field, index) => <div className={`${reviewStyles.field} ${field.wide ? reviewStyles.fieldWide : ""}`} key={field.label}><label htmlFor={`letter-field-${index}`}>{field.label}</label><input id={`letter-field-${index}`} value={field.value} onChange={(event) => setFields((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item))} /></div>)}</div><div className={reviewStyles.checkRow}><label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>계약서 원문과 비교해 위 정보를 확인했습니다.</span></label></div><div className={reviewStyles.actions}><button className={reviewStyles.primaryButton} type="button" disabled={!confirmed} onClick={() => setStage("request")}>요청 입력으로</button></div></section>
          </div>
        </>
      )}

      {stage === "request" && (
        <div className={styles.split}>
          <section className={styles.panel}><h2>상대방에게 무엇을 전달할까요?</h2><p className={styles.panelLead}>계약정보는 이미 확인했습니다. 있었던 일과 요구사항, 이행 기한을 자유롭게 적어주세요.</p><div className={styles.field}><label htmlFor="letter-request">상황과 요청</label><textarea id="letter-request" value={request} onChange={(event) => setRequest(event.target.value)} /></div>{error && <div className={styles.notice}><AlertCircle size={18} /><span>{error}</span></div>}<div className={styles.actions}><button className={styles.secondaryButton} type="button" onClick={() => setStage("verify")}>이전</button><button className={styles.primaryButton} type="button" onClick={generateLetter}><FileText size={17} />초안 만들기</button></div></section>
          <aside className={styles.sidePanel}><h2>이 내용을 포함하면 좋습니다</h2><div className={styles.exampleList}><p><b>문제가 생긴 사실</b><span>계약이 끝났지만 보증금을 받지 못했습니다.</span></p><p><b>구체적인 요구</b><span>보증금 전액을 반환해 주세요.</span></p><p><b>이행 기한</b><span>문서를 받은 날부터 7일 이내처럼 적어주세요.</span></p></div></aside>
        </div>
      )}

      {stage === "result" && (
        <>
          <div className={styles.notice}><AlertCircle size={18} /><span>이 문서는 AI가 계약서와 입력 내용을 바탕으로 작성한 초안입니다. 발송만으로 사실관계나 법적 효과가 확정되지는 않으므로 실제 정보와 최신 근거를 다시 확인하세요.</span></div>
          <article className={styles.letterPaper}>
            <h2>임대차보증금 반환 촉구서</h2>
            <div className={styles.letterPartyGrid}><section><span>수신인</span><strong>박도윤</strong><p>서울특별시 성동구 알파로 27</p></section><section><span>발신인</span><strong>김서연</strong><p>서울특별시 성동구 알파로 27, 101동 1203호</p></section></div>
            <div className={styles.letterBody}>{`수신인 귀하께서는 발신인과 체결한 임대차계약과 관련하여 다음과 같이 통지합니다.

【 계약 개요 】
- 임대차 목적물: 서울특별시 성동구 알파로 27, 알파아파트 101동 1203호
- 계약기간: 2026년 10월 31일 ~ 2028년 10월 30일
- 임대차보증금: 250,000,000원

【 사실관계 】
위 임대차계약이 2028년 10월 30일 종료되었으나, 발신인은 현재까지 임대차보증금을 반환받지 못했습니다.

【 요구사항 】
발신인은 수신인에게 임대차보증금 250,000,000원 전액을 2028년 11월 7일까지 반환할 것을 요청합니다.

【 협의 제안 및 후속 조치 】
발신인은 원만한 해결을 위해 반환 일정과 방법을 협의할 의사가 있습니다. 위 기한까지 반환 또는 구체적인 협의가 이루어지지 않을 경우, 보증금 반환을 위한 민사상 절차를 검토할 수 있음을 알려드립니다.

2028년 10월 31일
발신인: 김서연 (서명)`}</div>
          </article>
          <div className={styles.letterAnalysisGrid}>
            <section className={styles.panel}><h2>작성 전략</h2><p className={styles.panelLead}>계약 종료, 미반환 사실, 반환 금액과 기한을 순서대로 명확히 적고 협의 가능성을 남겼습니다. 과도한 위협 표현은 피하고 후속 절차는 검토 가능성으로 표현했습니다.</p></section>
            <section className={styles.panel}><h2>발송 후 대응</h2><p className={styles.panelLead}>기한까지 답변이 없다면 계약서, 보증금 지급 내역, 반환 요청 기록과 내용증명 발송 증명을 보관한 뒤 민사조정이나 보증금 반환 청구 절차를 검토하세요.</p></section>
          </div>
          <section className={styles.panel} style={{ marginTop: 18 }}><h2>관련 법령·판례</h2><div className={styles.basisGrid}><article className={styles.basisCard}><strong>주택임대차보호법 제3조의2</strong><p>보증금 회수와 우선변제권에 관한 조문</p><span>해당 조문은 임차인의 보증금 회수 절차와 요건을 확인할 때 참고하는 근거입니다. 실제 적용 여부는 대항요건과 확정일자 등 사실관계에 따라 달라집니다.</span><a href="https://www.law.go.kr/법령/주택임대차보호법/제3조의2" target="_blank" rel="noreferrer">원문 보기</a></article><article className={styles.basisCard}><strong>대법원 1987. 6. 23. 선고 87다카98 판결</strong><p>임대차보증금의 법적 성질과 반환 범위</p><span>임대차가 끝난 뒤 목적물을 반환할 때까지 발생한 임차인의 채무를 공제하고 남은 보증금의 반환의무와 목적물 반환의무가 동시이행 관계에 있다고 본 판례입니다.</span><a href="https://law.go.kr/LSW/precInfoP.do?precSeq=103300" target="_blank" rel="noreferrer">판례 원문 보기</a></article></div></section>
          <section className={styles.preflight}><h2>발송 전에 확인하세요</h2>{["수신인과 발신인의 이름·주소가 정확한지", "계약 종료일·보증금·반환 기한이 실제 사실과 같은지", "계약서·입금내역·대화 기록을 별도로 보관했는지", "우체국 발송본과 배달증명 등 발송 기록을 보관할지"].map((item) => <p key={item}><Check size={16} />{item}</p>)}</section>
          <div className={styles.actions}><button className={styles.secondaryButton} type="button" onClick={() => window.print()}><Download size={16} />인쇄·PDF 저장</button><button className={styles.primaryButton} type="button" onClick={() => { setLoaded(false); setConfirmed(false); setStage("upload"); }}>새 내용증명 작성</button></div>
        </>
      )}
    </main>
  );
}
