# game/native — 데이터주도 Godot 네이티브 클라이언트 + Linux→Windows .exe

AI 직원들이 산출한 `game/data/*.json`을 소비하는 Godot 4 네이티브 클라이언트와, 이 리눅스
박스에서 **Windows `.exe`를 크로스 익스포트**하는 빌드. 협업 빌드 파이프라인은 [`../COOP_BUILD.md`](../COOP_BUILD.md) 참조.

## 구조

| 파일 | 역할 |
|---|---|
| `Schema.gd` (`class_name GameSchema`) | `office/game_schema.py`·`game/src/schema.ts`와 **동치** 스키마 검증(GDScript 3번째 포팅). cross-file 참조무결성(`waves.spawn.enemyId ∈ enemies.id`)은 `validate_all`만. |
| `DataLoader.gd` | `res://data/{meta,enemies,weapons,waves,upgrades,balance}.json` 6파일 로드 → `validate_all`. 누락/위반 시 `.ok=false`. |
| `Main.gd` | 6파일 로드·검증 → minimal playable 씬(데이터주도). 헤드리스면 증거 로그 후 `--quit-after` 종료. |

데이터는 `game/data/`(소스·READ-only)를 빌드 시 `game/native/data/`로 복사해 `.pck`에 동봉
(`game/native/data/`·`.godot/`은 gitignore).

## 빌드 (`scripts/build_windows.sh`)

격리 toolchain `tools/godot/`(Godot 4.3-stable 바이너리 + Windows export template·gitignore·
base/conda 무오염). 새 클론/CI는 toolchain을 재다운로드해야 함(URL·sha는 git 히스토리의
US-7 작업 보고 참조).

```sh
bash .omc/guards/safe_run.sh 900 0 -- bash scripts/build_windows.sh [OUT_DIR]
# 2-step: ① --headless --import(.godot 캐시) ② --headless --export-release "Windows Desktop"
# 산출: <OUT>/AetherScourge.exe(+ .pck) · 첫 2바이트 MZ/PE 검증 · .pck 6파일 grep · data sha manifest
```

`embed_pck=false`라 `.exe`는 Windows export template 스텁이고 게임 코드·데이터는 `.pck`에 있다
(둘을 같은 폴더에 두고 실행). 실제 Windows 런타임 실행은 사용자 머신 몫(이 박스는 Wine 미사용).

## ★GOTCHA — "가짜 성공" 방지 (반드시 헤드리스로 실증)

**Godot 4.3 기본 프로젝트는 GDScript의 Variant 추론을 경고-에러로 처리**한다(`var x := variant_expr`,
동적 `callv()` 등). 그러면 **`.exe`는 산출되지만 스크립트가 컴파일되지 않은 채 패킹**된다 — `.exe`
존재만으로 PASS 판정하면 **가짜 성공**이다.

- **규약**: 모든 GDScript는 **명시 타이핑**(`var x: Type = ...`·`value is Type` 후 캐스트·
  정적 메서드는 `Callable(GameSchema, "validate_*")`).
- **검증 게이트**: `.exe` 존재만으로 PASS 금지. 반드시
  ```sh
  tools/godot/Godot_v4.3-stable_linux.x86_64 --headless --path game/native --quit-after 60
  ```
  로 **`6-file load+schema OK`·`RESULT: OK`(Main.gd) + `SCRIPT ERROR`/`Parse Error` 0**을 stdout으로
  확인한다(`.pck`를 직접 검증하려면 `--main-pack <pck>`). 이 헤드리스 실행이 컴파일 결함을 잡는다.

## 범위 한계 (정직)

minimal playable(6파일이 게임을 *구성*함을 증명하는 슬라이스)이다. upgrades 레벨업 UI·win(보스)/
lose 종료·결정론 seed·full `game/src/sim/sim.ts` 동치는 game-design follow-up이다([`../SLICE.md`](../SLICE.md) 참조).
