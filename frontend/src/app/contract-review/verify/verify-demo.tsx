"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, AlertTriangle, CheckCircle2 } from "lucide-react";
import { DemoDocument } from "@/components/demo-document";
import { contractFields, registryFields } from "@/lib/mock-data";
import type { DocumentMode } from "@/lib/demo-types";
import styles from "../contract-review.module.css";

type VerifyTab = "contract" | "registry";
type EditableField = { label: string; value: string; wide?: boolean };

function readMode(value: string | null): DocumentMode {
  return value === "contract" || value === "registry" ? value : "both";
}

export function VerifyDemo() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const mode = readMode(searchParams.get("documents"));
  const [tab, setTab] = useState<VerifyTab>(mode === "registry" ? "registry" : "contract");
  const [contractValues, setContractValues] = useState<EditableField[]>(contractFields);
  const [registryValues, setRegistryValues] = useState<EditableField[]>(registryFields);
  const [contractConfirmed, setContractConfirmed] = useState(false);
  const [registryConfirmed, setRegistryConfirmed] = useState(false);

  const fields = tab === "contract" ? contractValues : registryValues;
  const setFields = tab === "contract" ? setContractValues : setRegistryValues;
  const confirmed = tab === "contract" ? contractConfirmed : registryConfirmed;
  const setConfirmed = tab === "contract" ? setContractConfirmed : setRegistryConfirmed;
  const requiredConfirmed = mode === "both" ? contractConfirmed && registryConfirmed : confirmed;
  const missingFields = fields.filter((field) => !field.value.trim());
  const allFieldsComplete = missingFields.length === 0;

  function updateField(index: number, value: string) {
    setFields((current) => current.map((field, fieldIndex) => (
      fieldIndex === index ? { ...field, value } : field
    )));
    setConfirmed(false);
  }

  return (
    <>
      <div className={styles.warningBanner}>
        <AlertTriangle size={20} aria-hidden="true" />
        <span>
          자동으로 읽은 값에는 오류가 있을 수 있습니다. 특히 당사자, 주소, 날짜, 금액과 등기 권리사항은 원문과 비교하고 틀린 값은 직접 수정해주세요.
        </span>
      </div>

      {mode === "both" && (
        <div className={styles.verifyTabs} role="tablist" aria-label="확인할 문서">
          <button className={tab === "contract" ? styles.verifyTabActive : ""} type="button" onClick={() => setTab("contract")}>
            계약서 정보 {contractConfirmed && <CheckCircle2 size={16} aria-label="확인 완료" />}
          </button>
          <button className={tab === "registry" ? styles.verifyTabActive : ""} type="button" onClick={() => setTab("registry")}>
            등본 정보 {registryConfirmed && <CheckCircle2 size={16} aria-label="확인 완료" />}
          </button>
        </div>
      )}

      <div className={styles.verifyLayout}>
        <aside className={styles.documentPreview} aria-label="업로드 문서 미리보기">
          <DemoDocument kind={tab} />
        </aside>

        <section className={styles.formPanel}>
          <div className={styles.formHeading}>
            <span>{tab === "contract" ? "계약서" : "등기사항증명서"}</span>
            <h2>원문을 보며 정보를 확인해주세요</h2>
            <p>{tab === "contract" ? "분석과 생성에 사용되는 핵심 계약정보입니다." : "표제부·갑구·을구에서 읽은 주요 권리정보입니다."}</p>
          </div>
          <div className={styles.formGrid}>
            {fields.map((field, index) => (
              <div className={`${styles.field} ${field.wide ? styles.fieldWide : ""}`} key={field.label}>
                <label htmlFor={`${tab}-field-${index}`}>{field.label}</label>
                <input className={!field.value.trim() ? styles.invalidInput : ""} id={`${tab}-field-${index}`} value={field.value} onChange={(event) => updateField(index, event.target.value)} />
              </div>
            ))}
          </div>

          {!allFieldsComplete && <div className={styles.validationBanner} role="alert"><AlertCircle size={19} /><div><strong>확인하지 못한 정보가 있습니다</strong><span>{missingFields.map((field) => field.label).join(", ")} 값을 원문에서 확인해 입력해주세요.</span></div></div>}

          <div className={styles.checkRow}>
            <label>
              <input type="checkbox" checked={confirmed} disabled={!allFieldsComplete} onChange={(event) => setConfirmed(event.target.checked)} />
              <span>{tab === "contract" ? "계약서 원문과 비교해 위 계약정보를 확인했습니다." : "등기사항증명서 원문과 비교해 위 권리정보를 확인했습니다."}</span>
            </label>
          </div>

          {mode === "both" && !requiredConfirmed && (
            <p className={styles.confirmGuide}>계약서와 등본 정보를 각각 확인해야 검토 결과로 넘어갈 수 있습니다.</p>
          )}

          <div className={styles.actions}>
            <Link className={styles.secondaryButton} href="/contract-review">문서 다시 선택</Link>
            {mode === "both" && !registryConfirmed && tab === "contract" ? (
              <button className={styles.primaryButton} type="button" disabled={!contractConfirmed || !allFieldsComplete} onClick={() => setTab("registry")}>등본 정보 확인</button>
            ) : (
              <button className={styles.primaryButton} type="button" disabled={!requiredConfirmed || !allFieldsComplete} onClick={() => router.push(`/contract-review/result?documents=${mode}`)}>검토 결과 보기</button>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
