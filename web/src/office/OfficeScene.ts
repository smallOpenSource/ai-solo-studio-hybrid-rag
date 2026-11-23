/**
 * OfficeScene — Phaser 오피스 씬 (office-v2 → ★P3 Stitch 단일플로어 도트맵).
 *
 * 라이프사이클(Phaser 공식 패턴):
 *   - preload(): floorplan 배경 이미지(Stitch·1024×572)·16 역할 Nexus PNG 로드.
 *   - create(): buildFloor(floorplan 배경 레이어) → placeRoles(4구역 책상 좌표에 16 역할
 *     정면 스프라이트·8방향 없음) → 카메라 bounds+드래그+휠줌 → EventBus 씬 핸들 노출 +
 *     WS office-event 구독(말풍선).
 *   - update(): 정적(이동 없음).
 *
 * ★P3 전환: 타일맵(office.tmj) 렌더 대신 Stitch가 그린 단일 오픈플로어 floorplan을 배경
 *   이미지로 깔고, ROLE_DESK_COORDS(수기 4구역 좌표·tmj 무관)에 Nexus 스프라이트를 앉힌다.
 *   ★캔버스 비의존 로직(책상 배치·역할 매핑·에셋 키)은 sceneLogic.ts로 분리해 단위 테스트.
 *   create/preload는 jsdom WebGL 미지원이라 e2e(:33006)에서 검증한다.
 *   데이터 코어(deriveScene/SceneState/sceneTypes/events.ts/office.tmj)는 0수정.
 */

import Phaser from "phaser";
import {
  EventBus,
  OFFICE_EVENT_CHANNEL,
  SCENE_READY_CHANNEL,
  AGENT_SELECTED_CHANNEL,
  AGENT_ACTIVITY_CHANNEL,
} from "./EventBus";
import type { OfficeEvent } from "../types/events";
import {
  SCENE_ASSETS,
  FLOORPLAN_WORLD,
  ROLE_KEYS,
  LPC,
  WALK_SCALE,
  WALK_SPEED,
  deskPlacements,
  bubbleTargetRole,
  labelLOD,
  labelScale,
  walkDirection,
  walkFrames,
  idleFrame,
  roleWalkAssets,
  findPath,
  collaborationTarget,
  roomForRole,
  nearbyPairs,
  PROXIMITY_RADIUS,
  type Dir,
  type RoomId,
  type RoleKey,
} from "./sceneLogic";
import {
  workingRoles,
  type AgentActivityStatus,
} from "./deriveAgentActivity";
import { fullLabel } from "./roleNames";

/** 이 씬의 고유 키(scene.start·EventBus 식별용). */
export const OFFICE_SCENE_KEY = "OfficeScene";

/** 드래그 1px당 카메라 줌 변화량(휠). */
const ZOOM_STEP = 0.001;
const ZOOM_MIN = 0.4;
const ZOOM_MAX = 2;

/**
 * 역할 라벨 핸들 — 이름(머리 위)·직책(발 아래) 컨테이너를 분리한다(이름↑/역할↓).
 * 각 컨테이너에 줌 역보정 setScale, 각 텍스트에 LOD 가시성을 적용한다.
 */
interface LabelHandle {
  nameContainer: Phaser.GameObjects.Container;
  titleContainer: Phaser.GameObjects.Container;
  nameText: Phaser.GameObjects.Text;
  titleText: Phaser.GameObjects.Text;
}

/**
 * ★G004 한 역할의 배회(walk) 런타임 상태 — SHUTDOWN에서 일괄 정지·정리(누수 0).
 *
 * 이동은 전부 Phaser 객체(tween/timer) 기반이라 재귀나 자바스크립트 루프가 없다(무한루프 0).
 * 다음 목표·대기시간 같은 결정은 sceneLogic 순수 함수가, 실제 트윈 구동/스케줄만 씬이 한다.
 */
interface WanderState {
  /** 책상 발밑 중심 X(월드 px·홈 좌표·working 복귀 지점). */
  readonly homeX: number;
  /** 책상 발밑 Y(월드 px·홈 좌표·bob 기준선). */
  readonly homeY: number;
  /** 이 역할이 속한 방 id(협업 그룹·findPath/collaborationTarget 조회용). */
  readonly roomId: RoomId;
  /** 보행 기준 X — 이동 tween이 이 값을 갱신, update()가 sprite.x로 반영(bob과 충돌 회피). */
  baseX: number;
  /** 보행 기준 Y — 이동 tween이 이 값을 갱신, update()가 sprite.y로 반영. */
  baseY: number;
  /** ★현재 이동 목표(중복 walk 방지·도착 판정). 기본=홈. */
  targetX: number;
  targetY: number;
  /** 현재 진행 중인 위치 이동 tween(baseX/baseY 대상·있으면). */
  moveTween?: Phaser.Tweens.Tween;
  /** ★이동 중 여부 — true면 walk 애니 재생, false면 idle 프레임(정적·둥둥 없음). */
  moving: boolean;
  /** ★현재 향하는 방향(LPC 4방향 walk/idle 프레임 선택·도착 시 idle 프레임). */
  facing: Dir;
  /** ★발밑 접지 그림자(이동/정지 공통·바닥 고정=bob 미적용·SHUTDOWN destroy). */
  shadow?: Phaser.GameObjects.Ellipse;
}

export class OfficeScene extends Phaser.Scene {
  /** 역할별 배치 스프라이트(LPC walk Sprite·4방향 애니·말풍선 위치·역할 타겟팅). */
  private readonly sprites = new Map<RoleKey, Phaser.GameObjects.Sprite>();
  /** 역할별 머리 위 이름/직책 라벨(US-03·줌 역보정+LOD 대상). */
  private readonly labels = new Map<RoleKey, LabelHandle>();
  /** 역할 머리 위 말풍선(office-event로 텍스트·위치 갱신). */
  private bubble?: Phaser.GameObjects.Text;
  /** 선택 역할 강조 브래킷(gold 사각·US-N4 selected-agent). */
  private selectionRect?: Phaser.GameObjects.Graphics;
  /** ★근접 큐 — 가까운 역할 쌍을 잇는 단일 Graphics(재사용·depth 500·SHUTDOWN destroy·누수 0). */
  private proximityGfx?: Phaser.GameObjects.Graphics;
  /** working 역할별 글로우 펄스 트윈(US-N8·해제 추적). */
  private readonly workingTweens = new Map<RoleKey, Phaser.Tweens.Tween>();
  /** ★역할별 presence 상태(책상 앵커·현재 목표·이동 tween — SHUTDOWN 일괄 정리). */
  private readonly wander = new Map<RoleKey, WanderState>();
  /** ★"지금 일하는" 역할 집합 — onAgentActivity가 갱신·setWorkingPresence가 이동 구동. */
  private workingSet = new Set<RoleKey>();
  /** EventBus 핸들러 참조(shutdown에서 해제 — 리스너 누수 0).
   *  ★레이스 가드: 뷰토글 remount 창/늦은 SHUTDOWN에서 죽은·미부팅 씬의 핸들러가 발화하면
   *  this.add/this.tweens가 null이라 "reading 'add'" 에러가 난다 → sys.isActive()로 차단. */
  private readonly onOfficeEvent = (ev: OfficeEvent): void => {
    if (!this.sys?.isActive()) return;
    this.handleOfficeEvent(ev);
  };
  /** 선택 역할 수신 핸들러(App/맵 클릭 → 선택 글로우). */
  private readonly onAgentSelected = (role: RoleKey): void => {
    if (!this.sys?.isActive()) return;
    this.applySelectionGlow(role);
  };
  /** 활동 상태 수신 핸들러(App 파생 → working 글로우). */
  private readonly onAgentActivity = (
    byRole: Record<RoleKey, AgentActivityStatus>,
  ): void => {
    if (!this.sys?.isActive()) return;
    const roles = workingRoles(byRole);
    this.setWorkingPresence(roles);
    this.applyWorkingGlow(roles);
  };

  constructor() {
    super(OFFICE_SCENE_KEY);
  }

  preload(): void {
    // ★P3: 단일플로어 배경 이미지(Stitch floorplan ×2). 타일맵 대신 이 이미지를 깐다(tmj 무관).
    this.load.image(SCENE_ASSETS.floorplan.key, SCENE_ASSETS.floorplan.path);
    // ★16 역할 LPC walk 스프라이트시트(키=`${role}_walk`·832×256·13열×4행·64px 프레임).
    //   gather.town급 4방향 walk 애니. 정적 qwen PNG 대체(진짜 보행 프레임).
    for (const a of roleWalkAssets()) {
      this.load.spritesheet(a.key, a.path, {
        frameWidth: LPC.frameW,
        frameHeight: LPC.frameH,
      });
    }
  }

  create(): void {
    this.buildFloor();
    this.createWalkAnims();
    this.placeRoles();
    this.setupCamera(FLOORPLAN_WORLD.width, FLOORPLAN_WORLD.height);
    this.prepareBubble();
    // EventBus로 WS OfficeEvent 수신(말풍선 갱신) + 선택/활동 수신(글로우).
    EventBus.on(OFFICE_EVENT_CHANNEL, this.onOfficeEvent);
    EventBus.on(AGENT_SELECTED_CHANNEL, this.onAgentSelected);
    EventBus.on(AGENT_ACTIVITY_CHANNEL, this.onAgentActivity);
    // 씬 종료 시 리스너 해제 + 글로우 트윈 정지(StrictMode/destroy 누수 0).
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      EventBus.off(OFFICE_EVENT_CHANNEL, this.onOfficeEvent);
      EventBus.off(AGENT_SELECTED_CHANNEL, this.onAgentSelected);
      EventBus.off(AGENT_ACTIVITY_CHANNEL, this.onAgentActivity);
      this.applyWorkingGlow([]); // 모든 펄스 트윈 정지·틴트 복원.
      this.stopAllWander(); // ★G004 모든 이동/bob tween·step timer 정지·해제(누수 0).
      this.proximityGfx?.destroy(); // ★근접 큐 Graphics 해제(뷰토글 remount 누수 0).
      this.proximityGfx = undefined;
    });
    // 씬 준비 완료 — PhaserGame이 받아 부모(ref)에 핸들 노출(공식 패턴).
    EventBus.emit(SCENE_READY_CHANNEL, this);
  }

  update(): void {
    // ★move tween이 갱신한 baseX/baseY를 sprite로 반영(+그림자·라벨). ★bob 제거 — idle은
    //   정적(둥둥 없음). 이동 모션은 LPC walk 프레임(beginWalk)이 담당. sys.isActive() 가드는
    //   Phaser가 활성 씬만 update하므로 불필요(SHUTDOWN 후 호출 안 됨).
    for (const [role, state] of this.wander) {
      const sprite = this.sprites.get(role);
      if (!sprite) {
        continue;
      }
      sprite.x = state.baseX;
      sprite.y = state.baseY; // ★정적 — bob/부유 없음(현실 반영: 자리에 가만히).
      if (state.shadow) {
        state.shadow.x = state.baseX;
        state.shadow.y = state.baseY; // 발밑 접지.
      }
      this.syncLabel(role, sprite);
    }
    this.drawProximity(); // ★근접 큐 갱신(단일 Graphics 재사용·근접쌍 선).
  }

  /**
   * ★G004 라벨 컨테이너를 스프라이트(이동 후) 위치로 동기화한다(placeLabel 오프셋 동일).
   *
   * 이름=머리 위(상단-4px)·직책=발 아래(+4px). 캐릭터가 배회하면 이름표도 따라 움직여야
   * 한다(정적 배치 회귀 방지). 줌 역보정 setScale은 applyLabelView가 별도로 관리.
   *
   * @param role 16 역할 키.
   * @param sprite 갱신된 스프라이트.
   */
  private syncLabel(role: RoleKey, sprite: Phaser.GameObjects.Sprite): void {
    const label = this.labels.get(role);
    if (!label) {
      return;
    }
    // 기준 높이(=프레임 고유높이×스케일) — walk 프레임 교체에도 라벨 위치 불흔들림(고유값 기반).
    label.nameContainer.setPosition(sprite.x, sprite.y - this.baseDisplayH(sprite) - 4);
    label.titleContainer.setPosition(sprite.x, sprite.y + 4);
  }

  /**
   * 기준 표시 높이/너비 — LPC walk 프레임(64×64)의 고유 height/width × WALK_SCALE(불변).
   * 라벨/말풍선/근접선/선택사각 앵커가 프레임 교체와 무관히 안정되도록 고유값 기반으로 계산한다.
   */
  private baseDisplayH(sprite: Phaser.GameObjects.Sprite): number {
    return sprite.height * WALK_SCALE;
  }

  private baseDisplayW(sprite: Phaser.GameObjects.Sprite): number {
    return sprite.width * WALK_SCALE;
  }

  /**
   * ★근접 큐 — 서로 가까운 역할 쌍을 흐린 선으로 잇는다(게더타운식 "근처 상호작용" 신호).
   *
   * 단일 Graphics를 재사용해(객체 폭증 0·selectionRect 선례) 매 프레임 clear 후 nearbyPairs
   * (순수)가 고른 근접쌍만 다시 그린다. depth 500 = 스프라이트(0)↑·라벨(1000)↓·말풍선(2000)↓
   * (이름표/말풍선 가림 0). SHUTDOWN once에서 destroy(뷰토글 remount 누수 0).
   */
  private drawProximity(): void {
    if (!this.proximityGfx) {
      this.proximityGfx = this.add.graphics().setDepth(500);
    }
    const g = this.proximityGfx;
    g.clear();
    const positions: { role: RoleKey; x: number; y: number }[] = [];
    for (const [role, sprite] of this.sprites) {
      positions.push({ role, x: sprite.x, y: sprite.y });
    }
    const pairs = nearbyPairs(positions, PROXIMITY_RADIUS);
    if (pairs.length === 0) {
      return; // 근접쌍 없음 — 빈 그래픽(이미 clear).
    }
    g.lineStyle(1, 0x7fdfff, 0.5); // 흐린 청록 — 근접 상호작용 큐(은은히).
    for (const [a, b] of pairs) {
      const sa = this.sprites.get(a);
      const sb = this.sprites.get(b);
      if (sa && sb) {
        // 몸통 중앙끼리 잇는다(발밑 origin 0.5,1이라 -기준높이/2·squash 무관 디커플).
        g.lineBetween(
          sa.x,
          sa.y - this.baseDisplayH(sa) / 2,
          sb.x,
          sb.y - this.baseDisplayH(sb) / 2,
        );
      }
    }
  }

  /** ★P3: Stitch floorplan(단일 오픈플로어)을 배경 이미지 레이어로 깐다(좌상단 정렬·맨 뒤). */
  private buildFloor(): void {
    const floor = this.add
      .image(0, 0, SCENE_ASSETS.floorplan.key)
      .setOrigin(0, 0)
      .setDepth(-10); // 스프라이트(0)·라벨(1000)·말풍선(2000) 아래.
    floor.texture.setFilter(Phaser.Textures.FilterMode.NEAREST); // 도트 선명.
  }

  /** ★16 역할 LPC walk Sprite를 4구역 책상 좌표에 앉힌다(기본 idle-down 프레임·4방향 애니). */
  private placeRoles(): void {
    for (const spot of deskPlacements()) {
      // walk 스프라이트시트(키=`${role}_walk`)·기본 정면(idle-down) 프레임으로 생성.
      const sprite = this.add.sprite(spot.x, spot.y, `${spot.role}_walk`, idleFrame("down"));
      sprite.setOrigin(0.5, 1); // 중심 X·발밑 Y(책상 좌표와 짝).
      sprite.setScale(WALK_SCALE); // LPC 64px → 책상 비례(가시높이 qwen 근사).
      sprite.texture.setFilter(Phaser.Textures.FilterMode.NEAREST); // 도트 선명.
      // ★US-N4: 클릭 가능 — pointerdown 시 선택 역할을 EventBus로 알린다(App·씬 글로우).
      sprite.setInteractive({ useHandCursor: true });
      sprite.on(Phaser.Input.Events.POINTER_DOWN, () => {
        EventBus.emit(AGENT_SELECTED_CHANNEL, spot.role);
      });
      this.sprites.set(spot.role, sprite);
      this.placeLabel(spot.role, sprite);
      this.initWander(spot.role, spot.x, spot.y);
    }
    // 라벨 역보정·LOD 초기 적용은 setupCamera(fit 줌 확정 후)가 담당.
  }

  /**
   * ★16 역할 × 4방향 LPC walk 애니를 등록한다(create 시 텍스처 로드 후 1회).
   *
   * 각 역할 스프라이트시트(`${role}_walk`)에서 walkFrames(dir)(col1~8)로 4방향 walk 애니
   * (`${role}-walk-${dir}`)를 만든다. anims는 게임 수명(전역)이라 remount 시 재생성 — exists
   * 가드로 중복 등록 에러를 막는다. idle은 setFrame(idleFrame(dir))으로 처리(애니 불요).
   */
  private createWalkAnims(): void {
    const dirs: Dir[] = ["up", "left", "down", "right"];
    for (const role of ROLE_KEYS) {
      for (const dir of dirs) {
        const key = `${role}-walk-${dir}`;
        if (this.anims.exists(key)) {
          continue;
        }
        this.anims.create({
          key,
          frames: this.anims.generateFrameNumbers(`${role}_walk`, {
            frames: walkFrames(dir),
          }),
          frameRate: 10,
          repeat: -1,
        });
      }
    }
  }

  /**
   * ★역할 presence 상태 초기화 — 책상 앵커 + 접지 그림자. ★배회·bob 없음(idle은 책상 정지).
   *   이동은 setWorkingPresence(협업 이벤트)가 walkTo로 구동한다(현실 반영: 평소엔 자리에).
   * @param role 16 역할 키. @param x 책상 발밑 X(월드). @param y 책상 발밑 Y(월드).
   */
  private initWander(role: RoleKey, x: number, y: number): void {
    const state: WanderState = {
      homeX: x,
      homeY: y,
      roomId: roomForRole(role),
      baseX: x,
      baseY: y,
      targetX: x,
      targetY: y,
      moving: false,
      facing: "down",
    };
    // ★접지 그림자: 발밑 바닥 고정 반투명 타원(depth -5: floor(-10)↑·sprite(0)↓). SHUTDOWN destroy.
    const sprite = this.sprites.get(role);
    const shadowW = sprite ? sprite.width * WALK_SCALE * 0.55 : 24;
    state.shadow = this.add.ellipse(x, y, shadowW, 7, 0x000000, 0.22).setDepth(-5);
    this.wander.set(role, state);
    // ★배회 스케줄 없음 — 책상에 가만히. 협업 이동만 setWorkingPresence가 트리거.
  }

  /**
   * ★한 역할을 (tx,ty)로 걷게 한다 — findPath(A* obstacle 우회) + walkPath(LPC walk).
   *   현재 목표와 같으면 무시(중복 walk 0)·이미 도착해 있으면 idle 프레임 고정.
   * @param role 16 역할 키. @param tx 목표 X(월드). @param ty 목표 Y(월드).
   */
  private walkTo(role: RoleKey, tx: number, ty: number): void {
    const state = this.wander.get(role);
    const sprite = this.sprites.get(role);
    if (!state || !sprite) {
      return;
    }
    if (state.targetX === tx && state.targetY === ty) {
      return; // 이미 그 목표로 향함/도착 — 재이동 안 함.
    }
    state.targetX = tx;
    state.targetY = ty;
    state.moveTween?.stop();
    if (Math.hypot(tx - state.baseX, ty - state.baseY) < 2) {
      this.endMove(role); // 이미 도착 — idle 고정.
      return;
    }
    const path = findPath(state.roomId, state.baseX, state.baseY, tx, ty);
    this.walkPath(role, path, () => {
      /* 도착 — walkPath가 endMove로 idle 프레임 고정. */
    });
  }

  /**
   * ★A* 경로(waypoint 목록)를 따라 segment별로 걷는다(obstacle 우회·#2).
   *
   * 각 segment: walkDirection으로 방향 walk 애니 재생 + baseX/baseY를 등속(WALK_SPEED) tween.
   * onComplete에서 다음 waypoint로 진행(tween 체인·JS 루프/재귀 0). 마지막 도착 시 endMove
   * (idle 프레임) + onDone. sys.isActive 가드(죽은 씬 차단)·moveTween 단일(중복 0).
   */
  private walkPath(
    role: RoleKey,
    waypoints: Array<{ x: number; y: number }>,
    onDone: () => void,
  ): void {
    let i = 0;
    const stepTo = (): void => {
      if (!this.sys?.isActive()) {
        return;
      }
      const state = this.wander.get(role);
      if (!state) {
        return;
      }
      if (i >= waypoints.length) {
        this.endMove(role);
        onDone();
        return;
      }
      const wp = waypoints[i]!;
      const dist = Math.hypot(wp.x - state.baseX, wp.y - state.baseY);
      this.beginWalk(role, walkDirection(wp.x - state.baseX, wp.y - state.baseY));
      state.moveTween?.stop();
      state.moveTween = this.tweens.add({
        targets: state,
        baseX: wp.x,
        baseY: wp.y,
        duration: Math.max(80, dist / WALK_SPEED),
        ease: "Linear",
        onComplete: () => {
          i += 1;
          stepTo();
        },
      });
    };
    stepTo();
  }

  /** ★이동 시작 — moving=true·facing 갱신·해당 방향 walk 애니 재생(같은 애니면 재시작 안 함). */
  private beginWalk(role: RoleKey, dir: Dir): void {
    const state = this.wander.get(role);
    const sprite = this.sprites.get(role);
    if (!state || !sprite) {
      return;
    }
    state.moving = true;
    state.facing = dir;
    sprite.play(`${role}-walk-${dir}`, true); // 2번째 인자=ignoreIfPlaying.
  }

  /** ★이동 종료 — moving 해제·walk 애니 정지·facing 방향 idle(정지) 프레임으로 고정. */
  private endMove(role: RoleKey): void {
    const state = this.wander.get(role);
    const sprite = this.sprites.get(role);
    if (!state || !sprite) {
      return;
    }
    state.moving = false;
    sprite.stop();
    sprite.setFrame(idleFrame(state.facing));
  }

  /**
   * ★모든 역할의 이동 tween을 정지하고 그림자를 해제한다(SHUTDOWN 누수 0).
   * 죽은 씬에서 tween/콜백이 살아남아 this.tweens(null)에 접근하는 사고를 막는다.
   */
  private stopAllWander(): void {
    for (const state of this.wander.values()) {
      state.moveTween?.stop();
      state.shadow?.destroy(); // ★접지 그림자 GameObject 해제(뷰토글 remount 누수 0).
    }
    this.wander.clear();
  }

  /**
   * 한 역할의 이름(머리 위)·직책(발 아래) 라벨을 만든다(줌 역보정/LOD 대상).
   *
   * 이름과 직책을 서로 다른 컨테이너로 분리해 머리 위/발 아래에 각각 둔다.
   * 두 텍스트 모두 ★배경 블록 없음(transparent): 흰/밝은 글자 + 검정 외곽선(stroke) +
   * 그림자(shadow)로 어느 바닥색에서도 가독. backgroundColor 절대 추가 금지(요구의 핵심).
   */
  private placeLabel(role: RoleKey, img: Phaser.GameObjects.Image): void {
    const { name, title } = fullLabel(role);
    // 이름(머리 위): origin 0.5,1 → 컨테이너 원점 위로 자란다.
    const nameText = this.add
      .text(0, 0, name, {
        fontSize: "12px",
        color: "#ffffff",
        stroke: "#000000",
        strokeThickness: 3,
      })
      .setOrigin(0.5, 1);
    nameText.setShadow(0, 1, "#000000", 2, false, true); // 가독성 보강(배경 대신 그림자).
    // 직책(발 아래): origin 0.5,0 → 컨테이너 원점 아래로 자란다. 동일 원칙·약간 작게.
    const titleText = this.add
      .text(0, 0, title, {
        fontSize: "10px",
        color: "#eeeeee",
        stroke: "#000000",
        strokeThickness: 2,
      })
      .setOrigin(0.5, 0);
    titleText.setShadow(0, 1, "#000000", 2, false, true);
    // 이름 컨테이너 = 머리 위(스프라이트 상단보다 4px 위).
    const nameContainer = this.add
      .container(img.x, img.y - img.displayHeight - 4, [nameText])
      .setDepth(1000); // 스프라이트 위·말풍선(2000) 아래.
    // 직책 컨테이너 = 발 아래(img.y는 발밑·origin 0.5,1 기준 → +4px 아래).
    const titleContainer = this.add
      .container(img.x, img.y + 4, [titleText])
      .setDepth(1000);
    this.labels.set(role, { nameContainer, titleContainer, nameText, titleText });
  }

  /** 카메라 bounds(맵 전체)·진입 fit 줌·드래그 스크롤·휠 줌을 건다(가로 와이드 맵 전체 조망). */
  private setupCamera(worldW: number, worldH: number): void {
    const cam = this.cameras.main;
    cam.setBounds(0, 0, worldW, worldH);

    // 진입 fit: 캔버스에 월드 전체가 들어오는 줌(가로/세로 중 작은 비율) + 중앙 정렬.
    const canvasW = this.scale.width || 720;
    const canvasH = this.scale.height || 640;
    const fitZoom = Math.min(canvasW / worldW, canvasH / worldH);
    cam.setZoom(Math.max(fitZoom, ZOOM_MIN)); // ZOOM_MIN 하한(setZoom(0) 금지).
    cam.centerOn(worldW / 2, worldH / 2);
    this.applyLabelView(cam.zoom); // fit 줌 기준 라벨 역보정·LOD.

    this.input.on(
      Phaser.Input.Events.POINTER_MOVE,
      (p: Phaser.Input.Pointer) => {
        if (!p.isDown) {
          return;
        }
        cam.scrollX -= (p.x - p.prevPosition.x) / cam.zoom;
        cam.scrollY -= (p.y - p.prevPosition.y) / cam.zoom;
      }
    );
    // 휠: 일반=상하(트랙패드 가로=좌우) 패닝, Ctrl/⌘+휠=줌. 네이티브 리스너({passive:false})로
    // 브라우저 기본 스크롤/페이지줌을 막고 카메라만 제어한다(SHUTDOWN에서 해제·누수 0).
    const canvas = this.sys.game.canvas;
    const onWheel = (e: WheelEvent): void => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        // Ctrl/⌘+휠 = 줌(기존 동작 보존).
        const next = Phaser.Math.Clamp(cam.zoom - e.deltaY * ZOOM_STEP, ZOOM_MIN, ZOOM_MAX);
        cam.setZoom(next);
        this.applyLabelView(next); // 줌 변경 직후 라벨 역보정·LOD 재적용.
        return;
      }
      // 일반 휠 = 상하 패닝(요구: "위 아래로만 스크롤"). bounds는 Phaser가 클램프.
      cam.scrollY += e.deltaY;
      if (e.deltaX !== 0) {
        cam.scrollX += e.deltaX; // 트랙패드 가로 스와이프 = 좌우 패닝.
      }
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    // 브라우저 리사이즈 시 fitZoom 재계산 + 라벨 역보정 갱신(Scale.RESIZE 반영).
    const onResize = (): void => {
      const cw = this.scale.width;
      const ch = this.scale.height;
      const newFit = Math.min(cw / worldW, ch / worldH);
      const newZoom = Math.max(newFit, ZOOM_MIN);
      cam.setZoom(newZoom);
      cam.centerOn(worldW / 2, worldH / 2);
      this.applyLabelView(newZoom);
    };
    this.scale.on(Phaser.Scale.Events.RESIZE, onResize);
    // 입력 리스너 해제(SHUTDOWN): 휠(네이티브 canvas) + RESIZE(ScaleManager는 게임 수명이라
    // 미해제 시 씬 재생성마다 누적 — StrictMode 더블마운트/뷰토글 누수 0).
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      canvas.removeEventListener("wheel", onWheel);
      this.scale.off(Phaser.Scale.Events.RESIZE, onResize);
    });
  }

  /**
   * 라벨에 줌 역보정(화면 픽셀 유지)과 LOD 가시성을 적용한다.
   *
   * - 역보정: 이름/직책 컨테이너 setScale(labelScale(zoom)=1/zoom) → 줌 무관 화면 픽셀.
   * - LOD: labelLOD(zoom)으로 이름/직책 가시성(zoom<0.4 둘다 hide).
   *
   * @param zoom 현재 카메라 줌.
   */
  private applyLabelView(zoom: number): void {
    const scale = labelScale(zoom);
    const { showName, showTitle } = labelLOD(zoom);
    for (const {
      nameContainer,
      titleContainer,
      nameText,
      titleText,
    } of this.labels.values()) {
      nameContainer.setScale(scale);
      titleContainer.setScale(scale);
      nameText.setVisible(showName);
      titleText.setVisible(showTitle);
    }
  }

  /**
   * 기본 역할(ceo) 머리 위에 말풍선을 준비한다(office-event로 텍스트·위치 갱신).
   *
   * ★흰 배경 블록 제거(요구: CEO 머리 위 흰 박스 없애기). 라벨과 동일 원칙으로
   * 흰 글자 + 검정 외곽선 + 그림자만 쓰고, 발화가 없으면 숨겨 빈 박스를 없앤다.
   */
  private prepareBubble(): void {
    const anchor = this.sprites.get("ceo");
    const x = anchor?.x ?? 0;
    const y = anchor ? anchor.y - anchor.displayHeight : 0;
    this.bubble = this.add
      .text(x, y, "", {
        fontSize: "12px",
        color: "#ffffff",
        stroke: "#000000",
        strokeThickness: 3,
      })
      .setOrigin(0.5, 1)
      .setDepth(2000) // 라벨(1000) 위 — 말풍선이 이름표를 가리지 않도록.
      .setVisible(false); // 발화 도착 전까지 숨김(빈 흰 박스 제거).
    this.bubble.setShadow(0, 1, "#000000", 2, false, true);
  }

  /** WS OfficeEvent 반영: 노드 역할 위로 말풍선을 옮기고 TEXT_MESSAGE면 텍스트 갱신. */
  private handleOfficeEvent(ev: OfficeEvent): void {
    if (!this.bubble) {
      return;
    }
    const role = bubbleTargetRole(ev as { type: string; node_id?: string });
    const sprite = this.sprites.get(role);
    if (sprite) {
      this.bubble.setPosition(sprite.x, sprite.y - this.baseDisplayH(sprite));
    }
    if (ev.type === "TEXT_MESSAGE") {
      // 말풍선은 짧게(앞 24자) — 긴 발화는 채팅 패널이 전담. 빈 발화면 숨김(흰 잔상 0).
      const text = ev.content.slice(0, 24);
      this.bubble.setText(text);
      this.bubble.setVisible(text.length > 0);
    }
  }

  /**
   * ★US-N4: 선택 역할 스프라이트에 gold 브래킷(사각 테두리)을 그린다(이전 선택 대체).
   *
   * 단일 Graphics를 재사용해 선택 스프라이트 둘레에 사각을 다시 그린다(이동/8-dir 없음·
   * 시각만). 미지 역할이면 무시한다(방어).
   */
  private applySelectionGlow(role: RoleKey): void {
    const sprite = this.sprites.get(role);
    if (!sprite) {
      return;
    }
    if (!this.selectionRect) {
      this.selectionRect = this.add.graphics().setDepth(1500); // 라벨(1000)↑·말풍선(2000)↓.
    }
    const g = this.selectionRect;
    g.clear();
    g.lineStyle(2, 0xffd700, 1); // gold 테두리.
    const w = this.baseDisplayW(sprite);
    const h = this.baseDisplayH(sprite);
    // origin(0.5,1) 기준 좌상단 = (x - w/2, y - h). 2px 여유로 감싼다(squash 무관 기준높이).
    g.strokeRect(sprite.x - w / 2 - 2, sprite.y - h - 2, w + 4, h + 4);
  }

  /**
   * ★presence 이동 드라이버(현실 반영) — working 집합 변화 시 전 역할의 목표를 재계산해 walkTo.
   *
   * - idle(미working): 책상(home)에 가만히.
   * - working이지만 같은 방에 혼자: 책상에서 근무(이동 없음).
   * - ★같은 방 working ≥2(협업): 책상 무게중심(미팅스폿) 근처로 모인다(서로 다가감).
   * working이 끝나면 다시 책상으로. 이동은 walkTo(findPath A*+LPC walk·중복 walk 가드).
   * 상태원천=AGENT_ACTIVITY(새 이벤트 0)·무작위 배회 없음(자리 정지).
   *
   * @param roles working 역할 키 배열(workingRoles(byRole) 결과).
   */
  private setWorkingPresence(roles: ReadonlyArray<RoleKey>): void {
    this.workingSet = new Set<RoleKey>(roles);
    // 같은 방 working 역할 그룹(협업 판정·방별).
    const byRoom = new Map<RoomId, RoleKey[]>();
    for (const role of this.workingSet) {
      const rid = roomForRole(role);
      const list = byRoom.get(rid);
      if (list) {
        list.push(role);
      } else {
        byRoom.set(rid, [role]);
      }
    }
    // 16 역할 전체 목표 재계산 → walkTo(현재 목표와 같으면 무시·중복 walk 0).
    for (const [role, state] of this.wander) {
      let tx = state.homeX;
      let ty = state.homeY;
      if (this.workingSet.has(role)) {
        const peers = byRoom.get(state.roomId) ?? [];
        if (peers.length >= 2) {
          // ★협업 — 같은 방 working 책상들의 미팅스폿+클러스터 오프셋으로 모인다(다가감).
          const homes = peers.map((r) => {
            const s = this.wander.get(r);
            return { x: s ? s.homeX : 0, y: s ? s.homeY : 0 };
          });
          const t = collaborationTarget(state.roomId, homes, peers.indexOf(role));
          tx = t.x;
          ty = t.y;
        }
        // peers.length === 1 → 혼자 working: 책상 유지(tx,ty=home).
      }
      this.walkTo(role, tx, ty);
    }
  }

  /**
   * ★US-N8: "지금 일하는" 역할 스프라이트에 글로우 펄스(green 틴트 + 알파 yoyo)를 건다.
   *
   * roles 집합에 든 역할은 펄스를 켜고(없던 것만 생성), 빠진 역할은 정지·틴트/알파 복원한다.
   * ★이동/8-dir/y-bob 없음(자산 부재·자원·결정론). 시간은 Phaser 트윈 클록(내부 Date.now 0).
   *
   * @param roles working 역할 키 배열(workingRoles(byRole) 결과).
   */
  private applyWorkingGlow(roles: ReadonlyArray<RoleKey>): void {
    // 가드는 호출처(onAgentActivity·SHUTDOWN)에서 책임진다. SHUTDOWN은 비활성 직전에
    // applyWorkingGlow([])로 직접 정리하므로 여기선 sys.isActive() 가드를 두지 않는다.
    const working = new Set<RoleKey>(roles);
    for (const [role, sprite] of this.sprites) {
      const isWorking = working.has(role);
      const tween = this.workingTweens.get(role);
      if (isWorking && !tween) {
        sprite.setTint(0x39ff14); // pixel green = 작업중.
        const t = this.tweens.add({
          targets: sprite,
          alpha: { from: 1, to: 0.55 },
          duration: 600,
          yoyo: true,
          repeat: -1,
        });
        this.workingTweens.set(role, t);
      } else if (!isWorking && tween) {
        tween.stop();
        this.workingTweens.delete(role);
        sprite.clearTint();
        sprite.setAlpha(1);
      }
    }
  }
}
