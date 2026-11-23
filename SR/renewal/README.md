# AI GAME STUDIO — Claude Code 전달 묶음

Owner가 운용하는 **대화형 Claude Code(Opus 4.8)**가 이 시스템을 구축할 때 필요한 전체 파일.
작업 디렉터리(예: 리포지토리 루트 또는 `/srv/office/apps/`)에 풀고, 아래 순서로 진행한다.

## 0. 현재 상태 (2026-06-24)

이 시스템은 **이미 구축·라이브 가동 중**이다(빌드 핸드오프 → 운영 단계). 아래 §1~§4는 최초 구축
절차의 정본 기록이며, **일상 사용·접속·운영·암호 위치는 레포 최상위 [`README.md`](../../README.md)** 를 본다.

- **페이즈 게이트 전부 존재**: `verify_phase{1,2core,2_5,2,3,3b,4,5,6,7,8,9}.sh` (P1~P9 구현·각 자기 수용기준 동봉).
- **라이브 추가분**(SPEC 반영·운영 중):
  - 모델 **provider primary/fallback** — Azure OpenAI Responses(LLM)·DashScope Qwen-image(이미지)·로컬=fallback (`wiki/25`).
  - **Google Drive 인입** — rclone remote `gdrive`·폴더 `office_intake`(verify_phase9 US-9c-2 라이브 PASS).
  - **WorkAdventure 라이브** — TLS 프록시 + **접속 암호 게이트**(`.env` HITL_OWNER) + **9 직원 아바타 상주**(`aigs-wa-avatars`).
  - **발행 텍스트 humanize 윤문** — `orch/humanize.py`(자체 작성 룰북 `orch/humanize_rules.md`·글 발행 시 AI 티 제거·`stage_content` 배선).

## 1. 파일 목록

| 파일 | 역할 | 사용 시점 |
|---|---|---|
| `AI_GAME_STUDIO_IMPLEMENTATION_SPEC.md` | **SSoT 구현 명세**(§0~§29). 모든 결정·구성·코드 스켈레톤·런북·페르소나·데이터 스키마·보고 프로토콜 | **최우선 통독** |
| `app.env.example` | **단일 통합 환경설정** 템플릿(전 파라미터). `[필수]` 7항목만 채우면 동작 | 복사→`app.env` |
| `bootstrap.sh` | **Phase 0** 부트스트랩(환경 탐지·디렉터리·conda env 4종·`app.env` 생성·ports.json·Quadlet 렌더). 멱등·비파괴 | Phase 0 |
| `verify_phase1.sh` | **Phase 1** 검증(Redis Sentinel R/W·Qdrant healthz+컬렉션·vault git). 비파괴 스모크 테스트 | Phase 1 게이트 |

> 스크립트 2종은 명세 §23.8·§23.10에 동일 내용이 임베드되어 있다(파일=정본).

## 2. 실행 순서

```
0. 명세 정독 (특히 §0 행동계약 · §2 환경 불변식 · §12 Phase 게이트 · §23 구성 · §25 런북)
1. bash bootstrap.sh            # 골격·env·Quadlet·ports 렌더 (외부 입력 불필요)
2. /srv/office/config/app.env 의 [필수] 채움:
   COMFY_BASE · REDIS_SENTINELS · REDIS_PASSWORD ·
   DISCORD_TOKEN · DISCORD_GUILD_ID · OWNER_USER_ID · VAULT_REMOTE · START_ROOM_URL
3. systemctl --user daemon-reload && systemctl --user enable --now aigs-qdrant aigs-phoenix aigs-searxng
4. bash verify_phase1.sh        # Phase 1 수용 기준 통과 확인
5. 통과 시 §25 Phase 2~7 진행 (추론 접근 → 코어 → 연구 → 인터페이스 → 실행엔진 → 제품 1주기)
```

## 3. 핵심 원칙 (§0)

- **추정 금지**: 버전·경로·옵션은 §13 공식 URL을 `WebFetch`로 재확인 후 구현.
- **하드코딩·mocking 금지**: 모든 값은 `app.env`에서 주입(`os.environ`). 더미 통과 금지.
- **환경 불변식**: non-root·sudo無·rootless Podman(Qdrant/Phoenix/SearXNG)·`systemd --user`.
- **연속 품질 루프(§21.4)**: 제품 1사이클마다 **Opus 4.8 max-effort 정밀검수 → 에이전트 피드백 → 개선 → 교훈 적재(vault/quality/) → 다음 사이클 선조회**. 검사·개선 중 deep-research 능동 활용.
- **중간평가 보고(§29)**: Phase 경계·블로커·Owner 요청 시 §29.2 프롬프트로 **근거 기반 체크포인트 보고** 생성.

## 4. 실행 권한

```
chmod +x bootstrap.sh verify_phase1.sh
```
