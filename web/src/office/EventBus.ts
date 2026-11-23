/**
 * EventBus — Phaser↔React 디커플 통신 싱글톤 (office-v2 US-0d).
 *
 * Phaser 공식 template-react-ts 패턴: 단일 `Phaser.Events.EventEmitter`를
 * 모듈 스코프에 두어 React(셸)와 Phaser(캔버스)가 서로를 직접 참조하지 않고
 * 이벤트로만 통신한다.
 *
 * 사용:
 *   - OfficeScene.create()가 `EventBus.emit('current-scene-ready', this)`로
 *     씬 핸들을 노출 → PhaserGame.tsx가 on으로 받아 부모(ref)에 전달.
 *   - React(WS 수신)가 `EventBus.emit('office-event', ev)`로 OfficeEvent를 흘리면
 *     OfficeScene이 on으로 받아 렌더(말풍선/상태) 갱신(PoC는 최소).
 *
 * ★jsdom은 WebGL 미지원이라 Phaser.Game 생성은 e2e(US-0g)에서 검증한다.
 *   EventBus는 순수 EventEmitter라 단위 테스트 가능(emit/on).
 */

import Phaser from "phaser";

/** 전역 단일 이벤트 버스(Phaser.Events.EventEmitter). */
export const EventBus = new Phaser.Events.EventEmitter();

// ── 프론트 pub/sub 채널 상수 (★백엔드 SSE/WS 이벤트 11종과 무관·React↔Phaser 전용) ──
//
// 채널을 늘리는 것은 백엔드 "events 11종 고정" 규약 위반이 아니다(프론트 디커플 통신).
// 문자열 오타 방지·테스트 단언 용이를 위해 상수로 고정한다.

/** WS OfficeEvent를 씬으로 흘리는 채널(App emit → OfficeScene on·말풍선/활동). */
export const OFFICE_EVENT_CHANNEL = "office-event";
/** OfficeScene.create가 씬 핸들을 노출하는 채널(PhaserGame on). */
export const SCENE_READY_CHANNEL = "current-scene-ready";
/** 맵 스프라이트 클릭 시 선택 역할을 알리는 채널(OfficeScene emit → App/씬 on·US-N4). */
export const AGENT_SELECTED_CHANNEL = "agent-selected";
/** 역할별 활동 상태(byRole)를 씬에 전달하는 채널(App emit → OfficeScene on·US-N8 글로우). */
export const AGENT_ACTIVITY_CHANNEL = "agent-activity";
