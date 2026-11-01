"use client";

import Link from "next/link";
import { useActionState } from "react";
import { signupAction, type AuthActionState } from "@/app/auth/actions";
import styles from "@/app/auth/auth-form.module.css";

const initialState: AuthActionState = {};

export function SignupForm() {
  const [state, formAction, pending] = useActionState(signupAction, initialState);

  return (
    <div className={styles.card}>
      <p className={styles.eyebrow}>ALPHALAWVA ACCOUNT</p>
      <h1>회원가입</h1>
      <p className={styles.description}>이메일과 비밀번호만으로 시작할 수 있어요.</p>

      <form className={styles.form} action={formAction} noValidate>
        <div className={styles.field}>
          <label htmlFor="signup-email">이메일</label>
          <input
            id="signup-email"
            name="email"
            type="email"
            autoComplete="email"
            inputMode="email"
            required
            aria-describedby={state.fieldErrors?.email ? "signup-email-error" : undefined}
          />
          {state.fieldErrors?.email && (
            <p className={styles.fieldError} id="signup-email-error">
              {state.fieldErrors.email}
            </p>
          )}
        </div>

        <div className={styles.field}>
          <label htmlFor="signup-password">비밀번호</label>
          <input
            id="signup-password"
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
            aria-describedby="signup-password-hint signup-password-error"
          />
          <p className={styles.hint} id="signup-password-hint">
            영문과 숫자를 포함해 8자 이상 입력해 주세요.
          </p>
          {state.fieldErrors?.password && (
            <p className={styles.fieldError} id="signup-password-error">
              {state.fieldErrors.password}
            </p>
          )}
        </div>

        <div className={styles.field}>
          <label htmlFor="signup-password-confirm">비밀번호 확인</label>
          <input
            id="signup-password-confirm"
            name="passwordConfirm"
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
            aria-describedby={
              state.fieldErrors?.passwordConfirm ? "signup-password-confirm-error" : undefined
            }
          />
          {state.fieldErrors?.passwordConfirm && (
            <p className={styles.fieldError} id="signup-password-confirm-error">
              {state.fieldErrors.passwordConfirm}
            </p>
          )}
        </div>

        {state.message && (
          <p className={state.success ? styles.success : styles.formError} role="alert">
            {state.message}
          </p>
        )}

        <button className={styles.submit} type="submit" disabled={pending}>
          {pending ? "가입 중..." : "회원가입"}
        </button>
      </form>

      <p className={styles.switch}>
        이미 계정이 있나요?
        <Link href="/login">로그인</Link>
      </p>
    </div>
  );
}
