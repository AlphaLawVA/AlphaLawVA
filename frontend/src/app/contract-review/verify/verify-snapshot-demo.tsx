"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { AlertCircle, AlertTriangle, CheckCircle2, ChevronDown, FileSearch, MapPin, ShieldCheck } from "lucide-react";
import { DemoDocument } from "@/components/demo-document";
import { confirmedContractSnapshot, confirmedRegistrySnapshot, contractSnapshots } from "@/lib/mock-data";
import type { ConfirmedContractSnapshot, ConfirmedRegistrySnapshot, DocumentMode, FieldStatus, SnapshotField } from "@/lib/demo-types";
import { useRouter, useSearchParams } from "next/navigation";
import styles from "../contract-review.module.css";

type VerifyTab = "contract" | "registry";

type VerifySnapshotDemoProps = {
  initialContractSnapshot?: ConfirmedContractSnapshot;
  modeOverride?: DocumentMode;
  onBack?: () => void;
  onComplete?: () => void;
  submitLabel?: string;
};

const STATUS_LABEL: Record<FieldStatus, string> = {
  automatic: "자동 인식됨",
  "needs-confirmation": "확인 필요",
  "user-confirmed": "사용자 확인",
  "user-modified": "사용자 수정",
  "confirmed-empty": "확인된 빈값",
};

function readMode(value: string | null): DocumentMode {
  return value === "contract" || value === "registry" ? value : "both";
}

function isPending(field: SnapshotField) {
  return field.status === "needs-confirmation";
}

function copyContractSnapshot(): ConfirmedContractSnapshot {
  return { ...confirmedContractSnapshot, fields: confirmedContractSnapshot.fields.map((field) => ({ ...field, source: { ...field.source } })) };
}

function copyRegistrySnapshot(): ConfirmedRegistrySnapshot {
  return { ...confirmedRegistrySnapshot, fields: confirmedRegistrySnapshot.fields.map((field) => ({ ...field, source: { ...field.source } })), rights: confirmedRegistrySnapshot.rights.map((right) => ({ ...right, source: { ...right.source } })) };
}

export function VerifySnapshotDemo({ initialContractSnapshot, modeOverride, onBack, onComplete, submitLabel }: VerifySnapshotDemoProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const mode = modeOverride ?? readMode(searchParams.get("documents"));
  const [tab, setTab] = useState<VerifyTab>(mode === "registry" ? "registry" : "contract");
  const [contractSnapshot, setContractSnapshot] = useState(() => initialContractSnapshot ? { ...initialContractSnapshot, fields: initialContractSnapshot.fields.map((field) => ({ ...field, source: { ...field.source } })) } : copyContractSnapshot());
  const [registrySnapshot, setRegistrySnapshot] = useState(copyRegistrySnapshot);
  const [contractConfirmed, setContractConfirmed] = useState(false);
  const [registryConfirmed, setRegistryConfirmed] = useState(false);
  const [confirmedRightIds, setConfirmedRightIds] = useState<string[]>([]);
  const [showAutomatic, setShowAutomatic] = useState(false);
  const [activeAnchor, setActiveAnchor] = useState("contract-date");
  const snapshot = tab === "contract" ? contractSnapshot : registrySnapshot;
  const fields = snapshot.fields;
  const pendingFields = fields.filter(isPending);
  const pendingRights = tab === "registry" ? registrySnapshot.rights.filter((right) => right.status !== "cancelled" && !confirmedRightIds.includes(right.id)) : [];
  const documentReady = pendingFields.length === 0 && pendingRights.length === 0;
  const currentConfirmed = tab === "contract" ? contractConfirmed : registryConfirmed;
  const setCurrentConfirmed = tab === "contract" ? setContractConfirmed : setRegistryConfirmed;
  const contractReady = !contractSnapshot.fields.some(isPending) && contractConfirmed;
  const registryReady = !registrySnapshot.fields.some(isPending) && registrySnapshot.rights.filter((right) => right.status !== "cancelled").every((right) => confirmedRightIds.includes(right.id)) && registryConfirmed;
  const canSubmit = mode === "both" ? contractReady && registryReady : tab === "contract" ? contractReady : registryReady;
  const priorityFields = fields.filter((field) => field.priority !== "automatic");
  const automaticFields = fields.filter((field) => field.priority === "automatic");
  const groupedPriorityFields = useMemo(() => groupFields(priorityFields), [priorityFields]);
  const groupedAutomaticFields = useMemo(() => groupFields(automaticFields), [automaticFields]);

  function updateField(id: string, value: string) {
    const update = (field: SnapshotField) => field.id === id ? { ...field, value, status: value.trim() ? "user-modified" as const : "needs-confirmation" as const } : field;
    if (tab === "contract") {
      setContractSnapshot((current) => ({ ...current, fields: current.fields.map(update) }));
    } else {
      setRegistrySnapshot((current) => ({ ...current, fields: current.fields.map(update) }));
    }
    setCurrentConfirmed(false);
    setActiveAnchor(id);
  }

  function confirmField(id: string) {
    const confirm = (field: SnapshotField) => field.id === id ? { ...field, status: field.value.trim() ? "user-confirmed" as const : "confirmed-empty" as const } : field;
    if (tab === "contract") {
      setContractSnapshot((current) => ({ ...current, fields: current.fields.map(confirm) }));
    } else {
      setRegistrySnapshot((current) => ({ ...current, fields: current.fields.map(confirm) }));
    }
    setCurrentConfirmed(false);
  }

  function selectSource(label: string) {
    const matched = fields.find((field) => field.source.label === label);
    if (matched) setActiveAnchor(matched.id);
  }

  function toggleRight(id: string) {
    setConfirmedRightIds((current) => current.includes(id) ? current.filter((rightId) => rightId !== id) : [...current, id]);
    setRegistryConfirmed(false);
  }

  function changeContractType(contractType: ConfirmedContractSnapshot["contractType"]) {
    const next = contractSnapshots[contractType];
    setContractSnapshot({ ...next, fields: next.fields.map((field) => ({ ...field, source: { ...field.source } })) });
    setContractConfirmed(false);
    setActiveAnchor("contract-type");
  }

  function completeVerification() {
    if (onComplete) {
      onComplete();
      return;
    }
    router.push(`/contract-review/result?documents=${mode}`);
  }

  return <>
    <div className={styles.warningBanner}><AlertTriangle size={20} aria-hidden="true" /><span>OCR 인식값은 원문 대조를 돕기 위한 값입니다. 좌표·신뢰도는 검토 과정에만 사용하며, 계약서 검토 입력에는 확인된 정보만 전달됩니다.</span></div>
    {mode === "both" && <div className={styles.verifyTabs} role="tablist" aria-label="확인할 문서"><button className={tab === "contract" ? styles.verifyTabActive : ""} type="button" onClick={() => setTab("contract")}>계약서 정보 {contractConfirmed && <CheckCircle2 size={16} aria-label="확인 완료" />}</button><button className={tab === "registry" ? styles.verifyTabActive : ""} type="button" onClick={() => setTab("registry")}>등본 정보 {registryConfirmed && <CheckCircle2 size={16} aria-label="확인 완료" />}</button></div>}
    <div className={styles.verifyLayout}>
      <aside className={styles.documentPreview} aria-label="업로드 문서 미리보기"><div className={styles.previewHeading}><FileSearch size={17} /><span>원문 PDF · 1 / 2페이지</span></div><DemoDocument activeAnchor={activeAnchor} kind={tab} onSelectAnchor={selectSource} /></aside>
      <section className={styles.formPanel}>
        <div className={styles.formHeading}><span>{tab === "contract" ? "ConfirmedContractSnapshot" : "ConfirmedRegistrySnapshot"}</span><h2>확인할 정보부터 원문과 대조하세요</h2><p>{tab === "contract" ? "주소·당사자·금액·날짜·특약은 확인된 값만 계약서 검토에 사용합니다." : "현재 소유자와 유효한 권리부담을 먼저 확인하고, 말소 이력은 보조 정보로 확인합니다."}</p>{tab === "contract" && <div className={styles.contractTypeSwitch} role="group" aria-label="계약 유형"><span>계약 유형</span>{(["전세", "월세", "매매"] as const).map((contractType) => <button className={contractSnapshot.contractType === contractType ? styles.contractTypeActive : ""} key={contractType} type="button" onClick={() => changeContractType(contractType)}>{contractType}</button>)}</div>}</div>
        <div className={styles.confirmationSummary}><div><strong>확인 필요 {pendingFields.length + pendingRights.length}건</strong><span>원문과 비교한 뒤 개별 항목을 확인 처리해주세요.</span></div><MapPin size={20} aria-hidden="true" /></div>
        {mode === "both" && <ComparisonSummary />}
        <div className={styles.snapshotGroups}>
          {Object.entries(groupedPriorityFields).map(([group, groupFields]) => <section className={styles.snapshotGroup} key={group}><h3>{group}</h3><div className={styles.snapshotFieldList}>{groupFields.map((field) => <SnapshotFieldRow active={activeAnchor === field.id} field={field} key={field.id} onConfirm={confirmField} onFocus={setActiveAnchor} onUpdate={updateField} />)}</div></section>)}
          {tab === "registry" && <RegistryRights confirmedIds={confirmedRightIds} onConfirm={toggleRight} onFocus={setActiveAnchor} rights={registrySnapshot.rights} />}
          <details className={styles.automaticDisclosure} open={showAutomatic} onToggle={(event) => setShowAutomatic(event.currentTarget.open)}><summary><ChevronDown size={17} />자동 인식된 보조 정보 {automaticFields.length}건</summary><p>형식 검증을 통과한 일반 필드입니다. 필요할 때만 펼쳐 원문과 비교하세요.</p>{Object.entries(groupedAutomaticFields).map(([group, groupFields]) => <section className={styles.snapshotGroup} key={group}><h3>{group}</h3><div className={styles.snapshotFieldList}>{groupFields.map((field) => <SnapshotFieldRow active={activeAnchor === field.id} field={field} key={field.id} onConfirm={confirmField} onFocus={setActiveAnchor} onUpdate={updateField} />)}</div></section>)}</details>
        </div>
        {!documentReady && <div className={styles.validationBanner} role="alert"><AlertCircle size={19} /><div><strong>아직 확인할 항목이 있습니다</strong><span>{pendingFields.length + pendingRights.length}건을 원문과 대조해 확인 또는 수정한 뒤 제출할 수 있습니다.</span></div></div>}
        <div className={styles.checkRow}><label><input type="checkbox" checked={currentConfirmed} disabled={!documentReady} onChange={(event) => setCurrentConfirmed(event.target.checked)} /><span>{tab === "contract" ? "개별 확인을 마쳤으며, 이 계약서 정보를 검토 입력으로 확정합니다." : "개별 확인을 마쳤으며, 이 등본 정보를 검토 입력으로 확정합니다."}</span></label></div>
        {mode === "both" && !canSubmit && <p className={styles.confirmGuide}>계약서와 등본의 개별 확인 및 최종 확인을 모두 마쳐야 검토 결과를 볼 수 있습니다.</p>}
        <div className={styles.actions}>{onBack ? <button className={styles.secondaryButton} type="button" onClick={onBack}>문서 다시 선택</button> : <Link className={styles.secondaryButton} href="/contract-review">문서 다시 선택</Link>}{mode === "both" && tab === "contract" && !registryConfirmed ? <button className={styles.primaryButton} disabled={!contractReady} type="button" onClick={() => setTab("registry")}>등본 정보 확인</button> : <button className={styles.primaryButton} disabled={!canSubmit} type="button" onClick={completeVerification}>{submitLabel ?? "검토 결과 보기"}</button>}</div>
      </section>
    </div>
  </>;
}

function groupFields(fields: SnapshotField[]) {
  return fields.reduce<Record<string, SnapshotField[]>>((groups, field) => { (groups[field.group] ??= []).push(field); return groups; }, {});
}

function SnapshotFieldRow({ active, field, onConfirm, onFocus, onUpdate }: { active: boolean; field: SnapshotField; onConfirm: (id: string) => void; onFocus: (id: string) => void; onUpdate: (id: string, value: string) => void; }) {
  const pending = isPending(field);
  const className = `status${field.status.replace(/-([a-z])/g, (_, character: string) => character.toUpperCase())}`;
  const confirmed = ["user-confirmed", "user-modified", "confirmed-empty"].includes(field.status);
  return <article className={`${styles.snapshotField} ${active ? styles.snapshotFieldActive : ""} ${pending ? styles.snapshotFieldNeeds : ""}`}><div className={styles.snapshotFieldHeader}><label htmlFor={field.id}>{field.label}</label><div><span className={`${styles.fieldStatus} ${styles[className]}`}>{confirmed && <CheckCircle2 size={13} aria-hidden="true" />}{STATUS_LABEL[field.status]}</span><button className={styles.sourceButton} type="button" onClick={() => onFocus(field.id)}>원문 위치 보기</button></div></div>{field.multiline ? <textarea id={field.id} onFocus={() => onFocus(field.id)} onChange={(event) => onUpdate(field.id, event.target.value)} value={field.value} /> : <input id={field.id} onFocus={() => onFocus(field.id)} onChange={(event) => onUpdate(field.id, event.target.value)} value={field.value} />}{pending && <button className={styles.confirmFieldButton} type="button" onClick={() => onConfirm(field.id)}>{field.value.trim() ? "원문과 일치함" : "빈값으로 확인"}</button>}</article>;
}

function RegistryRights({ confirmedIds, onConfirm, onFocus, rights }: { confirmedIds: string[]; onConfirm: (id: string) => void; onFocus: (id: string) => void; rights: ConfirmedRegistrySnapshot["rights"]; }) {
  return <section className={styles.snapshotGroup}><h3>권리부담 목록</h3><div className={styles.rightsSections}>{(["갑구", "을구"] as const).map((section) => <div key={section}><h4>{section} · {section === "갑구" ? "소유권에 관한 사항" : "소유권 이외의 권리"}</h4>{rights.filter((right) => right.section === section).map((right) => { const pending = right.status !== "cancelled" && !confirmedIds.includes(right.id); return <article className={`${styles.rightCard} ${right.status === "cancelled" ? styles.rightCancelled : right.status === "active" ? styles.rightActive : styles.rightUnclear}`} key={right.id}><div className={styles.rightCardHeader}><strong>{right.rank}순위 · {right.purpose}</strong><span>{right.status === "active" ? "유효" : right.status === "cancelled" ? "말소" : "불명"}</span></div><dl><div><dt>접수일</dt><dd>{right.receivedDate}</dd></div><div><dt>권리자</dt><dd>{right.holder}</dd></div>{right.debtor && <div><dt>채무자</dt><dd>{right.debtor}</dd></div>}{right.maximumAmount && <div><dt>채권최고액</dt><dd>{right.maximumAmount}</dd></div>}</dl><div className={styles.rightActions}><button className={styles.sourceButton} type="button" onClick={() => onFocus(right.id)}>원문 위치 보기</button>{pending && <button className={styles.confirmFieldButton} type="button" onClick={() => onConfirm(right.id)}>권리부담 확인</button>}{!pending && right.status !== "cancelled" && <span className={styles.rightConfirmed}><CheckCircle2 size={15} />확인됨</span>}</div></article>; })}</div>)}</div></section>;
}

function ComparisonSummary() {
  return <section className={styles.comparisonSummary}><div><ShieldCheck size={18} /><strong>문서 간 비교</strong></div><ul><li><CheckCircle2 size={15} />계약서 주소와 등본 주소가 일치합니다.</li><li><CheckCircle2 size={15} />계약서 임대인과 현재 소유자가 일치합니다.</li><li><AlertTriangle size={15} />유효한 근저당권 1건이 확인됩니다.</li><li><CheckCircle2 size={15} />권리 설정 제한 특약 문구가 확인됩니다.</li></ul></section>;
}
