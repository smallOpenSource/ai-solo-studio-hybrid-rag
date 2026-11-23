/**
 * main.ts — Phaser 게임 진입점 (G005 US-G5-6 A2).
 *
 * index.html이 이 모듈을 로드한다(<script type="module" src="/src/main.ts">). 순수 시뮬
 * (sim/)을 Phaser로 렌더하는 GameScene을 띄운다 — `npm run build`→game/dist 플레이가능 빌드
 * (슬라이스 완주 산출물). ★시뮬 결정론은 렌더와 분리(헤드리스 playable.test.ts가 검증).
 */

import Phaser from "phaser";

import { GAME_CONFIG } from "./render/scene";

new Phaser.Game(GAME_CONFIG);
