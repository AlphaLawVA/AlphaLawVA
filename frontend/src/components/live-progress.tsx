"use client";

import { useEffect, useState } from "react";
import styles from "@/app/contract-review/contract-review.module.css";

type LiveProgressProps = {
  /** 진행률이 상한에서 멈춘 뒤에도 서버 응답을 기다리는 중임을 알릴 때 쓰는 안내 문구 */
  waitingHint: string;
  /** 이 시간(초)이 지나면 안내 문구를 보여 준다 */
  hintAfterSeconds?: number;
};

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * 오래 걸리는 작업에서 화면이 멈춘 것이 아니라 진행 중임을 보여 준다.
 * 진행률 막대와 별개로 깜빡이는 표시와 1초 단위 경과 시간을 계속 갱신한다.
 */
export function LiveProgress({ hintAfterSeconds = 20, waitingHint }: LiveProgressProps) {
  const [startedAt] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  return (
    <div aria-live="polite" className={styles.liveStatus} role="status">
      <span aria-hidden="true" className={styles.liveDot} />
      <span>작업 진행 중 · 경과 {formatElapsed(elapsed)}</span>
      {elapsed >= hintAfterSeconds && <em>{waitingHint}</em>}
    </div>
  );
}
