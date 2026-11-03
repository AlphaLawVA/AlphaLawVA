"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, Check, FileSearch, ScanText } from "lucide-react";
import {
  createContractOcrDraft,
  mapContractOcrDraftToSnapshot,
} from "@/lib/contract-ocr-api";
import {
  clearPendingContractOcrRequest,
  takePendingContractOcrRequest,
} from "@/lib/contract-ocr-request";
import { saveContractOcrDraft } from "@/lib/contract-ocr-session";
import styles from "../contract-review.module.css";

const stages = [
  { at: 12, label: "업로드한 문서 형식을 확인하고 있습니다", icon: FileSearch },
  { at: 30, label: "Google Document AI가 글자와 표 영역을 인식하고 있습니다", icon: ScanText },
  { at: 85, label: "계약정보를 사용자 확인 항목으로 구조화하고 있습니다", icon: ScanText },
  { at: 100, label: "확인 화면을 준비하고 있습니다", icon: Check },
];

export function AnalyzingDemo() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const documents = searchParams.get("documents") ?? "both";
  const isOcrFlow = searchParams.get("source") === "ocr";
  const [ocrRequest] = useState(() => (
    isOcrFlow ? takePendingContractOcrRequest() : null
  ));
  const [progress, setProgress] = useState(isOcrFlow ? 12 : 8);
  const [error, setError] = useState(() => (
    isOcrFlow && !ocrRequest
      ? "업로드한 파일 정보를 찾을 수 없습니다. 문서를 다시 선택해주세요."
      : ""
  ));
  const started = useRef(false);

  useEffect(() => {
    if (isOcrFlow) return;
    const timer = window.setInterval(() => {
      setProgress((value) => Math.min(100, value + 4));
    }, 140);
    return () => window.clearInterval(timer);
  }, [isOcrFlow]);

  useEffect(() => {
    if (!isOcrFlow || started.current) return;
    started.current = true;
    if (!ocrRequest) return;

    let active = true;
    const timer = window.setInterval(() => {
      setProgress((value) => Math.min(78, value + 3));
    }, 650);

    void (async () => {
      try {
        setProgress(40);
        const draft = await createContractOcrDraft(
          ocrRequest.file,
          ocrRequest.contractType,
        );
        if (!active) return;
        setProgress(88);
        saveContractOcrDraft(mapContractOcrDraftToSnapshot(draft));
        window.setTimeout(() => {
          if (active) {
            setProgress(100);
            router.replace("/contract-review/verify?documents=contract&source=ocr");
          }
        }, 400);
      } catch (requestError) {
        if (active) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "OCR 초안을 만들지 못했습니다.",
          );
        }
      } finally {
        window.clearInterval(timer);
      }
    })();

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [isOcrFlow, ocrRequest, router]);

  useEffect(() => {
    if (isOcrFlow || error) return;
    if (progress < 100) return;
    const timer = window.setTimeout(() => router.push(`/contract-review/verify?documents=${documents}`), 450);
    return () => window.clearTimeout(timer);
  }, [documents, error, isOcrFlow, progress, router]);

  const currentIndex = Math.max(
    0,
    stages.reduce((index, stage, nextIndex) => (
      progress >= stage.at ? nextIndex : index
    ), 0),
  );

  if (error) {
    return (
      <main className={styles.processingPage}>
        <section className={styles.processingPanel}>
          <span className={styles.processingIcon}><AlertCircle size={30} aria-hidden="true" /></span>
          <p className={styles.processingEyebrow}>문서 분석을 시작하지 못했습니다</p>
          <h1>업로드할 문서를 다시 확인해주세요</h1>
          <p>{error}</p>
          <Link className={styles.primaryButton} href="/contract-review" onClick={clearPendingContractOcrRequest}>문서 다시 선택</Link>
        </section>
      </main>
    );
  }

  return (
    <main className={styles.processingPage}>
      <section className={styles.processingPanel}>
        <span className={styles.processingIcon}><ScanText size={30} aria-hidden="true" /></span>
        <p className={styles.processingEyebrow}>문서 분석 중</p>
        <h1>업로드한 문서를 읽고 있습니다</h1>
        <p>{isOcrFlow ? "Google Document AI가 문서를 인식하고 있습니다. 이 화면을 닫거나 새로고침하지 말고 잠시 기다려주세요." : "페이지 수와 글씨 상태에 따라 시간이 조금 더 걸릴 수 있습니다. 이 화면을 닫지 말고 잠시 기다려주세요."}</p>
        <div className={styles.progressHeader}><span>{stages[currentIndex].label}</span><strong>{progress}%</strong></div>
        <div className={styles.progressTrack}><span style={{ width: `${progress}%` }} /></div>
        <ol className={styles.processingSteps}>
          {stages.map((stage, index) => {
            const done = progress === 100 || index < currentIndex;
            return <li className={done ? styles.processingDone : ""} key={stage.label}><i>{done ? <Check size={14} /> : index + 1}</i><span>{stage.label}</span></li>;
          })}
        </ol>
      </section>
    </main>
  );
}
