/**
 * rest.ts — fetchInbox 경계 정규화 회귀 가드(US-008 통합에서 발견된 계약 불일치).
 *
 * 배경: 백엔드 GET /api/inbox 의 review 항목은 식별자를 `interrupt_id`(= 이벤트 seq)로
 * 보낸다(office.approve._all_pending_interrupts). 프론트 컴포넌트(ReviewTab)는 `item.id`로
 * 승인/거부 POST 경로(/api/inbox/{id}/approve)를 만든다. 정규화가 없으면 id=undefined →
 * POST /api/inbox/undefined/approve 로 승인이 실패한다(US-008 e2e에서 실제로 깨졌던 버그).
 * fetchInbox가 interrupt_id → id 로 정규화함을 이 테스트가 못박는다.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchInbox, login, logout, authStatus } from "../api/rest";

afterEach(() => {
  vi.unstubAllGlobals();
});

/** fetch를 주어진 JSON으로 성공 응답하게 mock한다. */
function stubFetchJson(data: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: () => Promise.resolve(data),
    })
  );
}

describe("fetchInbox 정규화(interrupt_id → id)", () => {
  it("백엔드 review 항목의 interrupt_id를 id로 매핑한다", async () => {
    // 백엔드 실제 형태 — interrupt_id만 있고 id 없음(office.approve 계약).
    stubFetchJson({
      review: [
        {
          interrupt_id: 14,
          run_id: "run-x",
          node_id: "n3_email_send",
          tool: "email_send",
          risk: "high",
          ts: "2026-06-08T00:00:00+00:00",
          payload: { task_id: 1 },
          elapsed: 12,
        },
      ],
      notify: [],
      question: [],
    });

    const data = await fetchInbox();
    expect(data.review).toHaveLength(1);
    // ★핵심: id가 interrupt_id(14)로 채워져야 한다(승인 POST 경로의 식별자).
    expect(data.review[0].id).toBe(14);
    expect(data.review[0].tool).toBe("email_send");
    expect(data.review[0].node_id).toBe("n3_email_send");
    expect(data.review[0].elapsed).toBe(12);
  });

  it("notify·question은 그대로 통과시킨다", async () => {
    stubFetchJson({
      review: [],
      notify: [
        { seq: 19, type: "RUN_FINISHED", run_id: "run-x", status: "success", ts: "t" },
      ],
      question: [
        { seq: 5, type: "NODE_FAILED", run_id: "run-x", node_id: "n4", reason: "question", detail: "?", ts: "t" },
      ],
    });

    const data = await fetchInbox();
    expect(data.notify).toHaveLength(1);
    expect(data.notify[0].type).toBe("RUN_FINISHED");
    expect(data.question).toHaveLength(1);
    expect(data.question[0].reason).toBe("question");
  });

  it("필드 누락(빈 응답)에도 안전하다(빈 배열)", async () => {
    stubFetchJson({});
    const data = await fetchInbox();
    expect(data.review).toEqual([]);
    expect(data.notify).toEqual([]);
    expect(data.question).toEqual([]);
  });

  it("id 폴백 — interrupt_id가 없고 id만 있으면 id를 쓴다", async () => {
    // 방어적: 혹시 백엔드가 id로 보내도 동작(폴백 경로).
    stubFetchJson({
      review: [
        { id: 7, node_id: "n3", tool: "email_send", risk: "high", ts: "t" },
      ],
      notify: [],
      question: [],
    });
    const data = await fetchInbox();
    expect(data.review[0].id).toBe(7);
  });

  it("응답이 ok가 아니면 Error를 throw한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        json: () => Promise.resolve({}),
      })
    );
    await expect(fetchInbox()).rejects.toThrow(/fetchInbox 실패/);
  });
});

// ── 인증 함수(B2 — login/logout/authStatus) ────────────────────────────────────

describe("login — POST /api/login", () => {
  it("200 + body.ok:true이면 true를 반환하고 /api/login에 password를 POST한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: () => Promise.resolve({ ok: true }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await login("secret");
    expect(result).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ password: "secret" });
  });

  it("★200 + body.ok:false(틀린 암호)이면 false를 반환한다(throw 없음)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        json: () => Promise.resolve({ ok: false }),
      }),
    );
    await expect(login("wrong")).resolves.toBe(false);
  });

  it("★401(하위 호환) — throw하지 않고 false를 반환한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401, statusText: "Unauthorized" }),
    );
    await expect(login("wrong")).resolves.toBe(false);
  });

  it("5xx 서버 오류이면 Error를 throw한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Internal Server Error" }),
    );
    await expect(login("pw")).rejects.toThrow(/login 실패/);
  });
});

describe("logout — POST /api/logout", () => {
  it("/api/logout로 POST한다(ok면 정상 종료)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, statusText: "OK" });
    vi.stubGlobal("fetch", fetchMock);
    await logout();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/logout");
    expect(init.method).toBe("POST");
  });

  it("ok가 아니면 Error를 throw한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Err" }),
    );
    await expect(logout()).rejects.toThrow(/logout 실패/);
  });
});

describe("authStatus — GET /api/auth/status", () => {
  it("{authed:true} → true", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        json: () => Promise.resolve({ authed: true }),
      }),
    );
    await expect(authStatus()).resolves.toBe(true);
  });

  it("{authed:false} → false", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        json: () => Promise.resolve({ authed: false }),
      }),
    );
    await expect(authStatus()).resolves.toBe(false);
  });

  it("ok가 아니면 Error를 throw한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Err",
        json: () => Promise.resolve({}),
      }),
    );
    await expect(authStatus()).rejects.toThrow(/authStatus 실패/);
  });
});
