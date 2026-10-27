"use client";

import { useEffect, useState } from "react";
import { AlertCircle, Check, CheckCircle2, Download, FileText, LoaderCircle, ScanText, Upload } from "lucide-react";
import { PrecedentListItem, PrecedentModal } from "@/components/precedent-ui";
import { UploadGuide } from "@/components/upload-guide";
import { certifiedLetterContractSnapshot, certifiedLetterPrecedents, certifiedLetterResult, certifiedLetterSampleRequest, certifiedLetterStatutes } from "@/lib/certified-letter-data";
import type { CaseBasis } from "@/lib/demo-types";
import { validateDemoFile, validateRealEstateInput } from "@/lib/demo-validation";
import { clearDocumentPreviews, setDocumentPreview } from "@/lib/document-preview-store";
import { VerifySnapshotDemo } from "../contract-review/verify/verify-snapshot-demo";
import styles from "../feature-demo.module.css";

type Stage = "upload" | "processing" | "verify" | "request" | "generating" | "result";
const sampleRequest = certifiedLetterSampleRequest;

export default function CertifiedLetterPage() {
  const [stage, setStage] = useState<Stage>("upload");
  const [loaded, setLoaded] = useState(false);
  const [progress, setProgress] = useState(8);
  const [request, setRequest] = useState(sampleRequest);
  const [requestFocused, setRequestFocused] = useState(false);
  const [error, setError] = useState("");
  const [fileName, setFileName] = useState("");
  const [fileError, setFileError] = useState("");
  const [precedentModal, setPrecedentModal] = useState<{ precedent: CaseBasis; initialView: "detail" | "fullText" } | null>(null);

  useEffect(() => {
    if (stage !== "processing" && stage !== "generating") return;
    const timer = window.setInterval(() => setProgress((value) => Math.min(100, value + 5)), 120);
    return () => window.clearInterval(timer);
  }, [stage]);

  useEffect(() => {
    if ((stage !== "processing" && stage !== "generating") || progress < 100) return;
    const nextStage = stage === "processing" ? "verify" : "result";
    const timer = window.setTimeout(() => setStage(nextStage), 350);
    return () => window.clearTimeout(timer);
  }, [progress, stage]);

  function generateLetter() {
    const validationError = validateRealEstateInput(request, 35);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError("");
    setProgress(10);
    setStage("generating");
  }

  function selectFile(file?: File) {
    if (!file) return;
    const validationError = validateDemoFile(file);
    setFileError(validationError);
    setLoaded(!validationError);
    setFileName(validationError ? "" : file.name);
    if (!validationError) {
      clearDocumentPreviews();
      setDocumentPreview("contract", file);
    }
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
              <label className={styles.secondaryButton}><Upload size={17} />내 파일 선택<input className={styles.fileInput} type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={(event) => selectFile(event.target.files?.[0])} /></label>
              <button className={styles.textButton} type="button" onClick={() => { clearDocumentPreviews(); setLoaded(true); setFileName("전세_손글씨_샘플.pdf"); setFileError(""); }}><FileText size={16} />샘플 계약서 사용</button>
            </div>
            {fileError && <div className={styles.validationNotice} role="alert"><AlertCircle size={19} /><div><strong>파일을 읽을 수 없습니다</strong><span>{fileError}</span></div></div>}
            {loaded && <div className={styles.uploadedFile}><div><FileText size={17} /><span>{fileName || "전세_손글씨_샘플.pdf"}</span></div><CheckCircle2 size={18} /></div>}
            <div className={styles.actions}><button className={styles.primaryButton} type="button" disabled={!loaded} onClick={() => { setProgress(8); setStage("processing"); }}><ScanText size={17} />계약정보 읽기</button></div>
          </section>
          <UploadGuide purpose="letter" />
        </div>
      )}

      {stage === "processing" && (
        <section className={styles.processingCard}><span><ScanText size={28} /></span><h2>계약정보를 읽고 있습니다</h2><p>당사자, 목적물, 금액과 주요 날짜를 구조화하고 있습니다.</p><div className={styles.progressHeader}><span>계약서 OCR 및 필드 추출</span><strong>{progress}%</strong></div><div className={styles.progressTrack}><i style={{ width: `${progress}%` }} /></div></section>
      )}

      {stage === "verify" && <VerifySnapshotDemo initialContractSnapshot={certifiedLetterContractSnapshot} modeOverride="contract" onBack={() => setStage("upload")} onComplete={() => setStage("request")} submitLabel="요청 입력으로" />}

      {stage === "request" && (
        <div className={styles.split}>
          <section className={styles.panel}><h2>상대방에게 무엇을 전달할까요?</h2><p className={styles.panelLead}>계약정보는 이미 확인했습니다. 있었던 일과 요구사항, 이행 기한을 자유롭게 적어주세요.</p><div className={styles.field}><label htmlFor="letter-request">상황과 요청</label><textarea id="letter-request" onBlur={() => setRequestFocused(false)} onFocus={() => setRequestFocused(true)} placeholder={requestFocused ? "" : sampleRequest} value={request} onChange={(event) => setRequest(event.target.value)} /></div>{error && <div className={styles.notice}><AlertCircle size={18} /><span>{error}</span></div>}<div className={styles.actions}><button className={styles.secondaryButton} type="button" onClick={() => setStage("verify")}>이전</button><button className={styles.primaryButton} type="button" onClick={generateLetter}><FileText size={17} />초안 만들기</button></div></section>
          <aside className={styles.sidePanel}><h2>이 내용을 포함하면 좋습니다</h2><div className={styles.exampleList}><p><b>문제가 생긴 사실</b><span>계약이 끝났지만 보증금을 받지 못했습니다.</span></p><p><b>구체적인 요구</b><span>보증금 전액을 반환해 주세요.</span></p><p><b>이행 기한</b><span>문서를 받은 날부터 7일 이내처럼 적어주세요.</span></p></div></aside>
        </div>
      )}

      {stage === "generating" && (
        <section className={styles.processingCard}>
          <span><LoaderCircle className={styles.spinner} size={28} aria-hidden="true" /></span>
          <h2>내용증명 초안을 작성하고 있습니다</h2>
          <p>확인된 계약정보와 요청을 정리하고 관련 법령·판례 근거를 연결하고 있습니다.</p>
          <div className={styles.progressHeader}><span>문서 구조 및 근거 구성</span><strong>{progress}%</strong></div>
          <div className={styles.progressTrack}><i style={{ width: `${progress}%` }} /></div>
        </section>
      )}

      {stage === "result" && (
        <>
          <div className={styles.notice}><AlertCircle size={18} /><span>이 문서는 AI가 계약서와 입력 내용을 바탕으로 작성한 초안입니다. 발송만으로 사실관계나 법적 효과가 확정되지는 않으므로 실제 정보와 최신 근거를 다시 확인하세요.</span></div>
          <article className={styles.letterPaper}>
            <h2>{certifiedLetterResult.title}</h2>
            <div className={styles.letterPartyGrid}><section><span>수신인</span><strong>{certifiedLetterResult.recipient.name}</strong><p>{certifiedLetterResult.recipient.address}, {certifiedLetterResult.recipient.detailAddress}</p></section><section><span>발신인</span><strong>{certifiedLetterResult.sender.name}</strong><p>{certifiedLetterResult.sender.address}, {certifiedLetterResult.sender.detailAddress}</p></section></div>
            <div className={styles.letterBody} style={{ whiteSpace: "pre-wrap" }}>{certifiedLetterResult.body}</div>
          </article>
          <div className={styles.letterAnalysisGrid}>
            <section className={styles.panel}><h2>작성 전략</h2><p className={styles.panelLead}>{certifiedLetterResult.strategy}</p></section>
            <section className={styles.panel}><h2>다음 단계</h2><div className={styles.exampleList}>{certifiedLetterResult.nextSteps.map((step, index) => <p key={step}><b>{String(index + 1).padStart(2, "0")}</b><span>{step}</span></p>)}</div></section>
          </div>
          <section className={styles.panel} style={{ marginTop: 18 }}>
            <h2>관련 근거</h2>
            <details className={styles.detailDisclosure}>
              <summary>관련 법령 {certifiedLetterStatutes.length}건</summary>
              <div className={styles.basisGrid}>
                {certifiedLetterStatutes.map((statute) => <article className={styles.basisCard} key={`${statute.lawName}-${statute.articleLabel}`}><strong>{statute.lawName} {statute.articleLabel}</strong><p>{statute.articleTitle}</p><span>{statute.explanation}</span><p style={{ whiteSpace: "pre-wrap" }}>{statute.content}</p><a href={statute.url} target="_blank" rel="noreferrer">원문 보기</a></article>)}
              </div>
            </details>
            <details className={styles.detailDisclosure}>
              <summary>관련 판례 {certifiedLetterPrecedents.length}건</summary>
              {certifiedLetterPrecedents.map((precedent) => <PrecedentListItem key={precedent.caseNumber} onOpenDetail={() => setPrecedentModal({ precedent, initialView: "detail" })} onOpenFullText={() => setPrecedentModal({ precedent, initialView: "fullText" })} precedent={precedent} />)}
            </details>
          </section>
          <section className={styles.preflight}><h2>발송 전에 확인하세요</h2>{["수신인과 발신인의 이름·주소가 정확한지", "계약 종료일·보증금·반환 기한이 실제 사실과 같은지", "계약서·입금내역·대화 기록을 별도로 보관했는지", "우체국 발송본과 배달증명 등 발송 기록을 보관할지"].map((item) => <p key={item}><Check size={16} />{item}</p>)}</section>
          <div className={styles.actions}><button className={styles.secondaryButton} type="button" onClick={() => window.print()}><Download size={16} />인쇄·PDF 저장</button><button className={styles.primaryButton} type="button" onClick={() => { clearDocumentPreviews(); setLoaded(false); setStage("upload"); }}>새 내용증명 작성</button></div>
          {precedentModal && <PrecedentModal initialView={precedentModal.initialView} onClose={() => setPrecedentModal(null)} precedent={precedentModal.precedent} />}
        </>
      )}
    </main>
  );
}
