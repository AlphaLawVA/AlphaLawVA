"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { AlertCircle, ChevronRight, CircleHelp } from "lucide-react";
import type { ContractReviewItem, ContractReviewResult } from "@/lib/contract-api";
import styles from "../contract-review.module.css";

type ResultTab = "contract" | "registry";

function itemStatusLabel(status: ContractReviewItem["status"]) {
  if (status === "review_ready") return "검토 준비";
  if (status === "needs_input") return "추가 확인";
  return "근거 검색 중";
}

export function LiveReviewResult({ result }: { result: ContractReviewResult }) {
  const [tab, setTab] = useState<ResultTab>(
    result.contract_items.length ? "contract" : "registry",
  );
  const items = tab === "contract" ? result.contract_items : result.registry_items;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = useMemo(
    () => items.find((item) => item.item_id === selectedId) ?? items[0] ?? null,
    [items, selectedId],
  );
  const selectedAnalysis = result.analysis.find((item) => item.item_id === selected?.item_id) ?? null;
  const selectedEvidence = result.evidence.filter((item) => (
    item.item_id === selected?.item_id
    && (!selectedAnalysis || selectedAnalysis.evidence_source_ids.includes(item.source_id))
  ));
  const checkCount = items.filter((item) => item.status !== "review_ready").length;

  return <>
    <div className={styles.resultHeader}>
      <div className={styles.heading}><h1>계약서 검토 결과</h1><p>{result.message}</p></div>
      <div className={styles.summaryCounts}><span className={styles.safeBadge}>검토 준비 {items.length - checkCount}</span><span className={styles.checkBadge}>추가 확인 {checkCount}</span></div>
    </div>
    <div className={styles.aiNotice}><AlertCircle size={18} aria-hidden="true" /><span>{result.disclaimer}</span></div>
    {result.precheck_items.length > 0 && <div className={styles.validationBanner} role="status"><CircleHelp size={19} /><div><strong>분석 전에 확인할 정보가 있습니다</strong><span>{result.precheck_items.map((item) => item.label).join(", ")}</span></div></div>}
    {result.registry_items.length > 0 && <div className={styles.tabs} role="tablist" aria-label="검토 결과 종류"><button className={`${styles.tab} ${tab === "contract" ? styles.tabActive : ""}`} type="button" onClick={() => setTab("contract")}>계약서 항목 {result.contract_items.length}</button><button className={`${styles.tab} ${tab === "registry" ? styles.tabActive : ""}`} type="button" onClick={() => setTab("registry")}>등본 항목 {result.registry_items.length}</button></div>}
    {items.length === 0 ? <p className={styles.noEvidence}>표시할 검토 항목이 없습니다.</p> : <div className={styles.resultLayout}><div className={styles.resultList}>{items.map((item) => <button className={`${styles.resultItem} ${selected?.item_id === item.item_id ? styles.resultItemActive : ""}`} type="button" key={item.item_id} onClick={() => setSelectedId(item.item_id)}><span className={styles.resultItemTitle}><i className={styles.statusDot} style={{ background: item.status === "review_ready" ? "var(--success)" : "#e47e22" }} />{item.label}</span><ChevronRight size={17} aria-hidden="true" /></button>)}</div>{selected && <article className={styles.detailPanel}><span className={styles.detailStatus}><CircleHelp size={16} color={selected.status === "review_ready" ? "var(--success)" : "var(--warning)"} aria-hidden="true" />{itemStatusLabel(selected.status)}</span><h2>{selected.label}</h2>{selected.original_text && <section className={styles.detailSection}><h3>계약서 원문</h3><p>{selected.original_text}</p></section>}<section className={styles.detailSection}><h3>확인 안내</h3><p>{selected.message}</p></section>{selectedAnalysis && <section className={styles.detailSection}><h3>근거 기반 요약</h3><p>{selectedAnalysis.summary}</p></section>}<details open={selectedEvidence.length > 0}><summary>관련 근거 {selectedEvidence.length}건</summary><div className={styles.evidenceList}>{selectedEvidence.length ? selectedEvidence.map((evidence) => <article className={styles.evidenceCard} key={evidence.source_id}><div className={styles.evidenceHeading}><strong>{evidence.title}</strong><span>{evidence.source_type === "statute" ? "법령" : "판례"}</span></div><p className={styles.evidenceExcerpt}>{evidence.text}</p></article>) : <p className={styles.noEvidence}>직접 연결된 근거가 없습니다.</p>}</div></details></article>}</div>}
    <div className={styles.actions}><Link className={styles.secondaryButton} href="/contract-review">새 문서 검토</Link></div>
  </>;
}
