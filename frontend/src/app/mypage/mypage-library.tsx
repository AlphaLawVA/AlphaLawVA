"use client";

import { useEffect, useState } from "react";
import { Bookmark, FileText, LoaderCircle, Trash2, X } from "lucide-react";
import {
  getSavedCertifiedLetterResult,
  listCertifiedLetterResults,
} from "@/lib/certified-letter-api";
import type {
  SavedCertifiedLetterDetail,
  SavedCertifiedLetterSummary,
} from "@/lib/certified-letter-api";
import {
  deletePrecedentFavorite,
  listPrecedentFavorites,
} from "@/lib/precedent-api";
import type { PrecedentFavoriteResponse } from "@/lib/precedent-api";
import { createClient } from "@/lib/supabase/client";
import styles from "./mypage.module.css";

type LibraryState = {
  letters: SavedCertifiedLetterSummary[];
  favorites: PrecedentFavoriteResponse[];
};

export function MyPageLibrary() {
  const [library, setLibrary] = useState<LibraryState>({ letters: [], favorites: [] });
  const [selectedLetter, setSelectedLetter] = useState<SavedCertifiedLetterDetail | null>(null);
  const [accessToken, setAccessToken] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  const [deletingFavoriteId, setDeletingFavoriteId] = useState<string | null>(null);
  const [error, setError] = useState("");

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
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "보관함을 불러오지 못했습니다.");
        }
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
        <article className={styles.card}>
          <h2>계약서 검토 결과</h2>
          <p>검토한 계약서의 위험 조항과 수정 제안을 모아볼 수 있어요.</p>
          <span className={styles.status}>친구 기능 연결 후 저장 예정</span>
        </article>
        <article className={styles.card}>
          <h2>특약 생성 결과</h2>
          <p>생성한 특약과 협상 전략을 모아볼 수 있어요.</p>
          <span className={styles.status}>친구 기능 연결 후 저장 예정</span>
        </article>
      </section>

      <section className={styles.librarySection} aria-labelledby="letter-results-title">
        <div className={styles.sectionHeading}>
          <div><FileText size={20} /><h2 id="letter-results-title">내용증명 생성 결과</h2></div>
          <span>{library.letters.length}건</span>
        </div>
        {library.letters.length === 0 ? (
          <p className={styles.empty}>아직 보관한 내용증명이 없습니다.</p>
        ) : (
          <div className={styles.resultList}>
            {library.letters.map((letter) => (
              <button className={styles.resultItem} key={letter.result_id} type="button" onClick={() => void openLetter(letter.result_id)}>
                <span><strong>{letter.title}</strong><small>{formatDate(letter.created_at)}</small></span>
                <b>{detailLoadingId === letter.result_id ? "불러오는 중" : "상세 보기"}</b>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className={styles.librarySection} aria-labelledby="favorite-results-title">
        <div className={styles.sectionHeading}>
          <div><Bookmark size={20} /><h2 id="favorite-results-title">즐겨찾기 판례</h2></div>
          <span>{library.favorites.length}건</span>
        </div>
        {library.favorites.length === 0 ? (
          <p className={styles.empty}>아직 즐겨찾기한 판례가 없습니다.</p>
        ) : (
          <div className={styles.favoriteList}>
            {library.favorites.map((favorite) => (
              <article className={styles.favoriteItem} key={favorite.favorite_id}>
                <p className={styles.savedQuery}>“{favorite.query.text}”</p>
                <h3>{favorite.precedent.case_name ?? favorite.precedent.case_number ?? `판례 ${favorite.precedent.precedent_id}`}</h3>
                <p>{favorite.summaries.easy_summary ?? favorite.summaries.precedent_summary ?? "요약 정보가 없습니다."}</p>
                <div className={styles.itemActions}>
                  <a href={favorite.links.official_source} target="_blank" rel="noreferrer">공식 원문 보기 ↗</a>
                  <button type="button" disabled={deletingFavoriteId === favorite.favorite_id} onClick={() => void removeFavorite(favorite.favorite_id)}><Trash2 size={15} />{deletingFavoriteId === favorite.favorite_id ? "삭제 중" : "삭제"}</button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {selectedLetter && (
        <div className={styles.modalBackdrop} role="presentation" onMouseDown={() => setSelectedLetter(null)}>
          <article className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="saved-letter-title" onMouseDown={(event) => event.stopPropagation()}>
            <button className={styles.modalClose} type="button" aria-label="닫기" onClick={() => setSelectedLetter(null)}><X size={20} /></button>
            <p className={styles.modalEyebrow}>저장한 내용증명</p>
            <h2 id="saved-letter-title">{selectedLetter.result.document.title}</h2>
            <dl className={styles.parties}>
              <div><dt>발신인</dt><dd>{selectedLetter.result.document.sender.name}</dd></div>
              <div><dt>수신인</dt><dd>{selectedLetter.result.document.recipient.name}</dd></div>
            </dl>
            <div className={styles.letterBody}>{selectedLetter.result.document.body}</div>
            <section className={styles.modalSection}><h3>작성 전략</h3><p>{selectedLetter.result.strategy_summary}</p></section>
            <section className={styles.modalSection}><h3>관련 근거</h3><p>법령 {selectedLetter.result.statute_evidence.length}건 · 판례 {selectedLetter.result.similar_precedents.length}건</p></section>
          </article>
        </div>
      )}
    </>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium" }).format(new Date(value));
}
