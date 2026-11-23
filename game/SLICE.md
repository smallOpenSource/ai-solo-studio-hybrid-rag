# Aether Scourge — 게임 슬라이스 정직 상태 (P2 정직화)

> 2026-06-16 개선 프로그램 P2. "게임 슬라이스 완주" 주장을 **재현가능 검증**으로 뒷받침하고
> 서사를 정직화한다. (historical g5-* 리뷰 문서는 시점 기록이라 불수정 — 본 문서가 권위 상태.)

## 무엇인가

데이터 주도(idle/incremental survivor-like) 게임의 헤드리스 검증 가능한 슬라이스.
- 시뮬레이션: `game/src/sim/`(렌더 무의존·seed 결정론). 플레이어 자동 카이팅 + 무기 자동발사.
- 데이터: `game/data/{weapons,enemies,waves,upgrades,balance,meta}.json` — **LLM이 양산**
  (game_assemble/game_design/game_content/game_balance 워크플로·gemma).

## ★데이터 생성 vs 플레이가능성 (정직)

- **game_qa = `validate_all`(스키마 검증)만** 한다. 구조·참조무결성·값 범위는 보지만,
  **실제로 플레이가능한지(전투가 일어나고 생존/레벨업 가능한지)는 검증하지 않는다.**
- 플레이가능성은 **별도 dev 검증**: `src/__tests__/playable.test.ts`(vitest·seed 결정론)와
  `scripts/verify_slice.sh`가 그 간극을 메운다.

## ★P2 데이터 보정 (정직한 복구 기록)

LLM이 양산한 베이스라인 데이터는 **sim 좌표계와 단위 스케일이 불일치**해 playable 2/9 FAIL
이었다(git 두 커밋 모두 동일 — 한 번도 playable 통과한 적 없음). 근본:
- 무기 `range` 15/12 = **타일/추상 단위**(sim은 px·월드 960×640·투사체 360px/s) → 사거리가
  ~15px라 적(가장자리 수백 px 스폰)을 영영 못 맞춤 → 발사0·kills0.
- 무기 `fireRate` 0.2 = sim에선 **1/0.2 = 5초 쿨다운**("고속 연사포" 플레이버와 역설).
- enemy `speed` 3.5px/s·wave `rate` 10/s = 역시 추상 스케일(거의 정지 / 과밀 스웜).

**보정(데이터만·sim 코드 0수정)**: range 340/300·fireRate 8/2·enemy speed 38/28/70·
wave1 rate 1.2/0.3(전 wave 비례). 이는 LLM 추상값을 **sim의 px/초 단위로 매핑**한 보정이지
테스트 무력화가 아니다(단언은 그대로·전투/생존/레벨업이 실제로 일어나게 함).

## 검증 상태 (재현가능·`scripts/verify_slice.sh`)

- **playable.test.ts 9/9 통과**(seed 12345 결정론). 6 seed 마진 실측: 전부 30초 생존
  (hp 75~120·kills 20~25·level 3~4) — 과적합 아닌 견고한 통과.
- **schema.test.ts 41/41**(스키마 준수 — 보정값이 유효 범위 내).
- **build green**(dist를 현 데이터로 재생성·stale 해소).
- 한계(정직): 30초 슬라이스 검증이다. balance.json `survivalTargetSec:180`(full run)·웨이브
  2/3 난이도 램프·아트/오디오는 **game-design follow-up**이지 본 슬라이스 게이트 범위 아님.

## 게이트

`bash game/scripts/verify_slice.sh`(test → build → 데이터 sha manifest). ci_gate.sh에 편입.
