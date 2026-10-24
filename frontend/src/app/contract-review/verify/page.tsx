import { Suspense } from "react";
import { ReviewStepper } from "@/components/review-stepper";
import { VerifyDemo } from "./verify-demo";
import styles from "../contract-review.module.css";

export default function VerifyPage() {
  return (
    <main className={styles.page}>
      <div className={styles.heading}>
        <h1>계약서 검토</h1>
        <p>분석하기 전에 문서에서 읽은 정보가 정확한지 확인합니다.</p>
      </div>
      <ReviewStepper current={2} />
      <Suspense fallback={<div className={styles.panel}>확인 화면을 준비하고 있습니다.</div>}>
        <VerifyDemo />
      </Suspense>
    </main>
  );
}
