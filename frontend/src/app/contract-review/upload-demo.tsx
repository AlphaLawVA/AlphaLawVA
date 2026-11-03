"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, CheckCircle2, FileText, Upload } from "lucide-react";
import { UploadGuide } from "@/components/upload-guide";
import type { DocumentMode } from "@/lib/demo-types";
import {
  setPendingContractOcrRequest,
} from "@/lib/contract-ocr-request";
import { clearContractOcrDraft } from "@/lib/contract-ocr-session";
import { validateDemoFile } from "@/lib/demo-validation";
import { clearDocumentPreviews, setDocumentPreview } from "@/lib/document-preview-store";
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
  const [contractFile, setContractFile] = useState<File | null>(null);
  const [registryFile, setRegistryFile] = useState<File | null>(null);
  const [contractType, setContractType] = useState<"jeonse" | "monthly_rent" | "sale">("jeonse");
  const [providerConsent, setProviderConsent] = useState(false);
  const [isOcrSubmitting, setIsOcrSubmitting] = useState(false);
  const [fileError, setFileError] = useState("");

  const files = mode === "contract"
    ? ["전세_손글씨_샘플.pdf"]
    : mode === "registry"
      ? ["등기사항증명서_샘플.pdf"]
      : ["전세_손글씨_샘플.pdf", "등기사항증명서_샘플.pdf"];

  // 분석·확인 화면에서 "문서 다시 선택"으로 돌아오면 이전 화면 상태가 유지될 수 있어,
  // 화면을 떠날 때 선택 파일·동의·제출 상태를 처음 상태로 되돌린다.
  // 원본 미리보기(document-preview-store)는 확인 화면이 써야 하므로 지우지 않는다.
  useEffect(() => () => {
    setMode("both");
    setLoaded(false);
    setSelectedFiles([]);
    setContractFile(null);
    setRegistryFile(null);
    setContractType("jeonse");
    setProviderConsent(false);
    setIsOcrSubmitting(false);
    setFileError("");
  }, []);

  function selectMode(nextMode: DocumentMode) {
    setMode(nextMode);
    setLoaded(false);
    setSelectedFiles([]);
    setContractFile(null);
    setRegistryFile(null);
    setProviderConsent(false);
    setIsOcrSubmitting(false);
    setFileError("");
    clearDocumentPreviews();
  }

  function selectFiles(fileList: FileList | null) {
    const nextFiles = Array.from(fileList ?? []);
    const validationError = nextFiles.map(validateDemoFile).find(Boolean);

    if (validationError) {
      setFileError(validationError);
      return;
    }
    if (nextFiles.length === 0) return;

    if (mode !== "both") {
      const selectedFile = nextFiles[0];
      if (mode === "contract" && selectedFile.name.split(".").pop()?.toLowerCase() !== "pdf") {
        setFileError("현재 실시간 OCR은 3페이지 표준 계약서 PDF만 지원합니다.");
        return;
      }
      clearDocumentPreviews();
      setDocumentPreview(mode, selectedFile);
      setSelectedFiles([selectedFile.name]);
      setContractFile(mode === "contract" ? selectedFile : null);
      setFileError("");
      setLoaded(true);
      return;
    }

    const remainingCount = 2 - selectedFiles.length;
    if (nextFiles.length > remainingCount) {
      setFileError("계약서와 등기사항증명서는 각각 한 개씩만 선택할 수 있습니다. 문서 종류를 바꾸려면 다시 선택해주세요.");
      return;
    }

    if (nextFiles.some((file) => file.name.split(".").pop()?.toLowerCase() !== "pdf")) {
      setFileError("현재 실시간 OCR은 PDF 파일만 지원합니다. 계약서는 3페이지 표준 계약서, 등본은 등기사항증명서 PDF를 올려주세요.");
      return;
    }
    const documentKinds: Array<"contract" | "registry"> = ["contract", "registry"];
    const nextSelectedFiles = [...selectedFiles, ...nextFiles.map((file) => file.name)];
    nextFiles.forEach((file, index) => {
      const kind = documentKinds[selectedFiles.length + index];
      setDocumentPreview(kind, file);
      // 첫 번째 파일은 계약서, 두 번째 파일은 등기사항증명서로 실시간 OCR에 사용한다.
      if (kind === "contract") setContractFile(file);
      else setRegistryFile(file);
    });
    setSelectedFiles(nextSelectedFiles);
    setFileError("");
    setLoaded(nextSelectedFiles.length === 2);
  }

  function loadSamples() {
    clearDocumentPreviews();
    setSelectedFiles(files);
    setFileError("");
    setLoaded(true);
  }

  async function continueToVerification() {
    if (!contractFile) {
      router.push(`/contract-review/analyzing?documents=${mode}`);
      return;
    }
    if (mode === "both" && !registryFile) {
      setFileError("등기사항증명서 PDF도 함께 선택해주세요.");
      return;
    }
    if (!providerConsent) {
      setFileError("Google Document AI로 문서를 전송하는 데 동의해주세요.");
      return;
    }
    setFileError("");
    setIsOcrSubmitting(true);
    clearContractOcrDraft();
    setPendingContractOcrRequest({
      contract: { file: contractFile, contractType },
      registryFile: mode === "both" ? registryFile ?? undefined : undefined,
    });
    router.push(`/contract-review/analyzing?documents=${mode}&source=ocr`);
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
            <p>계약서 실시간 OCR은 3페이지 PDF를 지원합니다. 문서별 최대 20MB</p>
            <label className={styles.sampleButton}>
              <Upload size={17} aria-hidden="true" />
              {mode === "both" && selectedFiles.length === 1 ? "등본 파일 추가" : "내 파일 선택"}
              <input
                className={styles.fileInput}
                type="file"
                multiple={mode === "both"}
                accept=".pdf,.jpg,.jpeg,.png"
                onChange={(event) => {
                  selectFiles(event.target.files);
                  event.currentTarget.value = "";
                }}
              />
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
          {contractFile && mode !== "registry" && (
            <>
              <div className={styles.field}>
                <label htmlFor="contract-type">계약 유형</label>
                <select id="contract-type" value={contractType} onChange={(event) => setContractType(event.target.value as typeof contractType)}>
                  <option value="jeonse">전세</option>
                  <option value="monthly_rent">월세</option>
                  <option value="sale">매매</option>
                </select>
              </div>
              <label className={styles.consentRow}>
                <input checked={providerConsent} type="checkbox" onChange={(event) => setProviderConsent(event.target.checked)} />
                <span className={styles.consentCopy}>
                  <strong>Google Document AI 전송에 동의합니다</strong>
                  <span>OCR을 위해 선택한 {mode === "both" ? "계약서와 등기사항증명서 PDF가" : "계약서 PDF가"} Google LLC의 미국 us 리전으로 전송됩니다.</span>
                  <small>서버는 원본 파일과 OCR 원문을 처리 후 저장하지 않습니다. 동의하지 않으면 실시간 OCR을 사용할 수 없습니다.</small>
                </span>
              </label>
            </>
          )}
        </section>

        <UploadGuide purpose="review" includesRegistry={mode !== "contract"} />
      </div>

      <div className={styles.actions}>
        <button
          className={styles.primaryButton}
          type="button"
          disabled={!loaded || isOcrSubmitting || (contractFile !== null && !providerConsent)}
          onClick={() => void continueToVerification()}
        >
          {isOcrSubmitting ? "분석 화면으로 이동하고 있습니다" : "정보 확인하기"}
        </button>
      </div>
    </>
  );
}

