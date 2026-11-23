#!/usr/bin/env bash
# scripts/serve_knowledge.sh — knowledge HTTP 검색 서비스 기동(Phase 8 P, 옵션 B).
#
# 하는 일:
#   knowledge env가 `state/qdrant`(임베디드 벡터 창고)를 소유하는 작은 FastAPI 서비스
#   (knowledge.service:app)를 uvicorn 단일 워커로 띄운다. engine env 워커(serve_worker.sh)는
#   디스크를 직접 안 열고 이 서비스에 HTTP(POST /search)로 검색을 위임한다 → 워커가 임베디드
#   qdrant 락을 *물리적으로* 안 건드린다(cross-env qdrant 갭 해소·워커×ingest 경합 구조적 0).
#   라우트: POST /search(SearchRequest→SearchResponse) · GET /health(감독·런북 prerequisite).
#
# ★단일 인스턴스(동시성 1 — H-13 단일 디스크 소유자): uvicorn --workers 1로 못 박는다.
#   qdrant 임베디드는 프로세스 단위 파일락이라 state/qdrant를 여는 프로세스가 이 서비스
#   하나여야 한다(워커 동시성 1 × 서비스 단일 = 정합). 멀티워커면 같은 디스크를 동시 오픈해
#   "already accessed" RuntimeError(store.py:88-101)가 난다.
#
# ★서비스 가동 중에는 ingest 금지(H-13 이동·미제거): ingest(knowledge.ingest)는 여전히
#   state/qdrant를 직접 오픈(ingest.py:137)하므로, 이 서비스가 떠 있는 동안 ingest를 돌리면
#   디스크를 둘이 열어 충돌한다. 시드 코퍼스 ingest는 서비스 stop 후(또는 후속 ingest 엔드포인트)에.
#
# 사용법:
#   scripts/serve_knowledge.sh            # endpoint.yaml knowledge.service host/port로 기동
#   헬스체크: curl -fsS http://127.0.0.1:<port>/health  → 200 {"status":"ok"}
#
# ★런북 권고(사용자 영역): systemd/supervisor로 web·worker·knowledge-svc 3 unit 감독
#   (Restart=on-failure, knowledge-svc는 /health 기반). 배포 자동화·unit 파일은 비범위.
#
# 환경: 백엔드 conda env one_man_corp-knowledge(qdrant_client 보유).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="one_man_corp-knowledge"

# host/port는 config/endpoint.yaml knowledge.service 블록에서 읽는다(코드 하드코딩 0).
# 블록이 없으면 안전한 기본값(127.0.0.1:8810) — knowledge_client.py 기본 URL과 정합.
read -r HOST PORT < <(
  PYTHONPATH="$ROOT" conda run --no-capture-output -n "$CONDA_ENV" python - <<'PY'
from core.config import load_endpoint_config

cfg = load_endpoint_config().get("knowledge", {}) or {}
svc = cfg.get("service", {}) or {}
print(svc.get("host", "127.0.0.1"), int(svc.get("port", 8810)))
PY
)

echo "[serve_knowledge] knowledge 검색 서비스 기동(uvicorn --workers 1, ${HOST}:${PORT})."
echo "[serve_knowledge] ★state/qdrant 단일 소유자 — 가동 중 ingest 금지(서비스 stop 후 ingest)."
exec conda run --no-capture-output -n "$CONDA_ENV" \
  python -m uvicorn knowledge.service:app --workers 1 --host "$HOST" --port "$PORT"
