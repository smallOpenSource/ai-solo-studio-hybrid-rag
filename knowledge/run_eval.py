"""실서버 평가 오케스트레이션 + 게이트 A 엔진실증 — Phase 2 K8 (US-007).

이 모듈은 **실서버**(bge 임베딩·리랭크 서버, gemma LLM 서버)에 처음 접촉하는
오케스트레이션이다. 오프라인 게이트(`pytest -m "not slow"`)가 전부 green인 뒤에만
돌린다(계획 §0.5 전제 #9). 세 갈래로 나뉜다(계획 §0 게이트 A):

  ① --smoke    : 풀런 전제 확인. 실 bge 임베딩 1콜·리랭크 1콜 + Qdrant 실
                 서버(임시 컬렉션) 업서트→재오픈→검색 왕복 1회. 실패 시 body를
                 보존·출력하고 중단한다(무한 재시도 금지 — core 클라이언트가
                 네트워크 오류만 2회 재시도).
  ② --full     : 게이트 A-측정. wiki/ 인덱싱(실 bge 순차 임베딩) →
                 cases/knowledge_eval.jsonl로 evaluate(실 bge 리랭크) →
                 report.md(dense vs 리랭커 Hit@k·MRR 비교표 + dense회수/리랭크
                 실패 분리집계 + 카테고리별 + top-k 순위변경 사례)를
                 results/phase2/<ts>/에 보존. (knowledge/eval.py 재사용.)
  ③ --engine-demo : 게이트 A-엔진실증. **게이팅 켠 경로(run_full류)**로 tool 노드
                 1개를 실행한다 — 실 gemma LLM이 추출 1콜(thinking=OFF)로
                 knowledge_search 인자를 뽑고, always-allow라 suspend 없이
                 knowledge_search를 디스패치해 실 검색 결과를 돌려받는다.
                 이벤트(RUN_STARTED→TOOL_CALL_START→TOOL_CALL_RESULT→RUN_FINISHED)
                 와 결과를 results/phase2/에 보존한다.

설계 메모(불변 제약):
- 동시성 1: bge·LLM 호출은 전부 순차다(동시성 코드 없음). 엔진실증의 LLM 추출도
  1콜(gemma×OFF)뿐 — "LLM 0"은 인덱싱 한정(계획 rev2 M1).
- 키는 .env에서만(소스 grep 0). bge·LLM 클라이언트가 core.config 경유로 읽는다.
- 기존 모듈 재사용: ingest·evaluate·write_report(knowledge), WorkflowEngine·
  GatingPolicy·NodeExecutor·MOCK_TOOLS(office). knowledge_search는 이미
  tools_policy.yaml에 always-allow로 등록돼 있다(US-006).
- 실서버 5xx 지속·연결 불가 시: body 보존 출력·보고하고 멈춘다(무한 재시도 금지).

CLI:
    conda run -n one_man_corp-knowledge python -m knowledge.run_eval --smoke
    conda run -n one_man_corp-knowledge python -m knowledge.run_eval --full
    conda run -n one_man_corp-knowledge python -m knowledge.run_eval --engine-demo
    conda run -n one_man_corp-knowledge python -m knowledge.run_eval --all
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).parent.parent

# 기본 입력·출력 경로(계획 산출물).
_DEFAULT_WIKI_DIR = "wiki"
_DEFAULT_EVAL_JSONL = "cases/knowledge_eval.jsonl"
_RESULTS_DIR = _PROJECT_ROOT / "results" / "phase2"

# 엔진실증 모델 — office 기본(전제 #1 gemma-4-e4b-it). thinking=OFF.
_ENGINE_DEMO_MODEL = "gemma-4-e4b-it"
# 엔진실증 검색 질의(LLM이 이 프롬프트에서 knowledge_search 인자를 추출).
_ENGINE_DEMO_PROMPT = (
    "회사 지식 베이스에서 워크플로 엔진에 대한 내용을 찾아줘. "
    'knowledge_search 도구를 query="워크플로 엔진"으로 호출해.'
)


def _ts() -> str:
    """UTC 타임스탬프(파일명·디렉토리용)."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _print_server_error(label: str, exc: Exception) -> None:
    """실서버 오류를 body 보존하며 출력한다(무한 재시도 금지 — 보고 후 중단).

    LLMClient/BGEClient(httpx)는 HTTPStatusError 메시지에 이미 body[:300]를
    실어 둔다. 여기서는 그 메시지를 그대로 보존·출력한다.
    """
    print(f"[run_eval] {label} 실패 — 실서버 오류(body 보존):", file=sys.stderr)
    print(f"  {type(exc).__name__}: {exc}", file=sys.stderr)
    print(
        "[run_eval] 무한 재시도 금지 — 오류 보존·보고하고 중단합니다 "
        "(서버 5xx 지속·연결 불가 시 사용자 확인 필요).",
        file=sys.stderr,
    )


# ── ① smoke ───────────────────────────────────────────────────────────────────


def run_smoke() -> dict[str, Any]:
    """풀런 전제 확인 — 실 bge 임베딩·리랭크 1콜 + Qdrant 실 path 왕복.

    절차(계획 K8 smoke):
      1) BGEClient.embed(["..."]) 1콜 → dim=1024 단언.
      2) BGEClient.rerank(query, [2문서]) 1콜 → 정규화 가능 응답 단언.
      3) KnowledgeStore(서버·임시 컬렉션)에 2청크 업서트 →
         **재오픈** → search_dense 왕복 → 결과 회수 단언. (영속 검증 — pre-mortem 2.)

    Returns:
        {"ok": bool, "embed_dim": int, "rerank_items": int,
         "roundtrip_hits": int, "collection": str, "elapsed_ms": float}.
        실패 시 ok=False이고 호출부가 중단한다(예외는 여기서 잡아 보존·출력).
    """
    from core.bge_client import BGEClient
    from knowledge.chunk import Chunk
    from knowledge.store import KnowledgeStore

    t0 = time.perf_counter()
    bge = BGEClient()

    # 1) 임베딩 1콜 — dim=1024.
    try:
        vecs = bge.embed(["워크플로 엔진 스모크 테스트 문장"])
    except Exception as exc:  # noqa: BLE001 — 실서버 오류 보존
        _print_server_error("bge 임베딩 smoke", exc)
        return {"ok": False, "stage": "embed", "error": str(exc)}
    if not vecs or len(vecs[0]) != 1024:
        print(
            f"[run_eval] smoke 실패 — 임베딩 dim={len(vecs[0]) if vecs else 0} != 1024",
            file=sys.stderr,
        )
        return {
            "ok": False,
            "stage": "embed_dim",
            "embed_dim": len(vecs[0]) if vecs else 0,
        }
    embed_dim = len(vecs[0])

    # 2) 리랭크 1콜 — 정규화 가능 응답.
    try:
        raw = bge.rerank(
            "워크플로 엔진이란?", ["워크플로 엔진은 노드 그래프다.", "무관한 문장."]
        )
    except Exception as exc:  # noqa: BLE001
        _print_server_error("bge 리랭크 smoke", exc)
        return {"ok": False, "stage": "rerank", "error": str(exc)}
    from knowledge.search import _normalize_rerank

    ranked = _normalize_rerank(raw, n_docs=2)
    if not ranked:
        print("[run_eval] smoke 실패 — 리랭크 응답 정규화 0건", file=sys.stderr)
        return {"ok": False, "stage": "rerank_norm", "rerank_items": 0}
    rerank_items = len(ranked)

    # 3) Qdrant 업서트 → 재오픈 → 검색 왕복.
    #    임시 컬렉션으로 본 코퍼스(knowledge)를 건드리지 않는다.
    smoke_collection = f"_smoke_{_ts()}"
    try:
        store = KnowledgeStore(collection=smoke_collection, embedder=bge)
        chunks = [
            Chunk(
                text="워크플로 엔진은 노드 그래프를 순회한다.",
                source="smoke.md",
                chunk_id=0,
                meta={},
            ),
            Chunk(
                text="게이팅은 도구 실행 직전에 판정한다.",
                source="smoke.md",
                chunk_id=1,
                meta={},
            ),
        ]
        upserted = store.upsert(chunks)
        # 재오픈 — 서버 재접속으로 영속 검색 왕복을 검증한다(server persistence).
        del store
        store2 = KnowledgeStore(collection=smoke_collection, embedder=bge)
        hits = store2.search_dense("워크플로 엔진", top_n=5)
        roundtrip_hits = len(hits)
        # 임시 컬렉션 정리(본 코퍼스 영속과 분리).
        try:
            store2._client.delete_collection(smoke_collection)  # noqa: SLF001
        except Exception:  # noqa: BLE001 — 정리 실패는 smoke 판정에 무관
            pass
    except Exception as exc:  # noqa: BLE001
        _print_server_error("Qdrant 실 path 왕복 smoke", exc)
        return {"ok": False, "stage": "qdrant", "error": str(exc)}

    elapsed_ms = (time.perf_counter() - t0) * 1000
    ok = upserted == 2 and roundtrip_hits >= 1
    result = {
        "ok": ok,
        "embed_dim": embed_dim,
        "rerank_items": rerank_items,
        "upserted": upserted,
        "roundtrip_hits": roundtrip_hits,
        "collection": smoke_collection,
        "elapsed_ms": round(elapsed_ms, 1),
    }
    return result


# ── ② full(측정) ───────────────────────────────────────────────────────────────


def run_full_eval(
    wiki_dir: str = _DEFAULT_WIKI_DIR,
    eval_jsonl: str = _DEFAULT_EVAL_JSONL,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """게이트 A-측정 — wiki 인덱싱 → 평가 풀런(실 bge) → report 보존.

    절차(계획 K8 풀런):
      1) ingest(wiki_dir): 마크다운 청킹 → 실 bge 순차 임베딩 → Qdrant 업서트.
      2) evaluate(eval_jsonl): 각 질의를 ① dense-only ② dense+리랭커로 채점,
         실패를 dense회수/리랭크로 분리집계(knowledge/eval.py).
      3) write_report: report.md(비교표·분리집계·순위변경 사례) + raw.jsonl +
         per_query.jsonl을 results/phase2/<ts>/에 보존.

    Args:
        wiki_dir: 인덱싱할 위키 디렉토리(기본 wiki/).
        eval_jsonl: 평가셋 경로(기본 cases/knowledge_eval.jsonl).
        out_dir: 리포트 출력 디렉토리. None이면 results/phase2/<ts>/.

    Returns:
        {"n", "upserted", "report_path", "failure_breakdown",
         "rank_changed_count", "metrics"} 요약. 실서버 오류 시 호출부가 중단.
    """
    from knowledge.eval import evaluate, write_report
    from knowledge.ingest import ingest
    from knowledge.search import _default_top_n
    from knowledge.store import KnowledgeStore

    store = KnowledgeStore()  # 서버 컬렉션 knowledge (endpoint.yaml)

    # 1) 인덱싱(실 bge 순차 임베딩).
    print(f"[run_eval] 인덱싱 시작: {wiki_dir} → 컬렉션 '{store.collection}'")
    upserted = ingest(wiki_dir, store)
    print(
        f"[run_eval] 인덱싱 완료: {upserted}개 청크 업서트 (총 {store.count()}개 포인트)"
    )

    # 2) 평가 풀런(실 bge 리랭크) — 같은 store 재사용(검색은 store.search_dense).
    top_n = _default_top_n()
    print(f"[run_eval] 평가 풀런: {eval_jsonl} (top_n={top_n}, 실 bge 리랭크)")
    result = evaluate(eval_jsonl, store, top_n=top_n)

    # 3) report 보존.
    if out_dir is None:
        out_dir = _RESULTS_DIR / _ts()
    report_path = write_report(result, out_dir)

    summary = {
        "n": result["n"],
        "upserted": upserted,
        "points": store.count(),
        "report_path": str(report_path),
        "out_dir": str(Path(report_path).parent),
        "failure_breakdown": result["failure_breakdown"],
        "rank_changed_count": len(result["rank_changed_examples"]),
        "rank_changed_examples": result["rank_changed_examples"],
        "metrics": result["metrics"],
    }
    return summary


# ── ③ engine-demo(엔진실증) ─────────────────────────────────────────────────────


def _make_knowledge_demo_node() -> Any:
    """knowledge_search를 부르는 단일 tool 노드(gemma×OFF).

    crm_notify 패턴과 동일 — kind="tool", tools=[KNOWLEDGE_SEARCH_SCHEMA],
    thinking="off", next=None(워크플로 종료). 인라인 프롬프트로 LLM이 도구
    인자(query)를 추출하게 한다.
    """
    from office.nodes import Node
    from office.tools import KNOWLEDGE_SEARCH_SCHEMA

    return Node(
        id="n_knowledge_search",
        kind="tool",
        prompt=_ENGINE_DEMO_PROMPT,
        thinking="off",
        tools=[KNOWLEDGE_SEARCH_SCHEMA],
        next=None,
    )


def run_engine_demo(out_dir: Path | None = None) -> dict[str, Any]:
    """게이트 A-엔진실증 — 게이팅 켠 경로로 실 LLM→knowledge_search 1회 호출.

    **정직한 실증**(계획 rev2 M1/M2): run_full류와 동일하게 GatingPolicy()를
    주입한 WorkflowEngine으로 단일 tool 노드를 돌린다. 실 gemma LLM이 추출
    1콜(thinking=OFF)로 knowledge_search 인자를 뽑고, knowledge_search가
    always-allow라 suspend 없이 실 검색(dense→리랭커)을 디스패치한다.

    이벤트(RUN_STARTED→TOOL_CALL_START→TOOL_CALL_RESULT→RUN_FINISHED)를
    EventStore에서 읽어 gate_a_engine_events.jsonl로, 결과 요약을 JSON으로
    results/phase2/에 보존한다.

    Args:
        out_dir: 산출물 디렉토리. None이면 results/phase2/<ts>_engine_demo/.

    Returns:
        {"ok", "status", "run_id", "events", "tool_called", "result_count",
         "events_path", "result_path"} 요약. 실 LLM 오류 시 ok=False(보존·보고).
    """
    import tempfile

    from core.llm_client import LLMClient
    from office.engine import NodeExecutor, WorkflowEngine
    from office.event_store import EventStore
    from office.gating import GatingPolicy
    from office.tools import MOCK_TOOLS

    if out_dir is None:
        out_dir = _RESULTS_DIR / f"{_ts()}_engine_demo"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 엔진실증 전용 임시 DB(본 office.db 오염 방지) — 이벤트만 보존하면 충분.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = EventStore(db_path)
    # 실서버 LLM(gemma) — run_full과 동일. 도구는 실제 knowledge_search 위임.
    llm = LLMClient()
    gating_policy = (
        GatingPolicy()
    )  # config/tools_policy.yaml — knowledge_search=always-allow
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=MOCK_TOOLS,  # knowledge_search → office.tools.knowledge_search(실 search 위임)
        gating_policy=gating_policy,  # ★게이팅 켠 경로(정직한 실증)
    )

    run_id = f"gate_a_engine_{_ts()}"
    initial_state: dict[str, Any] = {"model": _ENGINE_DEMO_MODEL}

    print(
        f"[run_eval] 엔진실증(게이팅 ON): run_full류 경로, 모델={_ENGINE_DEMO_MODEL}, "
        "thinking=OFF, knowledge_search(always-allow)"
    )
    try:
        state = engine.run(
            nodes=[_make_knowledge_demo_node()],
            initial_state=initial_state,
            run_id=run_id,
            workflow="gate_a_knowledge_query",
        )
    except Exception as exc:  # noqa: BLE001 — 실 LLM 오류 보존
        _print_server_error("엔진실증 LLM 호출", exc)
        return {"ok": False, "stage": "engine_run", "error": str(exc), "run_id": run_id}

    status = state.get("_workflow_status", "unknown")

    # 이벤트를 DB에서 읽어 JSONL로 보존(단일 진실 = EventStore).
    events_path = out_dir / "gate_a_engine_events.jsonl"
    n_events = store.export_jsonl(run_id, events_path)
    rows = store.read_rows(run_id)
    event_types = [r["type"] for r in rows]

    # 도구 호출·결과 추출(TOOL_CALL_RESULT payload).
    tool_called = False
    result_count: int | None = None
    tool_result: dict[str, Any] | None = None
    extracted_args: dict[str, Any] | None = None
    for r in rows:
        payload = json.loads(r["payload_json"])
        if (
            payload.get("type") == "TOOL_CALL_START"
            and payload.get("name") == "knowledge_search"
        ):
            tool_called = True
            extracted_args = payload.get("args", {})
        if (
            payload.get("type") == "TOOL_CALL_RESULT"
            and payload.get("name") == "knowledge_search"
        ):
            tool_result = payload.get("result", {})
            if isinstance(tool_result, dict):
                result_count = tool_result.get("count")

    # suspend 없이 통과했는지(always-allow) — INTERRUPT_REQUESTED가 없어야 한다.
    suspended = "INTERRUPT_REQUESTED" in event_types or status == "suspended"
    ok = (
        status == "success"
        and tool_called
        and not suspended
        and result_count is not None
    )

    summary: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "run_id": run_id,
        "n_events": n_events,
        "event_types": event_types,
        "tool_called": tool_called,
        "extracted_args": extracted_args,
        "result_count": result_count,
        "suspended": suspended,
        "events_path": str(events_path),
    }
    # 결과 요약 JSON 보존.
    result_path = out_dir / "gate_a_engine_result.json"
    result_payload = dict(summary)
    if tool_result is not None:
        # 검색 결과 본문은 길 수 있어 source·score만 요약 + count.
        result_payload["tool_result_preview"] = {
            "count": tool_result.get("count"),
            "results": [
                {"source": x.get("source"), "score": x.get("score")}
                for x in (tool_result.get("results") or [])
            ],
        }
    result_path.write_text(
        json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary["result_path"] = str(result_path)
    summary["out_dir"] = str(out_dir)
    summary["tool_result_preview"] = result_payload.get("tool_result_preview")
    return summary


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점 — --smoke / --full / --engine-demo / --all.

    오프라인 게이트(`pytest -m "not slow"`)가 green인 뒤에만 실서버를 친다.
    각 단계는 실패 시 body를 보존·출력하고 0이 아닌 코드로 종료한다(무한 재시도
    금지). --all은 smoke 성공을 풀런·엔진실증의 전제로 둔다.
    """
    parser = argparse.ArgumentParser(
        description="실서버 평가 오케스트레이션 + 게이트 A 엔진실증 (Phase 2 K8)"
    )
    parser.add_argument(
        "--smoke", action="store_true", help="① 실 bge·Qdrant 왕복 smoke"
    )
    parser.add_argument(
        "--full", action="store_true", help="② 인덱싱→평가 풀런→report 보존"
    )
    parser.add_argument(
        "--engine-demo",
        action="store_true",
        help="③ 게이팅 켠 경로 LLM→knowledge_search 실증",
    )
    parser.add_argument(
        "--all", action="store_true", help="①→②→③ 순차(smoke 성공 전제)"
    )
    parser.add_argument(
        "--wiki-dir", default=_DEFAULT_WIKI_DIR, help="인덱싱 위키 디렉토리"
    )
    parser.add_argument(
        "--eval-jsonl", default=_DEFAULT_EVAL_JSONL, help="평가셋 JSONL 경로"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    do_smoke = args.smoke or args.all
    do_full = args.full or args.all
    do_demo = args.engine_demo or args.all
    if not (do_smoke or do_full or do_demo):
        parser.error("최소 하나가 필요: --smoke / --full / --engine-demo / --all")
        return 2

    # ① smoke
    if do_smoke:
        print("=" * 60)
        print("① smoke — 실 bge 임베딩·리랭크 1콜 + Qdrant 실 path 왕복")
        smoke = run_smoke()
        print(json.dumps(smoke, ensure_ascii=False, indent=2))
        if not smoke.get("ok"):
            print("[run_eval] smoke 실패 — 풀런·엔진실증 중단.", file=sys.stderr)
            return 1
        print(
            f"[run_eval] smoke OK: dim={smoke['embed_dim']}, "
            f"rerank_items={smoke['rerank_items']}, roundtrip_hits={smoke['roundtrip_hits']}, "
            f"{smoke['elapsed_ms']}ms"
        )

    # ② full(측정)
    if do_full:
        print("=" * 60)
        print("② full — wiki 인덱싱 → 평가 풀런(실 bge) → report 보존")
        try:
            full = run_full_eval(wiki_dir=args.wiki_dir, eval_jsonl=args.eval_jsonl)
        except Exception as exc:  # noqa: BLE001 — 실서버 오류 보존
            _print_server_error("평가 풀런", exc)
            return 1
        print(
            f"[run_eval] 풀런 완료: n={full['n']}, 업서트={full['upserted']}, "
            f"포인트={full['points']}"
        )
        print(f"[run_eval] 실패 분리집계: {full['failure_breakdown']}")
        print(f"[run_eval] top-k 순위변경 관측: {full['rank_changed_count']}건")
        print(f"[run_eval] report: {full['report_path']}")

    # ③ engine-demo(엔진실증)
    if do_demo:
        print("=" * 60)
        print("③ engine-demo — 게이팅 켠 경로 실 LLM→knowledge_search 1회 호출")
        demo = run_engine_demo()
        print(
            json.dumps(
                {k: v for k, v in demo.items() if k != "tool_result_preview"},
                ensure_ascii=False,
                indent=2,
            )
        )
        if demo.get("tool_result_preview"):
            print(f"[run_eval] 검색 결과 미리보기: {demo['tool_result_preview']}")
        if not demo.get("ok"):
            print("[run_eval] 엔진실증 실패.", file=sys.stderr)
            return 1
        print(
            f"[run_eval] 엔진실증 OK: status={demo['status']}, "
            f"tool_called={demo['tool_called']}, suspend={demo['suspended']}, "
            f"result_count={demo['result_count']}"
        )
        print(f"[run_eval] 이벤트: {demo['events_path']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
