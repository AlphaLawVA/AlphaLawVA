"use client";

import { useEffect, useState } from "react";
import { ArrowLeft, Bookmark, LoaderCircle, Search } from "lucide-react";
import { PrecedentListItem, PrecedentMeta, PrecedentModal, PrecedentReference, PrecedentSummary } from "@/components/precedent-ui";
import type { CaseBasis } from "@/lib/demo-types";
import {
  generateSimilarityExplanation,
  getPrecedentDetail,
  getPrecedentFullText,
  mapSearchItem,
  mergePrecedentDetail,
  mergePrecedentFullText,
  mergeSimilarityExplanation,
  searchPrecedents,
} from "@/lib/precedent-api";
import { validateRealEstateInput } from "@/lib/demo-validation";
import styles from "../feature-demo.module.css";

type Stage = "search" | "searching" | "results" | "empty" | "detail";
type ComparisonState = "idle" | "loading" | "complete" | "error";

const exampleQueries = [
  "전세 계약이 끝났는데 집주인이 보증금을 돌려주지 않아요.",
  "집을 샀는데 입주하고 나서 누수를 발견했어요.",
  "전셋집이 경매로 넘어가면 보증금을 돌려받을 수 있나요?",
];

function PrecedentSearchPage() {
  const [stage, setStage] = useState<Stage>("search");
  const [query, setQuery] = useState("");
  const [queryFocused, setQueryFocused] = useState(false);
  const [searchId, setSearchId] = useState<string | null>(null);
  const [results, setResults] = useState<CaseBasis[]>([]);
  const [selected, setSelected] = useState(0);
  const [favorites, setFavorites] = useState<Record<string, string>>({});
  const [comparisons, setComparisons] = useState<Record<string, ComparisonState>>({});
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [emptyMessage, setEmptyMessage] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [fullTextLoadingId, setFullTextLoadingId] = useState<string | null>(null);
  const [fullTextCase, setFullTextCase] = useState<number | null>(null);
  const [highlightSimilar, setHighlightSimilar] = useState(true);
  const [savedScrollY, setSavedScrollY] = useState(0);
  const current = results[selected];

  useEffect(() => {
    if (stage === "results" && savedScrollY > 0) {
      window.requestAnimationFrame(() => window.scrollTo({ top: savedScrollY }));
    }
  }, [savedScrollY, stage]);

  function updatePrecedent(precedentId: string, update: (item: CaseBasis) => CaseBasis) {
    setResults((items) => items.map((item) => item.precedentId === precedentId ? update(item) : item));
  }

  async function startSearch() {
    const validationError = validateRealEstateInput(query, 10);
    if (validationError) {
      setError(validationError);
      setStage("search");
      return;
    }

    setError("");
    setActionError("");
    setEmptyMessage("");
    setStage("searching");
    try {
      const response = await searchPrecedents(query);
      setSearchId(response.search_id);
      if (response.status !== "completed") {
        setResults([]);
        setEmptyMessage(response.message ?? "비슷한 쟁점의 판례를 찾지 못했습니다.");
        setStage("empty");
        return;
      }
      setResults(response.results.map(mapSearchItem));
      setSelected(0);
      setStage("results");
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : "판례 검색에 실패했습니다.");
      setStage("search");
    }
  }

  function toggleFavorite(index: number) {
    const precedent = results[index];
    if (!precedent) return;
    setFavorites((items) => {
      const next = { ...items };
      if (next[precedent.precedentId]) delete next[precedent.precedentId];
      else next[precedent.precedentId] = query;
      return next;
    });
  }

  async function openDetail(index: number) {
    const precedent = results[index];
    if (!precedent || !searchId) return;
    setSelected(index);
    setSavedScrollY(window.scrollY);
    setHighlightSimilar(true);
    setActionError("");
    setDetailLoading(true);
    setStage("detail");
    try {
      const detail = await getPrecedentDetail(searchId, precedent.precedentId);
      updatePrecedent(precedent.precedentId, (item) => mergePrecedentDetail(item, detail));
    } catch (detailError) {
      setActionError(detailError instanceof Error ? detailError.message : "판례 상세정보를 불러오지 못했습니다.");
    } finally {
      setDetailLoading(false);
    }
  }

  async function openFullText(index: number) {
    const precedent = results[index];
    if (!precedent || fullTextLoadingId) return;
    setActionError("");
    setFullTextLoadingId(precedent.precedentId);
    try {
      const fullText = await getPrecedentFullText(precedent.precedentId);
      updatePrecedent(precedent.precedentId, (item) => mergePrecedentFullText(item, fullText));
      setFullTextCase(index);
    } catch (fullTextError) {
      setActionError(fullTextError instanceof Error ? fullTextError.message : "판례 전문을 불러오지 못했습니다.");
    } finally {
      setFullTextLoadingId(null);
    }
  }

  async function compareCurrent() {
    if (!current || !searchId) return;
    const precedentId = current.precedentId;
    if (comparisons[precedentId] === "loading" || comparisons[precedentId] === "complete") return;
    setComparisons((items) => ({ ...items, [precedentId]: "loading" }));
    try {
      const explanation = await generateSimilarityExplanation(searchId, precedentId);
      if (explanation.status !== "generated" || !explanation.matched_reason) {
        setComparisons((items) => ({ ...items, [precedentId]: "error" }));
        return;
      }
      updatePrecedent(precedentId, (item) => mergeSimilarityExplanation(item, explanation));
      setComparisons((items) => ({ ...items, [precedentId]: "complete" }));
    } catch {
      setComparisons((items) => ({ ...items, [precedentId]: "error" }));
    }
  }

  if (stage === "detail" && current) {
    const comparison = comparisons[current.precedentId] ?? "idle";
    return (
      <main className={styles.page}>
        <button className={styles.backButton} type="button" onClick={() => setStage("results")}><ArrowLeft size={18} />검색 결과로 돌아가기</button>
        {actionError && <div className={styles.validationNotice} role="alert"><Search size={19} /><div><strong>정보를 불러오지 못했습니다</strong><span>{actionError}</span></div></div>}
        <article className={styles.caseDetailPage}>
          <div className={styles.caseDetailHead}>
            <div><PrecedentMeta precedent={current} /><h1>{current.caseNumber}</h1></div>
            <div className={styles.detailHeadActions}>
              <button className={styles.primaryButton} disabled={fullTextLoadingId === current.precedentId} type="button" onClick={() => void openFullText(selected)}>{fullTextLoadingId === current.precedentId ? "전문 불러오는 중" : "판례 전문 보기"}</button>
              <button className={styles.favoriteButton} type="button" onClick={() => toggleFavorite(selected)}><Bookmark size={18} fill={favorites[current.precedentId] ? "currentColor" : "none"} />{favorites[current.precedentId] ? "저장됨" : "즐겨찾기"}</button>
            </div>
          </div>
          {detailLoading ? (
            <p className={styles.loadingInline}><LoaderCircle className={styles.spinner} size={17} />판례 상세정보를 불러오고 있습니다.</p>
          ) : (
            <>
              <PrecedentSummary highlight={highlightSimilar} precedent={current} />
              <section className={styles.similarBox}>
                <h2>내 상황과 유사한 부분</h2>
                {comparison === "idle" && <><p>필요할 때 검색 질문과 판례 근거를 비교해 설명합니다.</p><button className={styles.primaryButton} type="button" onClick={() => void compareCurrent()}>내 상황과 비교하기</button></>}
                {comparison === "loading" && <p className={styles.loadingInline}><LoaderCircle className={styles.spinner} size={17} />비교 내용을 만들고 있습니다.</p>}
                {comparison === "complete" && <p>{current.matchedReason}</p>}
                {comparison === "error" && <><p>비교 내용을 만들지 못했습니다. 생성 모델 설정을 확인한 뒤 다시 시도해주세요.</p><button className={styles.primaryButton} type="button" onClick={() => void compareCurrent()}>재시도</button></>}
              </section>
              <PrecedentReference highlight={highlightSimilar} mode="search" onHighlightChange={setHighlightSimilar} precedent={current} />
              <div className={styles.caseActions}><a href={current.url} target="_blank" rel="noreferrer">국가법령정보센터 공식 원문 보기 ↗</a></div>
            </>
          )}
        </article>
        {fullTextCase !== null && <PrecedentModal fullTextReturnLabel="검색 결과로 돌아가기" highlight={highlightSimilar} initialView="fullText" onClose={() => setFullTextCase(null)} onHighlightChange={setHighlightSimilar} precedent={results[fullTextCase]} referenceMode="search" />}
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>비슷한 쟁점의 판례 찾기</p>
        <div className={styles.headerLine}><div><h1>판례 검색</h1><p>부동산 매매·임대차에서 겪은 상황을 평소 사용하는 말로 입력해보세요.</p></div></div>
      </header>
      <section className={styles.searchHero}>
        <label htmlFor="case-query">상황을 알려주세요</label>
        <div className={styles.searchBox}>
          <input id="case-query" onBlur={() => setQueryFocused(false)} onFocus={() => setQueryFocused(true)} onKeyDown={(event) => { if (event.key === "Enter" && stage !== "searching") void startSearch(); }} placeholder={queryFocused ? "" : "예: 전세 계약이 끝났는데 집주인이 보증금을 돌려주지 않아요."} value={query} onChange={(event) => setQuery(event.target.value)} />
          <button className={styles.primaryButton} disabled={stage === "searching"} type="button" onClick={() => void startSearch()}><Search size={17} />검색</button>
        </div>
        <p className={styles.searchGuide}>부동산 매매·임대차와 관련해 계약 종류, 문제 발생 시점, 상대방의 행동을 함께 적어주세요.</p>
        {error && <div className={styles.validationNotice} role="alert"><Search size={19} /><div><strong>검색 결과를 확인할 수 없습니다</strong><span>{error}</span></div></div>}
        {stage === "search" && <div className={styles.recentSearches}><span>예시 질문</span>{exampleQueries.map((item) => <button type="button" key={item} onClick={() => setQuery(item)}>{item}</button>)}</div>}
      </section>

      {stage === "searching" && (
        <section className={styles.processingCard} aria-live="polite">
          <span><LoaderCircle className={styles.spinner} size={28} aria-hidden="true" /></span>
          <h2>비슷한 쟁점의 판례를 찾고 있습니다</h2>
          <p>입력한 상황의 핵심 쟁점을 정리하고 판례 데이터베이스를 검색하고 있습니다.</p>
        </section>
      )}

      {stage === "results" && (
        <section className={styles.caseResults}>
          <div className={styles.resultsHeading}><h2>유사 판례 목록</h2><span>{results.length}건</span></div>
          {actionError && <div className={styles.validationNotice} role="alert"><Search size={19} /><div><strong>정보를 불러오지 못했습니다</strong><span>{actionError}</span></div></div>}
          <div className={styles.caseCardList}>
            {results.map((item, index) => <PrecedentListItem favorite={Boolean(favorites[item.precedentId])} key={item.precedentId} onOpenDetail={() => void openDetail(index)} onOpenFullText={() => void openFullText(index)} onToggleFavorite={() => toggleFavorite(index)} precedent={item} />)}
          </div>
        </section>
      )}

      {stage === "empty" && (
        <section className={styles.emptyState}>
          <Search size={30} aria-hidden="true" />
          <h2>비슷한 쟁점의 판례를 찾지 못했습니다</h2>
          <p>{emptyMessage || "표현을 바꾸거나 계약 종류, 문제 발생 시점과 상대방의 행동을 더 구체적으로 적어보세요."}</p>
          <button className={styles.primaryButton} type="button" onClick={() => setStage("search")}>검색어 수정</button>
        </section>
      )}
      {fullTextCase !== null && <PrecedentModal fullTextReturnLabel="검색 결과로 돌아가기" highlight={highlightSimilar} initialView="fullText" onClose={() => setFullTextCase(null)} onHighlightChange={setHighlightSimilar} precedent={results[fullTextCase]} referenceMode="search" />}
    </main>
  );
}

export default PrecedentSearchPage;
