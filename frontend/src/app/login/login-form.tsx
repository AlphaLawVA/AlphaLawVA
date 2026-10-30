"use client";

import Link from "next/link";
import { useActionState } from "react";
import { loginAction, type AuthActionState } from "@/app/auth/actions";
import styles from "@/app/auth/auth-form.module.css";

const initialState: AuthActionState = {};

export function LoginForm({ nextPath }: { nextPath: string }) {
  const [state, formAction, pending] = useActionState(loginAction, initialState);

  return (
    <div className={styles.card}>
      <p className={styles.eyebrow}>ALPHALAWVA ACCOUNT</p>
      <h1>로그인</h1>
      <p className={styles.description}>저장한 법률 결과와 판례를 내 보관함에서 이어서 확인하세요.</p>

      <form className={styles.form} action={formAction} noValidate>
        <input type="hidden" name="next" value={nextPath} />
        <div className={styles.field}>
          <label htmlFor="login-email">이메일</label>
          <input
            id="login-email"
            name="email"
            type="email"
            autoComplete="email"
            inputMode="email"
            required
            aria-describedby={state.fieldErrors?.email ? "login-email-error" : undefined}
          />
          {state.fieldErrors?.email && (
            <p className={styles.fieldError} id="login-email-error">
              {state.fieldErrors.email}
            </p>
          )}
        </div>

        <div className={styles.field}>
          <label htmlFor="login-password">비밀번호</label>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            aria-describedby={state.fieldErrors?.password ? "login-password-error" : undefined}
          />
          {state.fieldErrors?.password && (
            <p className={styles.fieldError} id="login-password-error">
              {state.fieldErrors.password}
            </p>
          )}
        </div>

        {state.message && (
          <p className={styles.formError} role="alert">
            {state.message}
          </p>
        )}

        <button className={styles.submit} type="submit" disabled={pending}>
          {pending ? "로그인 중..." : "로그인"}
        </button>
      </form>

      <p className={styles.switch}>
        아직 계정이 없나요?
        <Link href="/signup">회원가입</Link>
      </p>
    </div>
  );
}
