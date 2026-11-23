# 품질 엔진 (7층 방어)

## 개요

4B 소형 모델의 신뢰도 한계를 다층으로 방어하는 품질 엔진. 단일 호출의 영리함이 아니라 **구조+검증**으로 품질을 확보한다.

## 7층 방어 구조

| 층 | 기법 | 근거 |
|---|---|---|
| ① 구조 | **워크플로 우선** (결정론 경로 기본, 자율은 부족시만) | Anthropic 3-0 (R6) |
| ② 모델 | **BFCL 검증 공식 모델** (취미 distill 아님) | R5 A2 3-0 |
| ③ 프롬프트 | **GEPA 프롬프트 컴파일** (frozen, Qwen3-8B 검증) | R6 3-0 |
| ④ 상태 | **상태 캐리포워드 스캐폴딩** | R6 FuncBenchGen 3-0 |
| ⑤ 에스컬레이션 | **비자기평가 트리거** (tool-call 검증·자기일관성·규칙) | R5 B1 3-0 |
| ⑥ 검토 | **별도 검증 패스 + HITL** (9B/사람 judge) | R5 B1 |
| ⑦ 학습(선택) | **역할별 fine-tuning** (작은 라우터에 효과적) | R6 3-0 |

## BFCL 검증 원칙

- **BFCL V4**: Berkeley Function-Calling Leaderboard — 실제 함수 호출 정확도 기준
- 취미 distill 모델 배제 (실측이 무너지는 선택 금지)
- Phase 0 벤치: 24케이스 2×2 매트릭스(모델×thinking)로 자체 검증

## GEPA 프롬프트 컴파일

- Frozen 프롬프트 컴파일 방식 (Gradient-free Evolutionary Prompt Alignment)
- Qwen3-8B 검증 기반
- 프롬프트는 한 번 컴파일 후 고정 사용

## 비자기평가 에스컬레이션

소형 모델은 자기평가가 불가하므로(R2 리스크), 외부 트리거로 에스컬레이션:
- tool-call 검증 실패
- 자기일관성 불일치
- 규칙 기반 트리거 (예: 특정 키워드, 신뢰도 임계값)

## 벤치마크 하니스

Phase 0에서 구축한 `bench/` 패키지:
- `one_man_corp-bench` conda env에서 실행
- `cases/` 디렉토리: `toolcall_ko.jsonl`(12개), `toolcall_en.jsonl`(12개), `multistep.jsonl`(4개)
- 평가 지표: 정확도(%), parse_fail(%), p50 지연(ms), 콜당 토큰
- `pytest -m "not slow"` — 오프라인 결정론 게이트 (bge·LLM 실연결은 slow 마커)

## 리스크 목록

| # | 리스크 | 심각도 | 대응 |
|---|---|---|---|
| R1 | 4B/소형 tool-calling 신뢰도 | 높음 | 7층 방어·Phase 0 게이트·BFCL 검증 |
| R4 | Context Rot | 높음 | top-k≤5·리랭커·티켓 핸드오프 |
| R5 | 서빙 동시성 미검증 | 중간 | Phase 0 실측 전용 |
| R6 | 소형모델 그래프추출 불가 | 해결 | GraphRAG 배제, 벡터RAG+리랭커 |
| R7 | HITL 역설(1인 병목) | 해결 | 위험기반 게이팅+Plan Mode+HOTL |
| R11 | 과설계(1인 유지보수) | 중간 | 워크플로 우선·점진 도입·MVP 먼저 |
