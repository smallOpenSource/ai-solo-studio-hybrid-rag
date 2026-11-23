/**
 * control/ — SR/renewal §9.4 4-패널(P6c·WA-타깃 React/Vite/TS).
 *
 * 4 패널: MapAvatarPanel(맵+아바타·도크 오버레이 합성) · AgentStatusOverlay(8-상태 도크 상태판) ·
 * TaskQueuePanel(작업 큐) · CompanyChat(회사 채팅). 합성 루트 = ControlPanels.
 * 데이터: orch RO API(/agents/status·/tasks) + SSE TEXT_MESSAGE(events.ts 동결·import만).
 */

export * from "./types";
export * from "./api";
export { AgentStatusOverlay } from "./AgentStatusOverlay";
export { TaskQueuePanel } from "./TaskQueuePanel";
export { CompanyChat } from "./CompanyChat";
export { MapAvatarPanel } from "./MapAvatarPanel";
export { ControlPanels, useControlData } from "./ControlPanels";
