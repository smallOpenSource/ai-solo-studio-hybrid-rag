/**
 * scene.ts — Phaser 바인딩 (G005 US-G5-6 A2).
 *
 * 순수 시뮬(sim/)을 Phaser.Scene에 묶는다. create에서 createSim, update에서 tick + 엔티티를
 * 도형(아트 최소 — 도형/색)으로 렌더한다. ★렌더는 시뮬에 무영향(시뮬은 렌더 무의존·헤드리스
 * 실행 — playable.test.ts). 이 파일은 DOM/WebGL을 쓰므로 헤드리스 테스트가 import하지 않는다.
 */

import Phaser from "phaser";

import { createSim, tick, WORLD_W, WORLD_H } from "../sim/sim";
import { loadBaselineData } from "../sim/data";
import type { SimState } from "../sim/types";
import { Audio } from "../audio";

const DT = 0.05; // 시뮬 고정 dt(20Hz) — playable 정의와 동일(틱·렌더 분리)
const SEED = 12345;

// 도형 색(아트 최소). behavior별 적 색 구분.
const COLOR_BG = 0x0b0b14;
const COLOR_PLAYER = 0x4fd1c5;
const COLOR_PROJECTILE = 0xffe066;
const ENEMY_COLORS: Record<string, number> = {
  chase: 0xe53e3e,
  swarm: 0xb794f4,
  ranged: 0x63b3ed,
  charger: 0xed8936,
};

export class GameScene extends Phaser.Scene {
  private state!: SimState;
  private gfx!: Phaser.GameObjects.Graphics;
  private hud!: Phaser.GameObjects.Text;
  private acc = 0; // 시뮬 dt 누적기(가변 프레임→고정 틱)
  private prevKills = 0; // 오디오 SFX state-diff 추적(시뮬 순수 유지·렌더 레이어 전용)
  private prevLevel = 1;
  private prevOver = false;

  constructor() {
    super("GameScene");
  }

  create(): void {
    this.cameras.main.setBackgroundColor(COLOR_BG);
    this.state = createSim(loadBaselineData(), SEED);
    this.prevKills = this.state.kills;
    this.prevLevel = this.state.player.level;
    this.prevOver = this.state.gameOver;
    (window as unknown as { __survivorState: SimState }).__survivorState = this.state; // 헤드리스 검증 훅(live sim)
    // 오토플레이라 게임 입력이 없으므로 캔버스 첫 탭으로 오디오 unlock(autoplay 정책).
    this.input.on("pointerdown", () => Audio.unlock());
    this.gfx = this.add.graphics();
    this.hud = this.add.text(8, 8, "", {
      fontFamily: "monospace",
      fontSize: "14px",
      color: "#ffffff",
    });
  }

  update(_time: number, deltaMs: number): void {
    // 고정 dt 시뮬 스텝(렌더 fps와 무관·결정론 보존). 한 프레임에 과도 누적 방지(상한 4틱).
    this.acc += deltaMs / 1000;
    let steps = 0;
    while (this.acc >= DT && steps < 4) {
      tick(this.state, DT);
      this.acc -= DT;
      steps += 1;
    }
    // 오디오 SFX — 시뮬은 순수(audio 무의존)·렌더 레이어서 state-diff 로 이벤트 감지(프레임당 최대 1회).
    const s = this.state;
    if (s.kills > this.prevKills) Audio.play("kill");
    if (s.player.level > this.prevLevel) Audio.play("levelup");
    if (s.gameOver && !this.prevOver) Audio.play("gameover");
    this.prevKills = s.kills;
    this.prevLevel = s.player.level;
    this.prevOver = s.gameOver;
    this.draw();
  }

  private draw(): void {
    const g = this.gfx;
    g.clear();
    const s = this.state;

    // 투사체
    g.fillStyle(COLOR_PROJECTILE, 1);
    for (const pr of s.projectiles) {
      if (!pr.active) continue;
      g.fillCircle(pr.x, pr.y, 4);
    }

    // 적(behavior별 색)
    for (const e of s.enemies) {
      if (!e.active) continue;
      g.fillStyle(ENEMY_COLORS[e.behavior] ?? 0x999999, 1);
      g.fillCircle(e.x, e.y, 9);
    }

    // 플레이어
    g.fillStyle(COLOR_PLAYER, 1);
    g.fillCircle(s.player.x, s.player.y, 11);

    const p = s.player;
    const status = s.gameOver ? "GAME OVER" : "생존 중";
    this.hud.setText(
      `시간 ${s.time.toFixed(1)}s | HP ${Math.ceil(p.hp)}/${p.maxHp} | Lv ${p.level} | 처치 ${s.kills} | ${status}`
    );
  }
}

/** Phaser 게임 설정(main.ts가 사용). */
export const GAME_CONFIG: Phaser.Types.Core.GameConfig = {
  type: Phaser.AUTO,
  width: WORLD_W,
  height: WORLD_H,
  backgroundColor: COLOR_BG,
  parent: "app",
  // 모바일 반응형: FIT = 코어 캔버스(WORLD_W×WORLD_H)를 종횡비 유지한 채 CSS 로 부모에 맞춰 축소
  // (RESIZE 는 캔버스를 부모에 채우도록 리사이즈→GPU fill-rate 부담·고정 해상도엔 FIT 가 정답).
  // CENTER_BOTH = 부모(#app) 내 수평·수직 중앙. ★오토플레이(movePlayer=자동 회피)라 입력 배선 불요.
  scale: {
    mode: Phaser.Scale.FIT,
    autoCenter: Phaser.Scale.CENTER_BOTH,
  },
  scene: [GameScene],
};
