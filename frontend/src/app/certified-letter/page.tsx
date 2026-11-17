"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Bookmark, Check, CheckCircle2, Download, FileText, Info, LoaderCircle, ScanText, Upload } from "lucide-react";
import { LiveProgress } from "@/components/live-progress";
import { PrecedentListItem, PrecedentModal } from "@/components/precedent-ui";
import { UploadGuide } from "@/components/upload-guide";
import { createContractOcrDraft, mapContractOcrDraftToSnapshot } from "@/lib/contract-ocr-api";
import {
  CERTIFIED_LETTER_FOLLOW_UP_TITLES,
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
// 계약서 검토의 OCR 동의 문구·확인 화면과 디자인을 맞추기 위해 같은 스타일시트를 함께 쓴다.
import reviewStyles from "../contract-review/contract-review.module.css";
import styles from "../feature-demo.module.css";

type Stage = "upload" | "processing" | "verify" | "request" | "generating" | "result";
type DeadlineMode = "date" | "none" | "";

const purposeExample = "예: 계약 종료 후 보증금 전액을 반환받고 싶습니다.";
const factsExample =
  "예: 계약서에 기재된 종료일이 지났지만 보증금을 돌려받지 못했습니다. 임대인에게 반환을 요청했지만 아직 지급되지 않았습니다.";

// 계약서 검토의 OCR 분석 화면과 같은 단계 구성(copy 포함)을 그대로 쓴다.
const ocrStages = [
  { at: 12, label: "업로드한 문서 형식을 확인하고 있습니다" },
  { at: 30, label: "Google Document AI가 글자와 표 영역을 인식하고 있습니다" },
  { at: 85, label: "계약정보를 사용자 확인 항목으로 구조화하고 있습니다" },
  { at: 100, label: "확인 화면을 준비하고 있습니다" },
];

export default function CertifiedLetterPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("upload");
  const [loaded, setLoaded] = useState(false);
  const [progress, setProgress] = useState(8);
  const [senderRole, setSenderRole] = useState<"lessee" | "lessor" | "buyer" | "seller">("lessee");
  const [purpose, setPurpose] = useState("");
  const [facts, setFacts] = useState("");
  const [deadlineMode, setDeadlineMode] = useState<DeadlineMode>("");
  const [requestedDeadline, setRequestedDeadline] = useState("");
  const [senderDeliveryAddress, setSenderDeliveryAddress] = useState("");
  const [recipientDeliveryAddress, setRecipientDeliveryAddress] = useState("");
  const [confirmedSnapshot, setConfirmedSnapshot] = useState<ConfirmedContractSnapshot>(certifiedLetterContractSnapshot);
  const [draftResult, setDraftResult] = useState<CertifiedLetterResult | null>(null);
  const [generatedAt, setGeneratedAt] = useState<Date | null>(null);
  const [completedDraftId, setCompletedDraftId] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [saveError, setSaveError] = useState("");
  const [generationStatus, setGenerationStatus] = useState<"queued" | "processing">("queued");
  const [error, setError] = useState("");
  const [fileName, setFileName] = useState("");
  const [fileError, setFileError] = useState("");
  const [contractFile, setContractFile] = useState<File | null>(null);
  const [contractType, setContractType] = useState<"jeonse" | "monthly_rent" | "sale">("jeonse");
  const [providerConsent, setProviderConsent] = useState(false);
  const [isRealOcr, setIsRealOcr] = useState(false);
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

  // 샘플 계약서(실제 OCR 없음)일 때만 가짜 진행률로 바로 확인 화면까지 넘어간다.
  useEffect(() => {
    if (stage !== "processing" || isRealOcr) return;
    const timer = window.setInterval(() => setProgress((value) => Math.min(100, value + 5)), 120);
    return () => window.clearInterval(timer);
  }, [stage, isRealOcr]);

  useEffect(() => {
    if (stage !== "processing" || isRealOcr || progress < 100) return;
    const timer = window.setTimeout(() => setStage("verify"), 350);
    return () => window.clearTimeout(timer);
  }, [progress, stage, isRealOcr]);

  // 실제 OCR 요청 중에는 응답이 올 때까지 92%에서 기다린다.
  useEffect(() => {
    if (stage !== "processing" || !isRealOcr) return;
    const timer = window.setInterval(() => setProgress((value) => Math.min(92, value + 4)), 250);
    return () => window.clearInterval(timer);
  }, [stage, isRealOcr]);

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
      setGeneratedAt(new Date());
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
    if (!deadlineMode) {
      setError("이행 기한을 지정할지, 기한 없이 작성할지 선택해 주세요.");
      return;
    }
    if (deadlineMode === "date" && !requestedDeadline) {
      setError("상대방에게 요구할 이행 기한을 선택해 주세요.");
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
          requested_deadline: deadlineMode === "date" ? requestedDeadline : null,
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
    const validationError = validateDemoFile(file)
      || (file.name.split(".").pop()?.toLowerCase() !== "pdf"
        ? "현재 실시간 OCR은 3페이지 표준 계약서 PDF만 지원합니다."
        : "");
    setFileError(validationError);
    setLoaded(!validationError);
    setFileName(validationError ? "" : file.name);
    setContractFile(validationError ? null : file);
    setProviderConsent(false);
    if (!validationError) {
      clearDocumentPreviews();
      setDocumentPreview("contract", file);
    }
  }

  async function runContractOcr(file: File) {
    try {
      const draft = await createContractOcrDraft(file, contractType);
      if (!mounted.current) return;
      setConfirmedSnapshot(mapContractOcrDraftToSnapshot(draft));
      setProgress(100);
      window.setTimeout(() => {
        if (mounted.current) setStage("verify");
      }, 350);
    } catch (ocrError) {
      if (!mounted.current) return;
      setFileError(ocrError instanceof Error ? ocrError.message : "계약정보를 읽지 못했습니다.");
      setIsRealOcr(false);
      setStage("upload");
    }
  }

  function startReading() {
    setFileError("");
    if (contractFile) {
      setIsRealOcr(true);
      setProgress(12);
      setStage("processing");
      void runContractOcr(contractFile);
    } else {
      setIsRealOcr(false);
      setProgress(8);
      setStage("processing");
    }
  }

  function resetForm() {
    if (pollingTimer.current !== null) window.clearTimeout(pollingTimer.current);
    clearDocumentPreviews();
    setLoaded(false);
    setFileName("");
    setContractFile(null);
    setContractType("jeonse");
    setProviderConsent(false);
    setIsRealOcr(false);
    setDraftResult(null);
    setGeneratedAt(null);
    setCompletedDraftId(null);
    setSaveStatus("idle");
    setSaveError("");
    setConfirmedSnapshot(certifiedLetterContractSnapshot);
    setSenderRole("lessee");
    setPurpose("");
    setFacts("");
    setDeadlineMode("");
    setRequestedDeadline("");
    setSenderDeliveryAddress("");
    setRecipientDeliveryAddress("");
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

  const isSale = confirmedSnapshot.contractType === "매매";
  const contractAmountSummary = isSale
    ? "매매대금, 계약금·중도금·잔금(있는 경우)"
    : confirmedSnapshot.contractType === "월세"
      ? "보증금과 월세"
      : "전세보증금";
  const stepIndex = stage === "upload" || stage === "processing" ? 0 : stage === "verify" ? 1 : stage === "request" ? 2 : 3;
  const currentOcrStageIndex = Math.max(
    0,
    ocrStages.reduce((index, ocrStage, nextIndex) => (progress >= ocrStage.at ? nextIndex : index), 0),
  );
  const relatedPrecedents = draftResult?.similar_precedents.map(mapCertifiedLetterPrecedent) ?? [];
  const generatedDate = generatedAt
    ? new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric" }).format(generatedAt)
    : "";

  return (
    <main className={stage === "verify" ? `${styles.page} ${styles.pageWide}` : styles.page}>
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
              <button className={styles.textButton} type="button" onClick={() => { clearDocumentPreviews(); setLoaded(true); setFileName("전세_손글씨_샘플.pdf"); setFileError(""); setContractFile(null); setProviderConsent(false); }}><FileText size={16} />샘플 계약서 사용</button>
            </div>
            {fileError && <div className={styles.validationNotice} role="alert"><AlertCircle size={19} /><div><strong>파일을 읽을 수 없습니다</strong><span>{fileError}</span></div></div>}
            {loaded && <div className={styles.uploadedFile}><div><FileText size={17} /><span>{fileName || "전세_손글씨_샘플.pdf"}</span></div><CheckCircle2 size={18} /></div>}
            {contractFile && (
              <>
                <div className={styles.field}>
                  <label htmlFor="contract-type">계약 유형</label>
                  <select id="contract-type" value={contractType} onChange={(event) => setContractType(event.target.value as typeof contractType)}>
                    <option value="jeonse">전세</option>
                    <option value="monthly_rent">월세</option>
                    <option value="sale">매매</option>
                  </select>
                </div>
                <label className={reviewStyles.consentRow}>
                  <input checked={providerConsent} type="checkbox" onChange={(event) => setProviderConsent(event.target.checked)} />
                  <span className={reviewStyles.consentCopy}>
                    <strong>Google Document AI 전송에 동의합니다</strong>
                    <span>OCR을 위해 선택한 계약서 PDF가 Google LLC의 미국 us 리전으로 전송됩니다.</span>
                    <small>서버는 원본 파일과 OCR 원문을 처리 후 저장하지 않습니다. 동의하지 않으면 실시간 OCR을 사용할 수 없습니다.</small>
                  </span>
                </label>
              </>
            )}
            <div className={styles.actions}><button className={styles.primaryButton} type="button" disabled={!loaded || (contractFile !== null && !providerConsent)} onClick={startReading}><ScanText size={17} />계약정보 읽기</button></div>
          </section>
          <UploadGuide purpose="letter" />
        </div>
      )}

      {stage === "processing" && (
        <section className={reviewStyles.processingPanel} style={{ margin: "0 auto" }}>
          <span className={reviewStyles.processingIcon}><ScanText size={30} aria-hidden="true" /></span>
          <p className={reviewStyles.processingEyebrow}>문서 분석 중</p>
          <h1>업로드한 문서를 읽고 있습니다</h1>
          <p>{isRealOcr ? "Google Document AI가 문서를 인식하고 있습니다. 이 화면을 닫거나 새로고침하지 말고 잠시 기다려주세요." : "페이지 수와 글씨 상태에 따라 시간이 조금 더 걸릴 수 있습니다. 이 화면을 닫지 말고 잠시 기다려주세요."}</p>
          <div className={reviewStyles.progressHeader}><span>{ocrStages[currentOcrStageIndex].label}</span><strong>{progress}%</strong></div>
          <div className={`${reviewStyles.progressTrack} ${reviewStyles.progressTrackActive}`}><span style={{ width: `${progress}%` }} /></div>
          <LiveProgress waitingHint="문서 인식에 시간이 걸리고 있습니다. 서버가 처리 중이니 닫지 말고 기다려주세요." />
          <ol className={reviewStyles.processingSteps}>
            {ocrStages.map((ocrStage, index) => {
              const done = progress === 100 || index < currentOcrStageIndex;
              return <li className={done ? reviewStyles.processingDone : ""} key={ocrStage.label}><i>{done ? <Check size={14} /> : index + 1}</i><span>{ocrStage.label}</span></li>;
            })}
          </ol>
        </section>
      )}

      {stage === "verify" && <VerifySnapshotDemo allowedContractTypes={["전세", "월세", "매매"]} initialContractSnapshot={confirmedSnapshot} lockContractType={isRealOcr} modeOverride="contract" onBack={() => { setIsRealOcr(false); setStage("upload"); }} onComplete={(snapshot) => { setConfirmedSnapshot(snapshot); setSenderRole(snapshot.contractType === "매매" ? "buyer" : "lessee"); setStage("request"); }} submitLabel="요청 입력으로" />}

      {stage === "request" && (
        <div className={styles.split}>
          <section className={styles.panel}>
            <h2>요청과 사실관계를 알려주세요</h2>
            <p className={styles.panelLead}>계약서에 없는 현재 상황과 요구사항을 추가로 입력해 주세요. 법률 용어를 몰라도 괜찮습니다. 원하는 결과와 실제 있었던 일을 나누어 적어주세요.</p>
            <div className={styles.field}><label htmlFor="sender-role">내용증명 발신자</label><select id="sender-role" value={senderRole} onChange={(event) => setSenderRole(event.target.value as "lessee" | "lessor" | "buyer" | "seller")}>{confirmedSnapshot.contractType === "매매" ? <><option value="buyer">매수인</option><option value="seller">매도인</option></> : <><option value="lessee">임차인</option><option value="lessor">임대인</option></>}</select></div>
            <div className={styles.field}>
              <label htmlFor="letter-purpose">상대방에게 무엇을 요구하고 싶나요?</label>
              <p className={styles.fieldGuide} id="letter-purpose-guide">보증금 반환, 수리, 계약 해지 등 내용증명을 통해 원하는 결과를 간단히 적어주세요.</p>
              <textarea aria-describedby="letter-purpose-guide" id="letter-purpose" placeholder={purposeExample} value={purpose} onChange={(event) => setPurpose(event.target.value)} />
            </div>
            <div className={styles.field}>
              <label htmlFor="letter-facts">어떤 일이 있었는지 알려주세요.</label>
              <p className={styles.fieldGuide} id="letter-facts-guide">계약일, 문제 발생 시점, 상대방에게 요청한 내용과 현재 상황을 확인된 사실 중심으로 적어주세요.</p>
              <textarea aria-describedby="letter-facts-guide" id="letter-facts" placeholder={factsExample} value={facts} onChange={(event) => setFacts(event.target.value)} />
            </div>
            <div className={styles.field}>
              <fieldset className={styles.deadlineFieldset}>
                <legend>희망 이행 기한</legend>
                <p className={styles.fieldGuide} id="requested-deadline-guide">
                  상대방이 이행해야 할 사항이 있는 경우에는 기한을 지정해 주세요.<br />
                  별도의 이행 요구 없이 의사를 통지하는 경우에는 기한을 지정하지 않을 수 있습니다.
                </p>
                <div className={styles.deadlineChoices}>
                  <label className={deadlineMode === "date" ? styles.deadlineChoiceActive : undefined}>
                    <input
                      checked={deadlineMode === "date"}
                      name="deadline-mode"
                      type="radio"
                      value="date"
                      onChange={() => {
                        setDeadlineMode("date");
                        setError("");
                      }}
                    />
                    <span>기한 지정</span>
                  </label>
                  <label className={deadlineMode === "none" ? styles.deadlineChoiceActive : undefined}>
                    <input
                      checked={deadlineMode === "none"}
                      name="deadline-mode"
                      type="radio"
                      value="none"
                      onChange={() => {
                        setDeadlineMode("none");
                        setRequestedDeadline("");
                        setError("");
                      }}
                    />
                    <span>기한 미지정 (의사 통지)</span>
                  </label>
                </div>
                {deadlineMode === "date" && (
                  <input
                    aria-describedby="requested-deadline-guide"
                    id="requested-deadline"
                    type="date"
                    value={requestedDeadline}
                    onChange={(event) => {
                      setRequestedDeadline(event.target.value);
                      setError("");
                    }}
                  />
                )}
                {deadlineMode === "none" && (
                  <p className={styles.deadlineWarning} role="note">
                    <Info size={17} aria-hidden="true" />
                    상대방에게 지급·반환 등 이행을 요구한다면 기한을 지정해 주세요. 이 선택은 별도의 이행 요구 없이 의사를 통지하는 경우에 적합합니다.
                  </p>
                )}
              </fieldset>
            </div>
            {error && <div className={styles.validationNotice} role="alert"><AlertCircle size={19} /><div><strong>입력 내용을 확인해주세요</strong><span>{error}</span></div></div>}
            <div className={styles.actions}><button className={styles.secondaryButton} type="button" onClick={() => setStage("verify")}>이전</button><button className={styles.primaryButton} type="button" onClick={() => void generateLetter()}><FileText size={17} />초안 만들기</button></div>
          </section>
          <aside className={styles.sidePanel}>
            <h2>확인된 계약 정보</h2>
            <div className={styles.exampleList}>
              <p>
                <b>계약 기본정보</b>
                <span>{isSale
                  ? "계약일, 계약한 집의 주소, 인도일·소유권 이전일(있는 경우)"
                  : "계약일, 임대차 기간, 계약한 집의 주소"}</span>
              </p>
              <p>
                <b>당사자 정보</b>
                <span>{isSale ? "매도인·매수인의 이름과 주소" : "임대인·임차인의 이름과 주소"}</span>
              </p>
              <p><b>금액 정보</b><span>{contractAmountSummary}</span></p>
              <p><b>특약 사항</b><span>앞에서 확인한 계약서 특약</span></p>
            </div>
          </aside>
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
          <section className={styles.deliveryInfo} aria-labelledby="delivery-info-title">
            <div>
              <h2 id="delivery-info-title">발송 정보 입력</h2>
              <p>내용증명에 적을 실제 발신·수신 주소를 입력해 주세요. 계약 목적물 주소와 다를 수 있으며, 입력한 주소는 문서와 PDF에만 반영됩니다.</p>
            </div>
            <div className={styles.deliveryAddressFields}>
              <label htmlFor="sender-delivery-address">
                <span>발신인 송달 주소</span>
                <strong>{draftResult.document.sender.name}</strong>
                <input
                  id="sender-delivery-address"
                  placeholder="예: 서울특별시 ○○구 ○○로 00, ○○동 ○○호"
                  value={senderDeliveryAddress}
                  onChange={(event) => setSenderDeliveryAddress(event.target.value)}
                />
              </label>
              <label htmlFor="recipient-delivery-address">
                <span>수신인 송달 주소</span>
                <strong>{draftResult.document.recipient.name}</strong>
                <input
                  id="recipient-delivery-address"
                  placeholder="예: 서울특별시 ○○구 ○○로 00, ○○동 ○○호"
                  value={recipientDeliveryAddress}
                  onChange={(event) => setRecipientDeliveryAddress(event.target.value)}
                />
              </label>
            </div>
          </section>
          <article className={styles.letterPaper}>
            <h2 className={styles.letterDocumentHeading}>내용증명</h2>
            <dl className={styles.letterParties}>
              <div>
                <dt>발신인</dt>
                <dd><strong>{draftResult.document.sender.name}</strong>{senderDeliveryAddress && <span>{senderDeliveryAddress}</span>}</dd>
              </div>
              <div>
                <dt>수신인</dt>
                <dd><strong>{draftResult.document.recipient.name}</strong>{recipientDeliveryAddress && <span>{recipientDeliveryAddress}</span>}</dd>
              </div>
            </dl>
            <div className={styles.letterSubject}><span>제목</span><strong>{draftResult.document.title}</strong></div>
            <div className={styles.letterBody} style={{ whiteSpace: "pre-wrap" }}>{draftResult.document.body}</div>
            <footer className={styles.letterClosing}>
              <p><span>작성일</span><strong>{generatedDate}</strong></p>
              <p><span>발신인</span><strong>{draftResult.document.sender.name} (서명 또는 날인)</strong></p>
            </footer>
          </article>
          <div className={styles.draftNotice}>
            <Info size={18} aria-hidden="true" />
            <div className={styles.draftNoticeCopy}>
              <strong>발송 전 확인해 주세요</strong>
              <p>이 문서는 AI가 작성한 법률 검토 전 초안입니다.</p>
              <p>
                내용증명에 작성한 내용은 향후 분쟁이나 소송에서 중요한 자료로 쓰일 수 있으며, 의도와 다르게 해석될 여지가 있습니다.
                발송 전에 사실관계와 표현을 꼼꼼히 살펴보시고, 필요시 법률 전문가의 도움을 받아보세요.
              </p>
            </div>
          </div>
          <div className={styles.letterAnalysisGrid}>
            <section className={styles.panel}><h2>작성 전략</h2><p className={styles.panelLead}>{draftResult.strategy_summary}</p></section>
            <section className={styles.panel}><h2>다음 단계</h2><div className={styles.exampleList}>{draftResult.follow_up_actions.map((step) => <p key={`${step.order}-${step.action}`}><b>{String(step.order).padStart(2, "0")} {CERTIFIED_LETTER_FOLLOW_UP_TITLES[step.order - 1] ?? "다음 단계"}</b><span>{step.action}</span></p>)}</div></section>
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
