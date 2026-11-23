/**
 * sceneLogic 단위 테스트 (office-v2 US-AF1·Vitest).
 *
 * OfficeScene의 캔버스 비의존 순수 로직(spawn 파싱·역할 매핑·에셋 키)을 검증한다.
 * 캔버스(Phaser.Game) 생성·렌더는 e2e(:33006·오케스트레이터)에서 — 여기선 결정론 함수만.
 */

import { describe, it, expect } from "vitest";
import {
  ROLE_KEYS,
  ROLE_KEY_NAMES,
  isRoleKey,
  TILESET_NAME,
  LAYER_NAMES,
  SPAWN_LAYER_NAME,
  OFFICE_WORLD,
  MAP_SCALE,
  FLOORPLAN_WORLD,
  ROLE_DESK_COORDS,
  deskPlacements,
  SCENE_ASSETS,
  parseSpawns,
  bubbleTargetRole,
  pickRoleKey,
  labelLOD,
  labelScale,
  ROOMS,
  ROLE_ROOM,
  ROOM_IDS,
  roomForRole,
  roomWalkableWorld,
  roomObstaclesWorld,
  roomSafeSpotWorld,
  isWalkable,
  segmentClear,
  walkDirection,
  walkFrames,
  idleFrame,
  roleWalkAsset,
  findPath,
  roomCentroid,
  meetingSpot,
  clusterOffset,
  collaborationTarget,
  nearbyPairs,
  type RoleKey,
  type TiledObject,
} from "../sceneLogic";

describe("ROLE_KEYS / isRoleKey — 16 역할 멤버십", () => {
  it("정확히 16개 역할", () => {
    expect(ROLE_KEYS).toHaveLength(16);
  });

  it("ceo는 역할 키, foo는 아님", () => {
    expect(isRoleKey("ceo")).toBe(true);
    expect(isRoleKey("foo")).toBe(false);
  });
});

describe("TILESET_NAME / LAYER_NAMES — office.tmj 확인값", () => {
  it("tileset name은 office_v2(tmj tilesets[0].name과 일치)", () => {
    expect(TILESET_NAME).toBe("office_v2");
  });

  it("레이어 순서 floor → walls → furniture", () => {
    expect(LAYER_NAMES).toEqual(["floor", "walls", "furniture"]);
  });

  it("spawns objectgroup 이름", () => {
    expect(SPAWN_LAYER_NAME).toBe("spawns");
  });

  it("월드 픽셀(40×26·tile 32) = 1280×832", () => {
    expect(OFFICE_WORLD).toEqual({ width: 1280, height: 832 });
  });
});

describe("SCENE_ASSETS — preload 키·경로", () => {
  it("★P3 floorplan 배경 경로 = /assets/nexus/floorplan.png·키 floorplan", () => {
    expect(SCENE_ASSETS.floorplan.path).toBe("/assets/nexus/floorplan.png");
    expect(SCENE_ASSETS.floorplan.key).toBe("floorplan");
  });
});

describe("parseSpawns — objectgroup → 결정론 배치(발밑 중심)", () => {
  // 보정식(centerX=x+w/2·footY=y+h) 단위검산용 임의 픽셀(실 tmj 값 아님·계산식 검증 전용).
  const OBJECTS: TiledObject[] = [
    { name: "ceo", x: 480, y: 64, width: 32, height: 32 },
    { name: "dev", x: 256, y: 864, width: 32, height: 32 },
    { name: "ghost", x: 0, y: 0, width: 32, height: 32 }, // 미지 역할.
  ];

  it("미지 역할(ghost)은 무시하고 알려진 16 역할만 배치", () => {
    const spawns = parseSpawns(OBJECTS);
    expect(spawns).toHaveLength(2);
    expect(spawns.map((s) => s.role)).toEqual(["ceo", "dev"]);
  });

  it("(x,y)=좌상단 → 중심 X(x+w/2)·발밑 Y(y+h) 보정", () => {
    const [ceo] = parseSpawns(OBJECTS);
    expect(ceo).toEqual({ role: "ceo", x: 480 + 16, y: 64 + 32 }); // 496, 96.
  });

  it("입력 순서 보존·결정론(같은 입력 → 같은 출력)", () => {
    expect(parseSpawns(OBJECTS)).toEqual(parseSpawns(OBJECTS));
  });

  it("누락 필드(width/x 없음)는 0으로 안전 처리", () => {
    const spawns = parseSpawns([{ name: "hr" }]);
    expect(spawns[0]).toEqual({ role: "hr", x: 0, y: 0 });
  });

  // 단일룸 재설계 실 office.tmj object 좌표(§4.4): ceo(608,96)·dev(160,416)·designer(1120,416).
  // parseSpawns 보정 후 스프라이트 중심 X·발밑 Y = §4.4 spawn px/py와 정합해야 함.
  it("신 office.tmj 실좌표(40×26 단일룸) → §4.4 spawn px/py 매칭", () => {
    const REAL: TiledObject[] = [
      { name: "ceo", x: 608, y: 96, width: 32, height: 32 },
      { name: "dev", x: 160, y: 416, width: 32, height: 32 },
      { name: "designer", x: 1120, y: 416, width: 32, height: 32 },
    ];
    const spawns = parseSpawns(REAL);
    expect(spawns).toEqual([
      { role: "ceo", x: 624, y: 128 }, // §4.4 spawn px=624·py=128(발밑).
      { role: "dev", x: 176, y: 448 }, // §4.4 spawn px=176·py=448.
      { role: "designer", x: 1136, y: 448 }, // §4.4 spawn px=1136·py=448.
    ]);
  });
});

describe("★P3 deskPlacements — 4구역 책상 배치(floorplan 단일플로어)", () => {
  it("MAP_SCALE=2·FLOORPLAN_WORLD=1024×572(=floorplan.png 실치수)", () => {
    expect(MAP_SCALE).toBe(2);
    expect(FLOORPLAN_WORLD).toEqual({ width: 1024, height: 572 });
  });

  it("16 역할 전부 배치(누락 0)·결정론(같은 출력)", () => {
    const p = deskPlacements();
    expect(p).toHaveLength(16);
    expect([...p.map((d) => d.role)].sort()).toEqual([...ROLE_KEYS].sort());
    expect(deskPlacements()).toEqual(p);
  });

  it("좌표=네이티브×MAP_SCALE·전부 월드 bounds 안(발밑 origin 0.5,1)", () => {
    for (const d of deskPlacements()) {
      const native = ROLE_DESK_COORDS[d.role];
      expect(d.x).toBe(native.x * MAP_SCALE);
      expect(d.y).toBe(native.y * MAP_SCALE);
      expect(d.x).toBeGreaterThanOrEqual(0);
      expect(d.x).toBeLessThanOrEqual(FLOORPLAN_WORLD.width);
      expect(d.y).toBeGreaterThanOrEqual(0);
      expect(d.y).toBeLessThanOrEqual(FLOORPLAN_WORLD.height);
    }
  });

  it("4구역 분포: 좌상/우상/좌하/우하 각 정확히 4역할(중심 512/286 기준)", () => {
    const cx = FLOORPLAN_WORLD.width / 2;
    const cy = FLOORPLAN_WORLD.height / 2;
    const quad = { tl: 0, tr: 0, bl: 0, br: 0 };
    for (const d of deskPlacements()) {
      const left = d.x < cx;
      const top = d.y < cy;
      if (top && left) quad.tl += 1;
      else if (top && !left) quad.tr += 1;
      else if (!top && left) quad.bl += 1;
      else quad.br += 1;
    }
    expect(quad).toEqual({ tl: 4, tr: 4, bl: 4, br: 4 });
  });
});

describe("bubbleTargetRole — 이벤트 → 말풍선 역할 매핑(★rev4 단일 진실원천)", () => {
  it("node_id가 역할 키면 그 역할로(pickRoleKey passthrough)", () => {
    expect(bubbleTargetRole({ type: "TOOL_CALL_START", node_id: "dev" })).toBe("dev");
  });

  it("node_id 누락/빈 문자열이면 ceo 폴백(pickRoleKey 위임 前 가드)", () => {
    // TEXT_MESSAGE 등 node_id 없음 → ceo 유지(기존:184 그린·pickRoleKey('')→planner 회피).
    expect(bubbleTargetRole({ type: "TEXT_MESSAGE" })).toBe("ceo");
    expect(bubbleTargetRole({ type: "TOOL_CALL_START", node_id: "" })).toBe("ceo");
  });

  it("★미지 node_id는 ceo가 아니라 pickRoleKey 해시 역할(rev4 변경)", () => {
    // 이전: ceo 폴백. 현재: node_id 있으면 pickRoleKey에 위임 → 결정론 해시 역할.
    expect(bubbleTargetRole({ type: "TOOL_CALL_START", node_id: "unknown" })).toBe(
      pickRoleKey("unknown"),
    );
  });

  it("★단일 진실원천: node_id 있을 때 bubbleTargetRole === pickRoleKey(node_id)", () => {
    for (const id of ["n1_crm_get", "n2_5_summary", "n3_email_send", "dev", "xyz"]) {
      expect(bubbleTargetRole({ type: "TOOL_CALL_START", node_id: id })).toBe(pickRoleKey(id));
    }
  });
});

describe("ROLE_KEY_NAMES — 16 역할 한국어 이름", () => {
  it("모든 16 역할 키에 비어있지 않은 이름(누락 0·여분 0)", () => {
    for (const role of ROLE_KEYS) {
      expect(ROLE_KEY_NAMES[role]).toBeTruthy();
    }
    expect(Object.keys(ROLE_KEY_NAMES)).toHaveLength(16);
  });
});

describe("labelLOD — 줌 LOD(이름/직책 가시성·UI-3 NAME_MIN=TITLE_MIN=0.4 일원화)", () => {
  it("zoom<0.4 → 라벨 전체 hide(ZOOM_MIN 미만은 화면 밖 케이스)", () => {
    expect(labelLOD(0.39)).toEqual({ showName: false, showTitle: false });
    expect(labelLOD(0.2)).toEqual({ showName: false, showTitle: false });
  });

  it("zoom≥0.4 → 이름+직책 둘 다(0.4~0.55 전멸 구간 제거)", () => {
    // 구버전 임계(0.55)에서 라벨이 사라지던 0.4~0.55 구간이 이제 둘 다 표시.
    expect(labelLOD(0.45)).toEqual({ showName: true, showTitle: true });
    expect(labelLOD(0.5)).toEqual({ showName: true, showTitle: true });
    expect(labelLOD(0.5625)).toEqual({ showName: true, showTitle: true }); // 기존 fitZoom.
    expect(labelLOD(0.8)).toEqual({ showName: true, showTitle: true });
    expect(labelLOD(1.3)).toEqual({ showName: true, showTitle: true });
  });

  it("경계값 0.4(=ZOOM_MIN·적용줌 하한) 포함 → 둘 다 표시(진입 최악도 둘 다)", () => {
    expect(labelLOD(0.4)).toEqual({ showName: true, showTitle: true });
  });

  it("labelLOD(ZOOM_MAX 2.0) → 둘 다 표시", () => {
    expect(labelLOD(2.0)).toEqual({ showName: true, showTitle: true });
  });

  it("worst-case 반응형 fitZoom 경계 — 적용줌 max(fitZoom, 0.4)에서 둘 다 표시", () => {
    // 매우 넓은 뷰포트(예 2560×400, world 1280×832): fitZoom=min(2560/1280, 400/832)=0.4807…
    const wideFit = Math.min(2560 / 1280, 400 / 832);
    const appliedWide = Math.max(wideFit, 0.4); // setupCamera 하한 반영.
    expect(labelLOD(appliedWide)).toEqual({ showName: true, showTitle: true });

    // fitZoom이 0.4 미만으로 떨어지는 극단(예 3200×320): 적용줌은 하한 0.4로 클램프 → 둘 다.
    const extremeFit = Math.min(3200 / 1280, 320 / 832); // ≈0.3846 < 0.4.
    const appliedExtreme = Math.max(extremeFit, 0.4); // = 0.4(하한).
    expect(labelLOD(appliedExtreme)).toEqual({ showName: true, showTitle: true });
  });
});

describe("labelScale — 줌 역보정(1/zoom)", () => {
  it("labelScale(0.5625) ≈ 1.778 (=1/0.5625·fit 줌 판독성)", () => {
    expect(labelScale(0.5625)).toBeCloseTo(1.778, 3);
  });

  it("zoom=1이면 1배(역보정 없음)", () => {
    expect(labelScale(1)).toBeCloseTo(1, 6);
  });

  it("zoom=0/음수여도 0 나눗셈 가드(유한값)", () => {
    expect(Number.isFinite(labelScale(0))).toBe(true);
    expect(Number.isFinite(labelScale(-1))).toBe(true);
  });
});

describe("pickRoleKey — 식별자 → 16 역할 결정론 매핑", () => {
  const roleSet = new Set<RoleKey>(ROLE_KEYS);

  it("같은 식별자 → 항상 같은 RoleKey(결정론)", () => {
    const ids = ["assistant", "n2_5_summary", "worker-xyz", "dynamic_node_42", ""];
    for (const id of ids) {
      expect(pickRoleKey(id)).toBe(pickRoleKey(id));
    }
  });

  it("반환은 항상 16 역할 멤버", () => {
    const ids = ["assistant", "system", "n1_crm_get", "aaa", "zzz", "worker-foo", ""];
    for (const id of ids) {
      expect(roleSet.has(pickRoleKey(id))).toBe(true);
    }
  });

  it("식별자가 역할 키 자체면 그대로(8역할 밖 신규역할 포함)", () => {
    expect(pickRoleKey("dev")).toBe("dev");
    expect(pickRoleKey("devops")).toBe("devops"); // 16역할 신규.
    expect(pickRoleKey("worker-cfo")).toBe("cfo"); // worker- 접두 제거 후 매칭.
  });

  it("worker-0(대기 직원)은 ceo로 고정", () => {
    expect(pickRoleKey("worker-0")).toBe("ceo");
    expect(pickRoleKey("0")).toBe("ceo");
  });

  it("시드 node_id는 고정 역할로 매핑(8/16 표시 일관)", () => {
    expect(pickRoleKey("n1_crm_get")).toBe("secretary");
    expect(pickRoleKey("n2_order_get")).toBe("accountant");
    expect(pickRoleKey("n2_5_summary")).toBe("planner");
    expect(pickRoleKey("n3_email_send")).toBe("marketer");
    // worker- 접두 버전도 동일.
    expect(pickRoleKey("worker-n1_crm_get")).toBe("secretary");
  });
});

describe("★G005 US-G5-7 — 게임 제작 워크플로 node_id → 분업 역할 매핑(관찰창 가시화)", () => {
  // 해시폴백 역할(명시 매핑이 아니라 hash % 16으로 빠질 경우의 역할). 각 game node id의
  // 기대 역할이 이 폴백과 "다름"을 함께 단언해 폴백이 아닌 명시 SEED 매핑임을 보장한다.
  function hashFallback(id: string): RoleKey {
    let h = 5381;
    for (let i = 0; i < id.length; i += 1) {
      h = ((h << 5) + h + id.charCodeAt(i)) >>> 0;
    }
    return ROLE_KEYS[(h >>> 0) % ROLE_KEYS.length];
  }

  // [node_id, 기대 roleKey] — 5개 워크플로의 모든 node id(23개)·분업 5역할.
  const GAME_NODE_ROLES: ReadonlyArray<readonly [string, RoleKey]> = [
    // game_design → planner(강하은).
    ["n1_design_concept", "planner"],
    ["n2_write_meta", "planner"],
    // game_balance → data(윤시우). ★n1_read_meta(충돌 해소: balance=data).
    ["n1_read_meta", "data"],
    ["n2_design_numbers", "data"],
    ["n3_write_enemies", "data"],
    ["n4_write_weapons", "data"],
    ["n5_write_waves", "data"],
    ["n6_write_upgrades", "data"],
    ["n7_write_balance", "data"],
    // game_content → secretary(이수빈·콘텐츠작가).
    ["n1_read_enemies", "secretary"],
    ["n2_read_weapons", "secretary"],
    ["n3_write_flavor", "secretary"],
    ["n4_write_enemies", "secretary"],
    ["n5_write_weapons", "secretary"],
    // game_assemble → dev(한지훈). ★asm_ 프리픽스(충돌 해소: assemble=dev).
    ["asm_read_meta", "dev"],
    ["asm_read_enemies", "dev"],
    ["asm_read_weapons", "dev"],
    ["asm_read_waves", "dev"],
    ["asm_read_upgrades", "dev"],
    ["asm_assemble", "dev"],
    ["asm_write_assembly", "dev"],
    // game_qa → devops(오태경).
    ["n1_validate", "devops"],
    ["n2_write_report", "devops"],
  ];

  it("23개 게임 node id 전부 기대 역할로 명시 매핑(해시폴백 아님)", () => {
    for (const [id, role] of GAME_NODE_ROLES) {
      expect(pickRoleKey(id)).toBe(role);
    }
  });

  it("★node id 충돌 해소: balance n1_read_meta=data·assemble asm_read_meta=dev(서로 다른 역할)", () => {
    // 리네임 전에는 n1_read_meta가 balance·assemble 양쪽에 있어 pickRoleKey가 한 역할로만
    // 매핑돼 assemble의 read_meta가 엉뚱한 역할로 표시됐다. asm_ 프리픽스로 분리.
    expect(pickRoleKey("n1_read_meta")).toBe("data"); // game_balance.
    expect(pickRoleKey("asm_read_meta")).toBe("dev"); // game_assemble.
    expect(pickRoleKey("n1_read_meta")).not.toBe(pickRoleKey("asm_read_meta"));
  });

  it("명시 SEED 매핑이 해시폴백을 압도(분업 그룹은 해시 분포로 나올 수 없음·폴백 회귀 가드)", () => {
    // 일부 id는 해시폴백이 우연히 기대 역할과 같을 수 있으나(좌표 우연), 23개가 5개
    // 역할로 "깔끔히 그룹화"되는 건 해시 분포로는 불가능하다. 해시폴백만 썼다면 기대 역할과
    // 불일치하는 id가 다수 생긴다 — 즉 SEED 매핑이 실제로 다수 id를 지배함을 단언한다
    // (폴백 경로였다면 절반 이상이 어긋남). 동시에 매핑 누락(폴백으로 샘) 0을 보장.
    const mismatchIfHashOnly = GAME_NODE_ROLES.filter(
      ([id, role]) => hashFallback(id) !== role,
    ).length;
    // 23개 중 해시폴백이 기대와 어긋나는 id가 다수(>=10) → 그만큼 SEED가 교정하고 있음.
    expect(mismatchIfHashOnly).toBeGreaterThanOrEqual(10);
    // 그럼에도 pickRoleKey는 23개 전부 기대 역할(=SEED 매핑이 폴백을 가로채 교정).
    for (const [id, role] of GAME_NODE_ROLES) {
      expect(pickRoleKey(id)).toBe(role);
    }
  });

  it("미등록 game-like id는 해시폴백으로 안전(크래시 0·16 역할 멤버)", () => {
    const roleSet = new Set<RoleKey>(ROLE_KEYS);
    for (const id of ["n99_unknown", "asm_unknown", "game_nope"]) {
      expect(roleSet.has(pickRoleKey(id))).toBe(true);
      expect(pickRoleKey(id)).toBe(hashFallback(id)); // 미등록 → 폴백 경로.
    }
  });

  it("분업 5역할이 정확히 매핑됨(planner·data·secretary·dev·devops)", () => {
    const roles = new Set(GAME_NODE_ROLES.map(([, r]) => r));
    expect([...roles].sort()).toEqual(
      ["data", "dev", "devops", "planner", "secretary"].sort(),
    );
  });
});

// ── ★공간 엔진 (US-SE-1/2/3): 방·walkable·충돌·근접 — wanderZone(±책상박스) 대체 ──────

describe("★공간 US-SE-1 — roomForRole / ROLE_ROOM (16 역할 → 4 방)", () => {
  it("16 역할 전부 유효한 방 id로 매핑(누락 0)", () => {
    for (const role of ROLE_KEYS) {
      expect(ROOM_IDS).toContain(roomForRole(role));
    }
    expect(Object.keys(ROLE_ROOM)).toHaveLength(16);
  });

  it("4 구역 각 정확히 4역할(executive/operations/techdata/creative)", () => {
    const count: Record<string, number> = {};
    for (const role of ROLE_KEYS) {
      const r = roomForRole(role);
      count[r] = (count[r] ?? 0) + 1;
    }
    expect(count).toEqual({
      executive: 4,
      operations: 4,
      techdata: 4,
      creative: 4,
    });
  });

  it("ROLE_DESK_COORDS 4구역 주석과 정합(좌상=executive·우상=operations·좌하=techdata·우하=creative)", () => {
    expect(roomForRole("ceo")).toBe("executive");
    expect(roomForRole("pm")).toBe("operations");
    expect(roomForRole("dev")).toBe("techdata");
    expect(roomForRole("designer")).toBe("creative");
  });
});

describe("★공간 US-SE-1 — roomWalkableWorld / isWalkable (월드환산·경계·충돌)", () => {
  it("walkable 월드 = 네이티브 × MAP_SCALE", () => {
    const w = roomWalkableWorld("executive");
    expect(w).toEqual({
      x: ROOMS.executive.walkable.x * MAP_SCALE,
      y: ROOMS.executive.walkable.y * MAP_SCALE,
      w: ROOMS.executive.walkable.w * MAP_SCALE,
      h: ROOMS.executive.walkable.h * MAP_SCALE,
    });
  });

  it("walkable 안(장애물 밖)=true · walkable 밖=false", () => {
    const safe = roomSafeSpotWorld("executive");
    expect(isWalkable(safe.x, safe.y, "executive")).toBe(true);
    const w = roomWalkableWorld("executive");
    expect(isWalkable(w.x - 10, w.y - 10, "executive")).toBe(false); // walkable 밖.
  });

  it("obstacle 위 좌표=false(충돌·모든 방)", () => {
    for (const roomId of ROOM_IDS) {
      for (const o of roomObstaclesWorld(roomId)) {
        const midX = o.x + o.w / 2;
        const midY = o.y + o.h / 2;
        expect(isWalkable(midX, midY, roomId)).toBe(false);
      }
    }
  });
});

describe("★공간 US-SE-1 통합 — deskPlacements 16개 각자 자기 방 walkable∧¬obstacle (수기 좌표 정합)", () => {
  it("16 책상 좌표 전부 자기 방 walkable 안·obstacle 밖(returnHome 안전)", () => {
    for (const d of deskPlacements()) {
      const roomId = roomForRole(d.role);
      expect(isWalkable(d.x, d.y, roomId)).toBe(true);
    }
  });
});

describe("★공간 US-SE-2 — safeSpot 불변식(폴백 보장점)", () => {
  it("각 방 safeSpot은 자기 방 walkable∧¬obstacle(폴백 좌표 안전)", () => {
    for (const roomId of ROOM_IDS) {
      const safe = roomSafeSpotWorld(roomId);
      expect(isWalkable(safe.x, safe.y, roomId)).toBe(true);
    }
  });
});

describe("★맵수정 — findPath 다구간 경로 불변(전 waypoint walkable·segment 비관통)", () => {
  it("16역할 책상→방내 목표(다른 책상·safeSpot) findPath — 경로 obstacle 비관통·목표 도달", () => {
    for (const d of deskPlacements()) {
      const roomId = roomForRole(d.role);
      // 목표 후보: 방 safeSpot + 같은 방 다른 책상들(desk↔target 경로 검증·nextRoomWanderTarget 비의존).
      const targets: Array<{ x: number; y: number }> = [roomSafeSpotWorld(roomId)];
      for (const e of deskPlacements()) {
        if (roomForRole(e.role) === roomId && e.role !== d.role) {
          targets.push({ x: e.x, y: e.y });
        }
      }
      for (const t of targets) {
        const path = findPath(roomId, d.x, d.y, t.x, t.y);
        // ★전 waypoint가 walkable 셀(A* 4-연결 → 가구 내부 미진입·셀 단위 안전). 끝=정확한 목표.
        //   (셀 중심 간 segment가 가구 모서리에 접선할 수는 있으나 셀 walkable이라 내부 통과 0.
        //    엄격 segment-clear 라우팅은 "obstacle 반대편" findPath 테스트가 별도 검증.)
        for (const wp of path) {
          expect(isWalkable(wp.x, wp.y, roomId)).toBe(true);
        }
        const last = path[path.length - 1]!;
        expect(last.x).toBe(t.x);
        expect(last.y).toBe(t.y);
      }
    }
  });
});

describe("★맵수정 — segmentClear (선분-사각 충돌·지형 무시 차단)", () => {
  const OBST = [{ x: 100, y: 100, w: 50, h: 50 }]; // 사각 100~150 × 100~150.

  it("선분이 obstacle을 관통 → false", () => {
    expect(segmentClear(50, 125, 200, 125, OBST)).toBe(false); // 수평 관통.
    expect(segmentClear(125, 50, 125, 200, OBST)).toBe(false); // 수직 관통.
    expect(segmentClear(60, 60, 200, 200, OBST)).toBe(false); // 대각 관통.
  });

  it("선분이 obstacle 밖(비관통) → true", () => {
    expect(segmentClear(0, 0, 50, 50, OBST)).toBe(true); // 좌상 멀리.
    expect(segmentClear(0, 200, 80, 200, OBST)).toBe(true); // 아래로 비껴감.
    expect(segmentClear(160, 0, 160, 300, OBST)).toBe(true); // 우측으로 비껴감.
  });

  it("영길이 선분(시작=끝): 점이 안이면 false·밖이면 true", () => {
    expect(segmentClear(125, 125, 125, 125, OBST)).toBe(false); // 점이 사각 안.
    expect(segmentClear(10, 10, 10, 10, OBST)).toBe(true); // 점이 사각 밖.
  });

  it("경계 접선(모서리 스침)도 보수적으로 차단(false)", () => {
    expect(segmentClear(50, 100, 200, 100, OBST)).toBe(false); // 상변 따라 접촉.
    expect(segmentClear(100, 50, 100, 200, OBST)).toBe(false); // 좌변 따라 접촉.
  });

  it("obstacle 없음 → 항상 true", () => {
    expect(segmentClear(0, 0, 1000, 1000, [])).toBe(true);
  });

  it("실 방 obstacle 관통 검사(executive 중앙 가구 가로지름 → false)", () => {
    const obs = roomObstaclesWorld("executive");
    const r = obs[0]!;
    // 가구 중심을 좌→우로 가로지르는 선분.
    const cy = r.y + r.h / 2;
    expect(segmentClear(r.x - 20, cy, r.x + r.w + 20, cy, obs)).toBe(false);
  });
});

describe("★협업 presence — roomCentroid/meetingSpot/clusterOffset/collaborationTarget", () => {
  it("roomCentroid: 무게중심·빈배열 0", () => {
    expect(roomCentroid([{ x: 0, y: 0 }, { x: 10, y: 10 }])).toEqual({ x: 5, y: 5 });
    expect(roomCentroid([{ x: 4, y: 8 }])).toEqual({ x: 4, y: 8 });
    expect(roomCentroid([])).toEqual({ x: 0, y: 0 });
  });

  it("clusterOffset: n≤1→0·n=2 반대편·결정론", () => {
    expect(clusterOffset(0, 1)).toEqual({ dx: 0, dy: 0 });
    const a = clusterOffset(0, 2);
    const b = clusterOffset(1, 2);
    expect(a.dx).toBe(18); // i=0 → 각도0 → (R,0).
    expect(b.dx).toBe(-18); // i=1 → 각도π → (-R,0).
    expect(clusterOffset(1, 4)).toEqual(clusterOffset(1, 4)); // 결정론.
  });

  it("meetingSpot: walkable 무게중심은 그대로·가구 위 무게중심은 safeSpot 폴백", () => {
    // 단일 walkable 홈 → 그 점(무게중심=자기).
    expect(meetingSpot("executive", [{ x: 80, y: 180 }])).toEqual({ x: 80, y: 180 });
    // executive 4책상 무게중심은 중앙 obstacle 위 → safeSpot 폴백(walkable 보장).
    const homes = deskPlacements()
      .filter((d) => roomForRole(d.role) === "executive")
      .map((d) => ({ x: d.x, y: d.y }));
    const spot = meetingSpot("executive", homes);
    expect(isWalkable(spot.x, spot.y, "executive")).toBe(true);
  });

  it("collaborationTarget: 항상 walkable·결정론", () => {
    const homes = deskPlacements()
      .filter((d) => roomForRole(d.role) === "operations")
      .map((d) => ({ x: d.x, y: d.y }));
    for (let i = 0; i < homes.length; i += 1) {
      const t = collaborationTarget("operations", homes, i);
      expect(isWalkable(t.x, t.y, "operations")).toBe(true);
      expect(collaborationTarget("operations", homes, i)).toEqual(t); // 결정론.
    }
  });
});

describe("★맵수정 — findPath (A* obstacle 우회·#2)", () => {
  it("같은 셀(시작=목표) → [goal] 직행", () => {
    const path = findPath("executive", 80, 180, 80, 180);
    expect(path).toHaveLength(1);
    expect(path[0]).toEqual({ x: 80, y: 180 });
  });

  it("obstacle 반대편 목표 → 다중 waypoint 우회·전 waypoint walkable·연속 segment 비관통", () => {
    // executive 중앙 obstacle(월드 x176~376·y152~240) 좌측(80,180)→우측(440,180): 직선은 관통.
    const obstacles = roomObstaclesWorld("executive");
    expect(isWalkable(80, 180, "executive")).toBe(true);
    expect(isWalkable(440, 180, "executive")).toBe(true);
    expect(segmentClear(80, 180, 440, 180, obstacles)).toBe(false); // 직선은 관통(우회 필요).
    const path = findPath("executive", 80, 180, 440, 180);
    expect(path.length).toBeGreaterThan(1); // 우회 경로.
    for (const wp of path) {
      expect(isWalkable(wp.x, wp.y, "executive")).toBe(true);
    }
    let px = 80;
    let py = 180;
    for (const wp of path) {
      // 시작→첫 waypoint 포함 전 segment 비관통(인접 walkable 셀이라 보장).
      expect(segmentClear(px, py, wp.x, wp.y, obstacles)).toBe(true);
      px = wp.x;
      py = wp.y;
    }
    expect(path[path.length - 1]).toEqual({ x: 440, y: 180 });
  });

  it("결정론 — 같은 인자 → 같은 경로", () => {
    const p1 = findPath("techdata", 50, 360, 540, 360);
    const p2 = findPath("techdata", 50, 360, 540, 360);
    expect(p1).toEqual(p2);
  });
});

describe("★맵수정 — LPC walk 애니 헬퍼 (방향·프레임·에셋)", () => {
  it("walkDirection: 순수 4방향(우세축)", () => {
    expect(walkDirection(10, 0)).toBe("right");
    expect(walkDirection(-10, 0)).toBe("left");
    expect(walkDirection(0, 10)).toBe("down");
    expect(walkDirection(0, -10)).toBe("up");
  });

  it("walkDirection: 우세축 판정(|dx| vs |dy|)·tie는 좌우", () => {
    expect(walkDirection(10, 5)).toBe("right"); // dx 우세.
    expect(walkDirection(5, 10)).toBe("down"); // dy 우세.
    expect(walkDirection(-10, 5)).toBe("left");
    expect(walkDirection(5, -10)).toBe("up");
    expect(walkDirection(5, 5)).toBe("right"); // tie(|dx|>=|dy|) → 좌우 우선.
    expect(walkDirection(-5, 5)).toBe("left");
  });

  it("walkFrames: 방향별 col1~8 프레임 인덱스(행*13+col)", () => {
    expect(walkFrames("up")).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(walkFrames("left")).toEqual([14, 15, 16, 17, 18, 19, 20, 21]);
    expect(walkFrames("down")).toEqual([27, 28, 29, 30, 31, 32, 33, 34]);
    expect(walkFrames("right")).toEqual([40, 41, 42, 43, 44, 45, 46, 47]);
    // 13열×4행=52프레임 범위 내·col0(idle) 미포함.
    for (const dir of ["up", "left", "down", "right"] as const) {
      const f = walkFrames(dir);
      expect(f).toHaveLength(8);
      expect(Math.min(...f)).toBeGreaterThan(idleFrame(dir));
      expect(Math.max(...f)).toBeLessThan(52);
    }
  });

  it("idleFrame: 방향별 행 col0(정지)", () => {
    expect(idleFrame("up")).toBe(0);
    expect(idleFrame("left")).toBe(13);
    expect(idleFrame("down")).toBe(26);
    expect(idleFrame("right")).toBe(39);
  });

  it("roleWalkAsset: 키=`${role}_walk`·경로 정합", () => {
    expect(roleWalkAsset("ceo")).toEqual({
      key: "ceo_walk",
      path: "/assets/characters/roles_walk/ceo.png",
    });
  });
});

describe("★공간 US-SE-3 — nearbyPairs (근접쌍·대칭 dedup·결정론)", () => {
  it("반경 안 쌍만·대칭 dedup(같은 쌍 1회)", () => {
    const pos = [
      { role: "ceo" as RoleKey, x: 0, y: 0 },
      { role: "dev" as RoleKey, x: 10, y: 0 }, // ceo와 10px(근접).
      { role: "hr" as RoleKey, x: 500, y: 500 }, // 멀리.
    ];
    const pairs = nearbyPairs(pos, 60);
    expect(pairs).toHaveLength(1);
    expect(pairs[0]).toEqual(["ceo", "dev"]); // ROLE_KEYS 인덱스 순(ceo<dev).
  });

  it("쌍은 ROLE_KEYS 인덱스 오름차순 [a,b]로 정규화(입력 순서 무관)", () => {
    const pos = [
      { role: "dev" as RoleKey, x: 0, y: 0 }, // 입력은 dev 먼저.
      { role: "ceo" as RoleKey, x: 5, y: 0 }, // 그러나 ceo 인덱스가 작음.
    ];
    expect(nearbyPairs(pos, 60)[0]).toEqual(["ceo", "dev"]);
  });

  it("결정론(같은 입력 → 같은 출력)", () => {
    const pos = [
      { role: "ceo" as RoleKey, x: 0, y: 0 },
      { role: "dev" as RoleKey, x: 10, y: 0 },
      { role: "pm" as RoleKey, x: 20, y: 0 },
    ];
    expect(nearbyPairs(pos, 60)).toEqual(nearbyPairs(pos, 60));
  });

  it("빈/단일 입력 → 빈 배열(안전)", () => {
    expect(nearbyPairs([], 60)).toEqual([]);
    expect(nearbyPairs([{ role: "ceo" as RoleKey, x: 0, y: 0 }], 60)).toEqual([]);
  });

  it("반경 경계: 정확히 radius 거리는 포함(<=)·초과는 제외", () => {
    const at60 = [
      { role: "ceo" as RoleKey, x: 0, y: 0 },
      { role: "dev" as RoleKey, x: 60, y: 0 }, // 정확히 60.
    ];
    expect(nearbyPairs(at60, 60)).toHaveLength(1);
    const at61 = [
      { role: "ceo" as RoleKey, x: 0, y: 0 },
      { role: "dev" as RoleKey, x: 61, y: 0 }, // 61 > 60.
    ];
    expect(nearbyPairs(at61, 60)).toHaveLength(0);
  });
});
