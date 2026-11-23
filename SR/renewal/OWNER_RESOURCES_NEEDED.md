# Owner 제공 필요 자원 (SR/renewal 잔여 라이브 승격)

renewal Option A 번호 페이즈 **P1~P9 전부 구현·커밋·master 동기 완료**. 코드는 100%이고, 아래 항목만
**외부 자원/인프라가 Owner 측에서 제공**되면 해당 verify 게이트가 `ACTION → OK(라이브)`로 전환된다.
값(시크릿·키·토큰·URL)은 본 문서에 적지 않는다 — **설정 경로**와 **검증법**만 명시한다.

> 분류: `[ACTION]` = 코드 100%·라이브만 env/Owner 의존(≠pass·≠fail·green-fake 0). `BLOCKED-pending-Owner ≠ pass`.

---

## ✅ 이번 세션(2026-06-23)에 해소됨 — Owner 추가 제공 불필요

| 항목 | 상태 | 근거 |
|---|---|---|
| Server B 이미지 VAE | ✅ 라이브 | VAE 수리 후 `qwen-image` COLOR(black_ratio≈0.006) — verify_phase7 게이트6 PASS |
| Server B 영상 (video/video-audio) | ✅ 라이브 | wan(vp9)·ltx-2(h264+aac) 실 생성 — verify_phase7 게이트8 PASS |
| 트레일러 (실 footage 합성) | ✅ 라이브 | 게임 캡처 + LTX-2 b-roll → 합성 — verify_phase7 게이트10·verify_phase5 트레일러 PASS |
| SearXNG 메타검색 | ✅ 라이브 | `aigs-searxng` Up·HTTP 200·orch 통합 3결과 — verify_phase9 US-9a-1 PASS |
| crawl4ai 크롤 | ✅ 라이브 | crawl4ai 0.8.9·search→crawl 핸드오프(markdown 20KB) — verify_phase9 US-9a-2 PASS |
| legacy provenance 백필 | ✅ 완료 | 178pt(123 untrusted/55 trusted)·set_payload — verify_phase8 b2 PASS |

---

## ✅ 2026-06-24 추가 해소 — all-in-one(D·B·A2·A1)

| 항목 | 상태 | 근거 |
|---|---|---|
| Google Drive 인입 | ✅ 라이브 | rclone OAuth·폴더 `office_intake`·`RcloneSource.sync()` 왕복 — verify_phase9 US-9c-2 PASS |
| Redis async Sentinel 소비자(A2) | ✅ 라이브 | 진단=`REDIS_ENABLED` 가 orch 가 로드하는 `.env` 에 부재(config/app.env 에만)→disabled. 수정=orch 유닛 `Environment=REDIS_ENABLED=true`(.env 무수정). 실증=probe ok=True·aigs R/W 왕복·잔여0 + 라이브 orch `/tasks`→`"redis":"ok"` |
| WA 라이브 아바타 입장(B2) | ✅ 라이브(조건부 게이트) | aigs-wa-avatars 9/9 IN-OFFICE·`state/wa_avatars_status.json`(in_world=9·fresh) → verify_phase6 L148 조건부 라이브 PASS(서비스 stop/stale→ACTION 폴백·falsifiable) |
| vault 외부 백업(A1) | ✅ github(private)+Drive | `gh repo create <owner>/vault --private`+push(HTTPS·gh <owner> 인증)·HEAD=origin/master fc60bf9·**visibility=PRIVATE 확인**. ★실패원인=VAULT_REMOTE 가 SSH 플레이스홀더 `git@github.com:owner/vault`(미설정 템플릿·권한 아님). Drive 백업(`gdrive:vault_backup`)도 유지 |

---

## ⬜ Owner 제공 대기 — 아래 자원 제공 시 라이브 승격

### 1. WorkAdventure(WA) 라이브 — §9 (P6)
- **필요**: ① 공개 도메인 ② TLS 인증서(§9.3 PVE host) ③ WA 맵 URL(`START_ROOM_URL`)
- **설정 경로**: `config/app.env`(`START_ROOM_URL`·도메인)·WA Quadlet `deploy/systemd/wa/` + PVE host TLS
- **검증법**: `verify_phase6.sh` / `verify_phase6_wa_cmd.sh` 의 ACTION(WA-위-합성 렌더·라이브 아바타 입장·2액터 proximity·이미지 pull+브릭 gRPC 핸드셰이크)이 `OK`로 전환
- **현재**: ◑ 부분해소(2026-06-24) — **라이브 아바타 입장 ✅(B2·9/9 IN-OFFICE·조건부 게이트 PASS)**. 잔여=WA-위-합성 렌더·2액터 proximity·공개도메인/TLS/맵URL(Owner).

### 2. Discord 테스트 길드 — (P6)
- **필요**: `/cmd` 슬래시커맨드 라이브 디스패치 검증용 **테스트 길드 등록**(봇 초대 + 길드 ID)
- **설정 경로**: Discord 봇 토큰(기존)·테스트 길드 ID
- **검증법**: `verify_phase6.sh` "라이브 디스코드 길드 1회 /cmd 디스패치" ACTION → OK
- **현재**: ◑ 부분해소(2026-06-24) — guild 등록+guild-scoped sync 완료(DISCORD_GUILD_ID·API 검증)·잔여=**사람 1회 /cmd 타이핑**(자동화 불가).

### 3. Google Drive 인입 — §15/§16 (P9)
- **필요**: rclone Drive **OAuth 자격증명**(`rclone.conf`) + 공유 폴더
- **설정 경로**: `~/.config/rclone/rclone.conf` 또는 지정 경로(시크릿·gitignored)
- **검증법**: `verify_phase9.sh` US-9c-2 "라이브 Drive" ACTION → OK (현재 로컬 fixture e2e는 전량 falsifiable 통과)
- **현재**: ✅ **해소(2026-06-24)** — office_intake 라이브·`RcloneSource.sync()` 왕복·verify_phase9 US-9c-2 PASS(위 ✅ 표 참조).

### 4. vault 원격 저장소 — §16
- **필요**: `VAULT_REMOTE`(vault git push 대상 원격 URL)
- **설정 경로**: vault 별도 git tree(`vault/`·`aigs` 신원)의 remote
- **검증법**: vault 산출물(golden·ADR·아트·트레일러)이 원격에 push 가능
- **현재**: ✅ **해소(2026-06-24)** — `<owner>/vault`(**PRIVATE**) 생성+push(HTTPS·gh <owner> 인증). ★실패원인=VAULT_REMOTE 가 SSH 플레이스홀더(`git@github.com:owner/vault`)·**권한 아님**(main repo 는 gh CLI HTTPS 인증). vault/.git/config 에 HTTPS remote 영속·`git -C vault push` 향후 동작. config/app.env `VAULT_REMOTE` 도 `https://github.com/<owner>/vault.git` 로 갱신 완료(vault/.git/config 와 일치·미래 A1 자동화 일관). Drive 백업도 유지.

### 5. Redis Sentinel HA — §16
- **필요**: Redis **Sentinel 노드**(HA 승격)
- **설정 경로**: `config/app.env`(Sentinel 호스트·`REDIS_SENTINEL` 26379)
- **검증법**: `orch/redis_client.py`(async Sentinel·현재 **dormant**) 활성 경로 라이브
- **현재**: ✅ **A2 해소(2026-06-24)** — async Sentinel 소비자 라이브(orch 유닛 `Environment=REDIS_ENABLED=true`·orch `/tasks`→`"redis":"ok"`·probe R/W 왕복). 잔여=Sentinel **HA failover** 실측만 OBSERVE(아래).

---

## ◐ 본질적 OBSERVE / env (Owner 불요·닫히지 않는 관측)

| 항목 | 성격 | 비고 |
|---|---|---|
| 실모델 적대 저항 재평가 | 비결정 OBSERVE | `tools/p8_adversarial_observe.py`·이번 resist 15/15·회귀 차단은 결정론 게이트(test_evals_retrieval_borne)가 담당 |
| browser-use clean nav | env(Xvfb/egress) | `verify_phase9` US-9b-1·종료성은 결정론 입증(safe_run)·clean nav success는 OBSERVE |

---

## 참고
- 명세 SSoT: `SR/renewal/AI_GAME_STUDIO_IMPLEMENTATION_SPEC.md` · 권위: `renewal-spec-is-authority`(메모리)
- 검증 방법론: `wiki/23_renewal_verification_methodology.md`(적대 합의·green-theater 차단·falsifiable·ACTION≠pass)
- 각 페이즈 게이트: `SR/renewal/verify_phase{1..9}.sh`(4-counter·exit0 ⟺ FAIL==0 AND BLOCKED==0)
