import { Suspense } from "react";
import { redirect } from "next/navigation";
import { getAuthIdentity } from "@/lib/auth";
import styles from "./mypage.module.css";

const resultGroups = [
  ["계약서 검토 결과", "검토한 계약서의 위험 조항과 수정 제안을 모아볼 수 있어요."],
  ["특약 생성 결과", "생성한 특약과 협상 전략을 모아볼 수 있어요."],
  ["내용증명 생성 결과", "작성한 내용증명과 대응 전략을 모아볼 수 있어요."],
  ["즐겨찾기 판례", "질문별로 저장한 판례를 모아볼 수 있어요."],
] as const;

async function MyPageContent() {
  const identity = await getAuthIdentity();
  if (!identity) {
    redirect("/login?next=/mypage");
  }

  return (
    <main className={styles.page}>
      <section className={styles.heading}>
        <p className={styles.eyebrow}>MY ALPHALAWVA</p>
        <h1>내 보관함</h1>
        <p>{identity.email ?? "로그인한 사용자"}님의 법률 결과를 한곳에서 확인하세요.</p>
      </section>

      <section className={styles.grid} aria-label="저장 결과 분류">
        {resultGroups.map(([title, description]) => (
          <article className={styles.card} key={title}>
            <h2>{title}</h2>
            <p>{description}</p>
            <span className={styles.status}>저장 기능은 다음 단계에서 연결 예정</span>
          </article>
        ))}
      </section>
    </main>
  );
}

function MyPageFallback() {
  return (
    <main className={styles.page} aria-busy="true">
      <section className={styles.heading}>
        <p className={styles.eyebrow}>MY ALPHALAWVA</p>
        <h1>내 보관함</h1>
        <p>로그인 정보를 확인하고 있어요.</p>
      </section>
    </main>
  );
}

export default function MyPage() {
  return (
    <Suspense fallback={<MyPageFallback />}>
      <MyPageContent />
    </Suspense>
  );
}
