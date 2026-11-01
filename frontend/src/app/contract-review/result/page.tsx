import { Suspense } from "react";
import { ReviewStepper } from "@/components/review-stepper";
import { ResultDemo } from "./result-demo";
import styles from "../contract-review.module.css";

export default function ResultPage() {
  return (
    <main className={styles.page}>
      <ReviewStepper current={3} />
      <Suspense fallback={<div className={styles.panel}>검토 결과를 준비하고 있습니다.</div>}>
        <ResultDemo />
      </Suspense>
    </main>
  );
}
