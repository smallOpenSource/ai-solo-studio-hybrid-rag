# 동결 파일 (엔진 계약) — 의도·강제장치·해동 절차

> P3 강제장치(2026-06-16 개선 프로그램). 컨벤션-only 동결을 기술적 가드로 승격한다.

## 왜 동결하는가

이 프로젝트의 핵심 가치는 **워크플로 엔진의 안정 계약**이다. 동시성·기능·인터페이스
변경은 **전부 비동결 파일**(worker.py·task_queue.py·workflows/·web_server 비동결·web/src
비동결·adapters)에서 이뤄지고, 엔진 계약(이벤트 11종·노드 5종·SSE 스키마)은 고정한다.
이 "엔진 0수정 문화"가 회귀 표면을 좁히고, 비동결 레이어를 자유롭게 진화시킨다.

## 동결 목록 (단일 출처 = `.omc/state/protected_pre.sha`)

sha 매니페스트가 **유일한 진실원**이다. 강제장치(verify_freeze.sh·pre-commit)는 목록을
하드코딩하지 않고 이 파일에서 동적으로 읽는다. 현재(2026-06-16):

| # | 파일 | 계약 |
|---|---|---|
| 1 | `web/src/types/events.ts` | 이벤트 타입 11종(웹측 진실원) |
| 2 | `office/events.py` | 이벤트 dataclass 11종 |
| 3 | `office/event_store.py` | append-only 이벤트 영속·seq |
| 4 | `office/engine.py` | 노드 실행·messages seam(:212) |
| 5 | `office/nodes.py` | 노드 5종·그래프 |
| 6 | `office/gating.py` | 승인 게이팅 정책 |
| 7 | `web_server/runner.py` | SSE 런너(이벤트→프레임) |

> ★P9 정정(2026-06-16): P9 이전엔 #8 `web/src/render/twoD/deriveScene.ts`·#9 `sceneTypes.ts`가
> 동결 목록에 있었으나 live importer 0인 死code였다(동결돼 제거 불가 역설). 동결을 **9→7로
> 정정**(진짜 계약 7종만)하고 死code를 일괄 삭제했다 — 동결 미접촉 불변을 의도적으로 깨는
> 유일 항목이라 **code-reviewer 별도 승인** 후 수행, 이 표·sha 매니페스트·prd.json 동시 갱신.

## 강제장치 (3층)

1. **`scripts/verify_freeze.sh`** — sha 1회 대조(루프 0). 드리프트 시 파일 출력 + exit 1.
   preflight·CI(ci_gate.sh)에서 호출되는 **이식 가능 방벽**.
2. **`.git/hooks/pre-commit`** (버전관리 소스: `scripts/git-hooks/pre-commit`) — 동결 파일이
   스테이징되면 커밋 차단. **로컬 1차 방벽**(`.git/hooks`는 클론 미전파라 이식성은 ①이 담당).
   재설치: `cp scripts/git-hooks/pre-commit .git/hooks/ && chmod +x .git/hooks/pre-commit`.
3. **`scripts/preflight.sh`** — ①을 호출(커밋/머지 전 수동/CI 게이트).

### (선택·best-effort) 인에디터 가드 — harness PostToolUse 훅

`.git/hooks`/preflight는 **착지 후** 또는 **커밋 시점**에 잡는다. 인에디터 Edit를 **착지 전**
잡으려면 harness(Claude Code) PostToolUse Write|Edit 훅이 필요하다. 단 이는 **harness 지원
전제**이며, 미지원/비활성이면 위 ①②③이 유일 방벽이다(자동 in-editor 차단은 best-effort).
활성화하려면 `.claude/settings.json`의 `hooks`에 아래를 추가(권장: 비차단 advisory):

```jsonc
"PostToolUse": [
  { "matcher": "Write|Edit",
    "hooks": [ { "type": "command",
      "command": "bash /opt/studio/scripts/verify_freeze.sh >/dev/null 2>&1 || echo '[FREEZE] ⚠ 동결 드리프트 — verify_freeze.sh 확인' " } ] }
]
```

> 운영 중 세션 안정성을 위해 본 프로그램은 ①②③(견고·이식·CI 강제 가능)만 자동 설치하고,
> 위 인에디터 훅은 **수동 활성 옵션**으로 문서화한다(차단형 훅이 진행 중 세션을 막을 위험 회피).

## known-debt (동결이 가두고 있는 빚 — 해동 없이 산다)

- `office/engine.py` `execute()` ~305줄 단일 함수(복잡도 높음) — 동결상 즉시 리팩터 기각,
  본 문서로 **가시화**해 갈음. 비동결 레이어 진화로 우회.
- 이벤트 타입 추가 금지(11종 고정) — 새 관측 욕구는 기존 타입 재사용 또는 비동결 부가채널로.

## 해동(동결 변경) 비준 절차

1. **정당화**: 비동결 레이어로 불가능함을 입증(엔진 상태가정 변경이 불가피한가?).
2. **합의**: ralplan(또는 등가)로 설계 비준 + **code-reviewer 별도 승인**(self-approve 금지).
3. **재생성**: 변경 후 `sha256sum <파일들> > .omc/state/protected_pre.sha`로 매니페스트 갱신
   (이 표·`.omc/prd.json` constraints 동시 갱신 — 단일 진실원 유지).
4. **커밋**: pre-commit 우회는 `git commit --no-verify`(위 1~3 완료 책임 하에만).

## 해동 이력

- **2026-06-21 — `office/event_store.py` + `web_server/runner.py` (디스코드 재방송 인시던트 수정)**:
  - 변경: event_store.py `head_seq()`(읽기전용 `SELECT COALESCE(MAX(seq),0)` 추가·콜드스타트 시드용) /
    runner.py enqueue를 try/except로 감싸 비동결 `task_queue.WorkflowDisabledError`를 **409**로 변환.
  - 정당화: 둘 다 **순수 additive**(append-only 영속·seq 단조·이벤트→프레임 SSE 스키마·기존 /api/run 성공
    경로 무변경). head_seq는 events 테이블 소유자(EventStore)에 두는 게 정설이고, runner 409는 /api/run
    핸들러가 그 파일에 있어 불가피(대안 500은 더 나쁜 계약). 비동결로 동일 품질 불가.
  - 비준: code-reviewer **APPROVE**(계약 위반 0·회귀 위험 0). 회귀 engine 793·discord 219.
  - sha 매니페스트 재생성(`protected_pre.sha`·미변경 5파일 일치). 파일 목록·계약 무변(표 그대로).
