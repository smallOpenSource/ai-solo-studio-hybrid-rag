/**
 * NexusShell — Nexus OS 대시보드 셸 섀시 (US-1b · "Bit-Driven Logic").
 *
 * 순수 프레젠테이션 컴포넌트. 상단 헤더(로고 + HUD)·좌 사이드바(내비/CTA)·중앙 main
 * (children)·하단 systemLog 슬롯의 4영역 셸을 그린다. 색/폰트/베벨은 nexusTheme
 * 토큰에서만 가져온다(하드코딩 금지). 샤프 0px 라운드 금지(BORDER_RADIUS).
 *
 * 레이아웃 계약(절대):
 *   루트=flex column·height:100% / main=flex:1·minHeight:0(자식 Phaser 100% 체인 보존)
 *   / 중앙 children 슬롯도 flex:1·minHeight:0.
 *
 * 정본: work/stitch/pixel_office/dashboard_nexus_os.html (Tailwind→인라인 번역, 다크).
 */

import { useState, type CSSProperties, type ReactNode } from "react";
import {
  NEXUS_COLORS,
  NEXUS_FONTS,
  NEXUS_SPACING,
  BORDER_RADIUS,
  pixelButton,
} from "./nexusTheme";

// ── 내비 항목(정본 5개) ─────────────────────────────────────────────────────────

/** 사이드바 내비 라벨(정본 순서·sidebarActive와 매칭). */
const NAV_ITEMS = [
  "Dashboard",
  "Office",
  "Agents",
  "Knowledge",
  "Settings",
] as const;

/**
 * 접힘(collapsed) 상태에서 라벨 대신 표시할 약자(아이콘 폰트 없이 도트 무드 한 글자).
 * 펼침 시엔 전체 라벨을 쓰고, 접으면 이 약자만 표시한다(여전히 클릭 가능).
 */
const NAV_ABBR: Record<(typeof NAV_ITEMS)[number], string> = {
  Dashboard: "D",
  Office: "O",
  Agents: "A",
  Knowledge: "K",
  Settings: "S",
};

// ── Props ─────────────────────────────────────────────────────────────────────

export interface NexusShellProps {
  /** 중앙 main 영역 콘텐츠(예: 오피스 맵). */
  children: ReactNode;
  /** 하단 시스템 로그 슬롯(SystemLogPanel 등). */
  systemLog: ReactNode;
  /**
   * 현재 활성 내비 라벨(일치하면 gold 베벨 강조).
   * ★셸은 이 라벨로 "강조"만 한다 — 섹션 콘텐츠 조립은 App이 children으로 담당(순수 유지·
   *   activeSection prop을 받지 않는다).
   */
  sidebarActive?: string;
  /** 내비 클릭 콜백(라벨 전달 — App이 activeSection 전환). */
  onNavSelect?: (label: string) => void;
  /** DEPLOY AGENT 클릭 콜백(App이 워크플로 시작). */
  onDeploy?: () => void;
  /** Log Out 클릭 콜백(App이 confirm+리셋). */
  onLogout?: () => void;
  /**
   * DEPLOY 상태 피드백 (US-G3-3).
   * null=대기, "starting"=진행중, "error"=실패.
   */
  deployStatus?: "starting" | "error" | "success" | null;
  /** DEPLOY 실패 메시지(deployStatus="error"일 때만 의미 있음). */
  deployError?: string | null;
}

// ── NexusShell ──────────────────────────────────────────────────────────────────

/**
 * 대시보드 셸 섀시. children=중앙 main·systemLog=하단 슬롯.
 */
export function NexusShell({
  children,
  systemLog,
  sidebarActive,
  onNavSelect,
  onDeploy,
  onLogout,
  deployStatus,
  deployError,
}: NexusShellProps) {
  // ★C1: 사이드바 접힘 상태(내부 useState — App은 모름). 접으면 폭 축소·라벨 숨김(약자만).
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div style={rootStyle}>
      <NexusHeader />
      <div style={bodyRowStyle}>
        <NexusSidebar
          active={sidebarActive}
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((c) => !c)}
          onNavSelect={onNavSelect}
          onDeploy={onDeploy}
          onLogout={onLogout}
          deployStatus={deployStatus}
          deployError={deployError}
        />
        <div style={mainColumnStyle}>
          {/* main 영역: flex:1·minHeight:0 — 자식 Phaser 100% 높이 체인 보존(절대 누락 금지). */}
          <main style={mainStyle}>
            <div style={childrenSlotStyle}>{children}</div>
          </main>
          {/* 하단 시스템 로그 슬롯. */}
          <div data-testid="nexus-systemlog" style={systemLogSlotStyle}>
            {systemLog}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── 헤더(로고 + HUD) ────────────────────────────────────────────────────────────

/**
 * 상단 헤더: 로고(gold·headline) + 제품명 라벨(green·terminal).
 * ★D1: 가짜 텔레메트리(SYS: OK / GPU: 42°C / UP: 99.9%)는 제거했다 — 실측이 없는
 *   하드코딩 값이므로 정직하게 빼고 제품명만 둔다(연결상태 등 실값은 App 인디케이터가 표시).
 */
function NexusHeader() {
  return (
    <header data-testid="nexus-header" style={headerStyle}>
      <span style={logoStyle}>NEXUS OS v1.0</span>
      <span style={hudItemStyle}>Pixel Agent Office</span>
    </header>
  );
}

// ── 사이드바(내비 + CTA) ────────────────────────────────────────────────────────

/**
 * 좌 사이드바: 접펼 토글 + 계정 헤더 + 내비 5개 + DEPLOY AGENT + Log Out.
 * ★C1: collapsed면 폭 축소(56px)·라벨 숨김(약자/아이콘만)·여전히 클릭 가능.
 * ★D1: 계정 헤더의 가짜 값(Admin Console / Nexus-Root-01)은 제품/회사 의미 값으로 정직화.
 */
function NexusSidebar({
  active,
  collapsed,
  onToggleCollapse,
  onNavSelect,
  onDeploy,
  onLogout,
  deployStatus,
  deployError,
}: {
  active?: string;
  collapsed: boolean;
  onToggleCollapse: () => void;
  onNavSelect?: (label: string) => void;
  onDeploy?: () => void;
  onLogout?: () => void;
  deployStatus?: "starting" | "error" | "success" | null;
  deployError?: string | null;
}) {
  return (
    <nav
      data-testid="nexus-sidebar"
      data-collapsed={collapsed}
      style={collapsed ? sidebarCollapsedStyle : sidebarStyle}
    >
      {/* 접펼 토글(아이콘) — 접힘이면 ▶(펼치기)·펼침이면 ◀(접기). */}
      <button
        type="button"
        data-testid="sidebar-toggle"
        onClick={onToggleCollapse}
        title={collapsed ? "사이드바 펼치기" : "사이드바 접기"}
        aria-label={collapsed ? "사이드바 펼치기" : "사이드바 접기"}
        style={collapseToggleStyle}
      >
        {collapsed ? "▶" : "◀"}
      </button>

      {/* 계정/제품 헤더(정직화 — 펼침에서만 부제 표시). */}
      <div style={sidebarAccountStyle}>
        <div style={sidebarAccountTitleStyle}>{collapsed ? "PAO" : "Pixel Agent Office"}</div>
        {!collapsed && <div style={sidebarAccountSubStyle}>one_man_corp</div>}
      </div>

      {/* 내비 목록(클릭 → onNavSelect(label)). 접힘이면 약자만 표시. */}
      <div style={sidebarNavListStyle}>
        {NAV_ITEMS.map((label) => {
          const isActive = label === active;
          return (
            <div
              key={label}
              role="button"
              tabIndex={0}
              data-active={isActive}
              onClick={() => onNavSelect?.(label)}
              title={collapsed ? label : undefined}
              style={isActive ? navItemActiveStyle : navItemStyle}
            >
              {collapsed ? NAV_ABBR[label] : label}
            </div>
          );
        })}
      </div>

      {/* 하단 액션. 접힘이면 짧은 라벨/약자. */}
      <div style={sidebarFooterStyle}>
        <button
          type="button"
          data-testid="deploy-btn"
          style={{
            ...deployBtnStyle,
            opacity: deployStatus === "starting" ? 0.6 : 1,
            cursor: deployStatus === "starting" ? "not-allowed" : "pointer",
          }}
          onClick={() => onDeploy?.()}
          disabled={deployStatus === "starting"}
          title={collapsed ? "DEPLOY AGENT" : undefined}
        >
          {collapsed
            ? deployStatus === "starting" ? "⏳" : "▶▶"
            : deployStatus === "starting" ? "배포 중…" : "DEPLOY AGENT"}
        </button>
        {/* ★US-G3-3: DEPLOY 피드백 — starting 토스트·error 메시지. 접힘에선 숨김. */}
        {!collapsed && deployStatus === "starting" && (
          <div data-testid="deploy-feedback" style={deployFeedbackStyle}>
            에이전트를 시작하는 중입니다…
          </div>
        )}
        {!collapsed && deployStatus === "success" && (
          <div data-testid="deploy-feedback" style={deployFeedbackStyle}>
            워크플로를 시작했습니다 ✓
          </div>
        )}
        {!collapsed && deployStatus === "error" && deployError && (
          <div data-testid="deploy-error" style={deployErrorStyle}>
            오류: {deployError}
          </div>
        )}
        <div
          role="button"
          tabIndex={0}
          style={logoutStyle}
          onClick={() => onLogout?.()}
          title={collapsed ? "Log Out" : undefined}
        >
          {collapsed ? "⎋" : "Log Out"}
        </div>
      </div>
    </nav>
  );
}

// ── 스타일(인라인·nexusTheme 토큰 전용) ─────────────────────────────────────────

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  // ★flex:1 + minWidth:0 — flex 래퍼(display:flex row) 안에서 폭을 가득 채운다.
  //   없으면 셸이 콘텐츠 폭으로 줄어 Agents 그리드가 1열로 붕괴(맵 섹션은 캔버스
  //   고유폭에 의존해 우연히 넓었던 것 — 섹션 무관하게 폭을 고정한다).
  flex: 1,
  minWidth: 0,
  width: "100%",
  backgroundColor: NEXUS_COLORS.surface,
  color: NEXUS_COLORS.onSurface,
  fontFamily: NEXUS_FONTS.body,
};

const bodyRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  flex: 1,
  minHeight: 0,
};

const mainColumnStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flex: 1,
  minHeight: 0,
  padding: NEXUS_SPACING.gutter,
  gap: NEXUS_SPACING.gutter,
};

// main: 자식 Phaser 100% 체인 보존(flex:1·minHeight:0 — 절대 누락 금지).
const mainStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flex: 1,
  minHeight: 0,
};

// 중앙 children 슬롯도 flex:1·minHeight:0 전달.
const childrenSlotStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flex: 1,
  minHeight: 0,
};

const systemLogSlotStyle: CSSProperties = {
  flexShrink: 0,
};

// 헤더: indigo 배경·샤프 코너.
const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  flexShrink: 0,
  height: 56,
  padding: `0 ${NEXUS_SPACING.margin}px`,
  backgroundColor: NEXUS_COLORS.indigo,
  borderBottomStyle: "solid",
  borderBottomWidth: 2,
  borderBottomColor: NEXUS_COLORS.outlineVariant,
  borderRadius: BORDER_RADIUS,
};

const logoStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.headline,
  fontWeight: 700,
  fontSize: 22,
  letterSpacing: "-0.5px",
  color: NEXUS_COLORS.gold,
};

const hudItemStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 13,
  color: NEXUS_COLORS.green,
};

// 사이드바: indigo 톤 섀시·폭 ~220px.
const sidebarStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  width: 220,
  flexShrink: 0,
  backgroundColor: NEXUS_COLORS.surfaceLow,
  borderRightStyle: "solid",
  borderRightWidth: 2,
  borderRightColor: NEXUS_COLORS.outlineVariant,
};

// 접힘 사이드바: 폭 축소(56px)·라벨 숨김(약자만). 나머지 톤/베벨은 펼침과 동일.
const sidebarCollapsedStyle: CSSProperties = {
  ...sidebarStyle,
  width: 56,
};

// 접펼 토글 버튼(사이드바 최상단·작은 아이콘 버튼).
const collapseToggleStyle: CSSProperties = {
  ...pixelButton(),
  alignSelf: "flex-end",
  margin: NEXUS_SPACING.unit,
  padding: "2px 8px",
  fontSize: 12,
  fontWeight: 700,
  color: NEXUS_COLORS.gold,
  cursor: "pointer",
};

const sidebarAccountStyle: CSSProperties = {
  padding: NEXUS_SPACING.margin,
  borderBottomStyle: "solid",
  borderBottomWidth: 1,
  borderBottomColor: NEXUS_COLORS.outlineVariant,
};

const sidebarAccountTitleStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.headline,
  fontWeight: 700,
  fontSize: 16,
  color: NEXUS_COLORS.gold,
};

const sidebarAccountSubStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 12,
  color: NEXUS_COLORS.muted,
  marginTop: 4,
};

const sidebarNavListStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: NEXUS_SPACING.unit * 2,
  flex: 1,
  minHeight: 0,
  overflowY: "auto",
  padding: `${NEXUS_SPACING.gutter}px ${NEXUS_SPACING.unit * 2}px`,
};

// ★border 색은 longhand(4면)로만 지정한다 — 활성 스타일이 면별 베벨색을 덮어쓰므로,
//   shorthand borderColor와 섞으면 토글 리렌더 시 React가 경고한다(shorthand/longhand 혼용).
const navItemStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 13,
  fontWeight: 700,
  color: NEXUS_COLORS.onSurfaceVariant,
  padding: "8px 10px",
  borderStyle: "solid",
  borderWidth: 2,
  borderTopColor: "transparent",
  borderLeftColor: "transparent",
  borderRightColor: "transparent",
  borderBottomColor: "transparent",
  borderRadius: BORDER_RADIUS,
  cursor: "pointer",
};

// 활성: gold 베벨 강조(면별 색 덮어쓰기 — navItemStyle도 면별 longhand라 혼용 0).
const navItemActiveStyle: CSSProperties = {
  ...navItemStyle,
  color: NEXUS_COLORS.gold,
  backgroundColor: NEXUS_COLORS.surfaceHigh,
  borderTopColor: NEXUS_COLORS.gold,
  borderLeftColor: NEXUS_COLORS.gold,
  borderRightColor: NEXUS_COLORS.bevelDark,
  borderBottomColor: NEXUS_COLORS.bevelDark,
};

const sidebarFooterStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: NEXUS_SPACING.gutter,
  marginTop: "auto",
  padding: NEXUS_SPACING.margin,
  borderTopStyle: "solid",
  borderTopWidth: 1,
  borderTopColor: NEXUS_COLORS.outlineVariant,
};

const deployBtnStyle: CSSProperties = {
  ...pixelButton(),
  width: "100%",
  padding: "8px 0",
  fontSize: 13,
  fontWeight: 700,
  color: NEXUS_COLORS.gold,
  textAlign: "center",
};

const logoutStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 13,
  fontWeight: 700,
  color: NEXUS_COLORS.onSurfaceVariant,
  cursor: "pointer",
};

// ★US-G3-3: DEPLOY 피드백 스타일
const deployFeedbackStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 11,
  color: NEXUS_COLORS.green,
};

const deployErrorStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 11,
  color: NEXUS_COLORS.error,
  wordBreak: "break-all",
};

export default NexusShell;
