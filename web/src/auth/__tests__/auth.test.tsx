/**
 * 로그인 게이트 단위 테스트 (B2) — useAuth / LoginScreen / AuthGate.
 *
 * 검증 항목:
 *   - useAuth: 초기 authed null→status 반영(false/true)·signIn 성공/실패·signOut·주기 폴링.
 *   - LoginScreen: 암호 제출→onSignIn·실패 시 에러 메시지·testid.
 *   - AuthGate: null→로딩·false→LoginScreen·true→children·만료(폴링 401)→LoginScreen 복귀.
 *
 * ★api/rest(login/logout/authStatus)를 mock해 실 fetch를 회피한다.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, act, waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";

// ── api/rest mock(인증 3함수) ───────────────────────────────────────────────────
const loginMock = vi.fn();
const logoutMock = vi.fn();
const authStatusMock = vi.fn();
vi.mock("../../api/rest", () => ({
  login: (pw: string) => loginMock(pw),
  logout: () => logoutMock(),
  authStatus: () => authStatusMock(),
}));

// ── mock 이후 import ───────────────────────────────────────────────────────────
import { useAuth, POLL_INTERVAL_MS } from "../useAuth";
import { LoginScreen } from "../LoginScreen";
import { AuthGate } from "../AuthGate";

beforeEach(() => {
  loginMock.mockReset();
  logoutMock.mockReset();
  authStatusMock.mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ── useAuth ─────────────────────────────────────────────────────────────────

describe("useAuth — 인증 상태 훅", () => {
  it("초기 authed는 null(확인 중)이며 status 응답 후 true로 반영된다", async () => {
    authStatusMock.mockResolvedValue(true);
    const { result } = renderHook(() => useAuth());
    // 초기엔 null(아직 status 응답 전).
    expect(result.current.authed).toBeNull();
    await waitFor(() => expect(result.current.authed).toBe(true));
    expect(authStatusMock).toHaveBeenCalled();
  });

  it("status가 false면 authed=false", async () => {
    authStatusMock.mockResolvedValue(false);
    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.authed).toBe(false));
  });

  it("status 호출이 throw하면 보수적으로 authed=false", async () => {
    authStatusMock.mockRejectedValue(new Error("network"));
    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.authed).toBe(false));
  });

  it("signIn 성공 시 authed=true·true 반환", async () => {
    authStatusMock.mockResolvedValue(false);
    loginMock.mockResolvedValue(true);
    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.authed).toBe(false));

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.signIn("pw");
    });
    expect(ok).toBe(true);
    expect(loginMock).toHaveBeenCalledWith("pw");
    expect(result.current.authed).toBe(true);
  });

  it("signIn 실패(false) 시 authed는 그대로 false·false 반환", async () => {
    authStatusMock.mockResolvedValue(false);
    loginMock.mockResolvedValue(false);
    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.authed).toBe(false));

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.signIn("wrong");
    });
    expect(ok).toBe(false);
    expect(result.current.authed).toBe(false);
  });

  it("signOut 호출 시 logout 후 authed=false", async () => {
    authStatusMock.mockResolvedValue(true);
    logoutMock.mockResolvedValue(undefined);
    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.authed).toBe(true));

    await act(async () => {
      await result.current.signOut();
    });
    expect(logoutMock).toHaveBeenCalled();
    expect(result.current.authed).toBe(false);
  });

  it("★주기 폴링: interval 경과 시 authStatus 재호출(만료 동기화 — true→false)", async () => {
    vi.useFakeTimers();
    authStatusMock.mockResolvedValue(true);
    const { result } = renderHook(() => useAuth());
    // 초기 1회(마이크로태스크 플러시).
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.authed).toBe(true);
    expect(authStatusMock).toHaveBeenCalledTimes(1);

    // 만료 모사: 다음 폴링부터 false.
    authStatusMock.mockResolvedValue(false);
    await act(async () => {
      vi.advanceTimersByTime(POLL_INTERVAL_MS);
      // 폴링 호출 → authStatus resolve → setState. 마이크로태스크를 충분히 플러시.
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(authStatusMock).toHaveBeenCalledTimes(2);
    expect(result.current.authed).toBe(false);
  });

  it("★언마운트 시 폴링 interval이 해제된다(추가 호출 0·누수 차단)", async () => {
    vi.useFakeTimers();
    authStatusMock.mockResolvedValue(true);
    const { unmount } = renderHook(() => useAuth());
    await act(async () => {
      await Promise.resolve();
    });
    expect(authStatusMock).toHaveBeenCalledTimes(1);
    unmount();
    // 언마운트 후 시간이 흘러도 폴링이 더 일어나지 않는다.
    await act(async () => {
      vi.advanceTimersByTime(POLL_INTERVAL_MS * 3);
      await Promise.resolve();
    });
    expect(authStatusMock).toHaveBeenCalledTimes(1);
  });
});

// ── LoginScreen ─────────────────────────────────────────────────────────────

describe("LoginScreen — 암호 로그인 화면", () => {
  it("testid(login-screen·login-password·login-submit) 렌더", () => {
    render(<LoginScreen onSignIn={vi.fn().mockResolvedValue(true)} />);
    expect(screen.getByTestId("login-screen")).toBeDefined();
    expect(screen.getByTestId("login-password")).toBeDefined();
    expect(screen.getByTestId("login-submit")).toBeDefined();
    // 암호 input은 type=password.
    expect((screen.getByTestId("login-password") as HTMLInputElement).type).toBe("password");
  });

  it("제출 시 onSignIn(암호)을 호출한다", async () => {
    const onSignIn = vi.fn().mockResolvedValue(true);
    render(<LoginScreen onSignIn={onSignIn} />);
    fireEvent.change(screen.getByTestId("login-password"), { target: { value: "secret" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("login-submit"));
    });
    expect(onSignIn).toHaveBeenCalledWith("secret");
  });

  it("로그인 실패(false) 시 에러 메시지를 표시한다", async () => {
    const onSignIn = vi.fn().mockResolvedValue(false);
    render(<LoginScreen onSignIn={onSignIn} />);
    fireEvent.change(screen.getByTestId("login-password"), { target: { value: "wrong" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("login-submit"));
    });
    expect(screen.getByTestId("login-error")).toBeDefined();
    expect(screen.getByTestId("login-error").textContent).toContain("올바르지 않습니다");
  });

  it("성공 시엔 에러 메시지가 없다", async () => {
    const onSignIn = vi.fn().mockResolvedValue(true);
    render(<LoginScreen onSignIn={onSignIn} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("login-submit"));
    });
    expect(screen.queryByTestId("login-error")).toBeNull();
  });
});

// ── AuthGate ──────────────────────────────────────────────────────────────────

describe("AuthGate — 게이트 분기", () => {
  it("authed null(확인 중)엔 로딩 표시·children 미마운트", async () => {
    // status를 영원히 pending으로 둬 null 상태를 고정.
    authStatusMock.mockReturnValue(new Promise(() => {}));
    render(
      <AuthGate>
        <div data-testid="protected-app">APP</div>
      </AuthGate>,
    );
    expect(screen.getByTestId("auth-loading")).toBeDefined();
    expect(screen.queryByTestId("protected-app")).toBeNull();
    expect(screen.queryByTestId("login-screen")).toBeNull();
  });

  it("authed false면 LoginScreen 표시·children 미마운트", async () => {
    authStatusMock.mockResolvedValue(false);
    render(
      <AuthGate>
        <div data-testid="protected-app">APP</div>
      </AuthGate>,
    );
    await waitFor(() => expect(screen.getByTestId("login-screen")).toBeDefined());
    expect(screen.queryByTestId("protected-app")).toBeNull();
  });

  it("authed true면 children(App 셸) 마운트", async () => {
    authStatusMock.mockResolvedValue(true);
    render(
      <AuthGate>
        <div data-testid="protected-app">APP</div>
      </AuthGate>,
    );
    await waitFor(() => expect(screen.getByTestId("protected-app")).toBeDefined());
    expect(screen.queryByTestId("login-screen")).toBeNull();
  });

  it("★로그인 성공 전환: false→signIn→true → children 마운트(SSE/WS는 이때만 연결)", async () => {
    authStatusMock.mockResolvedValue(false);
    loginMock.mockResolvedValue(true);
    render(
      <AuthGate>
        <div data-testid="protected-app">APP</div>
      </AuthGate>,
    );
    await waitFor(() => expect(screen.getByTestId("login-screen")).toBeDefined());
    // 암호 입력→제출→authed true→children 등장.
    fireEvent.change(screen.getByTestId("login-password"), { target: { value: "ok" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("login-submit"));
    });
    await waitFor(() => expect(screen.getByTestId("protected-app")).toBeDefined());
    expect(screen.queryByTestId("login-screen")).toBeNull();
  });

  it("★만료 동기화: true(children)에서 폴링 401 감지 시 LoginScreen 복귀(children 언마운트)", async () => {
    vi.useFakeTimers();
    authStatusMock.mockResolvedValue(true);
    render(
      <AuthGate>
        <div data-testid="protected-app">APP</div>
      </AuthGate>,
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByTestId("protected-app")).toBeDefined();

    // 만료: 다음 폴링부터 false → LoginScreen 복귀·children 언마운트.
    authStatusMock.mockResolvedValue(false);
    await act(async () => {
      vi.advanceTimersByTime(POLL_INTERVAL_MS);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("login-screen")).toBeDefined();
    expect(screen.queryByTestId("protected-app")).toBeNull();
  });
});
