/**
 * LoginScreen — 암호 로그인 화면 (B2 로그인 게이트).
 *
 * Nexus OS 도트 테마(nexusTheme 토큰 전용·pixelPanel/pixelButton·NO PURPLE·다크·gold/green).
 * 암호 input(type=password) + 제출 버튼. 제출 → onSignIn(pw) → 실패 시 에러 메시지.
 * AuthGate가 authed===false일 때 렌더한다.
 */

import { useState, type CSSProperties, type FormEvent } from "react";
import {
  NEXUS_COLORS,
  NEXUS_FONTS,
  NEXUS_SPACING,
  pixelPanel,
  pixelButton,
  pixelInset,
} from "../office/nexusTheme";

export interface LoginScreenProps {
  /** 암호 제출 콜백(useAuth.signIn). 성공 시 true 반환. */
  onSignIn: (password: string) => Promise<boolean>;
}

/** 암호 불일치 시 표시 문구(스펙 고정). */
const WRONG_PASSWORD_MSG = "암호가 올바르지 않습니다.";
/** 네트워크/서버 오류 시 문구. */
const NETWORK_ERROR_MSG = "로그인 중 오류가 발생했습니다. 다시 시도해 주세요.";

/**
 * 암호 로그인 화면. 제출 중엔 버튼 비활성·중복 제출 차단.
 */
export function LoginScreen({ onSignIn }: LoginScreenProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const ok = await onSignIn(password);
      // 성공 시 AuthGate가 App을 마운트하므로 이 화면은 곧 사라진다. 실패만 처리.
      if (!ok) setError(WRONG_PASSWORD_MSG);
    } catch {
      setError(NETWORK_ERROR_MSG);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div data-testid="login-screen" style={backdropStyle}>
      <form style={panelStyle} onSubmit={(e) => void handleSubmit(e)}>
        <div style={brandStyle}>PIXEL AGENT OFFICE</div>
        <div style={subtitleStyle}>인증이 필요합니다</div>

        <label style={labelStyle} htmlFor="login-password-input">
          PASSWORD
        </label>
        <input
          id="login-password-input"
          data-testid="login-password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
          autoComplete="current-password"
          style={inputStyle}
        />

        <button
          type="submit"
          data-testid="login-submit"
          disabled={submitting}
          style={{ ...submitBtnStyle, opacity: submitting ? 0.6 : 1 }}
        >
          {submitting ? "확인 중…" : "LOG IN"}
        </button>

        {error && (
          <div data-testid="login-error" style={errorStyle}>
            {error}
          </div>
        )}
      </form>
    </div>
  );
}

// ── 스타일(인라인·nexusTheme 토큰 전용·NO PURPLE) ───────────────────────────────

const backdropStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  backgroundColor: NEXUS_COLORS.surface,
  fontFamily: NEXUS_FONTS.body,
};

const panelStyle: CSSProperties = {
  ...pixelPanel(),
  display: "flex",
  flexDirection: "column",
  gap: NEXUS_SPACING.gutter - 4,
  width: 320,
  maxWidth: "90vw",
  padding: NEXUS_SPACING.margin,
};

const brandStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.headline,
  fontWeight: 700,
  fontSize: 22,
  letterSpacing: "-0.5px",
  color: NEXUS_COLORS.gold,
  textAlign: "center",
};

const subtitleStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 12,
  color: NEXUS_COLORS.muted,
  textAlign: "center",
  marginBottom: NEXUS_SPACING.unit * 2,
};

const labelStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 11,
  fontWeight: 700,
  color: NEXUS_COLORS.onSurfaceVariant,
};

const inputStyle: CSSProperties = {
  ...pixelInset(NEXUS_COLORS.black),
  padding: "8px 10px",
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 14,
  color: NEXUS_COLORS.green,
  outline: "none",
};

const submitBtnStyle: CSSProperties = {
  ...pixelButton(),
  marginTop: NEXUS_SPACING.unit * 2,
  padding: "10px 0",
  fontSize: 14,
  fontWeight: 700,
  color: NEXUS_COLORS.gold,
  textAlign: "center",
};

const errorStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 12,
  color: NEXUS_COLORS.error,
  textAlign: "center",
};

export default LoginScreen;
