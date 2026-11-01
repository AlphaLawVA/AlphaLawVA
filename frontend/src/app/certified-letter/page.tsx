"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Bookmark, Check, CheckCircle2, Download, FileText, LoaderCircle, ScanText, Upload } from "lucide-react";
import { PrecedentListItem, PrecedentModal } from "@/components/precedent-ui";
import { UploadGuide } from "@/components/upload-guide";
import {
  buildCertifiedLetterContract,
  createCertifiedLetterDraft,
  getCertifiedLetterDraft,
  mapCertifiedLetterPrecedent,
  saveCertifiedLetterResult,
} from "@/lib/certified-letter-api";
import type { CertifiedLetterResult } from "@/lib/certified-letter-api";
import { certifiedLetterContractSnapshot } from "@/lib/certified-letter-data";
import type { CaseBasis, ConfirmedContractSnapshot } from "@/lib/demo-types";
import { validateDemoFile } from "@/lib/demo-validation";
import { clearDocumentPreviews, setDocumentPreview } from "@/lib/document-preview-store";
import { createClient } from "@/lib/supabase/client";
import { VerifySnapshotDemo } from "../contract-review/verify/verify-snapshot-demo";
import styles from "../feature-demo.module.css";

type Stage = "upload" | "processing" | "verify" | "request" | "generating" | "result";

const samplePurpose = "계약 종료 후 보증금을 반환받고 싶습니다.";
const sampleFacts = "임대차계약이 종료되었지만 임대인이 보증금을 아직 반환하지 않았습니다.";

export default function CertifiedLetterPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("upload");
  const [loaded, setLoaded] = useState(false);
  const [progress, setProgress] = useState(8);
  const [senderRole, setSenderRole] = useState<"lessee" | "lessor">("lessee");
  const [purpose, setPurpose] = useState(samplePurpose);
  const [facts, setFacts] = useState(sampleFacts);
  const [requestedDeadline, setRequestedDeadline] = useState("");
  const [confirmedSnapshot, setConfirmedSnapshot] = useState<ConfirmedContractSnapshot>(certifiedLetterContractSnapshot);
  const [draftResult, setDraftResult] = useState<CertifiedLetterResult | null>(null);
  const [completedDraftId, setCompletedDraftId] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [saveError, setSaveError] = useState("");
  const [generationStatus, setGenerationStatus] = useState<"queued" | "processing">("queued");
  const [error, setError] = useState("");
  const [fileName, setFileName] = useState("");
  const [fileError, setFileError] = useState("");
  const [precedentModal, setPrecedentModal] = useState<{ precedent: CaseBasis; initialView: "detail" | "fullText" } | null>(null);
  const pollingTimer = useRef<number | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (pollingTimer.current !== null) window.clearTimeout(pollingTimer.current);
    };
  }, []);

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

  useEffect(() => {
    if (stage !== "generating") return;
    const timer = window.setInterval(
      () => setProgress((value) => Math.min(92, value + 1)),
      900,
    );
    return () => window.clearInterval(timer);
  }, [stage]);

  async function pollDraft(draftId: string) {
    try {
      const job = await getCertifiedLetterDraft(draftId);
      if (!mounted.current) return;
      if (job.status === "queued" || job.status === "processing") {
        setGenerationStatus(job.status);
        pollingTimer.current = window.setTimeout(() => void pollDraft(draftId), 1500);
        return;
      }
      if (job.status === "failed") {
        setError(job.error?.message ?? "내용증명 초안을 생성하지 못했습니다.");
        setStage("request");
        return;
      }
      if (job.outcome !== "draft_ready" || !job.result) {
        const missing = job.missing_information.length > 0
          ? ` 필요한 정보: ${job.missing_information.join(", ")}`
          : "";
        setError(`${job.message ?? "내용증명 초안을 생성하지 못했습니다."}${missing}`);
        setStage("request");
        return;
      }
      setDraftResult(job.result);
      setCompletedDraftId(job.draft_id);
      setProgress(100);
      setStage("result");
    } catch (pollError) {
      if (!mounted.current) return;
      setError(pollError instanceof Error ? pollError.message : "생성 상태를 확인하지 못했습니다.");
      setStage("request");
    }
  }

  async function generateLetter() {
    if (purpose.trim().length < 2) {
      setError("내용증명으로 달성하려는 목적을 조금 더 구체적으로 적어주세요.");
      return;
    }
    try {
      const contract = buildCertifiedLetterContract(confirmedSnapshot);
      setError("");
      setProgress(12);
      setGenerationStatus("queued");
      setStage("generating");
      const accepted = await createCertifiedLetterDraft(
        {
          sender_role: senderRole,
          purpose: purpose.trim(),
          facts: facts.trim(),
          requested_deadline: requestedDeadline || null,
          contract,
        },
        crypto.randomUUID(),
      );
      await pollDraft(accepted.draft_id);
    } catch (generationError) {
      setError(generationError instanceof Error ? generationError.message : "내용증명 생성을 요청하지 못했습니다.");
      setStage("request");
    }
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

  function resetForm() {
    if (pollingTimer.current !== null) window.clearTimeout(pollingTimer.current);
    clearDocumentPreviews();
    setLoaded(false);
    setFileName("");
    setDraftResult(null);
    setCompletedDraftId(null);
    setSaveStatus("idle");
    setSaveError("");
    setConfirmedSnapshot(certifiedLetterContractSnapshot);
    setSenderRole("lessee");
    setPurpose(samplePurpose);
    setFacts(sampleFacts);
    setRequestedDeadline("");
    setError("");
    setFileError("");
    setGenerationStatus("queued");
    setProgress(8);
    setStage("upload");
  }

  async function saveToLibrary() {
    if (!completedDraftId || saveStatus !== "idle") return;
    setSaveError("");
    setSaveStatus("saving");
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        setSaveStatus("idle");
        router.push("/login?next=/certified-letter");
        return;
      }
      await saveCertifiedLetterResult(completedDraftId, session.access_token);
      setSaveStatus("saved");
    } catch (saveResultError) {
      setSaveStatus("idle");
      setSaveError(
        saveResultError instanceof Error
          ? saveResultError.message
          : "내용증명 결과를 저장하지 못했습니다.",
      );
    }
  }

  const stepIndex = stage === "upload" || stage === "processing" ? 0 : stage === "verify" ? 1 : stage === "request" ? 2 : 3;
  const relatedPrecedents = draftResult?.similar_precedents.map(mapCertifiedLetterPrecedent) ?? [];

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

      {stage === "verify" && <VerifySnapshotDemo allowedContractTypes={["전세", "월세"]} initialContractSnapshot={confirmedSnapshot} modeOverride="contract" onBack={() => setStage("upload")} onComplete={(snapshot) => { setConfirmedSnapshot(snapshot); setStage("request"); }} submitLabel="요청 입력으로" />}

      {stage === "request" && (
        <div className={styles.split}>
          <section className={styles.panel}>
            <h2>요청과 사실관계를 알려주세요</h2>
            <p className={styles.panelLead}>계약서에서 확인되지 않는 현재 상황과 요구만 추가로 입력합니다.</p>
            <div className={styles.field}><label htmlFor="sender-role">내용증명 발신자</label><select id="sender-role" value={senderRole} onChange={(event) => setSenderRole(event.target.value as "lessee" | "lessor")}><option value="lessee">임차인</option><option value="lessor">임대인</option></select></div>
            <div className={styles.field}><label htmlFor="letter-purpose">내용증명으로 달성하려는 목적</label><textarea id="letter-purpose" value={purpose} onChange={(event) => setPurpose(event.target.value)} /></div>
            <div className={styles.field}><label htmlFor="letter-facts">확인된 구체적인 사실</label><textarea id="letter-facts" value={facts} onChange={(event) => setFacts(event.target.value)} /><p className={styles.searchGuide}>추측이나 평가보다 날짜, 금액, 상대방의 행동처럼 직접 확인한 내용을 적어주세요.</p></div>
            <div className={styles.field}><label htmlFor="requested-deadline">희망 이행 기한 (선택)</label><input id="requested-deadline" type="date" value={requestedDeadline} onChange={(event) => setRequestedDeadline(event.target.value)} /></div>
            {error && <div className={styles.validationNotice} role="alert"><AlertCircle size={19} /><div><strong>입력 내용을 확인해주세요</strong><span>{error}</span></div></div>}
            <div className={styles.actions}><button className={styles.secondaryButton} type="button" onClick={() => setStage("verify")}>이전</button><button className={styles.primaryButton} type="button" onClick={() => void generateLetter()}><FileText size={17} />초안 만들기</button></div>
          </section>
          <aside className={styles.sidePanel}><h2>계약서에서 이미 가져온 정보</h2><div className={styles.exampleList}><p><b>계약 기본정보</b><span>계약 종류, 기간, 목적물 주소</span></p><p><b>당사자 정보</b><span>임대인·임차인의 이름과 주소</span></p><p><b>금액 정보</b><span>보증금과 월세</span></p></div></aside>
        </div>
      )}

      {stage === "generating" && (
        <section className={styles.processingCard} aria-live="polite">
          <span><LoaderCircle className={styles.spinner} size={28} aria-hidden="true" /></span>
          <h2>내용증명 초안을 작성하고 있습니다</h2>
          <p>{generationStatus === "queued" ? "생성 요청을 접수하고 있습니다." : "확인된 계약정보와 요청을 정리하고 관련 법령·판례 근거를 연결하고 있습니다."}</p>
          <div className={styles.progressHeader}><span>{generationStatus === "queued" ? "생성 대기" : "문서 구조 및 근거 구성"}</span><strong>{progress}%</strong></div>
          <div className={styles.progressTrack}><i style={{ width: `${progress}%` }} /></div>
        </section>
      )}

      {stage === "result" && draftResult && (
        <>
          <div className={styles.notice}><AlertCircle size={18} /><span>이 문서는 AI가 계약서와 입력 내용을 바탕으로 작성한 초안입니다. 발송만으로 사실관계나 법적 효과가 확정되지는 않으므로 실제 정보와 최신 근거를 다시 확인하세요.</span></div>
          {draftResult.warnings.map((warning) => <div className={styles.notice} key={warning}><AlertCircle size={18} /><span>{warning}</span></div>)}
          <article className={styles.letterPaper}>
            <h2>{draftResult.document.title}</h2>
            <div className={styles.letterPartyGrid}><section><span>수신인</span><strong>{draftResult.document.recipient.name}</strong><p>{[draftResult.document.recipient.address, draftResult.document.recipient.detail_address].filter(Boolean).join(", ")}</p></section><section><span>발신인</span><strong>{draftResult.document.sender.name}</strong><p>{[draftResult.document.sender.address, draftResult.document.sender.detail_address].filter(Boolean).join(", ")}</p></section></div>
            <div className={styles.letterBody} style={{ whiteSpace: "pre-wrap" }}>{draftResult.document.body}</div>
          </article>
          <div className={styles.letterAnalysisGrid}>
            <section className={styles.panel}><h2>작성 전략</h2><p className={styles.panelLead}>{draftResult.strategy_summary}</p></section>
            <section className={styles.panel}><h2>다음 단계</h2><div className={styles.exampleList}>{draftResult.follow_up_actions.map((step) => <p key={`${step.order}-${step.action}`}><b>{String(step.order).padStart(2, "0")}</b><span>{step.action}</span></p>)}</div></section>
          </div>
          <section className={styles.panel} style={{ marginTop: 18 }}>
            <h2>관련 근거</h2>
            <details className={styles.detailDisclosure}>
              <summary>관련 법령 {draftResult.statute_evidence.length}건</summary>
              {draftResult.statute_evidence.length > 0 ? (
                <div className={styles.basisGrid}>
                  {draftResult.statute_evidence.map((statute) => (
                    <article className={styles.basisCard} key={`${statute.statute_id ?? statute.law_name}-${statute.article_label}`}>
                      <strong>{statute.law_name} {statute.article_label}</strong>
                      {statute.article_title && <p>{statute.article_title}</p>}
                      <span>{statute.explanation}</span>
                      <p style={{ whiteSpace: "pre-wrap" }}>{statute.content}</p>
                      {statute.source_url && <a href={statute.source_url} target="_blank" rel="noreferrer">원문 보기</a>}
                    </article>
                  ))}
                </div>
              ) : <p>최종 내용증명 본문에 직접 인용된 관련 법령이 없습니다.</p>}
            </details>
            <details className={styles.detailDisclosure}>
              <summary>관련 판례 {relatedPrecedents.length}건</summary>
              {relatedPrecedents.map((precedent) => <PrecedentListItem key={precedent.precedentId} onOpenDetail={() => setPrecedentModal({ precedent, initialView: "detail" })} onOpenFullText={() => setPrecedentModal({ precedent, initialView: "fullText" })} precedent={precedent} />)}
            </details>
          </section>
          <section className={styles.preflight}><h2>발송 전에 확인하세요</h2>{["수신인과 발신인의 이름·주소가 정확한지", "계약 종료일·보증금·반환 기한이 실제 사실과 같은지", "계약서·입금내역·대화 기록을 별도로 보관했는지", "우체국 발송본과 배달증명 등 발송 기록을 보관할지"].map((item) => <p key={item}><Check size={16} />{item}</p>)}</section>
          {saveError && <div className={styles.validationNotice} role="alert"><AlertCircle size={19} /><div><strong>보관함에 저장하지 못했습니다</strong><span>{saveError}</span></div></div>}
          <div className={styles.actions}><button className={styles.secondaryButton} type="button" onClick={() => window.print()}><Download size={16} />인쇄·PDF 저장</button><button className={styles.secondaryButton} type="button" disabled={saveStatus !== "idle"} onClick={() => void saveToLibrary()}><Bookmark size={16} />{saveStatus === "saving" ? "저장 중..." : saveStatus === "saved" ? "보관함에 저장됨" : "보관함에 저장"}</button><button className={styles.primaryButton} type="button" onClick={resetForm}>새 내용증명 작성</button></div>
          {precedentModal && <PrecedentModal initialView={precedentModal.initialView} onClose={() => setPrecedentModal(null)} precedent={precedentModal.precedent} />}
        </>
      )}
    </main>
  );
}
