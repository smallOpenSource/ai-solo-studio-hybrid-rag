# 지식 레이어 (Phase 2)

## 채택/배제 결정

| 후보 | 판정 | 근거 |
|---|---|---|
| **벡터 RAG** (Qdrant 하이브리드 dense+sparse+RRF, bge-m3) | 유지(1순위) | 소형 로컬모델에 안전 |
| **로컬 리랭커** (bge-reranker-v2-m3) | 신규 채택 | Contextual Retrieval 67% 개선의 상당부분이 "리랭킹 일반" 기여 → 가장 싸고 큰 승, 완전 로컬·한국어 |
| **Contextual Retrieval** (청크에 문서맥락 주입) | 보강 채택(후속) | 검색실패 -49%(+BM25). 인덱싱 1회 비용 $1.02/M(캐싱). Phase 2 비범위 |
| GraphRAG / LightRAG | **영구 배제** | 소형모델 그래프추출 실패/붕괴: GraphRAG는 SLM서 파이프라인 실행 자체 실패, LightRAG 56.9%→35.4% 붕괴 |
| late-interaction (ColBERT/MUVERA) | 보류(차선) | 한국어·단일박스서 벡터RAG 대비 우위 미입증 |

## 파이프라인

```
수집(Crawl4AI 증분 + 직접작성 위키) → Docling 청킹(Hybrid)
  → [Contextual] 각 청크에 문서맥락 1줄 주입 (인덱싱 1회, 오프라인 배치) [후속]
  → 임베딩(bge-m3) → Qdrant 업서트(dense+sparse)
검색: 하이브리드 RRF top-N(예 20) → bge-reranker-v2-m3 재랭크 → top-k≤5 (Context Rot 대응)
```

## 점진 도입 원칙

벡터RAG 하이브리드 → **리랭커 추가(최대 가성비)** → 효과 측정 후 Contextual Retrieval. 한꺼번에 풀스택 금지(과설계 금지).

## Qdrant 배포 방식

**임베디드(qdrant-client 로컬 모드, 디스크 영속)** 채택.

- `QdrantClient(path="state/qdrant")` — 영속
- `QdrantClient(":memory:")` — 테스트 전용
- 별도 서버·도커 불필요. Phase 3 SQLite 단일파일 절제와 동일 결.
- 도커 서버(1B 옵션)는 다중 클라이언트·부하 필요 시 후속 전환 자리 예약.

## bge 임베딩/리랭크 스펙

- **엔드포인트**: `:48802`
- **dim**: 1024 (고정)
- **요청 포맷**: `{"model": ..., "texts": [...]}`
- **임베딩 응답**: `{"dense_embeddings": [[...], ...]}`
- **리랭크 요청**: `{"query": ..., "documents": [...]}`
- **BGEClient 위치**: `core/bge_client.py` (bench/bge_client.py에서 승격, 단일 진실)

## Phase 2 비범위

- **Contextual Retrieval** (청크 맥락 주입) — 후속 1순위 (로컬 gemma 오프라인 배치)
- **하이브리드 sparse 벡터** (BM25/sparse+RRF) — bge 엔드포인트가 dense만 반환, 후속
- **Crawl4AI 야간 증분 크롤링** — 직접 위키 인덱싱이 먼저
- **GraphRAG/LightRAG** — 영구 배제

## conda 환경

- **신규 환경명**: `one_man_corp-knowledge`
- 의존성: `qdrant-client`, `pyyaml`, `python-dotenv`, `httpx`, `pytest`
- 기존 `one_man_corp-bench`, `one_man_corp-engine`과 분리(의존성 격리)
