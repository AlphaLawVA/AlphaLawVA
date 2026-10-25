"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Bookmark, LoaderCircle, Search } from "lucide-react";
import { PrecedentListItem, PrecedentMeta, PrecedentModal, PrecedentReference, PrecedentSummary } from "@/components/precedent-ui";
import { validateRealEstateInput } from "@/lib/demo-validation";
import { searchPrecedents } from "@/lib/mock-data";
import styles from "../feature-demo.module.css";

type Stage = "search" | "searching" | "results" | "empty" | "detail";
type ComparisonState = "idle" | "loading" | "complete" | "error";

const exampleQueries = [
  "전세 계약이 끝났는데 집주인이 보증금을 돌려주지 않아요.",
  "집을 샀는데 입주하고 나서 누수를 발견했어요.",
  "전셋집이 경매로 넘어가면 보증금을 돌려받을 수 있나요?",
];

function PrecedentSearchDemo() {
  const searchParams = useSearchParams();
  const [stage, setStage] = useState<Stage>(searchParams.has("case") ? "detail" : "search");
  const [query, setQuery] = useState("");
  const [queryFocused, setQueryFocused] = useState(false);
  const requestedCase = searchParams.get("case");
  const initialCase = Math.max(0, searchPrecedents.findIndex((item) => item.caseNumber === requestedCase));
  const [selected, setSelected] = useState(initialCase);
  const [favorites, setFavorites] = useState<Record<string, string>>({});
  const [comparisons, setComparisons] = useState<Record<string, ComparisonState>>({});
  const [progress, setProgress] = useState(10);
  const [error, setError] = useState("");
  const [fullTextCase, setFullTextCase] = useState<number | null>(null);
  const [highlightSimilar, setHighlightSimilar] = useState(true);
  const [savedScrollY, setSavedScrollY] = useState(0);
  const current = searchPrecedents[selected];

  useEffect(() => {
    if (stage !== "searching") return;
    const timer = window.setInterval(() => setProgress((value) => Math.min(100, value + 5)), 100);
    return () => window.clearInterval(timer);
  }, [stage]);

  useEffect(() => {
    if (stage !== "searching" || progress < 100) return;
    const timer = window.setTimeout(() => setStage(/층간소음|주차|소음/.test(query) ? "empty" : "results"), 350);
    return () => window.clearTimeout(timer);
  }, [progress, query, stage]);

  useEffect(() => {
    if (stage === "results" && savedScrollY > 0) {
      window.requestAnimationFrame(() => window.scrollTo({ top: savedScrollY }));
    }
  }, [savedScrollY, stage]);

  function startSearch() {
    const validationError = validateRealEstateInput(query, 10);
    if (validationError) {
      setError(validationError);
      setStage("search");
      return;
    }
    setError("");
    setProgress(10);
    setStage("searching");
  }

  function toggleFavorite(index: number) {
    const precedent = searchPrecedents[index];
    setFavorites((items) => {
      const next = { ...items };
      if (next[precedent.caseNumber]) delete next[precedent.caseNumber];
      else next[precedent.caseNumber] = query;
      return next;
    });
  }

  function openDetail(index: number) {
    setSelected(index);
    setSavedScrollY(window.scrollY);
    setHighlightSimilar(true);
    setStage("detail");
  }

  function compareCurrent() {
    const caseNumber = current.caseNumber;
    if (comparisons[caseNumber] === "loading" || comparisons[caseNumber] === "complete") return;
    setComparisons((items) => ({ ...items, [caseNumber]: "loading" }));
    window.setTimeout(() => {
      setComparisons((items) => ({ ...items, [caseNumber]: "complete" }));
    }, 900);
  }

  if (stage === "detail") {
    const comparison = comparisons[current.caseNumber] ?? "idle";
    return (
      <main className={styles.page}>
        <button className={styles.backButton} type="button" onClick={() => setStage("results")}><ArrowLeft size={18} />검색 결과로 돌아가기</button>
        <article className={styles.caseDetailPage}>
          <div className={styles.caseDetailHead}>
            <div><PrecedentMeta precedent={current} /><h1>{current.caseNumber}</h1></div>
            <div className={styles.detailHeadActions}>
              <button className={styles.primaryButton} type="button" onClick={() => setFullTextCase(selected)}>판례 전문 보기</button>
              <button className={styles.favoriteButton} type="button" onClick={() => toggleFavorite(selected)}><Bookmark size={18} fill={favorites[current.caseNumber] ? "currentColor" : "none"} />{favorites[current.caseNumber] ? "저장됨" : "즐겨찾기"}</button>
            </div>
          </div>
          <PrecedentSummary highlight={highlightSimilar} precedent={current} />
          <section className={styles.similarBox}>
            <h2>내 상황과 유사한 부분</h2>
            {comparison === "idle" && <><p>필요할 때 검색 질문과 판례 근거를 비교해 설명합니다.</p><button className={styles.primaryButton} type="button" onClick={compareCurrent}>내 상황과 비교하기</button></>}
            {comparison === "loading" && <p className={styles.loadingInline}><LoaderCircle className={styles.spinner} size={17} />비교 내용을 만들고 있습니다.</p>}
            {comparison === "complete" && <p>입력한 상황은 보증금 반환 또는 임대차 관계의 책임 범위를 확인해야 한다는 점에서 이 판례의 참고 부분과 연결해 살펴볼 수 있습니다. 다만 실제 결과는 계약 내용과 사실관계에 따라 달라질 수 있습니다.</p>}
            {comparison === "error" && <><p>비교 내용을 만들지 못했습니다.</p><button className={styles.primaryButton} type="button" onClick={compareCurrent}>재시도</button></>}
          </section>
          <PrecedentReference highlight={highlightSimilar} mode="search" onHighlightChange={setHighlightSimilar} precedent={current} />
          <div className={styles.caseActions}><a href={current.url} target="_blank" rel="noreferrer">국가법령정보센터 공식 원문 보기 ↗</a></div>
        </article>
        {fullTextCase !== null && <PrecedentModal fullTextReturnLabel="검색 결과로 돌아가기" highlight={highlightSimilar} initialView="fullText" onClose={() => setFullTextCase(null)} onHighlightChange={setHighlightSimilar} precedent={searchPrecedents[fullTextCase]} referenceMode="search" />}
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
          <input id="case-query" onBlur={() => setQueryFocused(false)} onFocus={() => setQueryFocused(true)} placeholder={queryFocused ? "" : "예: 전세 계약이 끝났는데 집주인이 보증금을 돌려주지 않아요."} value={query} onChange={(event) => setQuery(event.target.value)} />
          <button className={styles.primaryButton} type="button" onClick={startSearch}><Search size={17} />검색</button>
        </div>
        <p className={styles.searchGuide}>부동산 매매·임대차와 관련해 계약 종류, 문제 발생 시점, 상대방의 행동을 함께 적어주세요.</p>
        {error && <div className={styles.validationNotice} role="alert"><Search size={19} /><div><strong>검색 범위를 확인해주세요</strong><span>{error}</span></div></div>}
        {stage === "search" && <div className={styles.recentSearches}><span>예시 질문</span>{exampleQueries.map((item) => <button type="button" key={item} onClick={() => setQuery(item)}>{item}</button>)}</div>}
      </section>

      {stage === "searching" && (
        <section className={styles.processingCard}>
          <span><LoaderCircle className={styles.spinner} size={28} aria-hidden="true" /></span>
          <h2>비슷한 쟁점의 판례를 찾고 있습니다</h2>
          <p>입력한 상황의 핵심 쟁점을 정리하고 판례 데이터베이스를 검색하고 있습니다.</p>
        </section>
      )}

      {stage === "results" && (
        <section className={styles.caseResults}>
          <div className={styles.resultsHeading}><h2>유사 판례 목록</h2><span>{searchPrecedents.length}건</span></div>
          <div className={styles.caseCardList}>
            {searchPrecedents.map((item, index) => <PrecedentListItem favorite={Boolean(favorites[item.caseNumber])} key={item.caseNumber} onOpenDetail={() => openDetail(index)} onOpenFullText={() => setFullTextCase(index)} onToggleFavorite={() => toggleFavorite(index)} precedent={item} />)}
          </div>
        </section>
      )}

      {stage === "empty" && (
        <section className={styles.emptyState}>
          <Search size={30} aria-hidden="true" />
          <h2>비슷한 쟁점의 판례를 찾지 못했습니다</h2>
          <p>표현을 바꾸거나 계약 종류, 문제 발생 시점과 상대방의 행동을 더 구체적으로 적어보세요.</p>
          <button className={styles.primaryButton} type="button" onClick={() => setStage("search")}>검색어 수정</button>
        </section>
      )}
      {fullTextCase !== null && <PrecedentModal fullTextReturnLabel="검색 결과로 돌아가기" highlight={highlightSimilar} initialView="fullText" onClose={() => setFullTextCase(null)} onHighlightChange={setHighlightSimilar} precedent={searchPrecedents[fullTextCase]} referenceMode="search" />}
    </main>
  );
}

export default function PrecedentSearchPage() {
  return <Suspense><PrecedentSearchDemo /></Suspense>;
}
