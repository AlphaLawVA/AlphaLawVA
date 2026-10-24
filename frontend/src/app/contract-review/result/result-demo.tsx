"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AlertCircle, BookOpen, ChevronRight, CircleCheck, CircleHelp, ExternalLink } from "lucide-react";
import { clauseReviews, registryReviews } from "@/lib/mock-data";
import type { DocumentMode } from "@/lib/demo-types";
import styles from "../contract-review.module.css";

type ResultTab = "clauses" | "registry";

function readMode(value: string | null): DocumentMode {
  return value === "contract" || value === "registry" ? value : "both";
}

export function ResultDemo() {
  const searchParams = useSearchParams();
  const mode = readMode(searchParams.get("documents"));
  const initialTab: ResultTab = mode === "registry" ? "registry" : "clauses";
  const [tab, setTab] = useState<ResultTab>(initialTab);
  const [selectedId, setSelectedId] = useState(clauseReviews[0].id);
  const selectedClause = useMemo(
    () => clauseReviews.find((clause) => clause.id === selectedId) ?? clauseReviews[0],
    [selectedId],
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
            <section className={styles.detailSection}>
              <h3>수정 결과</h3>
              <p className={styles.revisionBox}>{selectedClause.revision}</p>
            </section>
            <section className={styles.detailSection}>
              <h3>협상 전략 및 법적 영향</h3>
              <p>{selectedClause.negotiation}</p>
            </section>
            <details>
              <summary>관련 법령</summary>
              <div className={styles.evidenceList}>
                {selectedClause.statutes.map((statute) => (
                  <article className={styles.evidenceCard} key={`${statute.law}-${statute.article}`}>
                    <div className={styles.evidenceHeading}>
                      <strong>{statute.law} {statute.article}</strong>
                      <a href={statute.url} target="_blank" rel="noreferrer">원문 <ExternalLink size={14} /></a>
                    </div>
                    <p className={styles.evidenceExcerpt}>{statute.excerpt}</p>
                    <p>{statute.explanation}</p>
                  </article>
                ))}
              </div>
            </details>
            <details>
              <summary>관련 판례</summary>
              {selectedClause.precedents.length > 0 ? (
                <div className={styles.evidenceList}>
                  {selectedClause.precedents.map((precedent) => (
                    <article className={styles.evidenceCard} key={precedent.caseNumber}>
                      <strong>{precedent.title}</strong>
                      <p className={styles.caseNumber}>{precedent.caseNumber} · {precedent.precedentId}</p>
                      <p>{precedent.summary}</p>
                      <div className={styles.evidenceActions}>
                        <Link href={`/precedent-search?case=${encodeURIComponent(precedent.caseNumber)}`}><BookOpen size={15} />판례 상세보기</Link>
                        <a href={precedent.url} target="_blank" rel="noreferrer">원문 보기 <ExternalLink size={14} /></a>
                      </div>
                    </article>
                  ))}
                </div>
              ) : <p className={styles.noEvidence}>이 조항의 판단 근거로 직접 연결된 판례는 없습니다.</p>}
            </details>
          </article>
        </div>
      ) : (
        <div className={styles.registryGrid}>
          {registryReviews.map((item) => (
            <article className={styles.registryItem} key={item.id}>
              <span className={item.status === "safe" ? styles.safeBadge : styles.checkBadge}>
                {item.status === "safe" ? "문제없음" : "확인 필요"}
              </span>
              <h3>{item.title}</h3>
              <p className={styles.registryValue}>{item.value}</p>
              <p className={styles.registrySummary}>{item.summary}</p>
              {item.action && <p className={styles.registryAction}>{item.action}</p>}
            </article>
          ))}
        </div>
      )}

      <div className={styles.actions}>
        <Link className={styles.secondaryButton} href="/contract-review">새 문서 검토</Link>
      </div>
    </>
  );
}

