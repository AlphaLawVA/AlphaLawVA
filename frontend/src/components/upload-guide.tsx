import { LockKeyhole } from "lucide-react";
import styles from "./upload-guide.module.css";

type UploadGuideProps = {
  includesRegistry?: boolean;
  purpose: "review" | "letter";
};

export function UploadGuide({ includesRegistry = false, purpose }: UploadGuideProps) {
  const items = purpose === "review"
    ? [
        "계약서는 전체 페이지가 잘리지 않고 선명하게 보여야 합니다.",
        includesRegistry
          ? "등본은 발급일과 표제부·갑구·을구가 모두 포함된 문서를 사용하세요."
          : "당사자·금액·날짜와 특약 문구가 흐리지 않은지 확인하세요.",
        "주민등록번호 등 분석에 불필요한 개인정보는 업로드 전에 가려주세요.",
      ]
    : [
        "계약서 전체 페이지가 빠짐없이 포함되어 있는지 확인하세요.",
        "당사자·목적물·금액·계약기간과 주요 날짜가 선명하게 보여야 합니다.",
        "주민등록번호 등 내용증명 작성에 불필요한 개인정보는 업로드 전에 가려주세요.",
      ];

  return (
    <aside className={styles.guide}>
      <div className={styles.heading}>
        <h2>업로드 전에 확인하세요</h2>
        <span className={styles.badge}>필수 확인</span>
      </div>
      <ol className={styles.list}>
        {items.map((item, index) => (
          <li key={item}><b>{String(index + 1).padStart(2, "0")}</b><span>{item}</span></li>
        ))}
      </ol>
      <div className={styles.privacy}>
        <LockKeyhole size={18} aria-hidden="true" />
        <span>필요한 계약정보는 암호화하여 보관하고, OCR 분석에 불필요한 정보는 마스킹 후 폐기합니다.</span>
      </div>
    </aside>
  );
}
