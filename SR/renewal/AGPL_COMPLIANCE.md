# P6 착수 게이트 — AGPL 컴플라이언스 + 인터페이스 토폴로지 결정 (P6-PRE)

> SR/renewal P6 관제/인터페이스. 합의계획 `.omc/plans/ralplan-p6-interface.md`(v2·Architect SOUND + Critic APPROVE) §3.1·§3.3·P6-PRE 근거. **이 4 결정이 기록되기 전 P6c(패널)/P6d(WA) 착수 불가.**

---

## 결정 ① — AGPL-3.0 §13 = 트리거 **미발생** (옵션 a 확정)

**이진 질문:** "P6에서 WorkAdventure 브릭(`play`/`back`/`map-storage`/`icon`/`uploader`) **소스를 수정**하는가?" → **아니오.**

**근거(실코드):** `/tmp/wa-compose.yaml`의 모든 WA 서비스는 **upstream 공식 prebuilt 이미지**만 참조한다 — `thecodingmachine/workadventure-play:${VERSION}`·`-back`·`-map-storage`·`-uploader`·`matthiasluedtke/iconserver`·`redis:6`. **로컬 `build:`/`context:` 지시 0건**(빌드 컨텍스트 부재). 우리 산출물 = 컨테이너 오케스트레이션(systemd-user 유닛) + env 배선 + **별도 React 패널 앱**(스크립팅 API/iframe로 클라이언트-사이드 통신).

**판정:** AGPL-3.0 §13(수정 버전을 네트워크로 상호작용시키면 대응 소스 제공 의무)은 **프로그램을 *수정*한 경우에만** 발동한다. WA를 **비수정 prebuilt로 운영** + 스크립팅 API/iframe로 별도 React 앱(독립 저작물·별도 프로세스·별도 origin·런타임 HTTP IPC·라이브러리 링크 아님)을 얹는 것은 §13 의무 **미발생**.

**가드(사후·verify):** `verify_phase6.sh` 게이트7이 (i) WA 유닛/compose가 upstream 이미지 핀만 참조함을 grep 단언(로컬 빌드 컨텍스트 부재), (ii) 본 파일 존재를 확인.

**정지 조건(단서 b·본 계획 미채택):** 구현 중 패널이 스크립팅 API/iframe로 불가능해 **브릭 소스 fork·수정이 필요하다고 판명되면 → 즉시 정지 · Owner에 소스-오퍼 결정 재상신**(수정 소스를 `VAULT_REMOTE`/공개 도메인 게시 메커니즘). 자동 진행 금지.

---

## 결정 ② — Agent Status 배지 좌표계 = **viewport 도크 상태판** (아바타-추종 아님)

**문제:** §9.4 line411은 "Agent Status Overlay = 아바타 *위* 상태 배지"라 적으나, WorkAdventure 스크립팅 API(`WA.ui`)에는 **아바타에 고정돼 함께 움직이는 per-avatar floating UI 프리미티브가 없다**(openPopup=타일 고정·modal/website=viewport 도크·actionBar/banner=고정·onRemotePlayerClicked=클릭 트리거). "아바타 위 배지"를 글자 그대로 구현하려면 `play` 클라이언트 브릭을 fork해야 하고 → 결정 ① AGPL 트리거.

**결정:** 배지 = **viewport 우측 도크 상태판**(직원별 상태 리스트·8값·선택적 맵 좌표 근사 마커). **아바타-추종 floating 아님.** **play 브릭 비침투**(스크립팅 API/iframe/postMessage만).

**근거:** §9.4 line405가 "WA UI 오버레이/iframe 또는 **스크립팅 API**로 연동"을 명시 허용 → 도크 재해석은 line411 워딩의 **정당한 구현 재량**(자의적 축소 아님). §9.4·§6.3 line249의 본질은 "직원 상태 가시화"이지 물리적 아바타-앵커가 아니다. 이 재해석이 결정 ①(비수정 경로)의 **필요조건**이다(예측 가능한 fork 유혹을 P6c 착수 *전* 제거).

---

## 결정 ③ — §9.2 토폴로지 = **(a) 포트모델 전환** (traefik 제거)

**모순:** `/tmp/wa-compose.yaml`은 `traefik:v3.6.1` 리버스프록시 + ACME(Let's Encrypt) + **PathPrefix 경로라우팅**(play `/`·back `/api`+stripprefix·map-storage `/map-storage`·icon `/icon`·uploader `/uploader`)를 토대로 한다. 이는 §9.2(line395 "경로(path) 라우팅·리버스 프록시·Caddy **없음**"·포트기반만)와 **정면 모순**. (v1 계획의 "compose=오케스트레이션+env뿐" 서사는 거짓이었다.)

**결정 (a 채택·스펙 준수):** **traefik 제거 + 포트모델 전환.** 각 외부 브릭에 포트(레지스트리 2817-2820) 배정·평문 HTTP 제공 → PVE host 터널이 `https://studio.example.com:<port>`로 공개. `play` env를 포트별 절대 URL로 재배선:
- `PUSHER_URL=https://studio.example.com:2817`(**끝슬래시 제거** — compose 실제값 `https://${DOMAIN}/`은 끝슬래시 有 → OIDC/`Invalid playUri` 함정 §9.2). `PUBLIC_MAP_STORAGE_URL`·`ICON_URL`·`UPLOADER_URL`도 포트별. 내부 `API_URL=back:50051`(gRPC)·`INTERNAL_*`은 VM-로컬 유지.
- PathPrefix 라우팅 → 루트(포트가 라우팅 대체). stripprefix 미들웨어 제거.

**근거:** 명세 §9.2가 "포트 기반·리버스프록시 없음"을 리터럴 확정(SSoT). (b §9.2 일탈 ADR은 미채택 — 스펙 준수가 가능하므로.) traefik→포트 전환은 *배선/env*이지 WA 브릭 *소스 수정*이 아니라 결정 ① AGPL과 무관.

---

## 결정 ④ — 브릭 연결성 프로브 로컬 가능범위 (§3.3 경계 동결)

| 프로브 | 로컬 가능 (verify PASS 대상) | Owner-gated (pending·≠pass) |
|---|---|---|
| `play↔back` gRPC :50051 | ✅ 컨테이너 내부 DNS(`back:50051`)·이미지 pull 시 로컬 핸드셰이크 | — |
| `pusher(play)↔redis` pub/sub | ✅ 로컬 redis(공유 Sentinel)·1 메시지 왕복 | — |
| `map-storage` fetch | ✅ 로컬 포트 응답 | — |
| **라이브 아바타 입장** | — | ❌ `START_ROOM_URL`(GitHub Pages 맵)·공개 도메인·TLS(§9.3 PVE host) |
| **2-액터 proximity** | — | ❌ 라이브 입장 의존 + 2-세션 하네스 |

**규칙:** 이미지 pull 가능 시 좌열 프로브 실행(로컬·도메인 무관). pull 불가 시 좌열도 "code 100%/runtime pending: 이미지 pull". 우열은 항상 `[ACTION] pending Owner`(verify 미합산·≠fail·≠pass·BLOCKED≠pass·green-fake 금지).

---

> **P6-PRE 게이트 통과 조건:** 위 4 결정 전부 기록 + `verify_phase6.sh` 게이트7이 본 파일 존재·upstream 이미지 핀 grep·§9.2 택1(=a) 기록을 단언. 통과 후에만 P6c(패널)/P6d(WA) 착수.
