/**
 * Nexus OS 디자인 토큰 — "Bit-Driven Logic" (영웅전설 16비트 JRPG 도트 + 레트로 브루탈리즘).
 *
 * 출처: 사용자 제작 Stitch "Pixel Agent Solo Office"(projects/<stitch-project-id>)의
 *   designTheme + dashboard_nexus_os.html. 디자인시스템 DARK 정본(HTML의 class="light"/#fcf9f8는
 *   1회 변이 → surface/background는 다크로 의식 치환).
 *
 * 원칙(절대):
 *  - 샤프 0px(라운드 금지) → BORDER_RADIUS === 0.
 *  - 하드엣지 베벨(소프트 섀도 금지) → pixelPanel()/pixelInset()/pixelBtn() 헬퍼.
 *  - 아트(캐릭터/맵/초상)는 NO PURPLE. 단 UI 구조색 Deep Indigo(#2E2A5E)는 허용(섀시 한정).
 *  - 새 npm 의존성 0 — Tailwind 미사용·인라인 스타일 전용. 폰트는 index.html <link>.
 */

import type { CSSProperties } from "react";

/** 샤프 픽셀아트 — 라운드 코너 전면 금지. */
export const BORDER_RADIUS = 0 as const;

/**
 * Bit-Driven Logic 팔레트(다크 정본).
 *  - indigo: 구조/사이드바/헤더(Deep Indigo·UI 섀시 한정).
 *  - green: 터미널 텍스트·데이터 리드아웃·1차 CTA(Pixel Green).
 *  - black: 터미널/맵 뷰포트 바닥(Neutral Black).
 *  - gold: 강조/타이틀 액센트(Amber/Gold).
 *  - 상태색: emerald(online)/amber(busy)/slate(offline) — RPG 상태 관례.
 */
export const NEXUS_COLORS = {
  // 코어 3색(AC 단언 대상)
  indigo: "#2E2A5E",
  green: "#39FF14",
  black: "#1A1A1A",
  // 액센트
  gold: "#F4C542",
  // 표면 계층(다크)
  surface: "#131313",
  surfaceLow: "#1C1B1B",
  surface2: "#20201F",
  surfaceHigh: "#2A2A2A",
  surfaceHighest: "#353535",
  // 텍스트
  onSurface: "#E5E2E1",
  onSurfaceVariant: "#C8C5D0",
  muted: "#928F9A",
  // 외곽선/베벨
  outline: "#928F9A",
  outlineVariant: "#47464F",
  bevelLight: "#3A3A3A",
  bevelDark: "#0E0E0E",
  // 상태(RPG)
  statusOnline: "#2FF801",
  statusBusy: "#F4C542",
  statusOffline: "#928F9A",
  error: "#FFB4AB",
} as const;

/** 폰트 스택(index.html <link>로 로드 — Space Mono/Inter/JetBrains Mono). */
export const NEXUS_FONTS = {
  /** 헤드라인·로고·내비(16비트 무드). */
  headline: "'Space Mono', ui-monospace, monospace",
  /** 본문·로그(가독). */
  body: "'Inter', system-ui, sans-serif",
  /** 터미널 출력·라벨(개발자 무드). */
  terminal: "'JetBrains Mono', ui-monospace, monospace",
} as const;

/** 4px 그리드 간격. */
export const NEXUS_SPACING = {
  unit: 4,
  gutter: 16,
  margin: 24,
} as const;

/**
 * 하드엣지 베벨 패널(Level 1 — 올라온 표면).
 * 위/왼쪽=하이라이트, 아래/오른쪽=섀도(소프트 섀도 대신 픽셀 베벨).
 */
export function pixelPanel(bg: string = NEXUS_COLORS.surface2): CSSProperties {
  return {
    backgroundColor: bg,
    borderStyle: "solid",
    borderWidth: 2,
    borderTopColor: NEXUS_COLORS.bevelLight,
    borderLeftColor: NEXUS_COLORS.bevelLight,
    borderRightColor: NEXUS_COLORS.bevelDark,
    borderBottomColor: NEXUS_COLORS.bevelDark,
    borderRadius: BORDER_RADIUS,
  };
}

/** 눌린/인셋 패널(Level -1 — 함몰). 베벨 방향 반전. */
export function pixelInset(bg: string = NEXUS_COLORS.surfaceLow): CSSProperties {
  return {
    backgroundColor: bg,
    borderStyle: "solid",
    borderWidth: 2,
    borderTopColor: NEXUS_COLORS.bevelDark,
    borderLeftColor: NEXUS_COLORS.bevelDark,
    borderRightColor: NEXUS_COLORS.bevelLight,
    borderBottomColor: NEXUS_COLORS.bevelLight,
    borderRadius: BORDER_RADIUS,
  };
}

/** 레트로 3D 버튼(기본 상태). 클릭 시 translate(1,1)+베벨 반전은 호출부 :active로. */
export function pixelButton(bg: string = NEXUS_COLORS.surfaceHigh): CSSProperties {
  return {
    backgroundColor: bg,
    color: NEXUS_COLORS.onSurface,
    borderStyle: "solid",
    borderWidth: 2,
    borderTopColor: NEXUS_COLORS.bevelLight,
    borderLeftColor: NEXUS_COLORS.bevelLight,
    borderRightColor: NEXUS_COLORS.outline,
    borderBottomColor: NEXUS_COLORS.outline,
    borderRadius: BORDER_RADIUS,
    cursor: "pointer",
    fontFamily: NEXUS_FONTS.terminal,
  };
}

/** 터미널 창(검정 바탕 + 그린 텍스트). */
export function terminalWindow(): CSSProperties {
  return {
    backgroundColor: NEXUS_COLORS.black,
    color: NEXUS_COLORS.green,
    fontFamily: NEXUS_FONTS.terminal,
    borderStyle: "solid",
    borderWidth: 2,
    borderColor: NEXUS_COLORS.outlineVariant,
    borderRadius: BORDER_RADIUS,
  };
}

/** 편의 집약 객체(테스트·소비부에서 nexusTheme.colors.indigo 형태 접근). */
export const nexusTheme = {
  colors: NEXUS_COLORS,
  fonts: NEXUS_FONTS,
  spacing: NEXUS_SPACING,
  borderRadius: BORDER_RADIUS,
  pixelPanel,
  pixelInset,
  pixelButton,
  terminalWindow,
} as const;

export default nexusTheme;
