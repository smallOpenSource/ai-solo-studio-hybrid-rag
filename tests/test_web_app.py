"""US-002(T2) 웹서버 골격 + SSE 스트림 통합 테스트 — FastAPI TestClient.

오프라인 결정론(실서버·LLM 0). tmp_path db 주입으로 state/office.db 무오염.
slow 마커 없음.

테스트 범위(수용 기준 4·5·18):
- /events/stream: mock DB에 이벤트 append → SSE 응답에 id:{seq}·data:{원문
  payload_json}이 발행 순서대로(원문 보존·image_url·NodeFailed.reason 무변형).
- 잘못된 reason 1건이 스트림을 5xx로 안 떨굼(payload_json 원문 패스스루라
  event_from_dict 미경유 — DB에 비표준 reason JSON을 심고 그대로 흐르는지).
- Last-Event-ID 재연결: 그 seq 이후만, 결손·중복 0.
- 정적 마운트 404/정상.
- config/endpoint.yaml web 블록 로딩(코드 하드코딩 0).

TestClient SSE는 무한 스트림이라, 짧게 읽고 끊는 패턴(httpx stream + 제한
읽기)으로 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from core.config import get_web_config
from office.db import get_connection
from office.event_store import EventStore
from office.events import NodeFailed, RunFinished, RunStarted, TextMessage
from office.events import FailReason
from web_server.app import create_app
from web_server.sse import EventFanout


# ── 헬퍼 ────────────────────────────────────────────────────────────────────


def _ts() -> str:
    return "2026-06-08T00:00:00"


def _store(tmp_path: Path) -> EventStore:
    """테스트용 EventStore — tmp_path/office.db (실 파일 생성 금지)."""
    return EventStore(db_path=tmp_path / "office.db")


def _read_sse_frames(
    client: TestClient,
    *,
    expected: int,
    last_event_id: int | None = None,
    idle_close_ms: int = 700,
) -> list[tuple[int, str]]:
    """/events/stream을 짧게 읽어 (id, data) 프레임을 expected개 모은다.

    idle_close_ms로 스트림이 "밀린 이벤트를 다 받고 더 안 오면 스스로 종료"
    하게 한다(Starlette TestClient는 응답을 완결까지 버퍼링하므로 무한 스트림은
    쓸 수 없다 — 유휴 종료가 짧게 읽고 끊는 패턴의 핵심). ping 코멘트(`:`로
    시작)는 건너뛴다.

    Args:
        last_event_id: 주면 query 파라미터로 재연결 커서를 전달한다.
        idle_close_ms: 신규 이벤트가 이만큼 없으면 스트림이 정상 종료한다.
    """
    params: dict[str, str] = {"idle_close_ms": str(idle_close_ms)}
    if last_event_id is not None:
        params["last_event_id"] = str(last_event_id)

    frames: list[tuple[int, str]] = []
    cur_id: int | None = None
    with client.stream("GET", "/events/stream", params=params) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            # httpx iter_lines는 개행을 떼고 준다. 빈 줄 = 프레임 경계.
            if line.startswith(":"):
                continue  # keep-alive ping
            if line.startswith("id:"):
                cur_id = int(line[len("id:") :].strip())
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
                assert cur_id is not None, "data 앞에 id가 와야 한다"
                frames.append((cur_id, data))
                cur_id = None
                if len(frames) >= expected:
                    break
    return frames


# ── /events/stream: 발행 순서대로 원문 패스스루 ───────────────────────────────


def test_stream_emits_events_in_order_raw(tmp_path: Path) -> None:
    """SSE가 mock DB 이벤트를 발행 순서대로 id:{seq}·data:{원문}으로 흘린다."""
    store = _store(tmp_path)
    fanout = EventFanout(store)
    # 미리 적재해 둔다(poller가 없어도 큐에 들어가 있게).
    s1 = store.append(RunStarted(run_id="r1", ts=_ts(), workflow="crm_notify"))
    s2 = store.append(TextMessage(run_id="r1", ts=_ts(), content="직원 인사"))
    s3 = store.append(RunFinished(run_id="r1", ts=_ts(), status="success"))
    fanout.poll_once()

    app = create_app(store=store, fanout=fanout)
    with TestClient(app) as client:
        frames = _read_sse_frames(client, expected=3)

    got_ids = [fid for (fid, _) in frames]
    assert got_ids == [s1, s2, s3]

    # 각 data가 DB 저장 원문과 글자 단위로 일치한다(원문 패스스루).
    rows = store.read_rows("r1")
    stored = {row["seq"]: row["payload_json"] for row in rows}
    for fid, data in frames:
        assert data == stored[fid], f"seq {fid} 원문 불일치"


def test_stream_preserves_image_url_and_reason(tmp_path: Path) -> None:
    """image_url·NodeFailed.reason이 SSE에서 변형 없이 보존된다."""
    store = _store(tmp_path)
    fanout = EventFanout(store)
    store.append(
        TextMessage(run_id="r2", ts=_ts(), content="이미지", image_url="work/1.png")
    )
    store.append(
        NodeFailed(run_id="r2", ts=_ts(), node_id="n3", reason=FailReason.escalation)
    )
    fanout.poll_once()

    app = create_app(store=store, fanout=fanout)
    with TestClient(app) as client:
        frames = _read_sse_frames(client, expected=2)

    payloads = [json.loads(data) for (_, data) in frames]
    text_evt = next(p for p in payloads if p["type"] == "TEXT_MESSAGE")
    assert text_evt["image_url"] == "work/1.png"
    fail_evt = next(p for p in payloads if p["type"] == "NODE_FAILED")
    assert fail_evt["reason"] == "escalation"


# ── 잘못된 reason 1건이 스트림을 5xx로 안 떨굼 ────────────────────────────────


def test_stream_bad_reason_does_not_5xx(tmp_path: Path) -> None:
    """비표준 reason JSON을 DB에 직접 심어도 스트림이 원문 그대로 흘린다.

    payload_json 원문 패스스루라 event_from_dict(reason ValueError)를 안 탄다.
    스트림은 200으로 유지되고, 그 비표준 payload가 글자 그대로 나온다.
    """
    store = _store(tmp_path)
    fanout = EventFanout(store)

    bad_payload = json.dumps(
        {
            "run_id": "r3",
            "ts": _ts(),
            "type": "NODE_FAILED",
            "family": "Interrupt",
            "schema_v": 1,
            "node_id": "n1",
            "reason": "totally-invalid-reason",  # FailReason enum에 없는 값
            "detail": "비표준",
        },
        ensure_ascii=False,
    )
    with get_connection(tmp_path / "office.db") as conn:
        conn.execute(
            """
            INSERT INTO events (run_id, family, type, payload_json, ts, schema_v)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("r3", "Interrupt", "NODE_FAILED", bad_payload, _ts(), 1),
        )
        conn.commit()
    fanout.poll_once()

    app = create_app(store=store, fanout=fanout)
    with TestClient(app) as client:
        # status_code 200 단언은 _read_sse_frames 안에서 수행된다.
        frames = _read_sse_frames(client, expected=1)

    assert len(frames) == 1
    _, data = frames[0]
    # 원문 그대로 — 비표준 reason이 변형·차단 없이 흐른다.
    assert data == bad_payload
    assert json.loads(data)["reason"] == "totally-invalid-reason"


# ── Last-Event-ID 재연결: 결손·중복 0 ────────────────────────────────────────


def test_stream_last_event_id_resume_no_gap_no_dup(tmp_path: Path) -> None:
    """Last-Event-ID(=seq) 재연결 시 그 이후만, 결손·중복 0으로 이어받는다."""
    store = _store(tmp_path)
    fanout = EventFanout(store)
    seqs = []
    for i in range(5):
        seqs.append(store.append(TextMessage(run_id="r4", ts=_ts(), content=f"m{i}")))
    fanout.poll_once()

    app = create_app(store=store, fanout=fanout)
    with TestClient(app) as client:
        # seqs[1] 이후로 재연결 → 3,4,5 (seqs[2..4])
        frames = _read_sse_frames(client, expected=3, last_event_id=seqs[1])

    got_ids = [fid for (fid, _) in frames]
    assert got_ids == [seqs[2], seqs[3], seqs[4]]
    # 결손 0: seqs[1] 이하가 안 나온다. 중복 0: 각 seq 1회뿐.
    assert seqs[0] not in got_ids and seqs[1] not in got_ids
    assert len(got_ids) == len(set(got_ids))


def test_stream_last_event_id_via_header(tmp_path: Path) -> None:
    """Last-Event-ID 표준 헤더로도 재연결 커서가 동작한다."""
    store = _store(tmp_path)
    fanout = EventFanout(store)
    seqs = [
        store.append(TextMessage(run_id="r5", ts=_ts(), content=f"h{i}"))
        for i in range(3)
    ]
    fanout.poll_once()

    app = create_app(store=store, fanout=fanout)
    with TestClient(app) as client:
        with client.stream(
            "GET",
            "/events/stream",
            params={"idle_close_ms": "700"},
            headers={"Last-Event-ID": str(seqs[0])},
        ) as resp:
            assert resp.status_code == 200
            got_ids: list[int] = []
            for line in resp.iter_lines():
                if line.startswith("id:"):
                    got_ids.append(int(line[len("id:") :].strip()))
                    if len(got_ids) >= 2:
                        break

    assert got_ids == [seqs[1], seqs[2]]


# ── 백그라운드 폴링: append 후 라이브로 흘러나옴(poll_once 수동 호출 없이) ──────


def test_stream_live_append_flows_via_poller(tmp_path: Path) -> None:
    """fanout을 주입하지 않으면 앱이 만든 poller가 0.5초 폴링으로 라이브 적재.

    store만 주입하고 fanout은 앱이 만들게 둔다 → lifespan의 FanoutPoller가
    DB를 폴링한다. append한 이벤트가 수동 poll_once 없이 스트림에 나온다.
    """
    store = _store(tmp_path)
    app = create_app(store=store)  # fanout 미주입 → 앱이 생성·poller 폴링

    with TestClient(app) as client:
        # TestClient 진입으로 lifespan 시작 → poller 가동.
        s1 = store.append(TextMessage(run_id="r6", ts=_ts(), content="라이브"))
        # poller(0.5초)가 적재할 때까지 idle 창(2초)을 넉넉히 둬 스트림이
        # 라이브 이벤트를 받고 나서 종료하게 한다.
        frames = _read_sse_frames(client, expected=1, idle_close_ms=2000)

    assert len(frames) == 1
    assert frames[0][0] == s1
    assert json.loads(frames[0][1])["content"] == "라이브"


# ── 정적 마운트 404/정상 ─────────────────────────────────────────────────────


def test_static_work_serves_image(tmp_path: Path) -> None:
    """work/ 정적 마운트가 실제 이미지(work/1.png)를 200으로 서빙한다."""
    store = _store(tmp_path)
    app = create_app(store=store)
    with TestClient(app) as client:
        resp = client.get("/work/1.png")
    # work/1.png는 프로젝트에 실재한다.
    assert resp.status_code == 200


def test_static_missing_returns_404(tmp_path: Path) -> None:
    """없는 정적 파일은 404(스트림·앱이 죽지 않음)."""
    store = _store(tmp_path)
    app = create_app(store=store)
    with TestClient(app) as client:
        resp = client.get("/work/does-not-exist.png")
        assert resp.status_code == 404
        # dist 마운트도 살아 있어 없는 경로는 404(빌드 전 index 없음).
        resp2 = client.get("/no-such-page")
        assert resp2.status_code == 404


# ── config: web 블록 로딩(코드 하드코딩 0) ───────────────────────────────────


def test_web_config_block_loaded() -> None:
    """endpoint.yaml web 블록이 host/port/정적 경로를 제공한다(수용 18).

    ★host/port는 서빙 환경에 따라 바뀐다 — 2026-06-13 사용자 할당 포트(33006)로
    재기동하며 0.0.0.0:33006으로 변경됨(.env WEB_BASE_URL·재기동 절차와 동기).
    값을 코드에 하드코딩하지 않고 endpoint.yaml에서 읽는다는 것(로딩 동작)이 핵심.
    """
    cfg = get_web_config()
    assert cfg["host"] == "0.0.0.0"
    assert cfg["port"] == 33006
    assert cfg["static_dist"] == "web/dist"
    assert cfg["static_work"] == "work"
