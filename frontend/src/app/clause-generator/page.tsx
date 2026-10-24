"use client";

import { useMemo, useState } from "react";
import { AlertCircle, ArrowRight, ExternalLink, Plus, X } from "lucide-react";
import { restorationCase, restorationLaw } from "@/lib/mock-data";
import type { CaseBasis, LegalBasis } from "@/lib/demo-types";
import styles from "../feature-demo.module.css";

type Stage = "input" | "review" | "result";
type RequestItem = { id: number; value: string };
type GeneratedClause = {
  title: string;
  clause: string;
  reason: string;
  strategy: string;
  statutes: LegalBasis[];
  precedents: CaseBasis[];
};

function buildClause(request: string, index: number): GeneratedClause {
  const normalized = request.replace(/\s+/g, " ").trim();

  if (/반려|강아지|고양이|반려견|반려묘/.test(normalized)) {
    return {
      title: "반려동물 사육",
      clause: "임대인은 임차인이 반려동물 1마리를 사육하는 데 동의한다. 임차인은 사육으로 인해 통상적인 사용 범위를 넘는 시설 훼손이 발생한 경우 그 부분을 원상회복한다.",
      reason: "반려동물 사육 가능 여부와 실제 훼손이 발생했을 때의 책임 범위를 계약 전에 명확히 하기 위한 문구입니다.",
      strategy: "동물의 종류와 수, 공용공간 이용 수칙, 입주 당시 마감재 상태를 함께 기록해 두는 방안을 협의하세요.",
      statutes: [restorationLaw],
      precedents: [restorationCase],
    };
  }

  if (/못|타공|벽|액자|선반/.test(normalized)) {
    return {
      title: "벽면 타공",
      clause: "임차인은 생활에 필요한 범위에서 벽면을 타공할 수 있다. 퇴거 시 통상적인 보수가 가능한 타공은 임대인과 보수 방법을 협의하고, 타일·대리석 등 복구가 어려운 마감재는 사전 동의를 받는다.",
      reason: "벽면 타공을 전부 금지하거나 작은 흔적까지 일률적으로 복구하도록 요구하는 분쟁을 줄이기 위한 문구입니다.",
      strategy: "허용 위치와 개수, 제외할 벽면을 사진에 표시하고 퇴거 시 보수 방법을 구체적으로 정하세요.",
      statutes: [restorationLaw],
      precedents: [restorationCase],
    };
  }

  if (/담배|흡연/.test(normalized)) {
    return {
      title: "실내 흡연",
      clause: "임차인은 실내 흡연 여부와 그 범위를 임대인과 합의하고, 흡연으로 인해 통상적인 사용 범위를 넘는 벽지 변색이나 냄새 제거 비용이 발생한 경우 실제 손상 부분의 복구 방법을 협의한다.",
      reason: "흡연 허용 여부와 냄새·변색에 대한 복구 범위를 미리 정해 퇴거 시 책임 다툼을 줄이기 위한 문구입니다.",
      strategy: "실내 전체 또는 지정 공간 중 어디까지 허용할지, 입주 전 상태를 어떤 방식으로 기록할지 함께 정하세요.",
      statutes: [restorationLaw],
      precedents: [restorationCase],
    };
  }

  return {
    title: `요청 ${index + 1} 반영`,
    clause: `임대인과 임차인은 다음 사항에 관하여 별도로 합의한다. ${normalized} 구체적인 이행 방법과 책임 범위는 계약 체결 전에 상호 확인하여 기재한다.`,
    reason: "사용자가 요청한 내용을 계약 당사자가 확인할 수 있는 합의 문구의 형태로 정리한 초안입니다.",
    strategy: "누가, 언제까지, 어떤 범위에서 이행하는지와 위반했을 때의 처리 방법을 상대방과 구체적으로 협의하세요.",
    statutes: [],
    precedents: [],
  };
}

export default function ClauseGeneratorPage() {
  const [stage, setStage] = useState<Stage>("input");
  const [nextId, setNextId] = useState(3);
  const [requests, setRequests] = useState<RequestItem[]>([
    { id: 1, value: "반려견 한 마리를 키우고 싶어요." },
    { id: 2, value: "액자 설치를 위해 벽에 작은 못을 박고 싶어요." },
  ]);
  const [selected, setSelected] = useState(0);
  const validRequests = useMemo(() => requests.map((item) => item.value.trim()).filter(Boolean), [requests]);
  const generated = useMemo(() => validRequests.map(buildClause), [validRequests]);
  const current = generated[selected] ?? generated[0];

  function updateRequest(id: number, value: string) {
    setRequests((items) => items.map((item) => item.id === id ? { ...item, value } : item));
  }

  function addRequest() {
    setRequests((items) => [...items, { id: nextId, value: "" }]);
    setNextId((value) => value + 1);
  }

  function removeRequest(id: number) {
    setRequests((items) => items.filter((item) => item.id !== id));
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>내 상황을 계약 문구로</p>
        <h1>특약 생성</h1>
        <p>원하는 조건을 평소 사용하는 말로 적으면 협의에 사용할 특약 초안을 만듭니다.</p>
      </header>
      <div className={styles.steps} aria-label="특약 생성 단계">
        {(["input", "review", "result"] as Stage[]).map((item, index) => (
          <span className={stage === item ? styles.activeStep : ""} key={item}>
            {String(index + 1).padStart(2, "0")} {index === 0 ? "요청 입력" : index === 1 ? "입력 확인" : "생성 결과"}
          </span>
        ))}
      </div>

      {stage === "input" && (
        <div className={styles.splitWide}>
          <section className={styles.panel}>
            <h2>어떤 조건을 계약에 넣고 싶나요?</h2>
            <p className={styles.panelLead}>서로 다른 요청은 항목을 나누어 입력해주세요. 계약정보가 없어도 초안을 만들 수 있습니다.</p>
            <div className={styles.requestList}>
              {requests.map((request, index) => (
                <div className={styles.requestItem} key={request.id}>
                  <span className={styles.requestNumber}>{String(index + 1).padStart(2, "0")}</span>
                  <input aria-label={`요청 ${index + 1}`} placeholder="예: 반려묘 한 마리를 키우고 싶어요." value={request.value} onChange={(event) => updateRequest(request.id, event.target.value)} />
                  <button className={styles.iconButton} type="button" onClick={() => removeRequest(request.id)} title="요청 삭제"><X size={16} aria-hidden="true" /></button>
                </div>
              ))}
            </div>
            <button className={styles.addButton} type="button" onClick={addRequest}><Plus size={16} aria-hidden="true" />요청 추가</button>
          </section>
          <aside className={styles.sidePanel}>
            <h2>이렇게 적어보세요</h2>
            <div className={styles.exampleList}>
              <p><b>허용받고 싶은 행동</b><span>반려동물을 키우거나 벽에 못을 박고 싶어요.</span></p>
              <p><b>비용과 수리 책임</b><span>보일러가 고장 나면 임대인이 수리했으면 해요.</span></p>
              <p><b>계약 종료 시 처리</b><span>보증보험이 거절되면 계약금을 돌려받고 싶어요.</span></p>
            </div>
            <div className={styles.notice}><AlertCircle size={18} aria-hidden="true" /><span>구체적인 기한, 금액, 책임 범위를 적으면 더 분명한 문구를 만들 수 있습니다.</span></div>
          </aside>
        </div>
      )}

      {stage === "review" && (
        <section className={styles.panel}>
          <h2>이 요청으로 특약을 만들까요?</h2>
          <p className={styles.panelLead}>빠진 요청이나 서로 모순되는 내용이 없는지 확인해주세요.</p>
          <div className={styles.summaryList}>
            {validRequests.map((request, index) => <div className={styles.summaryItem} key={`${index}-${request}`}><span>요청 {index + 1}</span><p>{request}</p></div>)}
          </div>
        </section>
      )}

      {stage === "result" && current && (
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
            <article className={styles.detail}>
              <h2>{current.title}</h2>
              <section className={styles.detailSection}><h3>생성된 특약</h3><p className={styles.generatedText}>{current.clause}</p></section>
              <section className={styles.detailSection}><h3>생성된 이유</h3><p>{current.reason}</p></section>
              <section className={styles.detailSection}><h3>협상 전략</h3><p>{current.strategy}</p></section>
              <details className={styles.detailDisclosure}><summary>관련 법령</summary>
                {current.statutes.length ? current.statutes.map((law) => <article className={styles.basisCard} key={law.article}><strong>{law.law} {law.article}</strong><p>{law.excerpt}</p><span>{law.explanation}</span><a href={law.url} target="_blank" rel="noreferrer">원문 보기 <ExternalLink size={14} /></a></article>) : <p>이 초안에 직접 연결된 법령 근거는 없습니다.</p>}
              </details>
              <details className={styles.detailDisclosure}><summary>관련 판례</summary>
                {current.precedents.length ? current.precedents.map((item) => <article className={styles.basisCard} key={item.caseNumber}><strong>{item.title}</strong><p>{item.caseNumber} · {item.precedentId}</p><span>{item.summary}</span><a href={item.url} target="_blank" rel="noreferrer">원문 보기 <ExternalLink size={14} /></a></article>) : <p>이 초안에 직접 연결된 판례는 없습니다.</p>}
              </details>
            </article>
          </div>
        </>
      )}

      <div className={styles.actions}>
        {stage !== "input" && <button className={styles.secondaryButton} type="button" onClick={() => setStage(stage === "result" ? "review" : "input")}>이전</button>}
        {stage !== "result" && <button className={styles.primaryButton} type="button" disabled={validRequests.length === 0} onClick={() => { setSelected(0); setStage(stage === "input" ? "review" : "result"); }}>{stage === "input" ? "입력 내용 확인" : "특약 생성"}<ArrowRight size={17} aria-hidden="true" /></button>}
      </div>
    </main>
  );
}
