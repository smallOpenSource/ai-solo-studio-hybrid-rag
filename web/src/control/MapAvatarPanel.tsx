/**
 * MapAvatarPanel — §9.4 맵 + 직원 아바타 (P6c·패널 1).
 *
 * 이 패널은 WorkAdventure(WA) 맵 영역을 차지하는 **셸/placeholder** 이며, 그 위에 §9.4 Agent
 * Status Overlay(도크 상태판)를 **합성하는 지점**이다.
 *
 * ★WA-위-합성 렌더(라이브 WA 셸 위 아바타 + 도크 오버레이)는:
 *   "code 100% / runtime pending Owner: 맵URL(START_ROOM_URL) + 공개 도메인 + TLS"
 *   (SR/renewal/AGPL_COMPLIANCE.md 결정③·결정④ / ralplan-p6-interface.md §7 — Owner 하드요건).
 *   여기서는 WA 합성 지점을 **인터페이스(iframe src / postMessage 채널)로만** 노출하고,
 *   라이브 WA URL 이 없으면 placeholder 를 렌더한다(BLOCKED ≠ pass·green-fake 금지).
 *
 * ★AGPL: WA 브릭 비침투 — iframe/스크립팅 API(postMessage)로만 연동(브릭 소스 수정 0·결정①).
 */

import { type ReactNode } from "react";

export interface MapAvatarPanelProps {
  /**
   * 라이브 WA 맵 URL(START_ROOM_URL·Owner 제공). 미설정(undefined)이면 placeholder 셸을
   * 렌더한다(WA-위-합성 렌더 = pending-Owner). 설정 시 iframe 으로 WA 셸을 임베드한다.
   */
  waMapUrl?: string;
  /**
   * 맵 위에 합성할 오버레이(도크 상태판 등). MapAvatarPanel 은 이 children 을 WA 영역 우측
   * 도크 위치에 절대배치한다(아바타-추종 아님·결정②). WA 셸 유무와 무관하게 오버레이는 렌더.
   */
  children?: ReactNode;
}

/**
 * 맵 + 아바타 패널. waMapUrl 이 있으면 WA 셸을 iframe 으로 임베드, 없으면 placeholder.
 * 어느 경우든 children(도크 오버레이)을 우측에 합성한다.
 */
export function MapAvatarPanel({ waMapUrl, children }: MapAvatarPanelProps) {
  const live = typeof waMapUrl === "string" && waMapUrl.length > 0;
  return (
    <div
      className="control-map-panel"
      data-testid="map-avatar-panel"
      data-wa-live={live ? "1" : "0"}
      aria-label="Map + Avatars"
      style={{
        position: "relative",
        background: "#020617",
        borderRadius: 8,
        overflow: "hidden",
        minHeight: 320,
        minWidth: 360,
        flex: 1,
      }}
    >
      {live ? (
        // 라이브 WA 셸 — 비수정 prebuilt WA 를 iframe 으로 임베드(스크립팅 API/postMessage 로
        // orch RO API 소비·브릭 소스 수정 0·AGPL §13 미트리거).
        <iframe
          className="control-wa-frame"
          data-testid="wa-frame"
          title="WorkAdventure"
          src={waMapUrl}
          style={{ width: "100%", height: "100%", border: 0, position: "absolute", inset: 0 }}
        />
      ) : (
        // placeholder 셸 — 라이브 WA-위-합성 렌더는 pending-Owner(맵URL/도메인/TLS).
        <div
          className="control-map-placeholder"
          data-testid="map-placeholder"
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            color: "#475569",
            fontSize: 13,
            textAlign: "center",
            padding: 16,
            fontFamily: "ui-sans-serif, system-ui, sans-serif",
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 6 }}>WorkAdventure 맵 (셸)</div>
          <div style={{ maxWidth: 280, opacity: 0.8 }}>
            WA-위-합성 렌더는 pending Owner: 맵 URL(START_ROOM_URL) · 공개 도메인 · TLS
          </div>
        </div>
      )}
      {/* 도크 오버레이 합성 지점 — WA 영역 우측 상단에 절대배치(아바타-추종 아님·결정②). */}
      {children ? (
        <div
          className="control-dock-overlay"
          data-testid="dock-overlay-slot"
          style={{ position: "absolute", top: 8, right: 8, zIndex: 10 }}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
