/**
 * REST API 호출 모듈 (US-007 Agent Inbox + US-004 워크플로 시작).
 *
 * 백엔드 계약:
 *   GET  /api/inbox              → {review: [...], notify: [...], question: [...]}
 *   POST /api/inbox/{id}/approve → 승인 기록(resume=False, 재개는 폴링 루프)
 *   POST /api/inbox/{id}/reject  → 거부 기록(동일 경로)
 *   POST /api/run                → 워크플로 시작 (US-004, 있으면)
 *
 * 모든 호출은 fetch() 기반. 오류 시 Error를 throw한다.
 * 테스트에서는 vi.stubGlobal('fetch', ...) 또는 vi.spyOn으로 mock.
 */

// ── Inbox 관련 타입 ──────────────────────────────────────────────────────────

/** Review 칸 항목 — 미해소 INTERRUPT_REQUESTED */
export interface ReviewItem {
  /**
   * 식별자(승인/거부 POST 경로에 쓰임). 백엔드(GET /api/inbox)는 이 값을
   * `interrupt_id`(= 이벤트 seq)로 보낸다 — fetchInbox가 `id`로 정규화한다.
   */
  id: number;
  node_id: string;
  tool: string;
  risk: string;
  /** 요청된 시각 (ISO-8601) */
  ts: string;
  /** 추가 페이로드 */
  payload?: Record<string, unknown>;
  /** 경과시간(초·백엔드 _all_pending_interrupts) — 표시는 ts 기준 계산이 우선. */
  elapsed?: number;
}

/**
 * 백엔드 GET /api/inbox 의 review 항목 원본 형태(정규화 전).
 * office.approve._all_pending_interrupts 가 식별자를 `interrupt_id`로 보낸다.
 */
interface RawReviewItem {
  interrupt_id?: number;
  id?: number;
  node_id?: string;
  tool?: string;
  risk?: string;
  ts?: string;
  payload?: Record<string, unknown>;
  elapsed?: number;
}

/**
 * 백엔드 review 항목을 프론트 ReviewItem으로 정규화한다.
 * 백엔드는 식별자를 `interrupt_id`로 보내므로 이를 `id`로 매핑한다(없으면 id 폴백).
 * 이 경계 정규화 덕에 컴포넌트는 항상 `item.id`만 보면 된다(승인 POST 경로 일치).
 */
function normalizeReviewItem(raw: RawReviewItem): ReviewItem {
  return {
    id: raw.interrupt_id ?? raw.id ?? -1,
    node_id: raw.node_id ?? "",
    tool: raw.tool ?? "",
    risk: raw.risk ?? "",
    ts: raw.ts ?? "",
    payload: raw.payload,
    elapsed: raw.elapsed,
  };
}

/** Notify 칸 항목 — NODE_FAILED(escalation|gating) · RUN_FINISHED · RUN_ERROR */
export interface NotifyItem {
  seq: number;
  type: string;
  run_id: string;
  node_id?: string;
  reason?: string;
  detail?: string;
  status?: string;
  error?: string;
  ts: string;
  /** 프론트에서 읽음 처리용 플래그 (서버에는 저장 안 함, 로컬 상태) */
  read?: boolean;
}

/** Question 칸 항목 — NODE_FAILED(question) */
export interface QuestionItem {
  seq: number;
  type: string;
  run_id: string;
  node_id: string;
  reason: string;
  detail: string;
  ts: string;
}

/** GET /api/inbox 응답 형태 */
export interface InboxData {
  review: ReviewItem[];
  notify: NotifyItem[];
  question: QuestionItem[];
}

// ── REST 호출 함수 ────────────────────────────────────────────────────────────

/**
 * GET /api/inbox — Inbox 3분류 데이터를 가져온다.
 * 실패 시 Error throw.
 */
export async function fetchInbox(): Promise<InboxData> {
  // hang 가드(H-09): 서버 무응답 시 브라우저 기본(~300s) 대신 15s에 AbortError로
  // 끊어 탭 매달림을 막는다. 정상 응답(<15s)은 무변, 끊긴 경우 기존 try/catch가 처리.
  const res = await fetch("/api/inbox", { signal: AbortSignal.timeout(15000) });
  if (!res.ok) {
    throw new Error(`fetchInbox 실패: ${res.status} ${res.statusText}`);
  }
  const raw = (await res.json()) as {
    review?: RawReviewItem[];
    notify?: NotifyItem[];
    question?: QuestionItem[];
  };
  // review 항목만 정규화(interrupt_id → id). notify/question은 그대로.
  return {
    review: (raw.review ?? []).map(normalizeReviewItem),
    notify: raw.notify ?? [],
    question: raw.question ?? [],
  };
}

/**
 * POST /api/inbox/{id}/approve — 지정 항목을 승인한다.
 * resume=False라 기록만(재개는 백엔드 폴링 루프 단일 담당).
 */
export async function approve(id: number): Promise<void> {
  const res = await fetch(`/api/inbox/${id}/approve`, {
    method: "POST",
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    throw new Error(`approve 실패(id=${id}): ${res.status} ${res.statusText}`);
  }
}

/**
 * POST /api/inbox/{id}/reject — 지정 항목을 거부한다.
 */
export async function reject(id: number): Promise<void> {
  const res = await fetch(`/api/inbox/${id}/reject`, {
    method: "POST",
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    throw new Error(`reject 실패(id=${id}): ${res.status} ${res.statusText}`);
  }
}

/**
 * POST /api/run — 워크플로를 시작한다 (US-004 계약).
 * @param workflow 워크플로 이름 (예: "crm_notify")
 */
export async function startRun(workflow: string): Promise<void> {
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workflow }),
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    throw new Error(`startRun 실패(workflow=${workflow}): ${res.status} ${res.statusText}`);
  }
}

/**
 * POST /api/run/cancel-all — 진행 중인 모든 워크플로를 일괄 취소한다 (P1 step3.5).
 *
 * 멀티 워커풀(concurrency:N)에서 동시에 running/suspended인 여러 task를 한 번에 취소한다.
 * 동결 runner.py의 /cancel(단일 task)과 별개 경로. 202(cancelling-all) 또는 활성 없으면
 * 200(no_active_run). 반환: 취소 신호를 보낸 활성 task 수(count).
 */
export async function cancelAll(): Promise<number> {
  const res = await fetch("/api/run/cancel-all", {
    method: "POST",
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    throw new Error(`cancelAll 실패: ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as { status: string; count: number };
  return typeof data.count === "number" ? data.count : 0;
}

// ── 인증(B2 로그인 게이트) ────────────────────────────────────────────────────
//
// 백엔드 계약(이미 구현됨·쿠키 기반·same-origin이라 credentials 자동 전송):
//   POST /api/login {password}   → 200 {authed:true} + httponly 쿠키 / 401 {authed:false}
//   POST /api/logout             → {authed:false} + 쿠키 삭제
//   GET  /api/auth/status        → {authed:boolean}

/**
 * POST /api/login — 암호로 로그인한다.
 * ★백엔드는 항상 200을 반환하며 body.ok로 인증 결과를 전달한다.
 *   {ok:true}=성공, {ok:false}=틀린 암호.
 * 네트워크/타임아웃 등 진짜 실패만 Error로 전파된다.
 * @returns 성공 시 true, 암호 불일치(ok:false)면 false.
 */
export async function login(password: string): Promise<boolean> {
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    // 네트워크/서버 오류(5xx 등) — 진짜 실패이므로 throw.
    // 단, 401/403은 일부 백엔드가 틀린 암호에 쓰므로 false로 처리한다(하위 호환).
    if (res.status === 401 || res.status === 403) return false;
    throw new Error(`login 실패: ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as { ok?: boolean };
  return data.ok === true;
}

/**
 * POST /api/logout — 로그아웃(서버 세션 파기 + 쿠키 삭제).
 */
export async function logout(): Promise<void> {
  const res = await fetch("/api/logout", {
    method: "POST",
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    throw new Error(`logout 실패: ${res.status} ${res.statusText}`);
  }
}

/**
 * GET /api/auth/status — 현재 인증 여부를 확인한다(쿠키 검증).
 * @returns 인증되어 있으면 true.
 */
export async function authStatus(): Promise<boolean> {
  const res = await fetch("/api/auth/status", {
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    throw new Error(`authStatus 실패: ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as { authed?: boolean };
  return data.authed === true;
}
