import "server-only";

import { connection } from "next/server";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";

export type AuthIdentity = {
  id: string;
  email: string | null;
};

export async function getAuthIdentity(): Promise<AuthIdentity | null> {
  if (!isSupabaseConfigured()) {
    return null;
  }

  // Supabase validates JWT expiry against the current time, so auth checks
  // must run for the incoming request rather than during prerendering.
  await connection();

  const supabase = await createClient();
  const { data, error } = await supabase.auth.getClaims();

  if (error || !data?.claims?.sub) {
    return null;
  }

  return {
    id: data.claims.sub,
    email: typeof data.claims.email === "string" ? data.claims.email : null,
  };
}

export function safeNextPath(value: FormDataEntryValue | string | null | undefined) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) {
    return "/mypage";
  }

  return value;
}
