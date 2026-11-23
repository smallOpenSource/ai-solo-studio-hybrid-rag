/**
 * PhaserGame — React↔Phaser 브리지 (office-v2 US-0d, 공식 template-react-ts 패턴).
 *
 * ★StrictMode 이중마운트 가드(현 TwoDStage 가드의 교훈 이식):
 *   - useEffect에서 `new Phaser.Game(config)` 생성, **cleanup에서 `game.destroy(true)`**.
 *   - 생성 직전 기존 game이 살아있으면 먼저 파괴해 **단일 인스턴스만 살아남게** 한다
 *     (StrictMode의 mount→unmount→remount에서 WebGL 컨텍스트/리스너 누수 0 — S1 AC).
 *
 * `current-scene-ready`(OfficeScene.create가 EventBus로 발행)를 받아 부모(ref)에
 * 씬 핸들을 노출한다(공식 패턴 — React가 씬에 명령·조회 가능).
 *
 * ★jsdom은 WebGL 미지원이라 Phaser.Game 실제 생성은 e2e(US-0g)에서 검증한다.
 *   단위 테스트는 EventBus/OfficeScene 로직(sceneLogic)만 다룬다.
 */

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import Phaser from "phaser";
import { EventBus, SCENE_READY_CHANNEL } from "./EventBus";
import { OfficeScene } from "./OfficeScene";

// ── 캔버스 크기 초기값(UI-1 반응형) ──
// ★고정 치수가 아니라 "초기값"이다: 부모 div의 clientWidth/clientHeight로 게임을 생성하고
//   (0이면 이 값으로 폴백), 이후 ResizeObserver가 부모 크기 변화를 game.scale.resize로 반영.
//   Scale.RESIZE 모드라 캔버스가 부모를 가득 채운다(레터박스 없음).

const INIT_WIDTH = 720;
const INIT_HEIGHT = 640;
/** 배경색(Nexus OS 다크 캔버스·N-2) — 셸-캔버스 톤 일치. #1A1A1A=nexusTheme black.
 *  ★OfficeScene엔 backgroundColor 추가 금지(기존 요구) — 캔버스 클리어색만 다크화.
 *  바닥 자체의 다크화는 P3(floorplan_nochar 이미지 레이어)에서. */
const GAME_BG = "#1A1A1A";

/** ref로 노출하는 핸들 — 살아있는 game/scene(부모가 명령·조회). */
export interface PhaserGameHandle {
  game: Phaser.Game | null;
  scene: Phaser.Scene | null;
}

/**
 * Phaser.Game 설정을 만든다(parent div에 부착·OfficeScene 1개·pixelArt).
 * @param parent canvas를 붙일 컨테이너 div.
 */
function buildGameConfig(parent: HTMLElement): Phaser.Types.Core.GameConfig {
  // 부모 실측(0이면 초기값 폴백). Scale.RESIZE라 이후 ResizeObserver가 추적한다.
  const width = parent.clientWidth || INIT_WIDTH;
  const height = parent.clientHeight || INIT_HEIGHT;
  return {
    type: Phaser.AUTO,
    parent,
    width,
    height,
    backgroundColor: GAME_BG,
    pixelArt: true, // NEAREST 전역 — 도트 선명(¾탑다운 LPC·타일/스프라이트).
    roundPixels: true, // 서브픽셀 흐림 제거(드래그 스크롤·줌 시 도트 정합).
    scene: [OfficeScene],
    scale: {
      // ★UI-1: FIT → RESIZE(부모 충전·레터박스 제거). autoCenter는 RESIZE에선 불요(캔버스가
      //   부모 크기와 동일해지므로 중앙정렬 대상 여백이 없다) → NO_CENTER.
      mode: Phaser.Scale.RESIZE,
      autoCenter: Phaser.Scale.NO_CENTER,
    },
  };
}

/**
 * Phaser 캔버스를 마운트하는 forwardRef 컴포넌트.
 * StrictMode 이중마운트에서도 단일 game 인스턴스만 살아남도록 가드한다.
 */
export const PhaserGame = forwardRef<PhaserGameHandle>(function PhaserGame(_props, ref) {
  /** canvas를 붙일 컨테이너 div. */
  const containerRef = useRef<HTMLDivElement>(null);
  /** 살아있는 Phaser.Game(React 상태 밖 — 재생성 가드). */
  const gameRef = useRef<Phaser.Game | null>(null);
  /** 현재 준비된 씬 핸들(current-scene-ready 수신 시 갱신). */
  const sceneRef = useRef<Phaser.Scene | null>(null);

  // 부모에 game/scene 핸들 노출(공식 패턴 — ref.current.game / .scene).
  useImperativeHandle(
    ref,
    () => ({
      get game() {
        return gameRef.current;
      },
      get scene() {
        return sceneRef.current;
      },
    }),
    []
  );

  useEffect(() => {
    const parent = containerRef.current;
    if (!parent) {
      return;
    }

    // ★이중마운트 가드: 기존 game이 살아있으면 먼저 파괴(단일 인스턴스 보장).
    if (gameRef.current) {
      gameRef.current.destroy(true);
      gameRef.current = null;
    }

    // current-scene-ready: OfficeScene.create가 발행 → 부모(ref)에 씬 노출.
    const onSceneReady = (scene: Phaser.Scene): void => {
      sceneRef.current = scene;
    };
    EventBus.on(SCENE_READY_CHANNEL, onSceneReady);

    gameRef.current = new Phaser.Game(buildGameConfig(parent));

    // ★탭 복귀/WebGL 컨텍스트 복원 시 "빈 캔버스" 방어선(idempotent resize+refresh).
    //   연구(phaser@3.90 소스): 탭 hidden→visible 루프 pause/resume·컨텍스트 손실→복원은 Phaser가
    //   자동 처리하나, 복귀 시점 React 리사이즈 경로가 캔버스 크기를 0/이전값으로 고착시킬 수 있다.
    //   RESUME(탭 복귀)·RESTORE_WEBGL(컨텍스트 복원)에서 부모 실측>0이면 resize+refresh를 1회 한다.
    //   ★게임/렌더러 수명 리스너 → off는 useEffect cleanup에서만(씬 SHUTDOWN 아님·게임보다 김).
    const onResumeOrRestore = (): void => {
      if (!gameRef.current) {
        return;
      }
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      if (w > 0 && h > 0) {
        gameRef.current.scale.resize(w, h);
        gameRef.current.scale.refresh();
      }
    };
    // game.events는 생성 즉시 존재(RESUME 등록 안전). renderer는 boot 후 준비 → 아래 rAF에서 등록.
    gameRef.current.events.on(Phaser.Core.Events.RESUME, onResumeOrRestore);

    // ★BUG-001/BUG-007 수정: game 생성 직후 rAF로 레이아웃 settle 대기 후 부모 실측 재측정.
    //   생성 시점 parent.clientWidth=0(display:none 또는 아직 레이아웃 미확정)이었을 때
    //   초기값(720×640)으로 폴백된 캔버스를 실제 치수로 1회 보정한다.
    requestAnimationFrame(() => {
      if (!gameRef.current) {
        return;
      }
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      if (w > 0 && h > 0) {
        gameRef.current.scale.resize(w, h);
      }
      // renderer는 boot 후 준비됨(이 rAF 시점) → 컨텍스트 복원 핸들러 등록(WebGL만 emit·Canvas는 휴면).
      gameRef.current.renderer?.on(
        Phaser.Renderer.Events.RESTORE_WEBGL,
        onResumeOrRestore,
      );
    });

    // ★BUG-001/BUG-007 수정: IntersectionObserver로 컨테이너가 display:none→가시 전환될 때
    //   부모 실측 재측정+scale.resize 강제(섹션/뷰토글로 다시 보일 때마다 0 고착 방지).
    //   가시 전환 직후에도 clientWidth=0인 경우 rAF로 최대 MAX_RAF_RETRY회 재시도(무한루프 방지).
    const MAX_RAF_RETRY = 3;
    const resizeToParent = (retryCount = 0): void => {
      if (!gameRef.current) {
        return;
      }
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      if (w > 0 && h > 0) {
        gameRef.current.scale.resize(w, h);
      } else if (retryCount < MAX_RAF_RETRY) {
        requestAnimationFrame(() => {
          resizeToParent(retryCount + 1);
        });
      }
    };
    const intersectionObserver = new IntersectionObserver((entries) => {
      if (!gameRef.current) {
        return;
      }
      const entry = entries[0];
      // isIntersecting=true: display:none→block(flex) 전환으로 가시 영역 진입
      if (entry?.isIntersecting) {
        resizeToParent();
      }
    });
    intersectionObserver.observe(parent);

    // ★UI-1 반응형: 부모 div 크기 변화를 관찰해 game.scale.resize로 캔버스를 부모에 맞춘다.
    //   resize → OfficeScene의 RESIZE 핸들러(fitZoom 재계산·centerOn·applyLabelView)가 작동.
    const resizeObserver = new ResizeObserver((entries) => {
      // ★레이스 가드(StrictMode/destroy 후 콜백): game이 없으면 즉시 반환(destroy 가드와 동형).
      if (!gameRef.current) {
        return;
      }
      const entry = entries[0];
      if (!entry) {
        return;
      }
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) {
        gameRef.current.scale.resize(width, height);
      }
    });
    resizeObserver.observe(parent);

    // cleanup: observer 해제 + game.destroy(true)로 WebGL 컨텍스트·텍스처·리스너 해제(누수 0).
    return () => {
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
      EventBus.off(SCENE_READY_CHANNEL, onSceneReady);
      if (gameRef.current) {
        // ★게임/렌더러 수명 리스너 해제(destroy가 정리하나 명시 off로 누수 0·StrictMode remount 안전).
        gameRef.current.events.off(Phaser.Core.Events.RESUME, onResumeOrRestore);
        gameRef.current.renderer?.off(
          Phaser.Renderer.Events.RESTORE_WEBGL,
          onResumeOrRestore,
        );
        gameRef.current.destroy(true);
        gameRef.current = null;
      }
      sceneRef.current = null;
    };
  }, []);

  // 부모를 가득 채우는 컨테이너(Scale.RESIZE가 이 div 치수로 캔버스를 그린다).
  return (
    <div
      ref={containerRef}
      data-testid="phaser-game"
      style={{ width: "100%", height: "100%" }}
    />
  );
});
