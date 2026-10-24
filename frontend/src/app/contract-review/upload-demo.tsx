"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, CheckCircle2, FileText, Upload } from "lucide-react";
import { UploadGuide } from "@/components/upload-guide";
import type { DocumentMode } from "@/lib/demo-types";
import { validateDemoFile } from "@/lib/demo-validation";
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
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [fileError, setFileError] = useState("");

  const files = mode === "contract"
    ? ["전세_손글씨_샘플.pdf"]
    : mode === "registry"
      ? ["등기사항증명서_샘플.pdf"]
      : ["전세_손글씨_샘플.pdf", "등기사항증명서_샘플.pdf"];

  function selectMode(nextMode: DocumentMode) {
    setMode(nextMode);
    setLoaded(false);
    setSelectedFiles([]);
    setFileError("");
  }

  function selectFiles(fileList: FileList | null) {
    const nextFiles = Array.from(fileList ?? []);
    const validationError = nextFiles.map(validateDemoFile).find(Boolean);
    const requiredCount = mode === "both" ? 2 : 1;

    if (validationError) {
      setFileError(validationError);
      setSelectedFiles([]);
      setLoaded(false);
      return;
    }
    if (nextFiles.length < requiredCount) {
      setFileError(mode === "both" ? "계약서와 등기사항증명서를 각각 한 개씩 선택해주세요." : "검토할 문서 한 개를 선택해주세요.");
      setSelectedFiles(nextFiles.map((file) => file.name));
      setLoaded(false);
      return;
    }

    setFileError("");
    setSelectedFiles(nextFiles.map((file) => file.name));
    setLoaded(true);
  }

  function loadSamples() {
    setSelectedFiles(files);
    setFileError("");
    setLoaded(true);
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
            <label className={styles.sampleButton}>
              <Upload size={17} aria-hidden="true" />
              내 파일 선택
              <input className={styles.fileInput} type="file" multiple={mode === "both"} accept=".pdf,.jpg,.jpeg,.png" onChange={(event) => selectFiles(event.target.files)} />
            </label>
            <button className={styles.sampleTextButton} type="button" onClick={loadSamples}><FileText size={16} aria-hidden="true" />샘플 문서 사용</button>
          </div>

          {fileError && <div className={styles.validationBanner} role="alert"><AlertCircle size={19} /><div><strong>업로드할 문서를 확인해주세요</strong><span>{fileError}</span></div></div>}

          {selectedFiles.length > 0 && (
            <div className={styles.fileList} aria-live="polite">
              {selectedFiles.map((file) => (
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

        <UploadGuide purpose="review" includesRegistry={mode !== "contract"} />
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

