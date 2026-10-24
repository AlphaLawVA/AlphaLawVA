"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, FileText, LockKeyhole, Upload } from "lucide-react";
import type { DocumentMode } from "@/lib/demo-types";
import styles from "./contract-review.module.css";

const modeOptions: Array<{ mode: DocumentMode; title: string; description: string }> = [
  { mode: "contract", title: "계약서만", description: "특약과 계약정보 검토" },
  { mode: "both", title: "계약서 + 등본", description: "계약과 권리관계 함께 검토" },
  { mode: "registry", title: "등본만", description: "표제부·갑구·을구 확인" },
];

export function UploadDemo() {
  const router = useRouter();
  const [mode, setMode] = useState<DocumentMode>("both");
  const [loaded, setLoaded] = useState(false);

  const files = mode === "contract"
    ? ["전세_손글씨_샘플.pdf"]
    : mode === "registry"
      ? ["등기사항증명서_샘플.pdf"]
      : ["전세_손글씨_샘플.pdf", "등기사항증명서_샘플.pdf"];

  function selectMode(nextMode: DocumentMode) {
    setMode(nextMode);
    setLoaded(false);
  }

  return (
    <>
      <div className={styles.uploadGrid}>
        <section className={styles.panel}>
          <h2 className={styles.sectionTitle}>검토할 문서를 선택해주세요</h2>
          <div className={styles.modeGrid}>
            {modeOptions.map((option) => (
              <button
                className={`${styles.modeButton} ${mode === option.mode ? styles.modeButtonActive : ""}`}
                key={option.mode}
                type="button"
                onClick={() => selectMode(option.mode)}
              >
                <strong>{option.title}</strong>
                <span>{option.description}</span>
              </button>
            ))}
          </div>

          <div className={styles.dropzone}>
            <span className={styles.dropIcon}><Upload size={23} aria-hidden="true" /></span>
            <strong>PDF 또는 이미지 파일을 올려주세요</strong>
            <p>PDF, JPG, PNG · 문서별 최대 20MB</p>
            <button className={styles.sampleButton} type="button" onClick={() => setLoaded(true)}>
              <FileText size={17} aria-hidden="true" />
              샘플 문서 불러오기
            </button>
          </div>

          {loaded && (
            <div className={styles.fileList} aria-live="polite">
              {files.map((file) => (
                <div className={styles.fileItem} key={file}>
                  <div className={styles.fileName}>
                    <FileText size={17} aria-hidden="true" />
                    <span>{file}</span>
                  </div>
                  <CheckCircle2 className={styles.completeMark} size={18} aria-label="업로드 완료" />
                </div>
              ))}
            </div>
          )}
        </section>

        <aside className={styles.sidePanel}>
          <h2 className={styles.sectionTitle}>업로드 전에 확인하세요</h2>
          <ol className={styles.guideList}>
            <li><b>01</b><span>계약서 전체 페이지가 잘리지 않고 선명하게 보여야 합니다.</span></li>
            <li><b>02</b><span>등본은 발급일과 표제부·갑구·을구가 모두 포함된 문서를 사용하세요.</span></li>
            <li><b>03</b><span>주민등록번호는 수집하거나 저장하지 않으며 검토 대상에서도 제외합니다.</span></li>
          </ol>
          <div className={styles.privacyNotice}>
            <LockKeyhole size={18} aria-hidden="true" />
            <span>필요한 계약정보는 암호화하여 보관하고, 분석에 불필요한 정보는 마스킹 후 폐기합니다.</span>
          </div>
        </aside>
      </div>

      <div className={styles.actions}>
        <button
          className={styles.primaryButton}
          type="button"
          disabled={!loaded}
          onClick={() => router.push(`/contract-review/analyzing?documents=${mode}`)}
        >
          정보 확인하기
        </button>
      </div>
    </>
  );
}

