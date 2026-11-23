/**
 * AuthGate — 로그인 게이트 (B2).
 *
 * useAuth()로 인증 상태를 관리하며 자식(App)을 게이팅한다:
 *   - authed===null → 로딩 표시(초기 status 확인 중).
 *   - authed===false → <LoginScreen/>(App 미마운트 → SSE/WS 훅 미호출).
 *   - authed===true → {children}(인증된 앱 셸).
 *
 * ★false→true 전환 시 children(App)이 비로소 마운트되어 SSE/WS가 연결되고,
 *   true→false(만료/로그아웃) 시 App이 언마운트되어 SSE(EventSource 자동재연결)·
 *   WS(shouldReconnect) 훅이 자동 해제된다(좀비 재연결 폭주 차단).
 *
 * signOut은 SignOutContext로 App에 내려, 사이드바 Log Out이 호출하게 한다.
 * App 자체는 인증을 모르는 "인증된 앱 셸"로 유지된다(app.test가 <App/> 직접 렌더로 통과).
 */

import { createContext, useContext, type ReactNode, type CSSProperties } from "react";
import { useAuth } from "./useAuth";
import { LoginScreen } from "./LoginScreen";
import { NEXUS_COLORS, NEXUS_FONTS } from "../office/nexusTheme";

/**
 * signOut 컨텍스트 — AuthGate가 제공, App(사이드바 Log Out)이 소비.
 * 기본값은 no-op(컨텍스트 밖에서 App을 직접 렌더하는 app.test 대비·throw 안 함).
 */
const SignOutContext = createContext<() => void>(() => {});

/**
 * App에서 Log Out 핸들러를 얻는 훅. AuthGate 밖(테스트)에선 no-op을 반환한다.
 */
export function useSignOut(): () => void {
  return useContext(SignOutContext);
}

export interface AuthGateProps {
  /** 인증 성공 시 마운트할 앱 셸(App). */
  children: ReactNode;
}

/**
 * 인증 게이트. authed에 따라 로딩/로그인/children을 분기 렌더한다.
 */
export function AuthGate({ children }: AuthGateProps) {
  const { authed, signIn, signOut } = useAuth();

  if (authed === null) {
    return (
      <div data-testid="auth-loading" style={loadingStyle}>
        인증 확인 중…
      </div>
    );
  }

  if (!authed) {
    return <LoginScreen onSignIn={signIn} />;
  }

  // 인증됨: signOut을 컨텍스트로 내려 사이드바 Log Out이 호출하게 한다.
  return (
    <SignOutContext.Provider value={() => void signOut()}>
      {children}
    </SignOutContext.Provider>
  );
}

// ── 스타일(인라인·nexusTheme 토큰 전용) ─────────────────────────────────────────

const loadingStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  backgroundColor: NEXUS_COLORS.surface,
  color: NEXUS_COLORS.muted,
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 13,
};

export default AuthGate;
