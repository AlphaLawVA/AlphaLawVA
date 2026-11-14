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
import { getGenerationResult, listGenerationResults } from "@/lib/contract-api";
import type {
  ContractReviewResult,
  SavedGenerationDetail,
  SavedGenerationSummary,
  SpecialClauseDraftResult,
} from "@/lib/contract-api";
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
  generations: SavedGenerationSummary[];
};

type LetterPrecedentModal = {
  precedent: CaseBasis;
  initialView: "detail" | "fullText";
};

export function MyPageLibrary() {
  const [library, setLibrary] = useState<LibraryState>({ letters: [], favorites: [], generations: [] });
  const [selectedLetter, setSelectedLetter] = useState<SavedCertifiedLetterDetail | null>(null);
  const [selectedGeneration, setSelectedGeneration] = useState<SavedGenerationDetail | null>(null);
  const [selectedLetterPrecedent, setSelectedLetterPrecedent] = useState<LetterPrecedentModal | null>(null);
  const [selectedFavorite, setSelectedFavorite] = useState<PrecedentFavoriteResponse | null>(null);
  const [selectedFavoriteCase, setSelectedFavoriteCase] = useState<CaseBasis | null>(null);
  const [selectedFavoriteInitialView, setSelectedFavoriteInitialView] = useState<"detail" | "fullText">("detail");
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
        const [letters, favorites, generations] = await Promise.all([
          listCertifiedLetterResults(session.access_token),
          listPrecedentFavorites(session.access_token),
          // 저장 기능이 없는 서버에서도 보관함이 열리도록 실패하면 빈 목록으로 둔다.
          listGenerationResults(session.access_token).catch(() => ({ items: [] as SavedGenerationSummary[] })),
        ]);
        if (!active) return;
        setAccessToken(session.access_token);
        setLibrary({ letters: letters.items, favorites: favorites.favorites, generations: generations.items });
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

  async function openGeneration(resultId: string) {
    if (!accessToken || detailLoadingId) return;
    setError("");
    setDetailLoadingId(resultId);
    try {
      setSelectedGeneration(await getGenerationResult(resultId, accessToken));
    } catch (detailError) {
      setError(detailError instanceof Error ? detailError.message : "저장한 결과를 불러오지 못했습니다.");
    } finally {
      setDetailLoadingId(null);
    }
  }

  async function openFavorite(
    favorite: PrecedentFavoriteResponse,
    initialView: "detail" | "fullText",
  ) {
    if (!accessToken || favoriteDetailLoadingId) return;
    setError("");
    setFavoriteDetailLoadingId(favorite.favorite_id);
    try {
      const [detail, fullText] = await Promise.all([
        getPrecedentFavorite(favorite.favorite_id, accessToken),
        initialView === "fullText"
          ? getPrecedentFullText(favorite.precedent.precedent_id)
          : Promise.resolve(null),
      ]);
      const precedent = mapFavoriteResponse(detail);
      setSelectedFavorite(detail);
      setSelectedFavoriteCase(
        fullText ? mergePrecedentFullText(precedent, fullText) : precedent,
      );
      setSelectedFavoriteInitialView(initialView);
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

      {([
        ["contract_review", "계약서 검토 결과", "아직 보관한 계약서 검토 결과가 없습니다."],
        ["special_clause", "특약 생성 결과", "아직 보관한 특약 생성 결과가 없습니다."],
      ] as const).map(([kind, heading, emptyText]) => {
        const items = library.generations.filter((item) => item.kind === kind);
        return (
          <section className={styles.librarySection} aria-labelledby={`${kind}-results-title`} key={kind}>
            <div className={styles.sectionHeading}><div><FileText size={20} /><h2 id={`${kind}-results-title`}>{heading}</h2></div><span>{items.length}건</span></div>
            {items.length === 0 ? <p className={styles.empty}>{emptyText}</p> : (
              <div className={styles.resultList}>{items.map((item) => (
                <button className={styles.resultItem} key={item.result_id} type="button" onClick={() => void openGeneration(item.result_id)}><span><strong>{item.title}</strong><small>{formatDate(item.created_at)}</small></span><b>{detailLoadingId === item.result_id ? "불러오는 중" : "상세 보기"}</b></button>
              ))}</div>
            )}
          </section>
        );
      })}

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
                <div className={styles.favoriteEntry} key={favorite.favorite_id}>
                  <PrecedentListItem
                    onOpenDetail={() => void openFavorite(favorite, "detail")}
                    onOpenFullText={() => void openFavorite(favorite, "fullText")}
                    precedent={mapFavoriteResponse(favorite)}
                  />
                  <div className={styles.favoriteEntryFooter}>
                    {favorite.similarity_explanation.status === "generated" ? <span className={styles.comparisonSaved}>내 상황과 비교 결과 저장됨</span> : <span>비교 결과 미생성</span>}
                    <button type="button" disabled={deletingFavoriteId === favorite.favorite_id} onClick={() => void removeFavorite(favorite.favorite_id)}><Trash2 size={15} />{deletingFavoriteId === favorite.favorite_id ? "삭제 중" : "즐겨찾기 삭제"}</button>
                  </div>
                </div>
              ))}</div>
            </details>
          ))}</div>
        )}
      </section>

      {selectedGeneration && (
        <div className={styles.modalBackdrop} role="presentation" onMouseDown={() => setSelectedGeneration(null)}>
          <article className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="saved-generation-title" onMouseDown={(event) => event.stopPropagation()}>
            <button className={styles.modalClose} type="button" aria-label="닫기" onClick={() => setSelectedGeneration(null)}><X size={20} /></button>
            <p className={styles.modalEyebrow}>{selectedGeneration.kind === "contract_review" ? "저장한 계약서 검토" : "저장한 특약 생성"}</p>
            <h2 id="saved-generation-title">{selectedGeneration.title}</h2>
            <GenerationDetailBody detail={selectedGeneration} />
          </article>
        </div>
      )}

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

      {selectedFavorite && selectedFavoriteCase && <PrecedentModal comparisonQuery={selectedFavorite.query.text} comparisonStatus={favoriteComparisonLoading ? "generating" : selectedFavorite.similarity_explanation.status} comparisonText={selectedFavorite.similarity_explanation.matched_reason ?? undefined} fullTextLoading={favoriteFullTextLoading} initialView={selectedFavoriteInitialView} onClose={() => { setSelectedFavorite(null); setSelectedFavoriteCase(null); setSelectedFavoriteInitialView("detail"); }} onGenerateComparison={() => void compareFavorite()} onOpenFullText={loadFavoriteFullText} precedent={selectedFavoriteCase} referenceMode="search" />}
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

function GenerationDetailBody({ detail }: { detail: SavedGenerationDetail }) {
  if (detail.kind === "special_clause") {
    const draft = detail.payload as SpecialClauseDraftResult;
    return (
      <>
        {draft.items.map((item) => (
          <section className={styles.modalSection} key={item.request_index}>
            <h3>{item.title ?? `요청 ${item.request_index}`}</h3>
            <p>요청: {item.request_text}</p>
            {item.draft_text && <div className={styles.letterBody}>{item.draft_text}</div>}
            {item.needs_input.length > 0 && <ul>{item.needs_input.map((question) => <li key={question}>{question}</li>)}</ul>}
            {item.reason && <p>이유: {item.reason}</p>}
            {item.strategy && <p>협의 안내: {item.strategy}</p>}
          </section>
        ))}
        <p>{draft.disclaimer}</p>
      </>
    );
  }
  const review = detail.payload as ContractReviewResult;
  return (
    <>
      {review.analysis.length > 0 && (
        <section className={styles.modalSection}>
          <h3>검토 의견 {review.analysis.length}건</h3>
          {review.analysis.map((item) => (
            <div key={item.item_id}>
              <p>{item.summary}</p>
              {item.revision && <p>수정 제안: {item.revision}</p>}
              {item.negotiation && <p>협의 안내: {item.negotiation}</p>}
            </div>
          ))}
        </section>
      )}
      <section className={styles.modalSection}>
        <h3>확인 필요 항목</h3>
        <ul>{review.precheck_items.map((item) => <li key={item.item_id}><strong>{item.label}</strong> {item.message}</li>)}</ul>
      </section>
      <p>{review.disclaimer}</p>
    </>
  );
}
