"use client";

import { useEffect, useMemo, useState } from "react";
import { Bookmark, FileText, LoaderCircle, Trash2, X } from "lucide-react";
import { PrecedentListItem, PrecedentModal } from "@/components/precedent-ui";
import {
  getSavedCertifiedLetterResult,
  listCertifiedLetterResults,
  mapCertifiedLetterPrecedent,
} from "@/lib/certified-letter-api";
import type {
  SavedCertifiedLetterDetail,
  SavedCertifiedLetterSummary,
} from "@/lib/certified-letter-api";
import type { CaseBasis } from "@/lib/demo-types";
import {
  deletePrecedentFavorite,
  generateFavoriteSimilarityExplanation,
  getPrecedentFavorite,
  getPrecedentFullText,
  listPrecedentFavorites,
  mapFavoriteResponse,
  mergePrecedentFullText,
  mergeSimilarityExplanation,
} from "@/lib/precedent-api";
import type { PrecedentFavoriteResponse } from "@/lib/precedent-api";
import { createClient } from "@/lib/supabase/client";
import styles from "./mypage.module.css";

type LibraryState = {
  letters: SavedCertifiedLetterSummary[];
  favorites: PrecedentFavoriteResponse[];
};

type LetterPrecedentModal = {
  precedent: CaseBasis;
  initialView: "detail" | "fullText";
};

export function MyPageLibrary() {
  const [library, setLibrary] = useState<LibraryState>({ letters: [], favorites: [] });
  const [selectedLetter, setSelectedLetter] = useState<SavedCertifiedLetterDetail | null>(null);
  const [selectedLetterPrecedent, setSelectedLetterPrecedent] = useState<LetterPrecedentModal | null>(null);
  const [selectedFavorite, setSelectedFavorite] = useState<PrecedentFavoriteResponse | null>(null);
  const [selectedFavoriteCase, setSelectedFavoriteCase] = useState<CaseBasis | null>(null);
  const [accessToken, setAccessToken] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  const [favoriteDetailLoadingId, setFavoriteDetailLoadingId] = useState<string | null>(null);
  const [favoriteFullTextLoading, setFavoriteFullTextLoading] = useState(false);
  const [favoriteComparisonLoading, setFavoriteComparisonLoading] = useState(false);
  const [deletingFavoriteId, setDeletingFavoriteId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const favoriteGroups = useMemo(() => groupFavoritesByQuery(library.favorites), [library.favorites]);

  useEffect(() => {
    let active = true;

    async function loadLibrary() {
      try {
        const supabase = createClient();
        const { data: { session } } = await supabase.auth.getSession();
        if (!session?.access_token) throw new Error("로그인 정보를 확인할 수 없습니다.");
        const [letters, favorites] = await Promise.all([
          listCertifiedLetterResults(session.access_token),
          listPrecedentFavorites(session.access_token),
        ]);
        if (!active) return;
        setAccessToken(session.access_token);
        setLibrary({ letters: letters.items, favorites: favorites.favorites });
      } catch (loadError) {
        if (active) setError(loadError instanceof Error ? loadError.message : "보관함을 불러오지 못했습니다.");
      } finally {
        if (active) setLoading(false);
      }
    }

    void loadLibrary();
    return () => { active = false; };
  }, []);

  async function openLetter(resultId: string) {
    if (!accessToken || detailLoadingId) return;
    setError("");
    setDetailLoadingId(resultId);
    try {
      setSelectedLetter(await getSavedCertifiedLetterResult(resultId, accessToken));
    } catch (detailError) {
      setError(detailError instanceof Error ? detailError.message : "내용증명을 불러오지 못했습니다.");
    } finally {
      setDetailLoadingId(null);
    }
  }

  async function openFavorite(favoriteId: string) {
    if (!accessToken || favoriteDetailLoadingId) return;
    setError("");
    setFavoriteDetailLoadingId(favoriteId);
    try {
      const favorite = await getPrecedentFavorite(favoriteId, accessToken);
      setSelectedFavorite(favorite);
      setSelectedFavoriteCase(mapFavoriteResponse(favorite));
    } catch (detailError) {
      setError(detailError instanceof Error ? detailError.message : "즐겨찾기 판례를 불러오지 못했습니다.");
    } finally {
      setFavoriteDetailLoadingId(null);
    }
  }

  async function loadFavoriteFullText() {
    if (!selectedFavoriteCase || favoriteFullTextLoading) return;
    setError("");
    setFavoriteFullTextLoading(true);
    try {
      const fullText = await getPrecedentFullText(selectedFavoriteCase.precedentId);
      setSelectedFavoriteCase((current) => current ? mergePrecedentFullText(current, fullText) : current);
    } catch (fullTextError) {
      setError(fullTextError instanceof Error ? fullTextError.message : "판례 전문을 불러오지 못했습니다.");
      throw fullTextError;
    } finally {
      setFavoriteFullTextLoading(false);
    }
  }

  async function compareFavorite() {
    if (!selectedFavorite || !selectedFavoriteCase || !accessToken || favoriteComparisonLoading) return;
    setError("");
    setFavoriteComparisonLoading(true);
    try {
      const explanation = await generateFavoriteSimilarityExplanation(selectedFavorite.favorite_id, accessToken);
      const updated = { ...selectedFavorite, similarity_explanation: explanation };
      setSelectedFavorite(updated);
      setSelectedFavoriteCase((current) => current ? mergeSimilarityExplanation(current, explanation) : current);
      setLibrary((current) => ({
        ...current,
        favorites: current.favorites.map((favorite) => favorite.favorite_id === updated.favorite_id ? updated : favorite),
      }));
    } catch (comparisonError) {
      setError(comparisonError instanceof Error ? comparisonError.message : "내 상황과 비교한 결과를 만들지 못했습니다.");
    } finally {
      setFavoriteComparisonLoading(false);
    }
  }

  async function removeFavorite(favoriteId: string) {
    if (!accessToken || deletingFavoriteId) return;
    setError("");
    setDeletingFavoriteId(favoriteId);
    try {
      await deletePrecedentFavorite(favoriteId, accessToken);
      setLibrary((current) => ({
        ...current,
        favorites: current.favorites.filter((item) => item.favorite_id !== favoriteId),
      }));
      if (selectedFavorite?.favorite_id === favoriteId) {
        setSelectedFavorite(null);
        setSelectedFavoriteCase(null);
      }
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "즐겨찾기를 삭제하지 못했습니다.");
    } finally {
      setDeletingFavoriteId(null);
    }
  }

  if (loading) {
    return <div className={styles.loading}><LoaderCircle className={styles.spinner} size={22} />보관함을 불러오고 있어요.</div>;
  }

  return (
    <>
      {error && <p className={styles.error} role="alert">{error}</p>}

      <section className={styles.pendingGrid} aria-label="추후 연결할 결과 분류">
        <article className={styles.card}><h2>계약서 검토 결과</h2><p>검토한 계약서의 위험 조항과 수정 제안을 모아볼 수 있어요.</p><span className={styles.status}>친구 기능 연결 후 저장 예정</span></article>
        <article className={styles.card}><h2>특약 생성 결과</h2><p>생성한 특약과 협상 전략을 모아볼 수 있어요.</p><span className={styles.status}>친구 기능 연결 후 저장 예정</span></article>
      </section>

      <section className={styles.librarySection} aria-labelledby="letter-results-title">
        <div className={styles.sectionHeading}><div><FileText size={20} /><h2 id="letter-results-title">내용증명 생성 결과</h2></div><span>{library.letters.length}건</span></div>
        {library.letters.length === 0 ? <p className={styles.empty}>아직 보관한 내용증명이 없습니다.</p> : (
          <div className={styles.resultList}>{library.letters.map((letter) => (
            <button className={styles.resultItem} key={letter.result_id} type="button" onClick={() => void openLetter(letter.result_id)}><span><strong>{letter.title}</strong><small>{formatDate(letter.created_at)}</small></span><b>{detailLoadingId === letter.result_id ? "불러오는 중" : "상세 보기"}</b></button>
          ))}</div>
        )}
      </section>

      <section className={styles.librarySection} aria-labelledby="favorite-results-title">
        <div className={styles.sectionHeading}><div><Bookmark size={20} /><h2 id="favorite-results-title">즐겨찾기 판례</h2></div><span>{library.favorites.length}건</span></div>
        {favoriteGroups.length === 0 ? <p className={styles.empty}>아직 즐겨찾기한 판례가 없습니다.</p> : (
          <div className={styles.questionGroups}>{favoriteGroups.map((group) => (
            <details className={styles.questionGroup} key={group.query}>
              <summary><span>검색 질문</span><strong>{group.query}</strong><small>판례 {group.favorites.length}건</small></summary>
              <div className={styles.favoriteList}>{group.favorites.map((favorite) => (
                <article className={styles.favoriteItem} key={favorite.favorite_id}>
                  <h3>{favorite.precedent.case_name ?? favorite.precedent.case_number ?? `판례 ${favorite.precedent.precedent_id}`}</h3>
                  <p>{favorite.summaries.easy_summary ?? favorite.summaries.precedent_summary ?? "요약 정보가 없습니다."}</p>
                  {favorite.similarity_explanation.status === "generated" && <span className={styles.comparisonSaved}>내 상황과 비교 결과 저장됨</span>}
                  <div className={styles.itemActions}><button className={styles.detailButton} type="button" disabled={favoriteDetailLoadingId === favorite.favorite_id} onClick={() => void openFavorite(favorite.favorite_id)}>{favoriteDetailLoadingId === favorite.favorite_id ? "불러오는 중" : "상세보기"}</button><button type="button" disabled={deletingFavoriteId === favorite.favorite_id} onClick={() => void removeFavorite(favorite.favorite_id)}><Trash2 size={15} />{deletingFavoriteId === favorite.favorite_id ? "삭제 중" : "삭제"}</button></div>
                </article>
              ))}</div>
            </details>
          ))}</div>
        )}
      </section>

      {selectedLetter && (
        <div className={styles.modalBackdrop} role="presentation" onMouseDown={() => setSelectedLetter(null)}>
          <article className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="saved-letter-title" onMouseDown={(event) => event.stopPropagation()}>
            <button className={styles.modalClose} type="button" aria-label="닫기" onClick={() => setSelectedLetter(null)}><X size={20} /></button>
            <p className={styles.modalEyebrow}>저장한 내용증명</p><h2 id="saved-letter-title">{selectedLetter.result.document.title}</h2>
            <dl className={styles.parties}><div><dt>발신인</dt><dd>{selectedLetter.result.document.sender.name}</dd><small>{formatAddress(selectedLetter.result.document.sender)}</small></div><div><dt>수신인</dt><dd>{selectedLetter.result.document.recipient.name}</dd><small>{formatAddress(selectedLetter.result.document.recipient)}</small></div></dl>
            <div className={styles.letterBody}>{selectedLetter.result.document.body}</div>
            <section className={styles.modalSection}><h3>작성 전략</h3><p>{selectedLetter.result.strategy_summary}</p></section>
            <section className={styles.modalSection}><h3>다음 단계</h3><ol>{selectedLetter.result.follow_up_actions.map((item) => <li key={`${item.order}-${item.action}`}>{item.action}</li>)}</ol></section>
            <section className={styles.modalSection}><h3>관련 법령 {selectedLetter.result.statute_evidence.length}건</h3>{selectedLetter.result.statute_evidence.length === 0 ? <p>본문에 직접 인용된 관련 법령이 없습니다.</p> : <div className={styles.evidenceList}>{selectedLetter.result.statute_evidence.map((statute) => <article className={styles.evidenceCard} key={`${statute.statute_id ?? statute.law_name}-${statute.article_label}`}><strong>{statute.law_name} {statute.article_label}{statute.article_title ? ` (${statute.article_title})` : ""}</strong><p>{statute.explanation}</p><p>{statute.content}</p>{statute.source_url && <a href={statute.source_url} target="_blank" rel="noreferrer">원문 보기 ↗</a>}</article>)}</div>}</section>
            <section className={styles.modalSection}><h3>관련 판례 {selectedLetter.result.similar_precedents.length}건</h3><div className={styles.savedPrecedents}>{selectedLetter.result.similar_precedents.map((item) => { const precedent = mapCertifiedLetterPrecedent(item); return <PrecedentListItem key={precedent.precedentId} onOpenDetail={() => setSelectedLetterPrecedent({ precedent, initialView: "detail" })} onOpenFullText={() => setSelectedLetterPrecedent({ precedent, initialView: "fullText" })} precedent={precedent} />; })}</div></section>
            {selectedLetter.result.warnings.length > 0 && <section className={styles.modalSection}><h3>확인 사항</h3><ul>{selectedLetter.result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></section>}
          </article>
        </div>
      )}

      {selectedFavorite && selectedFavoriteCase && <PrecedentModal comparisonQuery={selectedFavorite.query.text} comparisonStatus={favoriteComparisonLoading ? "generating" : selectedFavorite.similarity_explanation.status} comparisonText={selectedFavorite.similarity_explanation.matched_reason ?? undefined} fullTextLoading={favoriteFullTextLoading} onClose={() => { setSelectedFavorite(null); setSelectedFavoriteCase(null); }} onGenerateComparison={() => void compareFavorite()} onOpenFullText={loadFavoriteFullText} precedent={selectedFavoriteCase} referenceMode="search" />}
      {selectedLetterPrecedent && <PrecedentModal initialView={selectedLetterPrecedent.initialView} onClose={() => setSelectedLetterPrecedent(null)} precedent={selectedLetterPrecedent.precedent} />}
    </>
  );
}

function groupFavoritesByQuery(favorites: PrecedentFavoriteResponse[]) {
  const groups = new Map<string, PrecedentFavoriteResponse[]>();
  for (const favorite of favorites) {
    const items = groups.get(favorite.query.text) ?? [];
    items.push(favorite);
    groups.set(favorite.query.text, items);
  }
  return [...groups.entries()].map(([query, items]) => ({ query, favorites: items }));
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium" }).format(new Date(value));
}

function formatAddress(party: { address: string; detail_address: string | null }) {
  return [party.address, party.detail_address].filter(Boolean).join(" ");
}
