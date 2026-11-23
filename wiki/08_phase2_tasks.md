# Phase 2 작업 분해 (K0~K8 + G1~G3)

## 지식 트랙 작업 목록

| 작업 | 내용 | 선행 | 닫는 방식 |
|---|---|---|---|
| **K0** | 지식 코퍼스 + 평가셋 라벨링. `wiki/`에 위키 문서 + `cases/knowledge_eval.jsonl` ≥20 | — | 코퍼스 N편 + 평가셋 ≥20(카테고리·난이도 분포) |
| **K1** | conda `one_man_corp-knowledge` 생성 + bge 어댑터 `core/bge_client.py`로 승격 | — | 회귀 0 (engine 341·bench 304 기준) |
| **K2** | `knowledge/chunk.py` — Docling Hybrid 청킹 + 단순분할 폴백 | K1 | 마크다운→청크 경계·메타 단위 테스트 |
| **K3** | `knowledge/store.py` — Qdrant 임베디드(dim=1024, `:memory:`/`path=`) | K1 | 인메모리 업서트→검색 라운드트립 |
| **K4** | `knowledge/search.py` — dense top-N → 리랭커 → top-k≤5 | K2, K3 | bge mock + 인메모리 오프라인 테스트 |
| **K5** | `knowledge/ingest.py` + CLI — 마크다운 디렉토리→청킹→임베딩→업서트(멱등) | K4, K0 | 2회 실행 동일 상태(멱등) |
| **K6** | `knowledge/eval.py` — Hit@k·MRR 비교표 + 실패 분리집계 | K4, K0 | 고정 코퍼스 mock으로 지표 계산 정확 |
| **K7** | 엔진 도구 연결 — `office/tools.py` + `tools_policy.yaml` always-allow | K4 | 스키마·callable·정책·레지스트리 주입 테스트 |
| **K8** | 실서버 smoke → 평가 풀런 + 관측성 | K5, K6, K7 | E2E(slow): 비교표+실패분리+엔진실증 |

## Google MCP 트랙 (지식 트랙과 병렬·독립)

| 작업 | 내용 |
|---|---|
| **G1** | 무료 MCP 제품 조사 (Gemini CLI·AI Studio API·NotebookLM·third-party Google Search MCP). fallback: 확증 후보 ≥2 없으면 '조사 보고서'로 강등 |
| **G2** | 비교표→1개 추천 + 연결 방식 확정. 토큰은 `.env` 이름만 |
| **G3 (사용자 대기)** | 사용자 협조로 인증·연결 → "리서치 강화 도구" 엔진 레지스트리 등록 → 1회 호출 smoke |

## 디렉토리 구조 (신규 추가분)

```
/opt/studio/
  wiki/                       # ★신규 — 인덱싱할 한국어 위키 문서 (K0 산출)
  cases/
    knowledge_eval.jsonl      # ★신규 — 평가셋 ≥20 (K0 산출)
    knowledge_eval_README.md  # ★신규 — 라벨링 절차 문서
  knowledge/                  # ★신규 — 지식 레이어 패키지 (K1~K7)
    __init__.py
    chunk.py
    store.py
    search.py
    ingest.py
    eval.py
    mcp_research.py           # Google MCP 어댑터 자리 (G3)
  core/
    bge_client.py             # ★bench/bge_client.py에서 승격 (K1)
  state/
    qdrant/                   # ★신규 — Qdrant 임베디드 영속 디렉토리
  tests/
    test_knowledge_eval_schema.py  # ★신규 — K0 스키마 검증 (US-002)
```

## conda env 설치 명령

```bash
conda create -n one_man_corp-knowledge python=3.12 -y
conda run -n one_man_corp-knowledge pip install -e . qdrant-client httpx pytest pyyaml python-dotenv
```

## 수용 기준 요약 (US-002 — K0)

1. `wiki/`에 한국어 코퍼스 문서 N편 실재 (청크 수십 개 이상)
2. `cases/knowledge_eval.jsonl` ≥20 항목, 난이도 분포 + 키워드 카테고리 ≥6
3. 라벨 무결성: 정답 먼저 독립 선정 → 질의 나중 작성 (자기참조 방지)
4. `tests/test_knowledge_eval_schema.py` 오프라인 통과
