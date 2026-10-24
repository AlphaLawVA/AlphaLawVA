import { ReviewStepper } from "@/components/review-stepper";
import { UploadDemo } from "./upload-demo";
import styles from "./contract-review.module.css";

export default function ContractReviewPage() {
  return (
    <main className={styles.page}>
      <div className={styles.heading}>
        <h1>계약서 검토</h1>
        <p>계약서와 등기사항증명서를 올리면 먼저 필요한 정보를 추출합니다.</p>
      </div>
      <ReviewStepper current={1} />
      <UploadDemo />
    </main>
  );
}
