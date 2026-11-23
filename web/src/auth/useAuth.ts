/**
 * useAuth — 백엔드 세션 인증 상태 훅 (B2 로그인 게이트).
 *
 * 백엔드 쿠키 기반 인증(same-origin·credentials 자동)을 React 상태로 노출한다.
 *   - mount 시 GET /api/auth/status로 초기 authed 확정(그 전엔 null=확인중).
 *   - ★주기 폴링(POLL_INTERVAL_MS)으로 status 재확인 → 만료/타 탭 로그아웃을 동기화.
 *     setInterval은 cleanup에서 반드시 해제(누수·좀비 폴링 0).
 *   - signIn(pw): POST /api/login → 성공 시 authed=true.
 *   - signOut(): POST /api/logout → authed=false.
 *
 * authed가 true→false로 떨어지면 AuthGate가 App을 언마운트한다(SSE/WS 훅 자동 해제·
 * 만료 후 좀비 재연결 폭주 차단 — 메모리 H-01/US-006b 정신).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { authStatus, login, logout } from "../api/rest";

/** 만료 동기화 폴링 주기(ms). 30초마다 서버 세션 유효성을 재확인한다. */
export const POLL_INTERVAL_MS = 30_000;

export interface UseAuthResult {
  /** 인증 여부. null=초기 확인 중(아직 status 응답 전). */
  authed: boolean | null;
  /** 암호로 로그인. 성공 시 authed=true·true 반환, 실패 시 false 반환(throw 안 함). */
  signIn: (password: string) => Promise<boolean>;
  /** 로그아웃. authed=false로 전환. */
  signOut: () => Promise<void>;
}

/**
 * 인증 상태 훅 — 초기 status 확인 + 주기 폴링 + signIn/signOut.
 */
export function useAuth(): UseAuthResult {
  const [authed, setAuthed] = useState<boolean | null>(null);

  // 언마운트 후 setState 방지(폴링 응답이 뒤늦게 도착하는 경우 가드).
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    /** status를 1회 조회해 authed에 반영(실패=인증 안 됨으로 보수적 처리). */
    const checkStatus = async (): Promise<void> => {
      try {
        const ok = await authStatus();
        if (mountedRef.current) setAuthed(ok);
      } catch {
        // 네트워크/타임아웃 등 — 인증되지 않은 것으로 간주(게이트가 LoginScreen 표시).
        if (mountedRef.current) setAuthed(false);
      }
    };

    // 초기 1회 확인.
    void checkStatus();
    // ★주기 폴링(만료/타 탭 로그아웃 동기화) — cleanup에서 반드시 해제.
    const timer = window.setInterval(() => void checkStatus(), POLL_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      window.clearInterval(timer);
    };
  }, []);

  const signIn = useCallback(async (password: string): Promise<boolean> => {
    const ok = await login(password);
    if (ok && mountedRef.current) setAuthed(true);
    return ok;
  }, []);

  const signOut = useCallback(async (): Promise<void> => {
    await logout();
    if (mountedRef.current) setAuthed(false);
  }, []);

  return { authed, signIn, signOut };
}

export default useAuth;
