# knowledge_eval.jsonl — 라벨링 절차 및 품질 기준

## 개요

`knowledge_eval.jsonl`은 `wiki/` 코퍼스에 대한 한국어 검색 평가셋이다. Phase 2(K0) 산출물로, dense 의미검색과 dense+리랭커 검색 품질을 비교하는 데 사용된다.

- **항목 수**: 24개 (≥20 요구사항 충족)
- **언어**: 한국어
- **용도**: Hit@k·MRR 비교 + dense 회수실패/리랭크실패 분리집계

---

## 스키마

```json
{
  "id":             "ke-XX",
  "query":          "한국어 자연어 질문",
  "answer_doc":     "wiki/XX_filename.md",
  "answer_snippet": "정답 청크 텍스트 일부 (코퍼스에 실제 존재)",
  "category":       "semantic | keyword",
  "difficulty":     "easy | medium | hard"
}
```

### 필드 설명

| 필드 | 설명 |
|---|---|
| `id` | 고유 식별자. `ke-01` ~ `ke-NN` 형식 |
| `query` | 한국어 자연어 질의. 정답 청크 텍스트를 과도하게 복사하지 않음(패러프레이즈) |
| `answer_doc` | 정답이 위치한 wiki 문서 경로 |
| `answer_snippet` | 정답 청크의 대표 텍스트 일부. 코퍼스에 실제 존재하는 텍스트 |
| `category` | 카테고리 태그 (아래 참조) |
| `difficulty` | 난이도 태그 (아래 참조) |

---

## 카테고리 정의

### semantic (의미검색 일반)
개념·요약 질의로, dense 벡터의 의미 유사도로 찾을 수 있는 케이스. 질의 키워드가 정답 청크에 그대로 나타나지 않아도 의미적으로 관련된다.

**예**: "GraphRAG를 배제한 근거는?" → "소형모델 그래프추출 실패/붕괴"

### keyword (키워드 정확매칭)
다음 중 하나 이상을 포함하는 질의. sparse 신호 측정용 (≥6건 필수):

- **고유명사**: `gemma-4-e4b-it`, `Qdrant`, `bge-m3-ko`, `qwen3.6-35b-a3b`, `BFCL V4`, `one_man_corp-knowledge`
- **숫자/단위**: `12GB`, `dim=1024`, `454ms`, `25%`, `동시성 1`
- **코드 토큰**: `_record_decision`, `knowledge_search`, `WorkflowEngine`, `FailReason`
- **경로/파일명**: `state/qdrant/`, `tools_policy.yaml`, `toolcall_ko.jsonl`

dense 단독으로 정확 매칭이 어렵고, sparse(BM25)가 더 유리한 케이스다.

---

## 난이도 정의

| 난이도 | 기준 |
|---|---|
| **easy** | 질의 키워드가 정답 청크에 그대로 있어 dense가 잘 찾음. 리랭커 없이도 top-1 가능성 높음 |
| **medium** | 동의어·패러프레이즈가 필요하거나 섹션이 분산됨. dense top-N 안에는 있으나 top-k에서 리랭커가 순위 조정 필요 |
| **hard** | 여러 청크에 걸쳐 있거나 dense 회수실패 가능성 있음. 키워드 정확매칭 hard 케이스는 sparse 트리거 후보 |

---

## 라벨링 절차 (자기참조 방지)

### 원칙: 정답 먼저 선정 → 질의 나중 작성

자기참조 편향이란 "dense가 잘 찾는 청크"와 "정답으로 선택한 청크"가 상관되는 것이다. 이를 방지하기 위해 다음 순서를 엄수했다.

### 구체 절차

1. **코퍼스 독립 읽기**: `wiki/` 문서들을 먼저 읽고 각 섹션/단락의 핵심 사실을 목록화한다.

2. **정답 청크 독립 선정**: 각 핵심 사실에 대해 "이 섹션/단락이 정답"임을 먼저 결정한다. 이 시점에서 질의는 아직 작성하지 않는다. `answer_doc`과 `answer_snippet`을 먼저 기록한다.

3. **카테고리·난이도 사전 분류**: 정답 청크의 내용을 보고 semantic/keyword 여부, easy/medium/hard 여부를 결정한다. 질의 작성 전에 결정한다.

4. **질의 나중 작성**: 선정된 정답 청크에 대한 자연스러운 한국어 질의를 작성한다. 이때 정답 텍스트를 그대로 복사하지 않고 패러프레이즈하여 dense 검색이 의미 유사도로 찾게 한다.

5. **검증**: 작성된 질의에 대해 정답 청크가 코퍼스에 실제로 존재하는지 확인한다. 존재하지 않는 정답이 설정된 질의는 포함하지 않는다.

### 이 절차가 방지하는 편향

- 질의를 먼저 작성하면 "내가 방금 읽은 청크"를 정답으로 설정하게 되어 최근성 편향이 발생한다.
- 또한 dense 임베딩이 잘 매칭하는 표현을 질의에 쓰게 되어 평가셋이 dense에 유리하게 편향된다.
- 정답을 먼저 고정하면 질의 작성자가 "이 청크를 찾아야 한다"는 목표 없이 자연스럽게 질의를 작성할 수 있다.

---

## 카테고리 분포

| 카테고리 | 항목 수 |
|---|---|
| keyword | 13 |
| semantic | 11 |
| **합계** | **24** |

keyword 카테고리 ≥6 요구사항: **13건으로 충족**

## 난이도 분포

| 난이도 | 항목 수 |
|---|---|
| easy | 7 |
| medium | 11 |
| hard | 6 |
| **합계** | **24** |

---

## 코퍼스 문서 목록

| 파일 | 주요 내용 | 예상 청크 수 |
|---|---|---|
| `wiki/01_project_overview.md` | 프로젝트 개요·원칙·Phase 로드맵 | ~8 |
| `wiki/02_model_lineup.md` | 모델 라인업·벤치 결과·접속 정보 | ~8 |
| `wiki/03_knowledge_layer.md` | 지식 레이어 설계·Qdrant·bge 스펙 | ~10 |
| `wiki/04_workflow_engine.md` | 워크플로 엔진·도구 레지스트리·게이팅 | ~9 |
| `wiki/05_hitl_and_gating.md` | HITL·Agent Inbox·승인 흐름 | ~8 |
| `wiki/06_phase2_evaluation.md` | 평가 설계·스키마·합격선 | ~8 |
| `wiki/07_quality_engine.md` | 7층 품질 방어·BFCL·리스크 | ~7 |
| `wiki/08_phase2_tasks.md` | Phase 2 작업 분해·디렉토리 구조 | ~7 |
| **합계** | | **~65 청크 이상** |

청크 크기는 단순분할 기준 약 300~500자 단위 가정 시 문서당 5~10개 청크.

---

## 유효성 규칙

평가셋 소비 코드(`knowledge/eval.py`)가 검사하는 규칙:

1. 항목 수 ≥ 20
2. 필수 필드: `id`, `query`, `answer_doc`, `answer_snippet`, `category`, `difficulty`
3. `category` ∈ `{"semantic", "keyword"}`
4. `difficulty` ∈ `{"easy", "medium", "hard"}`
5. `id` 중복 없음
6. `answer_doc`이 `wiki/`로 시작 (코퍼스 내 파일)
7. keyword 카테고리 항목 ≥ 6

---

*생성일: 2026-06-08 | Phase 2 K0 (US-002) 산출물*
