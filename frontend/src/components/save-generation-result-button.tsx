"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Bookmark } from "lucide-react";
import { saveGenerationResult } from "@/lib/contract-api";
import type { GenerationResultKind } from "@/lib/contract-api";
import { createClient } from "@/lib/supabase/client";

type Props = {
  kind: GenerationResultKind;
  sourceId: string;
  loginNext: string;
  className?: string;
};

// 방금 만든 결과를 로그인한 사용자의 보관함에 저장한다. 로그인이 없으면 로그인 화면으로 보낸다.
export function SaveGenerationResultButton({ kind, sourceId, loginNext, className }: Props) {
  const router = useRouter();
  const [status, setStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState("");

  async function save() {
    if (status !== "idle") return;
    setError("");
    setStatus("saving");
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        setStatus("idle");
        router.push(`/login?next=${encodeURIComponent(loginNext)}`);
        return;
      }
      await saveGenerationResult(kind, sourceId, session.access_token);
      setStatus("saved");
    } catch (saveError) {
      setStatus("idle");
      setError(saveError instanceof Error ? saveError.message : "결과를 저장하지 못했습니다.");
    }
  }

  return (
    <>
      <button className={className} type="button" disabled={status !== "idle"} onClick={() => void save()}>
        <Bookmark size={16} />
        {status === "saving" ? "저장 중..." : status === "saved" ? "보관함에 저장됨" : "보관함에 저장"}
      </button>
      {error && <span role="alert" style={{ color: "#b3261e", fontSize: "0.85rem" }}>{error}</span>}
    </>
  );
}
