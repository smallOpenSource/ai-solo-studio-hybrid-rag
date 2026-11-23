/**
 * roleCharacter — 8역할 직원 로스터 정의 + node_id→역할 결정론 매핑 (qwen 단일 스타일 오피스).
 *
 * ★모델 전환: 기존 LPC 노드별 아바타(avatar.id별 색 조합 캐릭터)를 폐기하고,
 * 8역할 직원을 고정 좌표에 항상 표시하는 ambient 로스터로 바꾼다. 워크플로의
 * SceneState.avatars는 pickRole로 역할에 매핑되어 해당 로스터 멤버에 상태 오버레이
 * (statusRing 색 + bubble)만 켠다. 캐릭터·책상·좌표 자체는 워크플로와 무관(정적).
 *
 * ★결정론 경계: 이 모듈은 순수 TypeScript다(pixi/React/canvas 무의존). 같은 avatar.id는
 * 항상 같은 Role로 매핑된다(해시 기반·안정). deriveScene/sceneTypes는 0수정 —
 * 매핑은 전적으로 렌더 레이어(이 모듈+applyScene)의 책임이다.
 */

// ── 역할 로스터 ────────────────────────────────────────────────────────────────

/**
 * 8역할 직원 — office_mock.html에서 검증된 단일 픽셀 스타일 캐릭터 세트.
 * 배열 순서는 의미 없음(좌표는 ROLE_LAYOUT, 매핑은 pickRole). 길이 8 고정(테스트 계약).
 */
export const ROLES = [
  "dev",
  "ceo",
  "designer",
  "planner",
  "marketer",
  "accountant",
  "secretary",
  "strategist",
] as const;

/** 한 직원의 역할 식별자. */
export type Role = (typeof ROLES)[number];

/** 역할별 표시 이름(이름표 — office_mock.html NAME 맵과 동일). */
export const ROLE_NAMES: Record<Role, string> = {
  ceo: "CEO",
  secretary: "비서",
  strategist: "사업전략",
  dev: "개발자",
  designer: "디자이너",
  planner: "기획자",
  marketer: "마케터",
  accountant: "회계사",
};

// ── 고정 좌표(office_mock.html 배치 — ★오케스트레이터 라이브 미세조정 지점) ─────
//
// 상단존: ceo·strategist·secretary(리더십), 하단존: dev·designer·planner·marketer·
// accountant(워커). 각 (x,y)는 캐릭터 발밑 기준 "중심 X·상단 Y"다(applyScene가
// 캐릭터/책상을 이 점 기준으로 중앙정렬). 스테이지 720x560 기준 픽셀(office_mock의
// 980x640 배치를 비례 축소·정돈). 캐릭터는 ~66x120이라 x는 중심, y는 머리 상단.

/** 역할 고정 좌표(중심 X·상단 Y·픽셀). ★시각 미세조정은 이 상수만 수정. */
export const ROLE_LAYOUT: Record<Role, { x: number; y: number }> = {
  // 상단 리더십 존(3).
  ceo: { x: 110, y: 60 },
  strategist: { x: 360, y: 52 },
  secretary: { x: 610, y: 60 },
  // 하단 워커 존(5).
  dev: { x: 70, y: 330 },
  designer: { x: 210, y: 380 },
  planner: { x: 360, y: 330 },
  marketer: { x: 510, y: 380 },
  accountant: { x: 650, y: 330 },
} as const;

// ── node_id → 역할 결정론 매핑 ────────────────────────────────────────────────

/**
 * 알려진 시드 node_id를 역할에 고정 매핑한다(가독성 — crm_notify 워크플로).
 * 미등록 node_id는 pickRole의 해시 폴백으로 결정론 배정된다.
 */
const SEED_ROLE_MAP: Record<string, Role> = {
  n1_crm_get: "secretary",
  n2_order_get: "accountant",
  n2_5_summary: "planner",
  n3_email_send: "marketer",
};

/**
 * 문자열을 32비트 부호 없는 정수로 해시한다(djb2 변형·결정론).
 * 같은 입력 → 같은 값. 미등록 node_id의 역할 인덱스 산출용.
 */
function hashString(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i += 1) {
    h = ((h << 5) + h + s.charCodeAt(i)) >>> 0; // h*33 + c, unsigned.
  }
  return h >>> 0;
}

/**
 * avatar.id("worker-{nodeId}" 또는 "worker-0")를 8역할 중 하나로 결정론 매핑한다.
 *
 * 같은 avatar.id → 항상 같은 Role(직원 정체성 안정). 시드 node_id는 고정 맵으로,
 * 그 외는 nodeId 해시 % 8로 배정한다. 대기 직원 "worker-0"은 ceo로 고정(대표가 대기).
 *
 * @param avatarId deriveScene이 낸 아바타 id(예 "worker-n1_crm_get", "worker-0").
 */
export function pickRole(avatarId: string): Role {
  // "worker-" 접두 제거 → 순수 nodeId. 접두가 없으면 입력 전체를 nodeId로 본다(방어).
  const nodeId = avatarId.startsWith("worker-")
    ? avatarId.slice("worker-".length)
    : avatarId;

  // 대기 직원(worker-0)은 대표(ceo)로 고정.
  if (nodeId === "0") {
    return "ceo";
  }
  // 알려진 시드 node_id는 고정 매핑(가독성).
  const seeded = SEED_ROLE_MAP[nodeId];
  if (seeded) {
    return seeded;
  }
  // 미등록 node_id는 해시 % 8로 결정론 배정.
  return ROLES[hashString(nodeId) % ROLES.length];
}
