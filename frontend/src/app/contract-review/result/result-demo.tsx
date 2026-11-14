"use client";

import Link from "next/link";
import { useMemo, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { AlertCircle, ChevronRight, CircleCheck, CircleHelp, ExternalLink } from "lucide-react";
import { PrecedentListItem, PrecedentModal } from "@/components/precedent-ui";
import { SaveGenerationResultButton } from "@/components/save-generation-result-button";
import { clauseReviews as demoClauseReviews, registryReviews as demoRegistryReviews } from "@/lib/mock-data";
import { loadContractReviewResult } from "@/lib/contract-review-session";
import type { CaseBasis, ClauseReview, DocumentMode, LegalBasis, RegistryReview } from "@/lib/demo-types";
import type { ContractEvidence, ContractReviewItem, ContractReviewResult } from "@/lib/contract-api";
import styles from "../contract-review.module.css";

type ResultTab = "clauses" | "registry";

function statuteBasis(evidence: ContractEvidence): LegalBasis {
  return {
    law: String(evidence.metadata.law_name ?? evidence.title),
    article: String(evidence.metadata.article_label ?? ""),
    excerpt: evidence.text,
    explanation: "검토 의견에 연결된 법령 근거입니다. 계약서 원문과 함께 확인하세요.",
    url: typeof evidence.metadata.url === "string" ? evidence.metadata.url : "",
  };
}

function precedentBasis(evidence: ContractEvidence): CaseBasis {
  return {
    title: evidence.title,
    caseNumber: String(evidence.metadata.case_number ?? evidence.title),
    precedentId: evidence.source_id,
    summary: evidence.text,
    url: typeof evidence.metadata.url === "string" ? evidence.metadata.url : "",
  };
}

function liveClauseReviews(result: ContractReviewResult): ClauseReview[] {
  return result.contract_items.map((item) => {
    const analysis = result.analysis.find((entry) => entry.item_id === item.item_id);
    const evidence = result.evidence.filter((entry) => (
      entry.item_id === item.item_id
      && (!analysis || analysis.evidence_source_ids.includes(entry.source_id))
    ));
    return {
      id: item.item_id,
      title: item.label,
      original: item.original_text ?? "확인된 계약서 원문이 없습니다.",
      status: analysis?.review_status ?? "check",
      reason: analysis?.summary ?? item.message,
      revision: analysis?.revision ?? "근거 기반 수정 초안을 생성하지 못했습니다. 원문을 직접 확인하세요.",
      negotiation: analysis?.negotiation ?? "적용 범위와 사실관계를 상대방과 확인하세요.",
      statutes: evidence.filter((entry) => entry.source_type === "statute").map(statuteBasis),
      precedents: evidence.filter((entry) => entry.source_type === "precedent").map(precedentBasis),
    };
  });
}

function liveRegistryReviews(result: ContractReviewResult): RegistryReview[] {
  // 등본 확인 필요 항목, 계약서 교차 안내, 특약 제안은 등본 검토 탭에 함께 보여준다.
  const attentionCategories = new Set([
    "registry_attention",
    "contract_registry_crosscheck",
    "special_term_suggestion",
  ]);
  const attentionItems = (result.precheck_items ?? []).filter((item) =>
    attentionCategories.has(item.category),
  );
  // 안내 항목이 같은 등기 원문을 이미 다루면 등기 카드는 따로 보여주지 않고 합친다.
  const coveredBy = (registryItem: ContractReviewItem) =>
    attentionItems.find((attention) =>
      registryItem.original_text
      && attention.original_text?.split("\n").includes(registryItem.original_text),
    );
  const standaloneRegistryItems = result.registry_items.filter((item) => !coveredBy(item));
  const reviews: RegistryReview[] = [...standaloneRegistryItems, ...attentionItems].map((item) => {
    const merged = result.registry_items.filter((entry) => coveredBy(entry)?.item_id === item.item_id);
    const analysis = result.analysis.find((entry) => entry.item_id === item.item_id)
      ?? result.analysis.find((entry) => merged.some((registryItem) => registryItem.item_id === entry.item_id));
    if (merged.length > 0) {
      return {
        id: item.item_id,
        title: item.label,
        value: item.original_text ?? "",
        status: analysis?.review_status ?? "check",
        summary: item.message,
        action: analysis ? `${analysis.summary} ${analysis.negotiation}`.trim() : undefined,
      };
    }
    return {
      id: item.item_id,
      title: item.label,
      // 등본 원문이 없는 안내 항목(특약 제안 등)은 원문 줄을 비워 둔다.
      value: item.original_text ?? (item.category === "registry_field" ? "확인된 원문 없음" : ""),
      status: analysis?.review_status ?? "check",
      summary: analysis?.summary ?? item.message,
      action: analysis?.negotiation,
    };
  });
  return reviews;
}

function readMode(value: string | null): DocumentMode {
  return value === "contract" || value === "registry" ? value : "both";
}

export function ResultDemo() {
  const searchParams = useSearchParams();
  const mode = readMode(searchParams.get("documents"));
  const initialTab: ResultTab = mode === "registry" ? "registry" : "clauses";
  const [tab, setTab] = useState<ResultTab>(initialTab);
  const [selectedId, setSelectedId] = useState(demoClauseReviews[0].id);
  const [precedentModal, setPrecedentModal] = useState<{ precedent: CaseBasis; initialView: "detail" | "fullText" } | null>(null);
  const liveResult = useSyncExternalStore(
    () => () => undefined,
    loadContractReviewResult,
    () => null,
  );
  const clauseReviews = useMemo(
    () => liveResult ? liveClauseReviews(liveResult) : demoClauseReviews,
    [liveResult],
  );
  const registryReviews = useMemo(
    () => liveResult ? liveRegistryReviews(liveResult) : demoRegistryReviews,
    [liveResult],
  );
  const selectedClause = useMemo(
    () => clauseReviews.find((clause) => clause.id === selectedId) ?? clauseReviews[0],
    [clauseReviews, selectedId],
  );
  const safeClauseCount = clauseReviews.filter((item) => item.status === "safe").length;
  const checkClauseCount = clauseReviews.length - safeClauseCount;
  const safeRegistryCount = registryReviews.filter((item) => item.status === "safe").length;
  const checkRegistryCount = registryReviews.length - safeRegistryCount;

  return (
    <>
      <div className={styles.resultHeader}>
        <div className={styles.heading}>
          <h1>{mode === "registry" ? "등본 검토 결과" : "계약서 검토 결과"}</h1>
          <p>확인이 필요한 항목부터 살펴보고 원문과 실제 사실관계를 다시 확인하세요.</p>
        </div>
        <div className={styles.summaryCounts} aria-label="검토 결과 요약">
          <span className={styles.safeBadge}>
            문제없음 {tab === "registry" ? safeRegistryCount : safeClauseCount}
          </span>
          <span className={styles.checkBadge}>
            확인 필요 {tab === "registry" ? checkRegistryCount : checkClauseCount}
          </span>
        </div>
      </div>

      <div className={styles.aiNotice}>
        <AlertCircle size={18} aria-hidden="true" />
        <span>
          이 결과는 입력된 문서와 법령·판례 자료를 바탕으로 만든 AI 검토 의견이며 법률 자문이나 법적 판단을 대신하지 않습니다.
          중요한 계약은 최신 원문과 전문가의 확인을 함께 받으세요.
        </span>
      </div>

      <div className={styles.tabs} role="tablist" aria-label="검토 결과 종류">
        {mode !== "registry" && (
          <button
            className={`${styles.tab} ${tab === "clauses" ? styles.tabActive : ""}`}
            type="button"
            role="tab"
            aria-selected={tab === "clauses"}
            onClick={() => setTab("clauses")}
          >
            특약 검토 {clauseReviews.length}
          </button>
        )}
        {mode !== "contract" && (
          <button
            className={`${styles.tab} ${tab === "registry" ? styles.tabActive : ""}`}
            type="button"
            role="tab"
            aria-selected={tab === "registry"}
            onClick={() => setTab("registry")}
          >
            등본 검토 {registryReviews.length}
          </button>
        )}
      </div>
      {mode === "contract" && <p className={styles.unreviewedNote}>등본은 확인하지 않았습니다.</p>}

      {tab === "clauses" && mode !== "registry" ? (
        <div className={styles.resultLayout}>
          <div className={styles.resultList}>
            {clauseReviews.map((clause) => (
              <button
                className={`${styles.resultItem} ${selectedClause.id === clause.id ? styles.resultItemActive : ""}`}
                type="button"
                key={clause.id}
                onClick={() => setSelectedId(clause.id)}
              >
                <span className={styles.resultItemTitle}>
                  <i
                    className={styles.statusDot}
                    style={{ background: clause.status === "safe" ? "var(--success)" : "#e47e22" }}
                  />
                  {clause.title}
                </span>
                <ChevronRight size={17} aria-hidden="true" />
              </button>
            ))}
          </div>

          <article className={styles.detailPanel}>
            <span className={styles.detailStatus}>
              {selectedClause.status === "safe"
                ? <CircleCheck size={16} color="var(--success)" aria-hidden="true" />
                : <CircleHelp size={16} color="var(--warning)" aria-hidden="true" />}
              {selectedClause.status === "safe" ? "문제없음" : "확인 필요"}
            </span>
            <h2>{selectedClause.title}</h2>

            <section className={styles.detailSection}>
              <h3>계약서 원문</h3>
              <p>{selectedClause.original}</p>
            </section>
            <section className={styles.detailSection}>
              <h3>이렇게 판단한 이유</h3>
              <p>{selectedClause.reason}</p>
            </section>
            {selectedClause.revision && (
              <section className={styles.detailSection}>
                <h3>수정 결과</h3>
                <p className={styles.revisionBox}>{selectedClause.revision}</p>
              </section>
            )}
            {selectedClause.negotiation && (
              <section className={styles.detailSection}>
                <h3>협상 전략 및 법적 영향</h3>
                <p>{selectedClause.negotiation}</p>
              </section>
            )}
            <details>
              <summary>관련 법령 {selectedClause.statutes.length}건</summary>
              <div className={styles.evidenceList}>
                {selectedClause.statutes.map((statute) => (
                  <article className={styles.evidenceCard} key={`${statute.law}-${statute.article}`}>
                    <div className={styles.evidenceHeading}>
                      <strong>{statute.law} {statute.article}</strong>
                      {statute.url && <a href={statute.url} target="_blank" rel="noreferrer">원문 <ExternalLink size={14} /></a>}
                    </div>
                    <p className={styles.evidenceExcerpt}>{statute.excerpt}</p>
                    <p>{statute.explanation}</p>
                  </article>
                ))}
              </div>
            </details>
            <details>
              <summary>관련 판례 {selectedClause.precedents.length}건</summary>
              {selectedClause.precedents.length > 0 ? (
                <div className={styles.evidenceList}>
                  {selectedClause.precedents.map((precedent) => (
                    <PrecedentListItem key={precedent.caseNumber} onOpenDetail={() => setPrecedentModal({ precedent, initialView: "detail" })} onOpenFullText={() => setPrecedentModal({ precedent, initialView: "fullText" })} precedent={precedent} />
                  ))}
                </div>
              ) : <p className={styles.noEvidence}>이 조항의 판단 근거로 직접 연결된 판례는 없습니다.</p>}
            </details>
          </article>
        </div>
      ) : (
        <div className={styles.registryGrid}>
          {registryReviews.map((item, index) => (
            <article className={styles.registryItem} key={`${item.id}-${index}`}>
              <span className={item.status === "safe" ? styles.safeBadge : styles.checkBadge}>
                {item.status === "safe" ? "문제없음" : "확인 필요"}
              </span>
              <h3>{item.title}</h3>
              {item.value && <p className={styles.registryValue}>{item.value}</p>}
              <p className={styles.registrySummary}>{item.summary}</p>
              {item.action && <p className={styles.registryAction}>{item.action}</p>}
            </article>
          ))}
        </div>
      )}

      <div className={styles.actions}>
        {liveResult && <SaveGenerationResultButton className={styles.secondaryButton} kind="contract_review" sourceId={liveResult.review_id} loginNext="/contract-review/result" />}
        <Link className={styles.secondaryButton} href="/contract-review">새 문서 검토</Link>
      </div>
      {precedentModal && <PrecedentModal initialView={precedentModal.initialView} onClose={() => setPrecedentModal(null)} precedent={precedentModal.precedent} />}
    </>
  );
}

