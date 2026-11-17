// 계약서 검토·특약 생성 같은 비동기 작업을 202 접수 후 GET으로 기다리는 공통 폴링 도구.

export type JobStatus = "queued" | "processing" | "completed" | "failed";

export type JobSnapshot<T> = {
  status: JobStatus;
  /** 서버가 처리 중인 단계 이름. 처리 중에만 값이 있다. */
  stage: string | null;
  result: T | null;
  error: { code: string; message: string } | null;
};

export class JobPollingError extends Error {
  constructor(
    message: string,
    readonly kind: "failed" | "expired" | "timeout" | "network",
  ) {
    super(message);
    this.name = "JobPollingError";
  }
}

type PollOptions<T> = {
  /** 상태가 바뀔 때마다 호출된다(단계 표시용). */
  onUpdate?: (snapshot: JobSnapshot<T>) => void;
  /** 이 시간이 지나도 끝나지 않으면 onSlow를 한 번 호출한다. */
  slowAfterMs: number;
  onSlow?: () => void;
  /** 이 시간이 지나면 폴링을 멈추고 timeout 오류를 던진다. */
  giveUpAfterMs: number;
  signal?: AbortSignal;
  /** 조회 중 404(만료·서버 재시작)를 판별하기 위해 HTTP 상태를 읽는 함수. */
  statusOf?: (error: unknown) => number | undefined;
};

const MAX_CONSECUTIVE_NETWORK_ERRORS = 3;
const FAST_INTERVAL_MS = 1500;
const SLOW_INTERVAL_MS = 3000;
const FAST_PHASE_MS = 20_000;

function sleep(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("aborted", "AbortError"));
      return;
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

/** 작업이 completed가 될 때까지 조회해 result를 돌려준다. failed·만료·시간 초과는 JobPollingError로 던진다. */
export async function pollJob<T>(
  fetchJob: () => Promise<JobSnapshot<T>>,
  options: PollOptions<T>,
): Promise<T> {
  const startedAt = Date.now();
  let slowNotified = false;
  let networkErrors = 0;

  for (;;) {
    if (options.signal?.aborted) throw new DOMException("aborted", "AbortError");
    const elapsed = Date.now() - startedAt;
    if (elapsed >= options.giveUpAfterMs) {
      throw new JobPollingError(
        "처리 시간이 너무 길어 기다리기를 멈췄습니다. 잠시 후 다시 시도해 주세요.",
        "timeout",
      );
    }
    if (!slowNotified && elapsed >= options.slowAfterMs) {
      slowNotified = true;
      options.onSlow?.();
    }

    try {
      const snapshot = await fetchJob();
      networkErrors = 0;
      options.onUpdate?.(snapshot);
      if (snapshot.status === "completed" && snapshot.result) return snapshot.result;
      if (snapshot.status === "failed") {
        throw new JobPollingError(
          snapshot.error?.message ?? "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
          "failed",
        );
      }
    } catch (error) {
      if (error instanceof JobPollingError || isAbortError(error)) throw error;
      const status = options.statusOf?.(error);
      if (status === 404) {
        throw new JobPollingError(
          "결과가 만료되었거나 서버가 다시 시작되었습니다. 다시 요청해 주세요.",
          "expired",
        );
      }
      // 일시적인 연결 오류는 몇 번까지 조용히 다시 시도한다.
      if (status === 0 && networkErrors < MAX_CONSECUTIVE_NETWORK_ERRORS) {
        networkErrors += 1;
      } else {
        throw error;
      }
    }

    await sleep(elapsed < FAST_PHASE_MS ? FAST_INTERVAL_MS : SLOW_INTERVAL_MS, options.signal);
  }
}
