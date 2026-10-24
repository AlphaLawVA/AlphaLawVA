import styles from "./review-stepper.module.css";

const steps = ["문서 업로드", "정보 확인·수정", "검토 결과"];

export function ReviewStepper({ current }: { current: 1 | 2 | 3 }) {
  return (
    <ol className={styles.steps} aria-label="계약서 검토 진행 단계">
      {steps.map((step, index) => {
        const number = index + 1;
        const active = number === current;
        const complete = number < current;
        return (
          <li className={active ? styles.active : complete ? styles.complete : ""} key={step}>
            <span>{String(number).padStart(2, "0")}</span>
            {step}
          </li>
        );
      })}
    </ol>
  );
}

