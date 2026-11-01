"use client";

import { Bookmark, ExternalLink, FileText, X } from "lucide-react";
import { useState } from "react";
import type { CaseBasis } from "@/lib/demo-types";
import styles from "./precedent-ui.module.css";

type ReferenceMode = "search" | "related";

type PrecedentReferenceProps = {
  precedent: CaseBasis;
  mode: ReferenceMode;
  highlight?: boolean;
  onHighlightChange?: (value: boolean) => void;
};

export function PrecedentMeta({ precedent }: { precedent: CaseBasis }) {
  return (
    <p className={styles.meta}>
      {[precedent.court, precedent.decisionDate, precedent.judgmentType].filter(Boolean).join(" · ")}
    </p>
  );
}

export function PrecedentReference({
  precedent,
  mode,
  highlight: controlledHighlight,
  onHighlightChange,
}: PrecedentReferenceProps) {
  const [localHighlight, setLocalHighlight] = useState(true);
  const highlight = controlledHighlight ?? localHighlight;
  const setHighlight = onHighlightChange ?? setLocalHighlight;
  const isSearch = mode === "search";
  const chunks = precedent.evidenceChunks ?? [];

  if (chunks.length === 0) return null;

  return (
    <section className={styles.referenceDisclosure}>
      <div className={styles.switchRow}>
        <span>{isSearch ? "비슷한 부분 표시" : "참고 부분 표시"}</span>
        <button
          aria-checked={highlight}
          className={`${styles.highlightSwitch} ${highlight ? styles.highlightSwitchOn : ""}`}
          onClick={() => setHighlight(!highlight)}
          role="switch"
          type="button"
        >
          <span />
        </button>
        <strong className={styles.switchValue}>{highlight ? "ON" : "OFF"}</strong>
      </div>
      <details className={styles.referenceDetails}>
        <summary>{isSearch ? "이 판례가 검색된 근거 보기" : "판례에서 참고한 부분 보기"}</summary>
        <div className={styles.chunkList}>
          {chunks.map((chunk) => (
            <article className={styles.chunk} key={`${chunk.section}-${chunk.content}`}>
              <strong>{chunk.section}</strong>
              <p><span className={highlight ? styles.textHighlight : undefined}>{chunk.content}</span></p>
            </article>
          ))}
        </div>
      </details>
    </section>
  );
}

export function PrecedentSummary({
  precedent,
}: {
  precedent: CaseBasis;
}) {
  return (
    <div className={styles.summaryColumns}>
      <section>
        <h3>판례요약</h3>
        <p>{precedent.summary}</p>
      </section>
      <section className={styles.easySummary}>
        <h3>쉬운요약</h3>
        <p>{precedent.easySummary ?? precedent.summary}</p>
      </section>
    </div>
  );
}

type ListItemProps = {
  precedent: CaseBasis;
  onOpenDetail: () => void;
  onOpenFullText: () => void;
  favorite?: boolean;
  onToggleFavorite?: () => void;
};

export function PrecedentListItem({
  precedent,
  onOpenDetail,
  onOpenFullText,
  favorite = false,
  onToggleFavorite,
}: ListItemProps) {
  return (
    <article className={styles.listItem}>
      <div className={styles.listMain}>
        <PrecedentMeta precedent={precedent} />
        <h3>{precedent.caseNumber}</h3>
        <p className={styles.preview}>{precedent.easySummary ?? precedent.summary}</p>
      </div>
      <div className={styles.listActions}>
        <button onClick={onOpenFullText} type="button"><FileText size={16} />판례 전문</button>
        <button onClick={onOpenDetail} type="button">상세보기</button>
        {onToggleFavorite && (
          <button aria-label="즐겨찾기" className={styles.bookmarkButton} onClick={onToggleFavorite} type="button">
            <Bookmark fill={favorite ? "currentColor" : "none"} size={18} />
          </button>
        )}
      </div>
    </article>
  );
}

type ModalProps = {
  precedent: CaseBasis;
  initialView?: "detail" | "fullText";
  referenceMode?: ReferenceMode;
  fullTextReturnLabel?: string;
  highlight?: boolean;
  onHighlightChange?: (value: boolean) => void;
  comparisonQuery?: string;
  comparisonStatus?: "generated" | "generating" | "not_generated" | "failed";
  comparisonText?: string;
  onGenerateComparison?: () => void;
  onOpenFullText?: () => Promise<void> | void;
  fullTextLoading?: boolean;
  onClose: () => void;
};

export function PrecedentModal({
  precedent,
  initialView = "detail",
  referenceMode = "related",
  fullTextReturnLabel = "관련 판례 목록으로 돌아가기",
  highlight: controlledHighlight,
  onHighlightChange,
  comparisonQuery,
  comparisonStatus,
  comparisonText,
  onGenerateComparison,
  onOpenFullText,
  fullTextLoading = false,
  onClose,
}: ModalProps) {
  const [view, setView] = useState<"detail" | "fullText">(initialView);
  const [localHighlight, setLocalHighlight] = useState(true);
  const highlight = controlledHighlight ?? localHighlight;
  const setHighlight = onHighlightChange ?? setLocalHighlight;
  const fullTextSections = [
    ["order", "주문", precedent.fullText?.order],
    ["claim", "청구취지", precedent.fullText?.claim],
    ["lowerCourt", "원심판결", precedent.fullText?.lowerCourt],
    ["reason", "이유", precedent.fullText?.reason],
  ].filter((item): item is [string, string, string] => Boolean(item[2]));
  const openedFromList = initialView === "fullText";

  async function openFullText() {
    try {
      await onOpenFullText?.();
      setView("fullText");
    } catch {
      // The caller reports the load error and keeps the detail view open.
    }
  }

  return (
    <div aria-modal="true" className={styles.overlay} role="dialog">
      <section className={styles.modal}>
        <header className={styles.modalHeader}>
          <div>
            <PrecedentMeta precedent={precedent} />
            <h2>{precedent.caseNumber}</h2>
          </div>
          <button aria-label="닫기" className={styles.closeButton} onClick={onClose} type="button"><X size={20} /></button>
        </header>

        {view === "detail" ? (
          <div className={styles.modalContent}>
            <PrecedentSummary precedent={precedent} />
            {comparisonQuery && (
              <section className={styles.comparisonBox}>
                <span>저장한 질문</span>
                <h3>{comparisonQuery}</h3>
                {comparisonStatus === "generated" && comparisonText ? (
                  <p>{comparisonText}</p>
                ) : comparisonStatus === "generating" ? (
                  <p>내 상황과 판례를 비교하고 있습니다.</p>
                ) : (
                  <>
                    <p>{comparisonStatus === "failed" ? "비교 결과를 만들지 못했습니다. 다시 시도해 주세요." : "저장한 질문과 이 판례를 비교한 설명은 아직 생성하지 않았습니다."}</p>
                    {onGenerateComparison && <button className={styles.primaryAction} onClick={onGenerateComparison} type="button">{comparisonStatus === "failed" ? "다시 비교하기" : "내 상황과 비교하기"}</button>}
                  </>
                )}
              </section>
            )}
            <PrecedentReference highlight={highlight} mode={referenceMode} onHighlightChange={setHighlight} precedent={precedent} />
            <div className={styles.modalActions}>
              <button className={styles.primaryAction} disabled={fullTextLoading} onClick={() => void openFullText()} type="button">{fullTextLoading ? "전문 불러오는 중" : "판례 전문 보기"}</button>
              <OfficialLink precedent={precedent} />
            </div>
          </div>
        ) : (
          <div className={styles.modalContent}>
            <button className={styles.returnButton} onClick={() => openedFromList ? onClose() : setView("detail")} type="button">
              {openedFromList ? fullTextReturnLabel : "상세보기로 돌아가기"}
            </button>
            <p className={styles.dataNotice}>보유 중인 판례 데이터로 제공하는 전문입니다.</p>
            <div className={styles.fullText}>
              {fullTextSections.map(([key, heading, content]) => (
                <section key={heading}>
                  <h3>{heading}</h3>
                  <p><HighlightedFullText content={content} highlight={highlight} ranges={findHighlightRanges(precedent, key, content)} /></p>
                </section>
              ))}
            </div>
            <div className={styles.modalActions}><OfficialLink precedent={precedent} /></div>
          </div>
        )}
      </section>
    </div>
  );
}

function HighlightedFullText({ content, highlight, ranges }: { content: string; highlight: boolean; ranges?: Array<{ startOffset: number; endOffset: number }> }) {
  if (!highlight) return content;
  if (ranges === undefined) return content;
  if (ranges.length === 0) return content;
  const parts: Array<{ text: string; highlighted: boolean }> = [];
  let cursor = 0;
  for (const range of [...ranges].sort((a, b) => a.startOffset - b.startOffset)) {
    const start = Math.max(cursor, range.startOffset);
    const end = Math.max(start, Math.min(content.length, range.endOffset));
    if (cursor < start) parts.push({ text: content.slice(cursor, start), highlighted: false });
    if (start < end) parts.push({ text: content.slice(start, end), highlighted: true });
    cursor = end;
  }
  if (cursor < content.length) parts.push({ text: content.slice(cursor), highlighted: false });
  return <>{parts.map((part, index) => <span className={part.highlighted ? styles.textHighlight : undefined} key={`${index}-${part.text.length}`}>{part.text}</span>)}</>;
}

function findHighlightRanges(precedent: CaseBasis, sectionKey: string, content: string) {
  const configured = sectionKey === "reason"
    ? (precedent.highlightChunks ?? []).map(({ startOffset, endOffset }) => ({ startOffset, endOffset }))
    : [];
  const exactMatches = (precedent.evidenceChunks ?? []).flatMap((chunk) => {
    const startOffset = content.indexOf(chunk.content);
    return startOffset < 0
      ? []
      : [{ startOffset, endOffset: startOffset + chunk.content.length }];
  });
  const ranges = [...configured, ...exactMatches]
    .filter((range) => range.startOffset >= 0 && range.endOffset > range.startOffset && range.startOffset < content.length)
    .sort((a, b) => a.startOffset - b.startOffset);
  return ranges.length > 0 ? ranges : undefined;
}

function OfficialLink({ precedent }: { precedent: CaseBasis }) {
  return (
    <a href={precedent.url} rel="noreferrer" target="_blank">
      국가법령정보센터 공식 원문 보기 <ExternalLink size={15} />
    </a>
  );
}
