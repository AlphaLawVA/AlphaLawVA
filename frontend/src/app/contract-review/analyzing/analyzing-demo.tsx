"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Check, FileSearch, ScanText } from "lucide-react";
import styles from "../contract-review.module.css";

const stages = [
  { at: 12, label: "문서의 페이지와 방향을 확인하고 있습니다", icon: FileSearch },
  { at: 38, label: "글자와 표 영역을 인식하고 있습니다", icon: ScanText },
  { at: 72, label: "계약정보와 권리관계를 구조화하고 있습니다", icon: ScanText },
  { at: 96, label: "확인할 항목을 정리하고 있습니다", icon: Check },
];

export function AnalyzingDemo() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const documents = searchParams.get("documents") ?? "both";
  const [progress, setProgress] = useState(8);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setProgress((value) => Math.min(100, value + 4));
    }, 140);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (progress < 100) return;
    const timer = window.setTimeout(() => router.push(`/contract-review/verify?documents=${documents}`), 450);
    return () => window.clearTimeout(timer);
  }, [documents, progress, router]);

  const currentIndex = Math.min(stages.length - 1, Math.floor(progress / 25));

  return (
    <main className={styles.processingPage}>
      <section className={styles.processingPanel}>
        <span className={styles.processingIcon}><ScanText size={30} aria-hidden="true" /></span>
        <p className={styles.processingEyebrow}>문서 분석 중</p>
        <h1>업로드한 문서를 읽고 있습니다</h1>
        <p>페이지 수와 글씨 상태에 따라 시간이 조금 더 걸릴 수 있습니다. 이 화면을 닫지 말고 잠시 기다려주세요.</p>
        <div className={styles.progressHeader}><span>{stages[currentIndex].label}</span><strong>{progress}%</strong></div>
        <div className={styles.progressTrack}><span style={{ width: `${progress}%` }} /></div>
        <ol className={styles.processingSteps}>
          {stages.map((stage, index) => {
            const done = progress >= stage.at;
            return <li className={done ? styles.processingDone : ""} key={stage.label}><i>{done ? <Check size={14} /> : index + 1}</i><span>{stage.label}</span></li>;
          })}
        </ol>
      </section>
    </main>
  );
}
