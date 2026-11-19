"use client";

import { PrecedentListItem } from "@/components/precedent-ui";
import { StatuteExcerpt } from "@/components/statute-excerpt";
import { contractEvidenceToPrecedent } from "@/lib/contract-api";
import type {
  ContractEvidence,
  ContractReviewAnalysis,
  ContractReviewItem,
  ContractReviewResult,
  SavedGenerationDetail,
  SpecialClauseDraftItem,
  SpecialClauseDraftResult,
} from "@/lib/contract-api";
import type { CaseBasis } from "@/lib/demo-types";
import { statuteOfficialUrl } from "@/lib/statute-link";
import styles from "./mypage.module.css";

type OpenPrecedent = (precedent: CaseBasis, initialView: "detail" | "fullText") => void;

const REVIEW_STATUS_LABEL: Record<string, string> = {
  safe: "문제없음",
  caution: "주의",
  check: "확인 필요",
};

const REVIEW_STATUS_CLASS: Record<string, string> = {
  safe: styles.statusSafe,
  caution: styles.statusCaution,
  check: styles.statusCheck,
};

export function SavedGenerationDetailBody({
  detail,
  onOpenPrecedent,
}: {
  detail: SavedGenerationDetail;
  onOpenPrecedent: OpenPrecedent;
}) {
  if (detail.kind === "special_clause") {
    return <SpecialClauseBody draft={detail.payload as SpecialClauseDraftResult} onOpenPrecedent={onOpenPrecedent} />;
  }
  return <ContractReviewBody review={detail.payload as ContractReviewResult} onOpenPrecedent={onOpenPrecedent} />;
}

function StatuteCards({ evidence }: { evidence: ContractEvidence[] }) {
  return (
    <details className={styles.disclosure}>
      <summary>관련 법령 {evidence.length}건</summary>
      {evidence.length === 0 ? <p>본문에 직접 연결된 법령이 없습니다.</p> : (
        <div className={styles.evidenceList}>
          {evidence.map((item) => {
            const law = String(item.metadata.law_name ?? item.title);
            const article = String(item.metadata.article_label ?? "");
            const url = typeof item.metadata.url === "string" ? item.metadata.url : statuteOfficialUrl(law, article);
            return (
              <article className={styles.evidenceCard} key={item.source_id}>
                <strong>{law} {article}</strong>
                <StatuteExcerpt text={item.text} />
                {url && <a href={url} target="_blank" rel="noreferrer">원문 보기 ↗</a>}
              </article>
            );
          })}
        </div>
      )}
    </details>
  );
}

function PrecedentCards({ evidence, onOpenPrecedent }: { evidence: ContractEvidence[]; onOpenPrecedent: OpenPrecedent }) {
  return (
    <details className={styles.disclosure}>
      <summary>관련 판례 {evidence.length}건</summary>
      {evidence.length === 0 ? <p>연결된 관련 판례가 없습니다.</p> : (
        <div className={styles.savedPrecedents}>
          {evidence.map((item) => {
            const precedent = contractEvidenceToPrecedent(item);
            return (
              <PrecedentListItem
                key={precedent.precedentId}
                onOpenDetail={() => onOpenPrecedent(precedent, "detail")}
                onOpenFullText={() => onOpenPrecedent(precedent, "fullText")}
                precedent={precedent}
              />
            );
          })}
        </div>
      )}
    </details>
  );
}

function SpecialClauseBody({ draft, onOpenPrecedent }: { draft: SpecialClauseDraftResult; onOpenPrecedent: OpenPrecedent }) {
  return (
    <>
      {draft.items.map((item) => (
        <SpecialClauseCard draft={draft} item={item} key={item.request_index} onOpenPrecedent={onOpenPrecedent} />
      ))}
      <p className={styles.disclaimer}>{draft.disclaimer}</p>
    </>
  );
}

function SpecialClauseCard({
  draft,
  item,
  onOpenPrecedent,
}: {
  draft: SpecialClauseDraftResult;
  item: SpecialClauseDraftItem;
  onOpenPrecedent: OpenPrecedent;
}) {
  const itemEvidence = draft.evidence.filter((entry) => entry.item_id === `request-${item.request_index}`);
  const statutes = itemEvidence.filter(
    (entry) => entry.source_type === "statute" && item.evidence_source_ids.includes(entry.source_id),
  );
  const precedents = itemEvidence.filter((entry) => entry.source_type === "precedent");
  const outOfScope = item.status === "out_of_scope";

  return (
    <section className={styles.resultCard}>
      <header className={styles.resultCardHeader}>
        <span className={styles.resultIndex}>특약 {item.request_index}</span>
        <h3>{outOfScope ? "범위 밖 요청" : item.title ?? `특약 ${item.request_index}`}</h3>
      </header>

      <div className={styles.requestQuote}>
        <small>요청하신 내용</small>
        <p>{item.request_text}</p>
      </div>

      {outOfScope ? (
        <p className={styles.plainText}>{item.reason ?? "부동산 계약 특약과 관련된 요청만 도와드릴 수 있어요."}</p>
      ) : (
        <>
          {item.draft_text && (
            <div className={styles.draftBlock}>
              <small>생성된 특약</small>
              <p>{item.draft_text}</p>
            </div>
          )}
          {item.strategy && (
            <div className={styles.noteBlock}>
              <small>협상 전략</small>
              <p>{item.strategy}</p>
            </div>
          )}
          {item.needs_input.length > 0 && (
            <div className={styles.noteBlock}>
              <small>특약 작성 시 함께 정해두면 좋은 내용</small>
              <ul>{item.needs_input.map((question) => <li key={question}>{question}</li>)}</ul>
            </div>
          )}
          <StatuteCards evidence={statutes} />
          <PrecedentCards evidence={precedents} onOpenPrecedent={onOpenPrecedent} />
        </>
      )}
    </section>
  );
}

function ContractReviewBody({ review, onOpenPrecedent }: { review: ContractReviewResult; onOpenPrecedent: OpenPrecedent }) {
  const analysisById = new Map(review.analysis.map((entry) => [entry.item_id, entry]));
  const registryAnalyses = review.analysis.filter((entry) => entry.item_id.startsWith("registry_"));
  const counts = { safe: 0, caution: 0, check: 0 };
  for (const entry of review.analysis) counts[entry.review_status] += 1;
  const attentionItems = review.precheck_items.filter((item) => item.message.trim());

  return (
    <>
      <div className={styles.summaryChips}>
        <span className={styles.statusCaution}>주의 {counts.caution}</span>
        <span className={styles.statusCheck}>확인 필요 {counts.check}</span>
        <span className={styles.statusSafe}>문제없음 {counts.safe}</span>
      </div>

      <h3 className={styles.groupTitle}>특약 검토 {review.contract_items.length}건</h3>
      {review.contract_items.map((item) => (
        <ReviewItemCard
          analysis={analysisById.get(item.item_id)}
          evidence={review.evidence.filter((entry) => entry.item_id === item.item_id)}
          item={item}
          key={item.item_id}
          onOpenPrecedent={onOpenPrecedent}
        />
      ))}

      {(registryAnalyses.length > 0 || attentionItems.length > 0) && (
        <>
          <h3 className={styles.groupTitle}>등본 검토</h3>
          {registryAnalyses.map((analysis) => {
            const item = review.registry_items.find((entry) => entry.item_id === analysis.item_id);
            return (
              <ReviewItemCard
                analysis={analysis}
                evidence={review.evidence.filter((entry) => entry.item_id === analysis.item_id)}
                item={item ?? null}
                key={analysis.item_id}
                onOpenPrecedent={onOpenPrecedent}
              />
            );
          })}
          {attentionItems.length > 0 && (
            <div className={styles.noteBlock}>
              <small>함께 확인하세요</small>
              <ul>{attentionItems.map((item) => <li key={item.item_id}>{item.message}</li>)}</ul>
            </div>
          )}
        </>
      )}
      <p className={styles.disclaimer}>{review.disclaimer}</p>
    </>
  );
}

function ReviewItemCard({
  item,
  analysis,
  evidence,
  onOpenPrecedent,
}: {
  item: ContractReviewItem | null;
  analysis: ContractReviewAnalysis | undefined;
  evidence: ContractEvidence[];
  onOpenPrecedent: OpenPrecedent;
}) {
  const status = analysis?.review_status ?? "check";
  const statutes = evidence.filter(
    (entry) => entry.source_type === "statute" && (!analysis || analysis.evidence_source_ids.includes(entry.source_id)),
  );
  const precedents = evidence.filter((entry) => entry.source_type === "precedent");

  return (
    <section className={styles.resultCard}>
      <header className={styles.resultCardHeader}>
        <h3>{item?.label ?? analysis?.item_id}</h3>
        <span className={`${styles.statusBadge} ${REVIEW_STATUS_CLASS[status] ?? styles.statusCheck}`}>
          {REVIEW_STATUS_LABEL[status] ?? REVIEW_STATUS_LABEL.check}
        </span>
      </header>

      {item?.original_text && (
        <div className={styles.requestQuote}>
          <small>계약서 원문</small>
          <p>{item.original_text}</p>
        </div>
      )}

      {analysis ? (
        <>
          <div className={styles.noteBlock}>
            <small>이렇게 판단한 이유</small>
            <p>{analysis.summary}</p>
          </div>
          {analysis.revision && (
            <div className={styles.draftBlock}>
              <small>수정 제안</small>
              <p>{analysis.revision}</p>
            </div>
          )}
          {analysis.negotiation && (
            <div className={styles.noteBlock}>
              <small>협의 안내</small>
              <p>{analysis.negotiation}</p>
            </div>
          )}
        </>
      ) : (
        <p className={styles.plainText}>이 항목은 AI 검토 의견이 만들어지지 않았습니다. 원문을 직접 확인하세요.</p>
      )}

      <StatuteCards evidence={statutes} />
      <PrecedentCards evidence={precedents} onOpenPrecedent={onOpenPrecedent} />
    </section>
  );
}
