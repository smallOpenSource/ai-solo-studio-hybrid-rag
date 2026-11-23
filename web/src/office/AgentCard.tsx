/**
 * AgentCard — JRPG 초상 프레임 에이전트 정보 카드 (US-1c · "Bit-Driven Logic").
 *
 * pixelPanel 래퍼·gold 코너 액센트·역할 초상(<img> + onError 폴백 체인)·이름·Class·
 * Morale/Load 바. ★P4 초상 경로=nexusPortraitPath(role, emotion·thinking→focused alias·US-2c
 * 96장). 색/폰트/베벨은 nexusTheme 토큰 전용. 텍스트뷰 getRolePortraitPath(/assets/portraits/)와
 * 분리한다(그 경로는 불변 규약).
 *
 * 정본: work/stitch/pixel_office/dashboard_nexus_os.html(Selected Agent Info 패널).
 */

import type { CSSProperties, SyntheticEvent } from "react";
import {
  NEXUS_COLORS,
  NEXUS_FONTS,
  NEXUS_SPACING,
  BORDER_RADIUS,
  pixelPanel,
} from "./nexusTheme";
import { nexusPortraitPath, PORTRAIT_FALLBACK } from "../config/emotionImages";
import type { AgentActivityStatus } from "./deriveAgentActivity";

// ── Props ─────────────────────────────────────────────────────────────────────

export interface AgentCardProps {
  /** 역할 식별자(초상 경로·pickRole 결과 등). */
  role: string;
  /** 표시 이름. */
  name: string;
  /** 직군 라벨(Class 표시). */
  cls?: string;
  /** 감정(초상 표정 — 미지정 시 neutral). */
  emotion?: string;
  /** 사기(0~100 — 바 width %). ★활동 바인딩 시 전역 세션 성공률(sessionScore). */
  morale?: number;
  /** 부하(0~100 — 바 width %). ★활동 바인딩 시 이진(working=100/idle=0). */
  load?: number;
  /** 활동 상태 배지(미지정 시 배지 숨김). */
  status?: AgentActivityStatus;
  /** 진행 중 노드 수("작업 N건" 라벨 — 미지정 시 숨김·실 카운트는 load 이진과 분리 표기). */
  activeCount?: number;
}

/** 활동 상태 → 한국어 배지 텍스트·색(idle/working/done/failed). */
const STATUS_BADGE: Record<AgentActivityStatus, { label: string; color: string }> = {
  idle: { label: "대기", color: NEXUS_COLORS.muted },
  working: { label: "작업중", color: NEXUS_COLORS.green },
  done: { label: "완료", color: NEXUS_COLORS.gold },
  failed: { label: "실패", color: NEXUS_COLORS.onSurfaceVariant },
};

// ── 초상 폴백 체인 (ChatPanel 관용구 일관) ──────────────────────────────────────
//
// nexus {role}_{emotion} → nexus {role}_neutral → PORTRAIT_FALLBACK → onerror=null(루프 차단).

/**
 * ★P4 표시 레이어 alias: thinking → focused(6번째 표정 art)로 치환한다.
 * classifyEmotion 재분류가 아니라 카드/다이얼로그 표시 단계의 매핑이다(분류 결과 불변).
 */
function aliasCardEmotion(emotion: string): string {
  return emotion === "thinking" ? "focused" : emotion;
}

/** 단계적 초상 폴백 onError 핸들러 생성(nexus 경로). */
function makePortraitFallback(role: string) {
  const neutralPath = nexusPortraitPath(role, "neutral");
  return (e: SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (!img.src.includes(neutralPath) && !img.src.includes(PORTRAIT_FALLBACK)) {
      img.src = neutralPath; // 1차: role_emotion 실패 → role_neutral.
    } else if (img.src.includes(neutralPath)) {
      img.src = PORTRAIT_FALLBACK; // 2차: role_neutral 실패 → 최종 폴백.
    } else {
      img.onerror = null; // 3차: 최종 폴백도 실패 → 루프 차단(src 고정).
    }
  };
}

// ── AgentCard ────────────────────────────────────────────────────────────────────

/**
 * 에이전트 정보 카드(JRPG 초상 프레임). morale/load 미지정 시 0으로 본다.
 */
export function AgentCard({
  role,
  name,
  cls,
  emotion,
  morale = 0,
  load = 0,
  status,
  activeCount,
}: AgentCardProps) {
  const portraitSrc = nexusPortraitPath(role, aliasCardEmotion(emotion ?? "neutral"));
  const badge = status ? STATUS_BADGE[status] : null;
  return (
    <div data-testid="agent-card" style={cardStyle}>
      {/* 좌: 초상 프레임(gold 코너 액센트). */}
      <div style={portraitColStyle}>
        <span style={{ ...cornerBase, ...cornerTopLeft }} />
        <span style={{ ...cornerBase, ...cornerTopRight }} />
        <span style={{ ...cornerBase, ...cornerBottomLeft }} />
        <span style={{ ...cornerBase, ...cornerBottomRight }} />
        <div style={portraitFrameStyle}>
          <img
            src={portraitSrc}
            alt={`${name} 초상`}
            data-testid="agent-portrait"
            onError={makePortraitFallback(role)}
            style={portraitImgStyle}
          />
        </div>
      </div>

      {/* 우: 이름(+상태 배지)·Class·작업 N건·Morale/Load 바. */}
      <div style={infoColStyle}>
        <div style={nameRowStyle}>
          <span style={nameStyle}>{name}</span>
          {badge && (
            <span data-testid="agent-status" style={{ ...statusBadgeStyle, color: badge.color }}>
              {badge.label}
            </span>
          )}
        </div>
        <div style={classStyle}>Class: {cls ?? "—"}</div>
        {activeCount !== undefined && (
          <div data-testid="agent-active-count" style={activeCountStyle}>
            작업 {activeCount}건
          </div>
        )}
        <div style={statsStyle}>
          <StatBar label="Morale" value={morale} color={NEXUS_COLORS.green} />
          <StatBar label="Load" value={load} color={NEXUS_COLORS.gold} />
        </div>
      </div>
    </div>
  );
}

// ── 스탯 바 1행 ──────────────────────────────────────────────────────────────────

/** 라벨 + value/100 + 채움 바(width %). */
function StatBar({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div style={statRowStyle}>
      <div style={statLabelRowStyle}>
        <span>{label}</span>
        <span>{value}/100</span>
      </div>
      <div style={statTrackStyle}>
        <div
          data-testid="agent-stat-fill"
          style={{ height: "100%", backgroundColor: color, width: `${value}%` }}
        />
      </div>
    </div>
  );
}

// ── 스타일(인라인·nexusTheme 토큰 전용) ─────────────────────────────────────────

const cardStyle: CSSProperties = {
  ...pixelPanel(),
  display: "flex",
  flexDirection: "row",
  minHeight: 0,
};

const portraitColStyle: CSSProperties = {
  position: "relative",
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  width: "33%",
  padding: NEXUS_SPACING.unit * 2,
  borderRightStyle: "solid",
  borderRightWidth: 2,
  borderRightColor: NEXUS_COLORS.gold,
  backgroundColor: NEXUS_COLORS.surfaceLow,
};

// gold 코너 액센트(JRPG 프레임).
const cornerBase: CSSProperties = {
  position: "absolute",
  width: 8,
  height: 8,
  borderStyle: "solid",
  borderColor: NEXUS_COLORS.gold,
  borderWidth: 0,
};

const cornerTopLeft: CSSProperties = {
  top: 4,
  left: 4,
  borderTopWidth: 2,
  borderLeftWidth: 2,
};
const cornerTopRight: CSSProperties = {
  top: 4,
  right: 4,
  borderTopWidth: 2,
  borderRightWidth: 2,
};
const cornerBottomLeft: CSSProperties = {
  bottom: 4,
  left: 4,
  borderBottomWidth: 2,
  borderLeftWidth: 2,
};
const cornerBottomRight: CSSProperties = {
  bottom: 4,
  right: 4,
  borderBottomWidth: 2,
  borderRightWidth: 2,
};

const portraitFrameStyle: CSSProperties = {
  width: 64,
  height: 64,
  backgroundColor: NEXUS_COLORS.black,
  borderStyle: "solid",
  borderWidth: 2,
  borderColor: NEXUS_COLORS.gold,
  borderRadius: BORDER_RADIUS,
  overflow: "hidden",
};

const portraitImgStyle: CSSProperties = {
  width: "100%",
  height: "100%",
  objectFit: "cover",
  imageRendering: "pixelated",
};

const infoColStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  width: "67%",
  padding: NEXUS_SPACING.gutter - 4,
};

const nameRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: NEXUS_SPACING.unit,
  marginBottom: 4,
};

const nameStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.headline,
  fontWeight: 700,
  fontSize: 14,
  color: NEXUS_COLORS.onSurface,
};

// 상태 배지: 작은 터미널 라벨(색은 status별로 인라인 덮어쓰기).
const statusBadgeStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 9,
  fontWeight: 700,
  padding: "1px 6px",
  borderStyle: "solid",
  borderWidth: 1,
  borderColor: NEXUS_COLORS.outlineVariant,
  borderRadius: BORDER_RADIUS,
  whiteSpace: "nowrap",
};

// "작업 N건" 라벨(실 카운트 — load 이진 바와 분리 표기).
const activeCountStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 10,
  color: NEXUS_COLORS.onSurfaceVariant,
  marginBottom: 6,
};

const classStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 11,
  color: NEXUS_COLORS.onSurfaceVariant,
  marginBottom: 8,
};

const statsStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: NEXUS_SPACING.unit,
  marginTop: "auto",
};

const statRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const statLabelRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 10,
  fontWeight: 700,
  color: NEXUS_COLORS.onSurface,
};

const statTrackStyle: CSSProperties = {
  width: "100%",
  height: 6,
  backgroundColor: NEXUS_COLORS.outlineVariant,
  borderRadius: BORDER_RADIUS,
  overflow: "hidden",
};

export default AgentCard;
