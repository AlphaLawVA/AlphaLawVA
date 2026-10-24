"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Bookmark, ExternalLink, Search } from "lucide-react";
import styles from "../feature-demo.module.css";

type Stage = "search" | "results" | "detail";

const cases = [
  {
    title: "임대차보증금",
    caseNumber: "대법원 2022. 3. 17. 선고 2021다210720 판결",
    issue: "주택 소유자가 바뀐 뒤 임대인의 지위와 보증금 반환채무가 누구에게 이어지는지",
    summary: "대항요건을 갖춘 임차주택의 양수인은 임대인의 지위를 승계하며, 보증금 반환채무도 소유권과 결합하여 양수인에게 이전된다고 판단한 판례입니다.",
    easy: "세입자가 대항력을 갖춘 상태에서 집주인이 바뀌면, 새 집주인이 기존 임대차와 보증금 반환 책임을 이어받는다는 취지입니다.",
    similar: "계약 중 집주인이 바뀌었고 새 소유자에게 보증금 반환을 요구할 수 있는지가 문제라는 점이 입력한 상황과 유사합니다.",
    url: "https://www.law.go.kr/LSW/precInfoP.do?precSeq=227209",
  },
  {
    title: "임대차보증금반환",
    caseNumber: "대법원 2015. 5. 29. 선고 2012다84370 판결",
    issue: "부동산 매수인이 보증금 반환채무를 인수했을 때 기존 임대인이 책임에서 벗어나는 조건",
    summary: "매수인이 보증금 반환채무를 인수하고 매매대금에서 공제했더라도, 기존 임대인을 면책하려면 원칙적으로 임차인의 승낙이 필요하다고 본 판례입니다.",
    easy: "새 소유자가 보증금을 돌려주기로 했다는 사정만으로 기존 임대인의 책임이 자동으로 사라지는 것은 아니며, 세입자의 동의 여부를 신중히 봐야 한다는 취지입니다.",
    similar: "소유권 이전 전후에 누구에게 보증금 반환을 요구할 수 있는지를 판단해야 한다는 점이 입력한 상황과 닮았습니다.",
    url: "https://www.law.go.kr/LSW/precInfoP.do?precSeq=178111",
  },
  {
    title: "토지인도",
    caseNumber: "대법원 2023. 11. 2. 선고 2023다249661 판결",
    issue: "임차인이 퇴거할 때 임대 당시부터 있던 시설까지 원상회복해야 하는지",
    summary: "임차인은 자신이 변경한 부분을 원칙적으로 원상회복해야 하지만, 임대 당시 이미 존재하던 시설까지 당연히 철거해야 하는 것은 아니며 계약 경위와 당시 상태 등을 구체적으로 살펴야 한다고 본 판례입니다.",
    easy: "세입자가 들어오기 전부터 있던 시설까지 무조건 원래대로 돌려놓아야 하는 것은 아니며, 누가 무엇을 바꿨는지와 계약 내용을 따져봐야 한다는 뜻입니다.",
    similar: "계약서의 원상회복 문구가 통상적인 사용으로 생긴 마모와 기존 시설까지 모두 임차인 책임으로 정하고 있는지가 문제라는 점과 연결됩니다.",
    url: "https://www.law.go.kr/LSW/precInfoP.do?precSeq=237925",
  },
];

const recentQueries = [
  "계약이 끝났는데 집주인이 보증금을 돌려주지 않아요",
  "집주인이 바뀌면 보증금은 누구에게 받아야 하나요",
];

function PrecedentSearchDemo() {
  const searchParams = useSearchParams();
  const [stage, setStage] = useState<Stage>(searchParams.has("case") ? "detail" : "search");
  const [query, setQuery] = useState("전세 계약이 끝났는데 집주인이 보증금을 돌려주지 않아요.");
  const requestedCase = searchParams.get("case");
  const initialCase = Math.max(0, cases.findIndex((item) => item.caseNumber === requestedCase));
  const [selected, setSelected] = useState(initialCase);
  const [favorites, setFavorites] = useState<number[]>([]);
  const current = cases[selected];

  function toggleFavorite(index: number) {
    setFavorites((items) => items.includes(index) ? items.filter((item) => item !== index) : [...items, index]);
  }

  if (stage === "detail") {
    return (
      <main className={styles.page}>
        <button className={styles.backButton} type="button" onClick={() => setStage("results")}><ArrowLeft size={18} />검색 결과</button>
        <article className={styles.caseDetailPage}>
          <div className={styles.caseDetailHead}>
            <div><p className={styles.eyebrow}>판례 상세</p><h1>{current.title}</h1><p>{current.caseNumber}</p></div>
            <button className={styles.favoriteButton} type="button" onClick={() => toggleFavorite(selected)}><Bookmark size={18} fill={favorites.includes(selected) ? "currentColor" : "none"} />{favorites.includes(selected) ? "저장됨" : "즐겨찾기"}</button>
          </div>
          <section className={styles.caseIssue}><span>입력한 상황과 비슷한 쟁점</span><p>{current.issue}</p></section>
          <div className={styles.summaryColumns}>
            <section><h2>판례 요약</h2><p>{current.summary}</p></section>
            <section className={styles.easySummary}><h2>쉬운 요약</h2><p>{current.easy}</p></section>
          </div>
          <section className={styles.similarBox}><h2>내 상황과 유사한 부분</h2><p>{current.similar}</p></section>
          <div className={styles.caseActions}><a href={current.url} target="_blank" rel="noreferrer">국가법령정보센터 원문 보기 <ExternalLink size={15} /></a></div>
        </article>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>비슷한 쟁점의 판례 찾기</p>
        <h1>판례 검색</h1>
        <p>부동산 매매·임대차에서 겪은 상황을 평소 사용하는 말로 입력해보세요.</p>
      </header>
      <section className={styles.searchHero}>
        <label htmlFor="case-query">상황을 알려주세요</label>
        <div className={styles.searchBox}>
          <input id="case-query" value={query} onChange={(event) => setQuery(event.target.value)} />
          <button className={styles.primaryButton} type="button" onClick={() => setStage("results")}><Search size={17} />검색</button>
        </div>
        <p className={styles.searchGuide}>부동산 매매·임대차와 관련해 계약 종류, 문제 발생 시점, 상대방의 행동을 함께 적어주세요.</p>
        {stage === "search" && <div className={styles.recentSearches}><span>최근 검색</span>{recentQueries.map((item) => <button type="button" key={item} onClick={() => { setQuery(item); setStage("results"); }}>{item}</button>)}</div>}
      </section>

      {stage === "results" && (
        <section className={styles.caseResults}>
          <div className={styles.resultsHeading}><div><p>검색한 내용</p><h2>“{query}”</h2></div><span>{cases.length}건</span></div>
          <div className={styles.caseCardList}>
            {cases.map((item, index) => (
              <article className={styles.caseCard} key={item.caseNumber}>
                <button className={styles.bookmarkIcon} type="button" aria-label="즐겨찾기" onClick={() => toggleFavorite(index)}><Bookmark size={19} fill={favorites.includes(index) ? "currentColor" : "none"} /></button>
                <p className={styles.caseMeta}>{item.caseNumber}</p>
                <h3>{item.title}</h3>
                <div><strong>비슷한 쟁점</strong><p>{item.issue}</p></div>
                <p className={styles.caseSummary}>{item.easy}</p>
                <div className={styles.cardActions}><button type="button" onClick={() => { setSelected(index); setStage("detail"); }}>상세보기</button><a href={item.url} target="_blank" rel="noreferrer">원문 <ExternalLink size={14} /></a></div>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

export default function PrecedentSearchPage() {
  return <Suspense><PrecedentSearchDemo /></Suspense>;
}
