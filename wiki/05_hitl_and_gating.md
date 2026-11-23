# HITL 및 위험기반 게이팅

## HITL 역설 해소

전수 승인은 명시적으로 비권장("approve every action will create friction without necessarily producing safety benefits"). 대신 위험기반 게이팅을 채택한다.

## 위험기반 게이팅 정책

| 정책 | 내용 |
|---|---|
| **위험기반 게이팅** | 행동별 always-allow / needs-approval / block. 저위험(읽기·초안)은 자동, 고위험·비가역(발행·전송·삭제·외부 push)만 승인 |
| **Plan Mode** | 에이전트가 전체 계획 선제시 → 사람 1회 검토·편집·승인 → 감독을 "단계"에서 "전략"으로 |
| **human-on-the-loop** | 승인이 아니라 "필요 시 개입할 위치 확보". 계획 승인 후 모니터+중단권 |
| **단계적 자율성** | 검증된 작업유형부터 자동승인 확대 (실측: 숙련 시 자동승인 20%→40%+) |

## Agent Inbox 패턴

Agent Inbox(오픈소스·셀프호스트·single-player)는 받은편지함+티켓팅 UI로 모든 직원 소통라인을 추적/조치한다. 웹·Discord 양 표면에 공용 컴포넌트로 사용.

### HITL 인터랙션 분류

- **Notify**: 알림 (RUN_FINISHED, RUN_ERROR, NODE_FAILED)
- **Question**: 질문 응답 요청 (NODE_FAILED with reason=question)
- **Review**: 승인·편집·피드백 (INTERRUPT_REQUESTED — 게이팅 needs-approval)

## Phase 4a 구현 사항

- **GET /api/inbox**: 3분류(review/notify/question) 반환
- **POST /api/inbox/{id}/approve|reject**: `_record_decision(resume=False)` — 기록만, 재개는 워크플로 스레드 폴링 루프가 단독 담당
- **FailReason enum**: `gating` | `escalation` | `question` (3값)
- **동시성**: 전용 단일 스레드 `ThreadPoolExecutor(max_workers=1)` + `threading.Lock()` + uvicorn `--workers 1`

## 승인 흐름

1. 버튼 클릭 → `POST /api/inbox/{id}/approve` → `_record_decision(resume=False)` (기록만)
2. 워크플로 전용 스레드 폴링 루프가 INTERRUPT_RESOLVED 감지 → 단일 재개
3. 도구 실행 정확히 1회 (멱등 보장)

## 데드락 방지

- `asyncio.create_task` / `BackgroundTasks`로 워크플로 기동 절대 금지 (동기 함수+무기한 폴링이라 데드락)
- 워크플로는 `_executor.submit(_run_workflow)` — 전용 OS 스레드에서만 실행
- 이벤트루프(uvicorn)는 GET/POST에 항상 응답 가능 (비블로킹)
