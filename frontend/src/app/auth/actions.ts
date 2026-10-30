"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { safeNextPath } from "@/lib/auth";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";

export type AuthActionState = {
  fieldErrors?: {
    email?: string;
    password?: string;
    passwordConfirm?: string;
  };
  message?: string;
  success?: boolean;
};

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PASSWORD_PATTERN = /^(?=.*[A-Za-z])(?=.*\d).{8,}$/;

function readText(formData: FormData, name: string) {
  const value = formData.get(name);
  return typeof value === "string" ? value : "";
}

function configurationError(): AuthActionState | null {
  if (isSupabaseConfigured()) {
    return null;
  }

  return {
    message: "로그인 서버 연결 설정이 아직 완료되지 않았어요. 잠시 후 다시 시도해 주세요.",
  };
}

export async function loginAction(
  _previousState: AuthActionState,
  formData: FormData,
): Promise<AuthActionState> {
  const configError = configurationError();
  if (configError) return configError;

  const email = readText(formData, "email").trim().toLowerCase();
  const password = readText(formData, "password");
  const fieldErrors: AuthActionState["fieldErrors"] = {};

  if (!EMAIL_PATTERN.test(email)) {
    fieldErrors.email = "이메일 형식을 확인해 주세요.";
  }
  if (!password) {
    fieldErrors.password = "비밀번호를 입력해 주세요.";
  }
  if (Object.keys(fieldErrors).length > 0) {
    return { fieldErrors };
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });

  if (error) {
    return { message: "이메일 또는 비밀번호가 올바르지 않아요." };
  }

  revalidatePath("/", "layout");
  redirect(safeNextPath(formData.get("next")));
}

export async function signupAction(
  _previousState: AuthActionState,
  formData: FormData,
): Promise<AuthActionState> {
  const configError = configurationError();
  if (configError) return configError;

  const email = readText(formData, "email").trim().toLowerCase();
  const password = readText(formData, "password");
  const passwordConfirm = readText(formData, "passwordConfirm");
  const fieldErrors: AuthActionState["fieldErrors"] = {};

  if (!EMAIL_PATTERN.test(email)) {
    fieldErrors.email = "이메일 형식을 확인해 주세요.";
  }
  if (!PASSWORD_PATTERN.test(password)) {
    fieldErrors.password = "영문과 숫자를 포함해 8자 이상 입력해 주세요.";
  }
  if (password !== passwordConfirm) {
    fieldErrors.passwordConfirm = "비밀번호가 서로 달라요.";
  }
  if (Object.keys(fieldErrors).length > 0) {
    return { fieldErrors };
  }

  const supabase = await createClient();
  const { data, error } = await supabase.auth.signUp({ email, password });

  if (error) {
    return { message: "회원가입을 완료하지 못했어요. 잠시 후 다시 시도해 주세요." };
  }

  if (!data.session) {
    return {
      success: true,
      message: "가입 요청이 완료됐어요. 이메일 인증 설정을 확인한 뒤 로그인해 주세요.",
    };
  }

  revalidatePath("/", "layout");
  redirect("/mypage");
}

export async function logoutAction() {
  if (isSupabaseConfigured()) {
    const supabase = await createClient();
    await supabase.auth.signOut();
  }

  revalidatePath("/", "layout");
  redirect("/login");
}
