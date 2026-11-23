/**
 * sceneLogic — OfficeScene의 캔버스 비의존 순수 로직 (office-v2 US-AF1).
 *
 * ★분리 이유: jsdom은 WebGL 미지원 → Phaser.Game(캔버스) 생성·렌더는 e2e(오케스트레이터)
 *   에서만 검증 가능하다. spawn 파싱·역할 매핑·에셋 키 같은 결정론 로직은 캔버스에
 *   무의존한 순수 함수로 분리해 단위 테스트한다(OfficeScene.ts는 이 함수들을 호출만).
 *
 * 데이터 코어(deriveScene/SceneState/events.ts)는 0수정 — 이 모듈은 렌더 레이어
 * 전용 헬퍼다(표준 Tiled `office.tmj` + 16 LPC 스프라이트 소비).
 */

// ── 16 역할 키 (office.tmj objectgroup 'spawns'의 obj.name과 1:1) ──────────────

/**
 * 배치할 16 역할 키. office.tmj의 spawns 오브젝트 name·캐릭터 PNG 파일명과 동일.
 * 미지 name(이 집합 밖)은 parseSpawns가 무시한다(방어).
 */
export const ROLE_KEYS = [
  "ceo",
  "dev",
  "designer",
  "accountant",
  "marketer",
  "planner",
  "secretary",
  "strategist",
  "sales",
  "cs",
  "cfo",
  "legal",
  "hr",
  "data",
  "pm",
  "devops",
] as const;

/** 16 역할 키의 유니온 타입. */
export type RoleKey = (typeof ROLE_KEYS)[number];

/** 빠른 멤버십 검사용 집합(미지 name 필터). */
const ROLE_SET = new Set<string>(ROLE_KEYS);

/** name이 알려진 16 역할 중 하나인지 (타입 가드). */
export function isRoleKey(name: string): name is RoleKey {
  return ROLE_SET.has(name);
}

/** 16 역할 키의 한국어 표시 이름(채팅 발화자명·이름표). */
export const ROLE_KEY_NAMES: Record<RoleKey, string> = {
  ceo: "CEO",
  dev: "개발자",
  designer: "디자이너",
  accountant: "회계사",
  marketer: "마케터",
  planner: "기획자",
  secretary: "비서",
  strategist: "사업전략",
  sales: "영업",
  cs: "고객지원",
  cfo: "재무책임자",
  legal: "법무",
  hr: "인사",
  data: "데이터분석",
  pm: "PM",
  devops: "데브옵스",
};

// ── 타일맵 메타 (office.tmj에서 확인한 상수) ─────────────────────────────────────

/** office.tmj tilesets[0].name — addTilesetImage 첫 인자로 정확히 일치해야 함. */
export const TILESET_NAME = "office_v2";

/** 깔 타일 레이어 이름(렌더 순서: floor → walls → furniture). */
export const LAYER_NAMES = ["floor", "walls", "furniture"] as const;

/** spawns 오브젝트가 들어있는 objectgroup 이름. */
export const SPAWN_LAYER_NAME = "spawns";

/** 월드 픽셀 크기 — office.tmj(40×26·tile 32 = 1280×832)에서 파생(카메라 bounds 폴백용). */
export const OFFICE_WORLD = { width: 40 * 32, height: 26 * 32 } as const;

// ── ★P3 단일플로어 도트맵 (Stitch floorplan 이미지 레이어 + 4구역 책상 배치) ──────────
//
// 타일맵(office.tmj) 대신 Stitch가 그린 단일 오픈플로어 floorplan을 배경 이미지 레이어로
// 깔고(US-3a), 16 역할 Nexus 스프라이트를 4구역 책상 좌표(ROLE_DESK_COORDS)에 정면 단일
// 프레임으로 앉힌다(US-3b·8방향 없음). deriveScene/sceneTypes/office.tmj는 0수정(비침투).

/** floorplan 네이티브 픽셀(좌표를 찍은 원본 512×286). */
const FLOORPLAN_NATIVE = { width: 512, height: 286 } as const;
/** 네이티브→월드 배율(책상 좌표·월드 크기 환산). floorplan.png는 이미 ×2(1024×572)로 안착. */
export const MAP_SCALE = 2;
/** P3 맵 월드 픽셀(=floorplan.png 실치수·카메라 bounds). */
export const FLOORPLAN_WORLD = {
  width: FLOORPLAN_NATIVE.width * MAP_SCALE,
  height: FLOORPLAN_NATIVE.height * MAP_SCALE,
} as const;

// ── 에셋 키/경로 (preload·create 공유 — 오타 방지) ────────────────────────────

/** OfficeScene이 로드하는 플로어플랜 배경 에셋의 키·경로(단일플로어 P3 렌더 전용). */
export const SCENE_ASSETS = {
  /** ★P3 단일플로어 배경 이미지(Stitch floorplan_nochar ×2 LANCZOS·1024×572·tmj 무관). */
  floorplan: { key: "floorplan", path: "/assets/nexus/floorplan.png" },
} as const;

// ── spawn 파싱 (objectgroup → 결정론 배치 목록) ────────────────────────────────

/** Tiled objectgroup 오브젝트의 최소 형태(파싱에 쓰는 필드만). */
export interface TiledObject {
  name?: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
}

/** 한 역할 스프라이트의 배치 — 텍스처 키와 발밑 중심 픽셀 좌표. */
export interface SpawnPlacement {
  role: RoleKey;
  /** 스프라이트 중심 X(픽셀). */
  x: number;
  /** 스프라이트 발밑 Y(픽셀·bottom). setOrigin(0.5,1) 기준. */
  y: number;
}

/**
 * office.tmj spawns objectgroup 오브젝트를 결정론 배치 목록으로 변환한다(순수).
 *
 * Tiled의 사각형(비-gid) 오브젝트는 (x,y)가 **좌상단** 기준이라, 32×32 셀 중앙·
 * 바닥에 캐릭터 발이 오도록 다음으로 보정한다(setOrigin(0.5,1)와 짝):
 *   - centerX = x + width/2
 *   - footY   = y + height   (셀 하단 = 발밑)
 * name이 16 역할 밖이면 무시한다(방어·미지 spawn 스킵).
 *
 * @param objects getObjectLayer('spawns').objects (또는 동형 배열).
 * @returns 입력 순서를 보존한 SpawnPlacement[](같은 입력 → 같은 출력).
 */
export function parseSpawns(objects: ReadonlyArray<TiledObject>): SpawnPlacement[] {
  const out: SpawnPlacement[] = [];
  for (const o of objects) {
    const name = o.name ?? "";
    if (!isRoleKey(name)) {
      continue; // 미지 역할 — 방어 스킵.
    }
    const x = o.x ?? 0;
    const y = o.y ?? 0;
    const w = o.width ?? 0;
    const h = o.height ?? 0;
    out.push({ role: name, x: x + w / 2, y: y + h });
  }
  return out;
}

// ── ★P3 4구역 책상 배치 (floorplan 단일플로어 — office.tmj 무관·수기 좌표 테이블) ──────

/** 한 역할이 앉는 책상의 floorplan 네이티브 좌표(발밑 중심·origin 0.5,1 기준). */
export interface DeskCoord {
  /** 발밑 중심 X(네이티브 512 기준). */
  x: number;
  /** 발밑 Y(네이티브 286 기준·bottom). */
  y: number;
}

/**
 * 16 역할 → 4구역 책상 좌표(floorplan 네이티브 512×286·발밑 중심). ★수기 좌표 테이블이라
 * office.tmj 무관(비침투). 4구역: 좌상 Executive·우상 Operations·좌하 Tech&Data·우하 Creative&Growth.
 * Record<RoleKey>라 16 역할 누락 시 tsc가 막는다. deskPlacements()가 MAP_SCALE을 곱해
 * 월드 좌표로 환산한다(HITL 튜닝 지점).
 */
export const ROLE_DESK_COORDS: Record<RoleKey, DeskCoord> = {
  // 좌상 Executive Wing(우드·회의테이블).
  ceo: { x: 60, y: 70 },
  strategist: { x: 150, y: 60 },
  cfo: { x: 60, y: 125 },
  legal: { x: 185, y: 125 },
  // 우상 Operations(큐비클 그리드).
  pm: { x: 335, y: 65 },
  planner: { x: 425, y: 65 },
  secretary: { x: 335, y: 125 },
  hr: { x: 445, y: 125 },
  // 좌하 Tech & Data(서버랙·워크스테이션). ★HITL: dev/data 우측 이동(라벨 세로겹침 해소)
  // → 2차 미세조정 dev 130→107(직전 +70의 1/3 좌측)·accountant 175→162(아주 조금 좌측)·방 이탈 0.
  dev: { x: 107, y: 215 },
  devops: { x: 60, y: 262 },
  data: { x: 215, y: 215 },
  accountant: { x: 162, y: 262 },
  // 우하 Creative & Growth(라운지).
  designer: { x: 340, y: 210 },
  marketer: { x: 450, y: 210 },
  sales: { x: 340, y: 262 },
  cs: { x: 455, y: 262 },
};

/** 한 역할 스프라이트의 월드 배치 — 텍스처 키(=역할명)·발밑 중심 월드 픽셀. */
export interface DeskPlacement {
  role: RoleKey;
  /** 월드 X(네이티브×MAP_SCALE). */
  x: number;
  /** 월드 Y(네이티브×MAP_SCALE·발밑). */
  y: number;
}

/**
 * ROLE_DESK_COORDS를 월드 좌표 배치 목록으로 환산한다(순수·결정론).
 *
 * ROLE_KEYS 순서를 보존하며 각 역할 좌표에 MAP_SCALE을 곱한다(발밑 중심 = setOrigin(0.5,1)).
 * @returns 16개 DeskPlacement(같은 입력 → 같은 출력).
 */
export function deskPlacements(): DeskPlacement[] {
  return ROLE_KEYS.map((role) => ({
    role,
    x: ROLE_DESK_COORDS[role].x * MAP_SCALE,
    y: ROLE_DESK_COORDS[role].y * MAP_SCALE,
  }));
}

// ── 라벨 줌 LOD / 역보정 (US-04 — 캔버스 무의존 순수 함수) ────────────────────────
//
// 줌 역보정: 카메라 줌이 바뀌어도 라벨 화면 픽셀 크기를 유지(fit 0.5625에서 12px가
//   6.75px로 작아지는 판독성 문제 해결). label.setScale(labelScale(zoom)) = 1/zoom.
// 줌 LOD: 진입 기본 줌에서 이름+직책이 항상 함께 보이도록 임계를 ZOOM_MIN(0.4)으로
//   일원화한다(UI-3). 적용줌 = max(fitZoom, ZOOM_MIN 0.4)이라 어떤 fitZoom(넓은 뷰포트로
//   fitZoom이 0.4~0.55로 떨어져도)에서도 ≥0.4 → 둘 다 표시(0.4~0.55 라벨 전멸 제거).

/** 라벨 LOD 줌 임계(이름+직책 표시 시작). =ZOOM_MIN(0.4)으로 일원화 — 둘 다 함께 켜짐. */
const LABEL_LOD_NAME_MIN = 0.4;

/**
 * 줌에 따른 라벨 가시성 단계를 정한다(순수·LOD).
 *
 * 임계=0.4(=ZOOM_MIN)으로 일원화 → 이름·직책이 같은 임계에서 함께 켜진다.
 * 적용줌은 항상 ≥0.4(setupCamera의 max(fitZoom, ZOOM_MIN))이므로 진입 어떤 fitZoom에서도
 * 둘 다 표시(0.4~0.55 전멸 구간 제거). 줌아웃 과밀 절제는 별도 임계로(현 범위 밖·후속).
 *
 * - zoom < 0.4  → {false,false} (라벨 전체 숨김 — 스프라이트만).
 * - zoom ≥ 0.4  → {true,true}   (이름 + 직책 함께).
 *
 * @param zoom 현재 카메라 줌.
 */
export function labelLOD(zoom: number): { showName: boolean; showTitle: boolean } {
  if (zoom < LABEL_LOD_NAME_MIN) {
    return { showName: false, showTitle: false };
  }
  return { showName: true, showTitle: true };
}

/**
 * 줌 역보정 스케일을 정한다(순수·=1/zoom). 라벨에 적용하면 줌 무관 화면 픽셀 유지.
 *
 * zoom이 0/음수여도 0 나눗셈을 피하도록 하한(0.0001)으로 가드한다.
 *
 * @param zoom 현재 카메라 줌.
 * @returns 라벨 컨테이너에 setScale할 역보정 배율.
 */
export function labelScale(zoom: number): number {
  return 1 / Math.max(zoom, 0.0001);
}

// ── 역할↔이벤트 매핑 (EventBus 말풍선 타겟) ─────────────────────────────────────

/**
 * OfficeEvent에서 말풍선을 띄울 역할 키를 고른다(순수·캔버스 무의존).
 *
 * ★rev4 단일 진실원천: node_id가 있을 때만 pickRoleKey에 위임한다(글로우·활동·말풍선이
 *   동일 node_id → 동일 역할). node_id가 없으면(TEXT_MESSAGE 등) ceo 가드를 유지한다
 *   (pickRoleKey("")는 planner로 빠지므로 빈/누락 분기를 위임 前에 처리 — 자기모순 제거).
 *   결과: 역할키 node_id→그대로(passthrough)·미지 node_id→해시 역할·node_id 없음→ceo.
 *
 * @param ev 수신 이벤트의 최소 형태(type·선택적 node_id).
 * @returns 말풍선 대상 역할 키.
 */
export function bubbleTargetRole(ev: { type: string; node_id?: string }): RoleKey {
  const id = ev.node_id;
  return typeof id === "string" && id.length > 0 ? pickRoleKey(id) : "ceo";
}

// ── node_id/식별자 → 16 역할 결정론 매핑 (채팅 발화자 초상) ─────────────────────
//
// ★TextMessage는 node_id를 담지 않는다(events.ts: role·content·image_url만). 따라서
// ChatPanel은 node_id가 있으면(백엔드 확장 대비) 그것을, 없으면 role 문자열을 식별자로
// 넘긴다. 어느 경우든 같은 식별자 → 항상 같은 16 역할(발화자 정체성 안정).

/**
 * 알려진 시드 node_id를 16 역할에 고정 매핑한다(가독성 — crm_notify 워크플로).
 * roleCharacter.SEED_ROLE_MAP과 동일 의도(8역할↔16역할 표시 일관).
 *
 * ★G005 US-G5-7: 5개 게임 제작 워크플로(game_design→balance→content→assemble→qa)의
 *   모든 node id를 담당 역할로 고정 매핑해 "AI 직원들이 게임을 분업 제작하는" 모습을
 *   오피스 관찰창에 가시화한다(pickRoleKey는 node_id 기준이므로 각 실제 node id 등록 필수).
 *   미등록 id는 해시폴백이라 크래시는 없으나 역할 고정이 보장되지 않아 명시 매핑한다.
 *   분업 5역할: game_design→planner(강하은)·game_balance→data(윤시우)·
 *   game_content→secretary(이수빈·콘텐츠작가)·game_assemble→dev(한지훈)·game_qa→devops(오태경).
 *   ★game_assemble의 node id는 asm_ 프리픽스(game_balance의 n1_read_meta와의 충돌 해소 —
 *   같은 id가 두 워크플로에 있으면 한 역할로만 매핑돼 조립 단계가 엉뚱한 역할로 표시됨).
 */
const SEED_ROLE_KEY_MAP: Record<string, RoleKey> = {
  // crm_notify(기존 시드).
  n1_crm_get: "secretary",
  n2_order_get: "accountant",
  n2_5_summary: "planner",
  n3_email_send: "marketer",
  // game_design → planner(강하은): 컨셉·테마·코어루프·승패·retry 과금 설계 → meta.json.
  n1_design_concept: "planner",
  n2_write_meta: "planner",
  // game_balance → data(윤시우): 수치 설계 → enemies/weapons/waves/upgrades + balance.json.
  n1_read_meta: "data",
  n2_design_numbers: "data",
  n3_write_enemies: "data",
  n4_write_weapons: "data",
  n5_write_waves: "data",
  n6_write_upgrades: "data",
  n7_write_balance: "data",
  // game_content → secretary(이수빈·콘텐츠작가): 플레이버 텍스트 양산 → enemies/weapons 병합.
  n1_read_enemies: "secretary",
  n2_read_weapons: "secretary",
  n3_write_flavor: "secretary",
  n4_write_enemies: "secretary",
  n5_write_weapons: "secretary",
  // game_assemble → dev(한지훈): 런타임 바인딩 검토/조립 → assembly.json. ★asm_ 프리픽스(충돌 해소).
  asm_read_meta: "dev",
  asm_read_enemies: "dev",
  asm_read_weapons: "dev",
  asm_read_waves: "dev",
  asm_read_upgrades: "dev",
  asm_assemble: "dev",
  asm_write_assembly: "dev",
  // game_qa → devops(오태경): 스키마/참조 무결성 검증 → qa_report.json.
  n1_validate: "devops",
  n2_write_report: "devops",
};

/**
 * 문자열을 32비트 부호 없는 정수로 해시한다(djb2 변형·결정론).
 * roleCharacter.hashString과 동일 알고리즘(같은 입력 → 같은 값).
 */
function hashString(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i += 1) {
    h = ((h << 5) + h + s.charCodeAt(i)) >>> 0; // h*33 + c, unsigned.
  }
  return h >>> 0;
}

/**
 * 식별자(node_id 또는 발화 role)를 16 역할 중 하나로 결정론 매핑한다(순수).
 *
 * 우선순위: ① "worker-" 접두 제거 → ② 16 역할 키와 정확히 일치하면 그대로 →
 * ③ 대기 직원("0")은 ceo 고정 → ④ 시드 node_id는 고정 맵 → ⑤ 그 외는 해시 % 16.
 * 같은 식별자 → 항상 같은 RoleKey. 빈 문자열도 안전(해시 → 결정론 배정).
 *
 * @param id node_id(예 "n1_crm_get") 또는 message role(예 "assistant").
 */
export function pickRoleKey(id: string): RoleKey {
  const nodeId = id.startsWith("worker-") ? id.slice("worker-".length) : id;
  if (isRoleKey(nodeId)) {
    return nodeId; // 식별자 자체가 역할 키(예 bubbleTargetRole 경로와 일관).
  }
  if (nodeId === "0") {
    return "ceo"; // 대기 직원(worker-0)은 대표.
  }
  const seeded = SEED_ROLE_KEY_MAP[nodeId];
  if (seeded) {
    return seeded;
  }
  return ROLE_KEYS[hashString(nodeId) % ROLE_KEYS.length];
}

// ── LPC 4방향 walk 애니(gather.town급·진짜 보행 프레임) ─────────────────────────
// roles_walk/{role}.png = LPC Universal walk 행 8-11 crop = 832×256(13열×4행·64px).
// 이전 squash cadence(가짜 walk)를 ★진짜 LPC walk 프레임으로 대체("날아다님" 근본 해소).

/** LPC walk 스프라이트시트 기하(13열×4행·64px 프레임). */
export const LPC = { frameW: 64, frameH: 64, cols: 13 } as const;

/** 맵 캐릭터(LPC 64px) 스케일 — qwen 128px×0.45 시절 가시높이에 근사(라이브 튜닝점). */
export const WALK_SCALE = 0.9;

/** 4방향(시트 행 순서: 0=up·1=left·2=down·3=right — LPC walk 행 8/9/10/11 crop 순). */
export type Dir = "up" | "left" | "down" | "right";
const DIR_ROW: Record<Dir, number> = { up: 0, left: 1, down: 2, right: 3 };

/** 이동 벡터(dx,dy)의 우세축으로 4방향을 고른다(순수). |dx|≥|dy|=좌/우, 아니면 상/하. */
export function walkDirection(dx: number, dy: number): Dir {
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx < 0 ? "left" : "right";
  }
  return dy < 0 ? "up" : "down";
}

/** 방향별 walk 프레임 인덱스 배열(col1~8·col0=정지 제외). 프레임index=행*cols+col. */
export function walkFrames(dir: Dir): number[] {
  const base = DIR_ROW[dir] * LPC.cols;
  return [1, 2, 3, 4, 5, 6, 7, 8].map((c) => base + c);
}

/** 방향별 idle(정지) 프레임 인덱스(각 행 col0). */
export function idleFrame(dir: Dir): number {
  return DIR_ROW[dir] * LPC.cols;
}

/** 역할 walk 스프라이트시트 에셋(키=`${role}_walk`·경로). */
export function roleWalkAsset(role: RoleKey): { key: string; path: string } {
  return { key: `${role}_walk`, path: `/assets/characters/roles_walk/${role}.png` };
}

/** 16역할 walk 에셋 목록(preload용·ROLE_KEYS 순서). */
export function roleWalkAssets(): ReadonlyArray<{ key: string; path: string }> {
  return ROLE_KEYS.map(roleWalkAsset);
}


// ── ★공간 엔진: 방·walkable·충돌·근접 (캔버스 무의존 순수 함수) ──────────────────────
//
// 리서치(harness 적합도 트랙B)가 지목한 "방·벽·충돌·근접 없는 소반경 배회"를 해소한다.
// Stitch floorplan(권위 디자인)은 유지하고, 그 위에 방 walkable 사각 − 장애물 사각으로
// "걸을 수 있는 영역"을 모델링해 idle 배회를 방 전체 로밍으로 확대하되 책상/벽을 피한다.
//
// ★raster 정합 한계(정직): floorplan은 raster라 walkable/obstacle 사각은 수기 근사다.
//   순수 테스트는 "목표가 사각 안"이라는 자기일관만 보증한다(동어반복). 사각이 그림과
//   실제로 맞는지는 자동으로 검증 불가 → 오버레이 PNG Read 육안 승인 baseline이 유일
//   시각 방벽이다(office-character-art-bar HITL 단일방벽 규약). ROOMS 좌표 = HITL 튜닝점.

/** 네이티브(512×286) 좌표계의 사각(좌상단 x,y + 폭/높이). */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 4 구역 방 id. ROLE_DESK_COORDS 주석의 4구역과 1:1. */
export type RoomId = "executive" | "operations" | "techdata" | "creative";

/** 4 방 id 목록(순회용·결정론 순서). */
export const ROOM_IDS = ["executive", "operations", "techdata", "creative"] as const;

/** 한 방의 공간 정의 — walkable 바닥 − obstacles(책상/벽) + safeSpot(폴백 보장점). 전부 네이티브. */
export interface RoomDef {
  /** 걸을 수 있는 방 바닥 사각(네이티브). */
  walkable: Rect;
  /** 걸으면 안 되는 사각들(책상 몸체/벽·네이티브). */
  obstacles: Rect[];
  /** walkable∧¬obstacle 보장점(거부재시도 폴백 목표·네이티브·테스트 단언). */
  safeSpot: { x: number; y: number };
}

/**
 * 4 구역 방 공간 모델(네이티브 512×286·×MAP_SCALE 2 → 월드). ★수기 좌표 = HITL 튜닝점
 * (ROLE_DESK_COORDS와 동일 규약·오버레이 승인으로 확정). walkable=방 바닥, obstacles=중앙
 * 가구/책상 몸체(그 위 금지), safeSpot=폴백 보장점. 기본값은 ROLE_DESK_COORDS 군집에서 파생.
 * ★불변식(테스트 단언): 각 역할의 책상 좌표·safeSpot은 자기 방 walkable∧¬obstacle.
 */
export const ROOMS: Record<RoomId, RoomDef> = {
  // 좌상 Executive Wing — ceo/strategist/cfo/legal.
  executive: {
    walkable: { x: 18, y: 24, w: 220, h: 142 },
    obstacles: [{ x: 88, y: 76, w: 100, h: 44 }], // 중앙 회의테이블(확대·desk-free 검증).
    safeSpot: { x: 34, y: 150 },
  },
  // 우상 Operations — pm/planner/secretary/hr.
  operations: {
    walkable: { x: 298, y: 24, w: 196, h: 142 },
    obstacles: [{ x: 352, y: 76, w: 96, h: 44 }], // 중앙 큐비클 블록(확대·desk-free 검증).
    safeSpot: { x: 312, y: 150 },
  },
  // 좌하 Tech & Data — dev/devops/data/accountant.
  techdata: {
    walkable: { x: 18, y: 174, w: 262, h: 108 },
    obstacles: [{ x: 88, y: 218, w: 100, h: 40 }], // 서버랙(확대·desk-free 검증).
    safeSpot: { x: 34, y: 180 },
  },
  // 우하 Creative & Growth — designer/marketer/sales/cs.
  creative: {
    walkable: { x: 298, y: 174, w: 200, h: 108 },
    obstacles: [{ x: 358, y: 216, w: 100, h: 40 }], // 라운지 테이블(확대·desk-free 검증).
    safeSpot: { x: 312, y: 180 },
  },
};

/** 16 역할 → 소속 방(ROLE_DESK_COORDS 4구역과 1:1). */
export const ROLE_ROOM: Record<RoleKey, RoomId> = {
  // 좌상 Executive.
  ceo: "executive",
  strategist: "executive",
  cfo: "executive",
  legal: "executive",
  // 우상 Operations.
  pm: "operations",
  planner: "operations",
  secretary: "operations",
  hr: "operations",
  // 좌하 Tech & Data.
  dev: "techdata",
  devops: "techdata",
  data: "techdata",
  accountant: "techdata",
  // 우하 Creative & Growth.
  designer: "creative",
  marketer: "creative",
  sales: "creative",
  cs: "creative",
};

/** 한 역할이 속한 방 id를 돌려준다(순수). */
export function roomForRole(role: RoleKey): RoomId {
  return ROLE_ROOM[role];
}

/** native Rect를 월드(×MAP_SCALE) Rect로 환산한다(순수·내부 헬퍼). */
function rectToWorld(r: Rect): Rect {
  return {
    x: r.x * MAP_SCALE,
    y: r.y * MAP_SCALE,
    w: r.w * MAP_SCALE,
    h: r.h * MAP_SCALE,
  };
}

/** 한 점이 사각 안인지(경계 포함·순수 내부 헬퍼). */
function inRect(x: number, y: number, r: Rect): boolean {
  return x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;
}

/**
 * 선분 (x0,y0)-(x1,y1)이 사각 r(AABB)과 교차/접촉하는지(순수·Liang-Barsky 슬랩 클리핑).
 * - 영길이 선분(시작=끝)은 점-사각 검사로 처리.
 * - 경계 접촉(접선)도 교차로 본다(보수적 — 가구 모서리 스침도 거부).
 */
function segmentIntersectsRect(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  r: Rect,
): boolean {
  const dx = x1 - x0;
  const dy = y1 - y0;
  if (dx === 0 && dy === 0) {
    return inRect(x0, y0, r); // 영길이 선분 = 점.
  }
  let t0 = 0;
  let t1 = 1;
  // 4 슬랩(좌/우/상/하): [p, q] — p*t <= q 형태. p===0이면 평행(q<0이면 바깥).
  const slabs: Array<readonly [number, number]> = [
    [-dx, x0 - r.x],
    [dx, r.x + r.w - x0],
    [-dy, y0 - r.y],
    [dy, r.y + r.h - y0],
  ];
  for (const [p, q] of slabs) {
    if (p === 0) {
      if (q < 0) {
        return false; // 슬랩과 평행 + 바깥 → 교차 없음.
      }
    } else {
      const t = q / p;
      if (p < 0) {
        if (t > t1) return false;
        if (t > t0) t0 = t;
      } else {
        if (t < t0) return false;
        if (t < t1) t1 = t;
      }
    }
  }
  return t0 <= t1;
}

/**
 * 선분 (x0,y0)-(x1,y1)이 모든 obstacle 사각을 관통/접촉하지 않는지(순수).
 * findPath 경로의 각 segment가 obstacle을 비관통함을 테스트가 검증하는 오라클로 쓴다(경로 안전).
 * @returns 어느 obstacle과도 교차하지 않으면 true(직선 이동 안전).
 */
export function segmentClear(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  obstacles: Rect[],
): boolean {
  for (const r of obstacles) {
    if (segmentIntersectsRect(x0, y0, x1, y1, r)) {
      return false;
    }
  }
  return true;
}

/** 방 walkable 사각을 월드 좌표로 돌려준다(순수). */
export function roomWalkableWorld(roomId: RoomId): Rect {
  return rectToWorld(ROOMS[roomId].walkable);
}

/** 방 obstacle 사각들을 월드 좌표로 돌려준다(순수). */
export function roomObstaclesWorld(roomId: RoomId): Rect[] {
  return ROOMS[roomId].obstacles.map(rectToWorld);
}

/** 방 safeSpot(폴백 보장점)을 월드 좌표로 돌려준다(순수). */
export function roomSafeSpotWorld(roomId: RoomId): { x: number; y: number } {
  const s = ROOMS[roomId].safeSpot;
  return { x: s.x * MAP_SCALE, y: s.y * MAP_SCALE };
}

/**
 * 월드 좌표 (x,y)가 방에서 걸을 수 있는지(순수): walkable 안 ∧ 모든 obstacle 밖.
 * @param x 월드 X. @param y 월드 Y. @param roomId 방 id.
 */
export function isWalkable(x: number, y: number, roomId: RoomId): boolean {
  if (!inRect(x, y, roomWalkableWorld(roomId))) {
    return false;
  }
  for (const obs of roomObstaclesWorld(roomId)) {
    if (inRect(x, y, obs)) {
      return false;
    }
  }
  return true;
}

/** 보행 속도(월드 px/ms) — walkPath가 segment 길이÷이 값으로 등속 이동시간 산출(라이브 튜닝점). */
export const WALK_SPEED = 0.06;


// ── A* obstacle 우회 경로탐색 (#2·방 격자·순수·결정론) ─────────────────────────
// 방 walkable 사각을 NAV_CELL 격자로 나눠, walkable 셀(중심이 isWalkable)만 4-연결 A*로
// 잇는다. setWorkingPresence가 고른 목표(가구 뒤일 수 있음)까지 obstacle을 우회하는
// waypoint 목록을 돌려준다 → OfficeScene walkPath가 segment별 walk 애니로 따라 걷는다.

/** A* 내비 셀 크기(월드 px). 방 walkable(≈400×284)을 이 격자로 분할. */
export const NAV_CELL = 24;

interface NavCell {
  c: number;
  r: number;
}

/** 방 내비 격자 차원(walkable 사각을 NAV_CELL로 분할·최소 1×1). */
function navDims(roomId: RoomId): { cols: number; rows: number; wr: Rect } {
  const wr = roomWalkableWorld(roomId);
  return {
    cols: Math.max(1, Math.round(wr.w / NAV_CELL)),
    rows: Math.max(1, Math.round(wr.h / NAV_CELL)),
    wr,
  };
}

/** 셀 (c,r) 중심 월드 좌표. */
function navCellCenter(
  roomId: RoomId,
  c: number,
  r: number,
): { x: number; y: number } {
  const { cols, rows, wr } = navDims(roomId);
  return {
    x: wr.x + ((c + 0.5) * wr.w) / cols,
    y: wr.y + ((r + 0.5) * wr.h) / rows,
  };
}

/** 월드 좌표 → 가장 가까운 셀(범위 클램프). */
function navWorldToCell(roomId: RoomId, x: number, y: number): NavCell {
  const { cols, rows, wr } = navDims(roomId);
  const c = Math.min(cols - 1, Math.max(0, Math.floor(((x - wr.x) / wr.w) * cols)));
  const r = Math.min(rows - 1, Math.max(0, Math.floor(((y - wr.y) / wr.h) * rows)));
  return { c, r };
}

/** 셀 중심이 walkable인지. */
function navCellWalkable(roomId: RoomId, c: number, r: number): boolean {
  const ctr = navCellCenter(roomId, c, r);
  return isWalkable(ctr.x, ctr.y, roomId);
}

/**
 * A* 경로 — (sx,sy)→(gx,gy)를 obstacle 우회해 waypoint(월드) 목록으로 돌려준다(순수·결정론).
 *
 * 시작 셀은 제외하고 경유 셀 중심들 + 정확한 목표점(gx,gy)을 끝에 붙인다. 같은 셀이면 [goal].
 * 경로 없음(목표 도달 불가)이면 [goal](직행 폴백). 4-연결·맨해튼 휴리스틱·결정론 타이브레이크
 * (f→삽입 seq). 유한 격자라 종료 보장(hang 0). 인접 walkable 셀 간 segment는 NAV_CELL 단거리라
 * obstacle 비관통(테스트가 segmentClear로 검증).
 */
export function findPath(
  roomId: RoomId,
  sx: number,
  sy: number,
  gx: number,
  gy: number,
): Array<{ x: number; y: number }> {
  const { cols, rows } = navDims(roomId);
  const start = navWorldToCell(roomId, sx, sy);
  const goal = navWorldToCell(roomId, gx, gy);
  const goalPt = { x: gx, y: gy };
  if (start.c === goal.c && start.r === goal.r) {
    return [goalPt];
  }
  const key = (c: number, r: number): number => r * cols + c;
  const heur = (c: number, r: number): number =>
    Math.abs(c - goal.c) + Math.abs(r - goal.r);
  interface ANode {
    c: number;
    r: number;
    g: number;
    f: number;
    seq: number;
  }
  const startKey = key(start.c, start.r);
  const gScore = new Map<number, number>([[startKey, 0]]);
  const came = new Map<number, number>();
  const open: ANode[] = [
    { c: start.c, r: start.r, g: 0, f: heur(start.c, start.r), seq: 0 },
  ];
  let seq = 1;
  const NB: ReadonlyArray<readonly [number, number]> = [
    [0, -1],
    [0, 1],
    [-1, 0],
    [1, 0],
  ];
  let found = false;
  while (open.length > 0) {
    // 최소 f(타이 시 작은 seq) 추출 — 결정론.
    let bi = 0;
    for (let i = 1; i < open.length; i += 1) {
      const o = open[i]!;
      const b = open[bi]!;
      if (o.f < b.f || (o.f === b.f && o.seq < b.seq)) {
        bi = i;
      }
    }
    const cur = open.splice(bi, 1)[0]!;
    if (cur.c === goal.c && cur.r === goal.r) {
      found = true;
      break;
    }
    for (const [dc, dr] of NB) {
      const nc = cur.c + dc;
      const nr = cur.r + dr;
      if (nc < 0 || nc >= cols || nr < 0 || nr >= rows) {
        continue;
      }
      if (!navCellWalkable(roomId, nc, nr)) {
        continue;
      }
      const nk = key(nc, nr);
      const ng = cur.g + 1;
      if (ng < (gScore.get(nk) ?? Infinity)) {
        gScore.set(nk, ng);
        came.set(nk, key(cur.c, cur.r));
        open.push({ c: nc, r: nr, g: ng, f: ng + heur(nc, nr), seq });
        seq += 1;
      }
    }
  }
  if (!found) {
    return [goalPt]; // 도달 불가 → 직행 폴백.
  }
  // came 역추적 → 시작 제외 경유 셀들.
  const cellsRev: NavCell[] = [];
  let k: number | undefined = key(goal.c, goal.r);
  while (k !== undefined && k !== startKey) {
    cellsRev.push({ c: k % cols, r: Math.floor(k / cols) });
    k = came.get(k);
  }
  cellsRev.reverse();
  const path = cellsRev.map((cell) => navCellCenter(roomId, cell.c, cell.r));
  // 마지막 셀중심을 정확한 목표점으로 치환(미세 정렬).
  if (path.length > 0) {
    path[path.length - 1] = goalPt;
  } else {
    path.push(goalPt);
  }
  return path;
}

// ── 협업 presence (현실 반영: idle=책상 정지·협업 시에만 서로 다가가 모임) ──────
// 무작위 배회 대신, 같은 방에서 ≥2명이 동시에 working이면 그들의 책상 무게중심(미팅스폿)
// 근처로 모인다(서로 다가감). 혼자 working이거나 idle이면 책상에 머문다.

/** 점들의 무게중심(순수). 빈 배열이면 (0,0). */
export function roomCentroid(
  pts: ReadonlyArray<{ x: number; y: number }>,
): { x: number; y: number } {
  if (pts.length === 0) {
    return { x: 0, y: 0 };
  }
  let sx = 0;
  let sy = 0;
  for (const p of pts) {
    sx += p.x;
    sy += p.y;
  }
  return { x: sx / pts.length, y: sy / pts.length };
}

/** 협업 미팅 스폿 — working 홈들의 무게중심(walkable이면 그대로, 가구 위면 safeSpot 폴백·순수). */
export function meetingSpot(
  roomId: RoomId,
  homes: ReadonlyArray<{ x: number; y: number }>,
): { x: number; y: number } {
  const c = roomCentroid(homes);
  return isWalkable(c.x, c.y, roomId) ? c : roomSafeSpotWorld(roomId);
}

/** 협업 클러스터 반경(월드 px) — 미팅스폿 둘레로 인원을 분산(겹침 방지). */
export const CLUSTER_RADIUS = 18;

/** n명 중 i번째의 소형 링 오프셋(겹침 방지·결정론·n≤1이면 0). */
export function clusterOffset(i: number, n: number): { dx: number; dy: number } {
  if (n <= 1) {
    return { dx: 0, dy: 0 };
  }
  const a = (i / n) * Math.PI * 2;
  return {
    dx: Math.round(Math.cos(a) * CLUSTER_RADIUS),
    dy: Math.round(Math.sin(a) * CLUSTER_RADIUS),
  };
}

/**
 * 협업 시 i번째 working 직원의 목표 좌표(미팅스폿 + 클러스터 오프셋·walkable 클램프·순수).
 * 오프셋이 가구 위로 가면 미팅스폿으로 클램프(목표는 항상 walkable 보장).
 * @param homes 같은 방 working 직원들의 책상 좌표(무게중심 산출용).
 * @param i homes 내 이 직원의 인덱스(오프셋 결정).
 */
export function collaborationTarget(
  roomId: RoomId,
  homes: ReadonlyArray<{ x: number; y: number }>,
  i: number,
): { x: number; y: number } {
  const spot = meetingSpot(roomId, homes);
  const off = clusterOffset(i, homes.length);
  const t = { x: spot.x + off.dx, y: spot.y + off.dy };
  return isWalkable(t.x, t.y, roomId) ? t : spot;
}

/** 근접 한 쌍 — 두 역할 키(결정론: ROLE_KEYS 인덱스 오름차순 [a,b]). */
export type NearPair = [RoleKey, RoleKey];

/** 근접 판정 기본 반경(월드 px·HITL 튜닝점). 캐릭터가 "가까이서 상호작용"하는 거리. */
export const PROXIMITY_RADIUS = 60;

/** 역할의 ROLE_KEYS 인덱스(결정론 정렬용·내부 헬퍼). */
function roleIndex(role: RoleKey): number {
  return ROLE_KEYS.indexOf(role);
}

/**
 * 서로 radius 안에 있는 역할 쌍을 결정론으로 모은다(순수).
 *
 * i<j 이중루프라 각 무순서 쌍은 1회만 생성(대칭 dedup 내재)되고, 각 쌍은 ROLE_KEYS 인덱스
 * 오름차순 [a,b](a<b)로 정규화한다. 전체 목록도 (a,b) 인덱스 순 정렬 → 같은 입력 같은 출력.
 * 빈/단일 입력은 빈 배열.
 *
 * @param positions 현재 좌표 목록({role,x,y}·월드 px).
 * @param radius 근접 반경(월드 px·기본 PROXIMITY_RADIUS).
 */
export function nearbyPairs(
  positions: ReadonlyArray<{ role: RoleKey; x: number; y: number }>,
  radius: number = PROXIMITY_RADIUS,
): NearPair[] {
  const r2 = radius * radius;
  const out: NearPair[] = [];
  for (let i = 0; i < positions.length; i += 1) {
    for (let j = i + 1; j < positions.length; j += 1) {
      const a = positions[i];
      const b = positions[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      if (dx * dx + dy * dy <= r2) {
        const pair: NearPair =
          roleIndex(a.role) <= roleIndex(b.role)
            ? [a.role, b.role]
            : [b.role, a.role];
        out.push(pair);
      }
    }
  }
  out.sort(
    (p, q) => roleIndex(p[0]) - roleIndex(q[0]) || roleIndex(p[1]) - roleIndex(q[1]),
  );
  return out;
}
