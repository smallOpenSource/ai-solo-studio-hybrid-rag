/**
 * nexusTheme 토큰 단언 (US-1a) — Bit-Driven Logic 코어 토큰·샤프코너·베벨 헬퍼.
 */
import { describe, it, expect } from "vitest";
import {
  nexusTheme,
  NEXUS_COLORS,
  BORDER_RADIUS,
  pixelPanel,
  terminalWindow,
} from "../nexusTheme";

describe("nexusTheme — Bit-Driven Logic 코어 토큰", () => {
  it("코어 3색이 정확하다 (indigo·green·black)", () => {
    expect(NEXUS_COLORS.indigo).toBe("#2E2A5E");
    expect(NEXUS_COLORS.green).toBe("#39FF14");
    expect(NEXUS_COLORS.black).toBe("#1A1A1A");
  });

  it("집약 객체로도 동일 토큰 접근", () => {
    expect(nexusTheme.colors.indigo).toBe("#2E2A5E");
    expect(nexusTheme.colors.green).toBe("#39FF14");
  });

  it("샤프 코너 — borderRadius는 0 (라운드 금지)", () => {
    expect(BORDER_RADIUS).toBe(0);
    expect(nexusTheme.borderRadius).toBe(0);
  });

  it("배경은 다크 정본 — 라이트(#fcf9f8) 누수 없음", () => {
    expect(NEXUS_COLORS.surface.toLowerCase()).not.toBe("#fcf9f8");
    expect(NEXUS_COLORS.surface).toBe("#131313");
  });

  it("폰트 스택 — Space Mono/Inter/JetBrains Mono", () => {
    expect(nexusTheme.fonts.headline).toContain("Space Mono");
    expect(nexusTheme.fonts.body).toContain("Inter");
    expect(nexusTheme.fonts.terminal).toContain("JetBrains Mono");
  });
});

describe("nexusTheme — 하드엣지 베벨 헬퍼", () => {
  it("pixelPanel: 위/왼쪽 라이트·아래/오른쪽 다크·라운드 0", () => {
    const s = pixelPanel();
    expect(s.borderTopColor).toBe(NEXUS_COLORS.bevelLight);
    expect(s.borderBottomColor).toBe(NEXUS_COLORS.bevelDark);
    expect(s.borderRadius).toBe(0);
  });

  it("terminalWindow: 검정 바탕 + 그린 텍스트", () => {
    const s = terminalWindow();
    expect(s.backgroundColor).toBe("#1A1A1A");
    expect(s.color).toBe("#39FF14");
  });
});
