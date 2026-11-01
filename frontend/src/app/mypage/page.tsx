import { Suspense } from "react";
import { redirect } from "next/navigation";
import { getAuthIdentity } from "@/lib/auth";
import { MyPageLibrary } from "./mypage-library";
import styles from "./mypage.module.css";

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

      <MyPageLibrary />
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
