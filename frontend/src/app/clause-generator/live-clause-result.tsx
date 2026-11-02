"use client";

import { useMemo, useState } from "react";
import { AlertCircle, ChevronRight, CircleHelp } from "lucide-react";
import type { SpecialClauseDraftItem, SpecialClauseDraftResult } from "@/lib/contract-api";
import styles from "../feature-demo.module.css";

function statusLabel(item: SpecialClauseDraftItem) {
  if (item.status === "draft_ready") return "초안 생성";
  if (item.status === "needs_input") return "추가 확인 필요";
  return "생성 대기";
}

export function LiveClauseResult({ result }: { result: SpecialClauseDraftResult }) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const selected = result.items[selectedIndex] ?? result.items[0];
  const evidence = useMemo(() => result.evidence.filter((item) => (
    selected?.evidence_source_ids.includes(item.source_id)
  )), [result.evidence, selected]);

  if (!selected) return null;

  return <>
    <div className={styles.notice}><AlertCircle size={18} aria-hidden="true" /><span>{result.disclaimer}</span></div>
    <div className={styles.resultSplit}>
      <div className={styles.resultList}>{result.items.map((item, index) => <button className={`${styles.resultButton} ${selectedIndex === index ? styles.resultButtonActive : ""}`} type="button" key={`${item.request_index}-${item.request_text}`} onClick={() => setSelectedIndex(index)}><strong>특약 {item.request_index}</strong><span>{statusLabel(item)}</span><ChevronRight size={16} aria-hidden="true" /></button>)}</div>
      <article className={styles.detail}>
        <h2>요청 {selected.request_index}</h2>
        <section className={styles.detailSection}><h3>사용자 요청</h3><p>{selected.request_text}</p></section>
        {selected.status === "draft_ready" && selected.draft_text ? <section className={styles.detailSection}><h3>생성된 특약 초안</h3><p className={styles.generatedText}>{selected.draft_text}</p></section> : <section className={styles.detailSection}><h3><CircleHelp size={17} aria-hidden="true" />확인이 필요한 정보</h3><ul>{selected.needs_input.length ? selected.needs_input.map((question) => <li key={question}>{question}</li>) : <li>생성 결과를 준비하지 못했습니다. 요청을 다시 확인해주세요.</li>}</ul></section>}
        <details className={styles.detailDisclosure} open={evidence.length > 0}><summary>관련 근거 {evidence.length}건</summary>{evidence.length ? evidence.map((item) => <article className={styles.basisCard} key={item.source_id}><strong>{item.title}</strong><p>{item.text}</p><span>{item.source_type === "statute" ? "법령 근거" : "판례 근거"}</span></article>) : <p>직접 연결된 근거가 없습니다.</p>}</details>
      </article>
    </div>
  </>;
}
