import { Suspense } from "react";
import { redirect } from "next/navigation";
import { LoginForm } from "./login-form";
import { getAuthIdentity, safeNextPath } from "@/lib/auth";
import styles from "@/app/auth/auth-form.module.css";

async function LoginContent({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const identity = await getAuthIdentity();
  if (identity) {
    redirect("/mypage");
  }

  const params = await searchParams;

  return <LoginForm nextPath={safeNextPath(params.next)} />;
}

function LoginFallback() {
  return (
    <div className={styles.card} aria-busy="true">
      <p className={styles.eyebrow}>ALPHALAWVA ACCOUNT</p>
      <h1>로그인</h1>
      <p className={styles.description}>로그인 화면을 준비하고 있어요.</p>
    </div>
  );
}

export default function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  return (
    <main className={styles.page}>
      <Suspense fallback={<LoginFallback />}>
        <LoginContent searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
