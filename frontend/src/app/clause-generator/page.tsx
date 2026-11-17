"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowRight, ExternalLink, LoaderCircle, Plus, X } from "lucide-react";
import { PrecedentListItem, PrecedentModal } from "@/components/precedent-ui";
import { SaveGenerationResultButton } from "@/components/save-generation-result-button";
import { StatuteExcerpt } from "@/components/statute-excerpt";
import { statuteOfficialUrl } from "@/lib/statute-link";
import { lessorMaintenanceLaw, restorationCase, restorationLaw } from "@/lib/mock-data";
import type { CaseBasis, LegalBasis } from "@/lib/demo-types";
import { hasContradictoryRequests, validateRealEstateInput } from "@/lib/demo-validation";
import { contractEvidenceToPrecedent, generateSpecialClauseDraft, type ContractEvidence, type SpecialClauseDraftResult } from "@/lib/contract-api";
import { isAbortError } from "@/lib/job-polling";
import { getPrecedentFullText, mergePrecedentFullText } from "@/lib/precedent-api";
import styles from "../feature-demo.module.css";

// 서버가 한 번에 받는 요청 개수 상한(api/models/contracts.py의
// SpecialClauseDraftRequest.requests max_length)과 같은 값으로 맞춘다.
const MAX_REQUESTS = 10;

type Stage = "input" | "review" | "generating" | "result";

// 서버가 알려 주는 처리 단계(scope, retrieve, generate, validate, repair)를 화면 문구와 진행도로 바꾼다.
const GENERATION_STAGES: Record<string, { label: string; progress: number }> = {
  scope: { label: "요청이 특약 작성 대상인지 확인하고 있습니다", progress: 15 },
  retrieve: { label: "관련 법령과 판례를 찾고 있습니다", progress: 40 },
  generate: { label: "특약 초안을 작성하고 있습니다", progress: 70 },
  validate: { label: "초안을 검증하고 있습니다", progress: 90 },
  repair: { label: "초안을 다듬고 있습니다", progress: 90 },
};
const QUEUED_STAGE = { label: "작업을 준비하고 있습니다", progress: 8 };
type RequestItem = { id: number; value: string };
type GeneratedClause = {
  title: string;
  clause: string;
  userInput: string;
  strategy: string;
  followUps?: string[];
  statutes: LegalBasis[];
  precedents: CaseBasis[];
};

function buildClause(request: string, index: number): GeneratedClause {
  const normalized = request.replace(/\s+/g, " ").trim();

  if (/반려|강아지|고양이|반려견|반려묘/.test(normalized)) {
    return {
      title: "반려동물 사육",
      clause: "임대인은 임차인이 반려동물 1마리를 사육하는 데 동의한다. 임차인은 사육으로 인해 통상적인 사용 범위를 넘는 시설 훼손이 발생한 경우 그 부분을 원상회복한다.",
      userInput: normalized,
      strategy: "동물의 종류와 수, 공용공간 이용 수칙, 입주 당시 마감재 상태를 함께 기록해 두는 방안을 협의하세요.",
      statutes: [restorationLaw],
      precedents: [restorationCase],
    };
  }

  if (/못|타공|벽|액자|선반/.test(normalized)) {
    return {
      title: "벽면 타공",
      clause: "임차인은 생활에 필요한 범위에서 벽면을 타공할 수 있다. 퇴거 시 통상적인 보수가 가능한 타공은 임대인과 보수 방법을 협의하고, 타일·대리석 등 복구가 어려운 마감재는 사전 동의를 받는다.",
      userInput: normalized,
      strategy: "허용 위치와 개수, 제외할 벽면을 사진에 표시하고 퇴거 시 보수 방법을 구체적으로 정하세요.",
      statutes: [restorationLaw],
      precedents: [restorationCase],
    };
  }

  if (/담배|흡연/.test(normalized)) {
    return {
      title: "실내 흡연",
      clause: "임차인은 실내 흡연 여부와 그 범위를 임대인과 합의하고, 흡연으로 인해 통상적인 사용 범위를 넘는 벽지 변색이나 냄새 제거 비용이 발생한 경우 실제 손상 부분의 복구 방법을 협의한다.",
      userInput: normalized,
      strategy: "실내 전체 또는 지정 공간 중 어디까지 허용할지, 입주 전 상태를 어떤 방식으로 기록할지 함께 정하세요.",
      statutes: [restorationLaw],
      precedents: [restorationCase],
    };
  }

  if (/보일러|수도|누수|수리|고장/.test(normalized)) {
    return {
      title: "시설 유지·수리",
      clause: "보일러, 수도, 배관 등 목적물의 기본 시설에 고장이 발생한 경우 임대인은 임차인의 고의 또는 과실로 발생한 고장을 제외하고 지체 없이 수리한다. 임차인은 고장 사실을 확인한 즉시 임대인에게 알린다.",
      userInput: normalized,
      strategy: "수리 요청 방법과 긴급 수리가 필요한 경우의 연락 수단, 임차인 과실 판단 기준을 함께 정하세요.",
      statutes: [lessorMaintenanceLaw],
      precedents: [],
    };
  }

  return {
    title: `요청 ${index + 1} 반영`,
    clause: `임대인과 임차인은 다음 사항에 관하여 별도로 합의한다. ${normalized} 구체적인 이행 방법과 책임 범위는 계약 체결 전에 상호 확인하여 기재한다.`,
    userInput: normalized,
    strategy: "누가, 언제까지, 어떤 범위에서 이행하는지와 위반했을 때의 처리 방법을 상대방과 구체적으로 협의하세요.",
    statutes: [],
    precedents: [],
  };
}

function evidenceToLaw(evidence: ContractEvidence): LegalBasis {
  const law = String(evidence.metadata.law_name ?? evidence.title);
  const article = String(evidence.metadata.article_label ?? "");
  return {
    law,
    article,
    excerpt: evidence.text,
    explanation: "생성된 특약 초안에 연결된 법령 근거입니다.",
    url: typeof evidence.metadata.url === "string" ? evidence.metadata.url : statuteOfficialUrl(law, article),
  };
}

function generatedFromApi(result: SpecialClauseDraftResult): GeneratedClause[] {
  return result.items.map((item) => {
    const itemId = `request-${item.request_index}`;
    const itemEvidence = result.evidence.filter((entry) => entry.item_id === itemId);
    const citedStatutes = itemEvidence.filter(
      (entry) => entry.source_type === "statute" && item.evidence_source_ids.includes(entry.source_id),
    );
    const relatedPrecedents = itemEvidence.filter((entry) => entry.source_type === "precedent");
    const needsInput = item.needs_input.join(" ");
    if (item.status === "out_of_scope") {
      return {
        title: "범위 밖 요청",
        clause: item.reason ?? "부동산 계약 특약과 관련된 요청만 도와드릴 수 있어요.",
        userInput: item.request_text,
        strategy: "특약으로 정하고 싶은 내용을 계약 상황과 함께 다시 입력해 주세요.",
        statutes: [],
        precedents: [],
      };
    }
    return {
      title: item.title ?? `특약 ${item.request_index}`,
      clause: item.draft_text ?? `요청이 구체적이지 않아 초안을 만들지 못했어요. ${needsInput || "어떤 상황에서 무엇을 원하는지 함께 적어 주세요."}`,
      userInput: item.request_text,
      strategy: item.strategy ?? "내용을 보완해 다시 입력하시면 그에 맞춰 초안을 만들어 드립니다.",
      // 초안이 있으면 남은 확인 질문은 '함께 정해두면 좋은 내용'으로 따로 보여준다.
      followUps: item.draft_text ? item.needs_input : undefined,
      statutes: citedStatutes.map(evidenceToLaw),
      precedents: relatedPrecedents.map(contractEvidenceToPrecedent),
    };
  });
}

export default function ClauseGeneratorPage() {
  const [stage, setStage] = useState<Stage>("input");
  const [nextId, setNextId] = useState(2);
  const [requests, setRequests] = useState<RequestItem[]>([
    { id: 1, value: "" },
  ]);
  const [selected, setSelected] = useState(0);
  const [jobStage, setJobStage] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const generationAbort = useRef<AbortController | null>(null);
  const [error, setError] = useState("");
  const [focusedRequestId, setFocusedRequestId] = useState<number | null>(null);
  const [draftResult, setDraftResult] = useState<SpecialClauseDraftResult | null>(null);
  const [precedentModal, setPrecedentModal] = useState<{ precedent: CaseBasis; initialView: "detail" | "fullText" } | null>(null);
  const [fullTextLoading, setFullTextLoading] = useState(false);
  const [fullTextLoadingId, setFullTextLoadingId] = useState<string | null>(null);
  const [precedentError, setPrecedentError] = useState("");
  const validRequests = useMemo(() => requests.map((item) => item.value.trim()).filter(Boolean), [requests]);
  const generated = useMemo(
    () => draftResult ? generatedFromApi(draftResult) : validRequests.map(buildClause),
    [draftResult, validRequests],
  );
  const current = generated[selected] ?? generated[0];
  const generationProgress = jobStage === "done"
    ? { label: "결과 화면을 구성하고 있습니다", progress: 100 }
    : (jobStage ? GENERATION_STAGES[jobStage] : undefined) ?? QUEUED_STAGE;

  // 화면을 떠나면 진행 중인 폴링을 멈춘다.
  useEffect(() => () => generationAbort.current?.abort(), []);

  function updateRequest(id: number, value: string) {
    setRequests((items) => items.map((item) => item.id === id ? { ...item, value } : item));
  }

  function addRequest() {
    setRequests((items) => (
      items.length >= MAX_REQUESTS ? items : [...items, { id: nextId, value: "" }]
    ));
    setNextId((value) => value + 1);
  }

  function removeRequest(id: number) {
    setRequests((items) => items.filter((item) => item.id !== id));
  }

  async function generateClauses() {
    setSelected(0);
    setJobStage(null);
    setSlow(false);
    setError("");
    setDraftResult(null);
    setStage("generating");
    generationAbort.current?.abort();
    const controller = new AbortController();
    generationAbort.current = controller;
    try {
      const result = await generateSpecialClauseDraft(validRequests, {
        signal: controller.signal,
        onUpdate: (snapshot) => setJobStage(snapshot.status === "completed" ? "done" : snapshot.stage),
        onSlow: () => setSlow(true),
      });
      setDraftResult(result);
      setJobStage("done");
      // 100%가 화면에 보인 뒤 결과 화면으로 넘어간다.
      await new Promise((resolve) => window.setTimeout(resolve, 450));
      if (controller.signal.aborted) return;
      setStage("result");
    } catch (requestError) {
      if (isAbortError(requestError)) return;
      setError(
        requestError instanceof Error
          ? requestError.message
          : "특약 생성 요청을 처리하지 못했습니다.",
      );
      setStage("review");
    }
  }

  function reviewRequests() {
    const invalid = validRequests.map((value) => validateRealEstateInput(value, 8)).find(Boolean);
    if (invalid) {
      setError(invalid);
      return;
    }
    if (hasContradictoryRequests(validRequests)) {
      setError("서로 반대되는 요청이 함께 입력되어 있습니다. 허용할 조건과 금지할 조건을 다시 확인해주세요.");
      return;
    }
    setError("");
    setStage("review");
  }

  function startNewRequest() {
    setRequests([{ id: nextId, value: "" }]);
    setNextId((value) => value + 1);
    setSelected(0);
    setDraftResult(null);
    setStage("input");
  }

  async function openPrecedentFullTextFromList(precedent: CaseBasis) {
    if (fullTextLoadingId) return;
    setPrecedentError("");
    setFullTextLoadingId(precedent.precedentId);
    try {
      const fullText = await getPrecedentFullText(precedent.precedentId);
      setPrecedentModal({ precedent: mergePrecedentFullText(precedent, fullText), initialView: "fullText" });
    } catch (loadError) {
      setPrecedentError(loadError instanceof Error ? loadError.message : "판례 전문을 불러오지 못했습니다.");
    } finally {
      setFullTextLoadingId(null);
    }
  }

  async function openPrecedentFullText() {
    if (!precedentModal || fullTextLoading) return;
    setFullTextLoading(true);
    try {
      const fullText = await getPrecedentFullText(precedentModal.precedent.precedentId);
      setPrecedentModal((modal) => modal && { ...modal, precedent: mergePrecedentFullText(modal.precedent, fullText) });
    } finally {
      setFullTextLoading(false);
    }
  }

  return (
    <main className={`${styles.page} ${styles.pageGuide}`}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>내 상황을 계약 문구로</p>
        <h1>특약 생성</h1>
        <p>원하는 조건을 평소 사용하는 말로 적으면 협의에 사용할 특약 초안을 만듭니다.</p>
      </header>
      <div className={styles.steps} aria-label="특약 생성 단계">
        {(["input", "review", "result"] as const).map((item, index) => (
          <span className={stage === item || (stage === "generating" && item === "result") ? styles.activeStep : ""} key={item}>
            {String(index + 1).padStart(2, "0")} {index === 0 ? "요청 입력" : index === 1 ? "입력 확인" : "생성 결과"}
          </span>
        ))}
      </div>

      {stage === "input" && (
        <div className={styles.splitGuide}>
          <section className={styles.panel}>
            <h2>어떤 조건을 계약에 넣고 싶나요?</h2>
            <p className={styles.panelLead}>서로 다른 요청은 항목을 나누어 입력해주세요. 계약정보가 없어도 초안을 만들 수 있습니다.</p>
            <p className={styles.panelLead}>한 번에 최대 {MAX_REQUESTS}개까지 요청할 수 있어요 ({requests.length}/{MAX_REQUESTS}).</p>
            <div className={styles.requestList}>
              {requests.map((request, index) => (
                <div className={styles.requestItem} key={request.id}>
                  <span className={styles.requestNumber}>{String(index + 1).padStart(2, "0")}</span>
                  <input aria-label={`요청 ${index + 1}`} onBlur={() => setFocusedRequestId(null)} onFocus={() => setFocusedRequestId(request.id)} placeholder={focusedRequestId === request.id ? "" : "예: 반려견 한 마리를 키우고 싶어요."} value={request.value} onChange={(event) => updateRequest(request.id, event.target.value)} />
                  <button className={styles.iconButton} type="button" onClick={() => removeRequest(request.id)} title="요청 삭제"><X size={16} aria-hidden="true" /></button>
                </div>
              ))}
            </div>
            {error && <div className={styles.validationNotice} role="alert"><AlertCircle size={19} /><div><strong>요청을 다시 확인해주세요</strong><span>{error}</span></div></div>}
            <button className={styles.addButton} disabled={requests.length >= MAX_REQUESTS} type="button" onClick={addRequest}><Plus size={16} aria-hidden="true" />요청 추가</button>
            {requests.length >= MAX_REQUESTS && <p className={styles.panelLead}>요청은 한 번에 최대 {MAX_REQUESTS}개까지 보낼 수 있어요. 나머지는 생성 후 다시 요청해주세요.</p>}
          </section>
          <aside className={styles.sidePanel}>
            <h2>이렇게 적어보세요</h2>
            <p className={styles.panelLead}>누가, 무엇을, 언제까지, 어떻게 하는지 중 알고 있는 것을 적을수록 더 분명한 문구가 나옵니다. 정하지 못한 부분은 비워 두셔도 됩니다.</p>
            <div className={styles.exampleList}>
              <p><b>허용받고 싶은 행동</b><span>반려견 한 마리를 키우고 싶어요. 벽에 선반용 못을 박아도 되는지 정해두고 싶어요.</span></p>
              <p><b>비용과 수리 책임</b><span>입주 후 6개월 안에 보일러가 고장 나면 임대인이 수리비를 부담했으면 해요.</span></p>
              <p><b>금액과 기간 조건</b><span>계약을 갱신할 때 월세는 최대 5%까지만 올릴 수 있게 해주세요.</span></p>
              <p><b>계약 해제와 반환</b><span>전세보증보험 가입이 거절되면 계약을 해제하고 계약금을 돌려받고 싶어요.</span></p>
              <p><b>매매 계약</b><span>잔금 전에 건물에 큰 하자가 발견되면 계약을 해제하고 계약금을 돌려받게 해주세요.</span></p>
            </div>
            <ul className={styles.guideTips}>
              <li><b>한 줄에 하나씩</b> 입력하면 요청마다 특약이 따로 만들어집니다.</li>
              <li><b>내 입장을 함께</b> 적어주세요. 임차인인지 임대인인지, 매수인인지 매도인인지에 따라 문구가 달라집니다.</li>
              <li><b>이름·연락처·계좌번호·주소</b> 같은 개인정보는 적지 마세요.</li>
              <li>특약과 관련 없는 질문은 초안이 만들어지지 않고, 내용이 너무 짧으면 추가로 알려달라고 안내합니다.</li>
            </ul>
            <div className={styles.notice}><AlertCircle size={18} aria-hidden="true" /><span>직접 연결할 법령이 없는 요청은 &apos;당사자가 합의하는 문구&apos;로 작성되고, 그 사실도 함께 알려드립니다. 결과의 법령과 판례는 참고 자료이며 법률 자문이 아닙니다.</span></div>
          </aside>
        </div>
      )}

      {stage === "review" && (
        <section className={styles.panel}>
          <h2>이 요청으로 특약을 만들까요?</h2>
          <p className={styles.panelLead}>빠진 요청이나 서로 모순되는 내용이 없는지 확인해주세요.</p>
          {error && <div className={styles.validationNotice} role="alert"><AlertCircle size={19} /><div><strong>특약 생성에 실패했습니다</strong><span>{error}</span></div></div>}
          <div className={styles.summaryList}>
            {validRequests.map((request, index) => <div className={styles.summaryItem} key={`${index}-${request}`}><span>요청 {index + 1}</span><p>{request}</p></div>)}
          </div>
        </section>
      )}

      {stage === "generating" && (
        <section className={styles.processingCard}>
          <span><LoaderCircle className={styles.spinner} size={28} aria-hidden="true" /></span>
          <h2>요청에 맞는 특약을 만들고 있습니다</h2>
          <p>요청별 문구를 구성하고 관련 법령과 판례 근거를 연결하고 있습니다.</p>
          <div className={styles.progressHeader}><span>{generationProgress.label}</span><strong>{generationProgress.progress}%</strong></div>
          <div className={styles.progressTrack}><i style={{ width: `${generationProgress.progress}%` }} /></div>
          {slow && <p role="status">평소보다 시간이 걸리고 있습니다. 서버가 처리 중이니 닫지 말고 기다려주세요.</p>}
        </section>
      )}

      {stage === "result" && (
        current && (
        <>
          <div className={styles.notice}><AlertCircle size={18} aria-hidden="true" /><span>생성된 문구는 협의를 위한 AI 초안입니다. 실제 계약에 반영하기 전에 상대방과 합의하고 필요한 경우 전문가에게 확인하세요.</span></div>
          <div className={styles.resultSplit}>
            <div className={styles.resultList}>
              {generated.map((item, index) => (
                <button className={`${styles.resultButton} ${selected === index ? styles.resultButtonActive : ""}`} type="button" key={`${index}-${item.title}`} onClick={() => setSelected(index)}>
                  <strong>특약 {index + 1}</strong><span>{item.title}</span>
                </button>
              ))}
            </div>
            <article className={styles.detail} key={selected}>
              <h2>{current.title}</h2>
              <section className={styles.detailSection}><h3>생성된 특약</h3><p className={styles.generatedText}>{current.clause}</p></section>
              <section className={styles.detailSection}><h3>요청하신 내용</h3><p>{current.userInput}</p></section>
              <section className={styles.detailSection}><h3>협상 전략</h3><p>{current.strategy}</p></section>
              {current.followUps && current.followUps.length > 0 && (
                <section className={styles.detailSection}>
                  <h3>특약 작성 시 함께 정해두면 좋은 내용</h3>
                  <p>상대방과 협의할 때 참고하세요.</p>
                  <ul>{current.followUps.map((question) => <li key={question}>{question}</li>)}</ul>
                </section>
              )}
              <details className={styles.detailDisclosure}><summary>관련 법령 {current.statutes.length}건</summary>
                {current.statutes.length ? current.statutes.map((law) => <article className={styles.basisCard} key={`${law.law}-${law.article}`}><strong>{law.law} {law.article}</strong><StatuteExcerpt text={law.excerpt} /><span>{law.explanation}</span>{law.url && <a href={law.url} target="_blank" rel="noreferrer">국가법령정보센터에서 원문 보기 <ExternalLink size={14} /></a>}</article>) : <p>이 초안에 직접 연결된 법령 근거는 없습니다.</p>}
              </details>
              <details className={styles.detailDisclosure}><summary>관련 판례 {current.precedents.length}건</summary>
                {precedentError && <p role="alert">{precedentError}</p>}
                {current.precedents.length ? current.precedents.map((item) => <PrecedentListItem key={item.caseNumber} fullTextLoading={fullTextLoadingId === item.precedentId} onOpenDetail={() => setPrecedentModal({ precedent: item, initialView: "detail" })} onOpenFullText={() => void openPrecedentFullTextFromList(item)} precedent={item} />) : <p>이 초안에 직접 연결된 판례는 없습니다.</p>}
              </details>
            </article>
          </div>
        </>
        )
      )}

      <div className={styles.actions}>
        {stage === "review" && <button className={styles.secondaryButton} type="button" onClick={() => setStage("input")}>이전</button>}
        {stage === "input" && <button className={styles.primaryButton} type="button" disabled={validRequests.length === 0} onClick={reviewRequests}>입력 내용 확인<ArrowRight size={17} aria-hidden="true" /></button>}
        {stage === "review" && <button className={styles.primaryButton} type="button" disabled={validRequests.length === 0} onClick={generateClauses}>특약 생성<ArrowRight size={17} aria-hidden="true" /></button>}
        {stage === "result" && draftResult && <SaveGenerationResultButton className={styles.secondaryButton} kind="special_clause" sourceId={draftResult.draft_id} loginNext="/clause-generator" />}
        {stage === "result" && <button className={styles.primaryButton} type="button" onClick={startNewRequest}><Plus size={17} aria-hidden="true" />새 요청 작성</button>}
      </div>
      {precedentModal && (
        <PrecedentModal
          initialView={precedentModal.initialView}
          onClose={() => setPrecedentModal(null)}
          precedent={precedentModal.precedent}
          onOpenFullText={openPrecedentFullText}
          fullTextLoading={fullTextLoading}
        />
      )}
    </main>
  );
}
