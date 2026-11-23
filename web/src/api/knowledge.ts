/**
 * knowledge.ts — Knowledge 검색 API (US-G3-5).
 *
 * 백엔드 계약:
 *   POST /api/knowledge/search {query: string, k: number}
 *     → {results: [{text: string, source: string, score: number}]}
 *
 * 오류 시 Error를 throw한다.
 */

export interface KnowledgeResult {
  text: string;
  source: string;
  score: number;
}

export interface KnowledgeSearchResponse {
  results: KnowledgeResult[];
}

/**
 * POST /api/knowledge/search — 지식베이스를 검색한다.
 * @param query 검색 질의
 * @param k 반환 결과 수(기본 5)
 */
export async function searchKnowledge(
  query: string,
  k = 5,
): Promise<KnowledgeSearchResponse> {
  const res = await fetch("/api/knowledge/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, k }),
    // ★US-G3-5: 실 bge reranker 지연(~52s+)·백엔드 read=90s에 맞춰 95s.
    //   30s면 느린 rerank 전에 abort돼 "signal timed out"이 난다.
    signal: AbortSignal.timeout(95000),
  });
  if (!res.ok) {
    throw new Error(`knowledge/search 실패: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as KnowledgeSearchResponse;
}
