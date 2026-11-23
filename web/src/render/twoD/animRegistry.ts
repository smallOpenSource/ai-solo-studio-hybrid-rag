/**
 * animRegistry — 아바타별 LPC 워크프레임 애니메이션 상태를 모듈 스코프에 보관
 * (2D 비주얼 폴리시 — 생동감 §4·Principle 6).
 *
 * ★우리 모델에 맞춘 설계: 아바타는 노드별 책상에 고정되어 책상간 이동이 거의 없다.
 * 그래서 "이동 트윈"이 아니라 status==="active"일 때 LPC walk 프레임을 느리게 순환해
 * "일하는 듯 미묘한 모션"만 준다(idle은 frame0 고정).
 *
 * ★왜 모듈 스코프인가(cameraRegister와 동일 발상): 2D↔텍스트 뷰 토글 시 Application은
 * destroy(true)된다. 애니 상태를 Application/Pixi 객체에 부착하면 토글 때 사라진다.
 * 모듈 변수는 ESM 그래프에 생존하므로 뷰를 껐다 켜도 프레임 위상이 이어진다.
 *
 * 이 모듈은 pixi를 import하지 않는다(순수·결정론·테스트 경량). 프레임 진행 로직만 보유하고,
 * 실제 Sprite texture 교체는 TwoDStage의 ticker 콜백이 updateCharacter로 수행한다.
 */

// ── 애니메이션 상수 (조정 가능) ──────────────────────────────────────────────

/**
 * active 워크 순환 프레임 수(LPC walk 행은 9프레임 0..8). frame이 이 값에 닿으면 0으로 래핑.
 * ★느린 모션: 프레임 전환 간 누적 시간이 FRAME_INTERVAL_MS를 넘어야 1프레임 진행.
 */
export const WALK_FRAME_COUNT = 9;

/** 프레임 1칸 전진에 필요한 누적 ms(≈150ms — "미묘한" 속도). 조정 가능. */
export const FRAME_INTERVAL_MS = 150;

// ── 아바타별 애니 상태 ──────────────────────────────────────────────────────

/** active(워크 순환) 또는 idle(frame0 고정). status에서 산출. */
export type AnimMode = "walk" | "idle";

/**
 * 한 아바타의 애니메이션 상태.
 *   frame : 현재 LPC walk 프레임 열(0..WALK_FRAME_COUNT-1).
 *   timer : 마지막 프레임 전진 이후 누적 ms(FRAME_INTERVAL_MS 넘으면 전진).
 *   mode  : "walk"(active) | "idle"(그 외).
 */
export interface AnimState {
  frame: number;
  timer: number;
  mode: AnimMode;
}

/** avatarId → AnimState. 모듈 스코프 — Application destroy를 초월해 생존. */
const _anims = new Map<string, AnimState>();

/**
 * 아바타의 애니 상태를 가져오거나 처음이면 생성한다(frame0·timer0·idle).
 * @param avatarId 직원 식별자(avatar.id).
 */
export function getAnim(avatarId: string): AnimState {
  let state = _anims.get(avatarId);
  if (!state) {
    state = { frame: 0, timer: 0, mode: "idle" };
    _anims.set(avatarId, state);
  }
  return state;
}

/**
 * 경과 시간(ms)만큼 아바타의 애니 프레임을 진행한다(순수 로직·결정론).
 *
 * - mode==="idle" → frame은 항상 0(정지 포즈), timer 리셋. (멈춤·대기·완료.)
 * - mode==="walk" → timer에 deltaMs 누적, FRAME_INTERVAL_MS를 넘을 때마다 frame을
 *   1칸 전진하고 그만큼 timer를 차감한다. frame은 WALK_FRAME_COUNT로 모듈러 래핑(0→8→0).
 *
 * 큰 deltaMs(탭 전환 후 복귀 등)도 안전하게 처리한다(while로 다중 전진·모듈러로 경계 안전).
 *
 * @param avatarId 직원 식별자.
 * @param mode     이번 프레임의 모드("walk" 또는 "idle").
 * @param deltaMs  지난 틱 이후 경과 ms(>=0 가정·음수는 0으로 클램프).
 * @returns 갱신된 현재 frame(렌더가 updateCharacter에 넘길 값).
 */
export function advanceAnim(avatarId: string, mode: AnimMode, deltaMs: number): number {
  const state = getAnim(avatarId);
  state.mode = mode;

  if (mode === "idle") {
    // 멈춤: frame0 고정·타이머 리셋(다음 walk 진입 시 깨끗하게 시작).
    state.frame = 0;
    state.timer = 0;
    return 0;
  }

  // walk: 경과 누적 → 임계 넘을 때마다 1프레임 전진(다중 전진 안전).
  state.timer += deltaMs > 0 ? deltaMs : 0;
  while (state.timer >= FRAME_INTERVAL_MS) {
    state.timer -= FRAME_INTERVAL_MS;
    state.frame = (state.frame + 1) % WALK_FRAME_COUNT;
  }
  return state.frame;
}

/** 테스트 격리용 — 모든 아바타 애니 상태를 초기화한다. */
export function __resetAnim(): void {
  _anims.clear();
}
