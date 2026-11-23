# Phase 2 지식 검색 평가 설계

## 평가 목표

자체 평가셋(질의-정답 쌍 ≥20)으로 **dense-only vs dense+리랭커**를 Hit@k·MRR로 비교한다. 통계 입증이 아니라 방향·사례 관찰이 목적이다(소표본 신호용).

## 평가셋 스키마

`cases/knowledge_eval.jsonl` 각 항목 형식:

```json
{
  "id": "ke-01",
  "query": "한국어 질문",
  "answer_doc": "wiki/03_knowledge_layer.md",
  "answer_snippet": "정답 청크 텍스트 일부",
  "category": "semantic|keyword",
  "difficulty": "easy|medium|hard"
}
```

## 카테고리 정의

- **semantic** (의미검색 일반): 개념·요약 질의. dense 벡터로 찾을 수 있는 케이스.
- **keyword** (키워드 정확매칭): 고유명사(예: `gemma-4-e4b-it`, `Qdrant`, `bge-m3-ko`), 숫자(예: `12GB`, `동시성 1`), 코드토큰(예: `_record_decision`, `knowledge_search`). sparse 신호 측정용으로 ≥6건 필수.

## 난이도 정의

- **easy**: 질의 키워드가 정답 청크에 그대로 있어 dense가 잘 찾음
- **medium**: 동의어·패러프레이즈가 필요하거나 섹션이 분산됨
- **hard**: 여러 청크에 걸쳐 있거나 dense 회수실패 가능성 있음

## 합격선 (rev2 M4 재정의)

합격 = 다음 셋이 모두 충족:
1. **비교표 산출** — dense-only vs dense+리랭커의 Hit@k(k=1,3,5)·MRR 표가 나옴
2. **실패 분리집계** — dense 회수실패(top-N에도 없음=sparse 신호) vs 리랭크실패(top-N엔 있으나 top-k 밖)가 분리 집계됨
3. **최소 1사례** — 리랭커가 top-k 순위를 바꾸는 것이 관측됨

**"통계 유의 주장 금지"** — ≥20 소표본에서 통계적 유의를 주장하지 않는다.

## 라벨 무결성 (자기참조 방지)

정답 청크를 **질의 작성 전에 독립 선정**한다:

1. 코퍼스 문서를 읽고 "이 청크/섹션이 특정 주제의 정답"을 먼저 목록화
2. 그 정답에 대한 자연스러운 한국어 질의를 나중에 작성
3. 질의가 정답 청크 텍스트를 과도하게 복사하지 않도록 패러프레이즈

이로써 "dense가 잘 찾는 청크"와 "정답"이 상관되는 편향을 방지한다.

## 평가 지표

- **Hit@k** (k=1,3,5): 정답이 상위 k개 안에 든 비율
- **MRR** (Mean Reciprocal Rank): 정답의 평균 역순위 (1등=1, 2등=0.5, ...)
- **dense 회수실패율**: 정답이 top-N(예 20)에도 없는 비율 → sparse 도입 트리거
- **리랭크실패율**: top-N엔 있으나 리랭크 후 top-k 밖인 비율 → 리랭커 한계 측정

## knowledge_search 도구 계약

```json
{
  "name": "knowledge_search",
  "parameters": {
    "query": "string (필수)",
    "k": "int (선택, 기본 5, top-k≤5)",
    "filter": "object|null (선택, 출처/태그 필터)"
  }
}
```

출력: `{"results": [{"text": str, "source": str, "score": float}], "count": int}`
