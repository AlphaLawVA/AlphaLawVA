import { Suspense } from "react";
import { redirect } from "next/navigation";
import { SignupForm } from "./signup-form";
import { getAuthIdentity } from "@/lib/auth";
import styles from "@/app/auth/auth-form.module.css";

async function SignupContent() {
  const identity = await getAuthIdentity();
  if (identity) {
    redirect("/mypage");
  }

  return <SignupForm />;
}

function SignupFallback() {
  return (
    <div className={styles.card} aria-busy="true">
      <p className={styles.eyebrow}>ALPHALAWVA ACCOUNT</p>
      <h1>회원가입</h1>
      <p className={styles.description}>회원가입 화면을 준비하고 있어요.</p>
    </div>
  );
}

export default function SignupPage() {
  return (
    <main className={styles.page}>
      <Suspense fallback={<SignupFallback />}>
        <SignupContent />
      </Suspense>
    </main>
  );
}
