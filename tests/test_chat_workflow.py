"""chat 워크플로(회사 직원 자유대화) — 2노드 [read meta → reply] 검증.

2026-06-15 "대화봇을 회사 직원으로"(컨텍스트 단절 수정): chat 워크플로가
game/data/meta.json을 읽어(n1·game_data_read) 그 게임 설계를 grounding으로
직원 페르소나 응답(n2·chat)을 내는지 검증한다. 기존 단일 chat 노드("어떤 게임이요?"
단절)를 대체.

전부 FakeLLM + 주입 mock game_data_read로 결정론(실 LLM/실 디스크 game/ 접촉 0).
실행은 scripts/run_tests.sh engine(bare pytest 금지·hang 3중가드).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import RunFinished, RunStarted, TextMessage, ToolCallResult
from office.run import (
    _build_chat_history,
    _chat_seed_messages,
    _HISTORY_SCAN_WINDOW,
    _load_workflow,
    CHAT_MEMORY_TURNS,
)
from office.tools import GAME_DATA_READ_SCHEMA
from workflows.chat import NODES, WORKFLOW_NAME


# ── Fake LLM (n1 game_data_read tool-call → n2 직원 응답) ─────────────────────


class ChatRecordingLLM:
    """chat 2노드용 결정론 fake LLM. messages를 calls에 기록한다.

    call 0 (n1·tool): game_data_read tool-call JSON(meta·required=false).
    call 1 (n2·chat): 직원 응답 텍스트(reply 인자로 주입 — grounding 검증에 사용).
    """

    def __init__(self, reply: str) -> None:
        self.calls: list[dict[str, Any]] = []
        self._reply = reply
        self._idx = 0

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append({"messages": list(messages)})
        if self._idx == 0:
            content = (
                '{"name":"game_data_read","arguments":'
                '{"path":"data/meta.json","required":false}}'
            )
        else:
            content = self._reply
        self._idx += 1
        return {
            "message": {
                "role": "assistant",
                "content": content,
                "reasoning_content": "",
                "tool_calls": [],
            },
            "content": content,
            "reasoning_content": "",
            "finish_reason": "stop",
            "usage": {},
            "latency_ms": 5.0,
        }


def _mock_game_data_read(exists: bool, content: str | None) -> Any:
    """주입용 mock game_data_read — tools.py 출력계약 모사(실 game/ 디스크 접촉 0).

    계약: {"exists":bool,"content":str|None,"path":str}. context= 인자(run_id/node_id)를
    받는 2-인자 시그니처(engine._run_tool가 context를 별도로 넘김)도 흡수한다.
    """

    def _fn(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "exists": exists,
            "content": content if exists else None,
            "path": "data/meta.json",
        }

    return _fn


def _make_engine(
    llm: ChatRecordingLLM, db_path: str, *, exists: bool, content: str | None
) -> tuple[WorkflowEngine, EventStore]:
    """주입 mock game_data_read를 가진 엔진(gating 기본 — read는 always-allow·interrupt 0)."""
    store = EventStore(db_path)
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"game_data_read": _mock_game_data_read(exists, content)},
    )
    return engine, store


def _tmp_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return f.name


_META_CONTENT = json.dumps(
    {
        "title": "Aether Scourge",
        "theme": "우주 생존 서바이버",
        "coreLoop": "이동 → 자동 전투 → 레벨업 → 웨이브 반복",
        "win": "최종 보스 처치",
        "lose": "HP 0",
    },
    ensure_ascii=False,
)


# ── 1. 로더 — 2노드 [tool(game_data_read) → chat] 계약 ───────────────────────


def test_loader_returns_two_node_chat() -> None:
    """_load_workflow('chat') = (2노드, 'chat') + 노드 계약(read meta → reply)."""
    nodes, name = _load_workflow("chat")
    assert name == "chat"
    assert len(nodes) == 2

    n1, n2 = nodes
    # N1 read meta (tool · game_data_read · next=n2_reply)
    assert n1.id == "n1_read_meta"
    assert n1.kind == "tool"
    assert n1.thinking == "off"
    assert n1.tools == [GAME_DATA_READ_SCHEMA]
    assert n1.next == "n2_reply"
    # N2 reply (chat · thinking=off · 종료)
    assert n2.id == "n2_reply"
    assert n2.kind == "chat"
    assert n2.thinking == "off"
    assert n2.next is None


def test_module_constants_match_loader() -> None:
    """workflows.chat.NODES/WORKFLOW_NAME가 로더 반환과 동일 식별자."""
    assert WORKFLOW_NAME == "chat"
    assert [n.id for n in NODES] == ["n1_read_meta", "n2_reply"]


# ── 2. mock 풀런 — 게임 설계 grounding + assistant 정확히 1건 ────────────────


def test_mock_full_run_grounds_reply_in_meta() -> None:
    """meta 존재 → n1이 읽어 n2 입력에 도달(grounding) + assistant TEXT_MESSAGE 1건."""
    db_path = _tmp_db()
    try:
        llm = ChatRecordingLLM(
            reply="저희가 만들고 있는 'Aether Scourge'를 말씀하시는군요!"
        )
        engine, store = _make_engine(llm, db_path, exists=True, content=_META_CONTENT)
        state = engine.run(nodes=list(NODES), run_id="chat1", workflow=WORKFLOW_NAME)

        assert state.get("_workflow_status") == "success"
        events = store.read("chat1")
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "success" for e in finished)

        # n1 game_data_read 실제 실행(ToolCallResult·exists=True).
        n1_results = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.node_id == "n1_read_meta"
        ]
        assert len(n1_results) == 1
        assert n1_results[0].name == "game_data_read"
        assert n1_results[0].result["exists"] is True

        # assistant TEXT_MESSAGE 정확히 1건·비어있지 않음(인바운드 라운드트립 가드 동형).
        texts = [
            e for e in events if isinstance(e, TextMessage) and e.role == "assistant"
        ]
        assert len(texts) == 1
        assert texts[0].content.strip() != ""

        # ★grounding: meta 내용(게임 제목)이 n2(reply) 입력 messages에 도달.
        n2_input = json.dumps(llm.calls[1]["messages"], ensure_ascii=False)
        assert "Aether Scourge" in n2_input
    finally:
        os.unlink(db_path)


# ── 3. meta 부재 graceful — required=false라 노드 실패 0 ─────────────────────


def test_meta_absent_is_graceful() -> None:
    """meta 부재(exists=false)여도 노드 실패 0·assistant 응답 1건(자유대화 비중단)."""
    db_path = _tmp_db()
    try:
        llm = ChatRecordingLLM(reply="안녕하세요! 무엇을 도와드릴까요?")
        engine, store = _make_engine(llm, db_path, exists=False, content=None)
        state = engine.run(nodes=list(NODES), run_id="chat2", workflow=WORKFLOW_NAME)

        assert state.get("_workflow_status") == "success"
        events = store.read("chat2")

        # n1 read는 exists=false로 graceful(노드 실패 0).
        n1_results = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.node_id == "n1_read_meta"
        ]
        assert len(n1_results) == 1
        assert n1_results[0].result["exists"] is False

        # 그래도 assistant 응답은 정확히 1건 발행(대화 비중단).
        texts = [
            e for e in events if isinstance(e, TextMessage) and e.role == "assistant"
        ]
        assert len(texts) == 1
        assert texts[0].content.strip() != ""
    finally:
        os.unlink(db_path)


# ── 4. chat 대화기억 (S1) — 이벤트스토어 필터 시딩 ─────────────────────────────


def _seed_store(events: list[Any]) -> tuple[EventStore, str]:
    """임시 DB에 이벤트를 순서대로 append한 EventStore를 만든다(read_since 검증용)."""
    db_path = _tmp_db()
    store = EventStore(db_path)
    for ev in events:
        store.append(ev)
    return store, db_path


def test_build_chat_history_filters_chat_origin() -> None:
    """AC-S1a: discord user 에코 + chat-run assistant만, oldest→newest, 비-chat 제외."""
    store, db_path = _seed_store(
        [
            TextMessage(
                run_id="discord-1",
                ts="t1",
                role="user",
                content="우리 게임 이름이 뭐였지?",
            ),
            RunStarted(run_id="rchat1", ts="t2", workflow="chat"),
            TextMessage(
                run_id="rchat1",
                ts="t3",
                role="assistant",
                content="저희 게임은 'Aether Scourge'입니다.",
            ),
            # 비-chat(crm_notify) run의 assistant — history에서 제외돼야 함.
            RunStarted(run_id="rcrm", ts="t4", workflow="crm_notify"),
            TextMessage(
                run_id="rcrm", ts="t5", role="assistant", content="CRM 요약: 주문 3건"
            ),
            TextMessage(
                run_id="discord-2", ts="t6", role="user", content="그거 언제 나와?"
            ),
        ]
    )
    try:
        history = _build_chat_history(store)
        assert history == [
            {"role": "user", "content": "우리 게임 이름이 뭐였지?"},
            {"role": "assistant", "content": "저희 게임은 'Aether Scourge'입니다."},
            {"role": "user", "content": "그거 언제 나와?"},
        ]
    finally:
        os.unlink(db_path)


def test_build_chat_history_empty_store() -> None:
    """AC-S1a: 빈 store → []."""
    store, db_path = _seed_store([])
    try:
        assert _build_chat_history(store) == []
    finally:
        os.unlink(db_path)


def test_build_chat_history_caps_to_max_turns() -> None:
    """AC-S1a: 최근 max_turns 교환(2*max_turns 메시지)으로 캡(오래된 것 절단)."""
    events: list[Any] = []
    n = CHAT_MEMORY_TURNS + 4  # max_turns 초과 교환.
    for i in range(n):
        events.append(
            TextMessage(run_id=f"discord-{i}", ts=f"u{i}", role="user", content=f"u{i}")
        )
        events.append(RunStarted(run_id=f"rc{i}", ts=f"s{i}", workflow="chat"))
        events.append(
            TextMessage(run_id=f"rc{i}", ts=f"a{i}", role="assistant", content=f"a{i}")
        )
    store, db_path = _seed_store(events)
    try:
        history = _build_chat_history(store, max_turns=CHAT_MEMORY_TURNS)
        assert len(history) == 2 * CHAT_MEMORY_TURNS
        assert history[0]["content"] == f"u{n - CHAT_MEMORY_TURNS}"  # 오래된 교환 절단.
        assert history[-1]["content"] == f"a{n - 1}"  # 최신 유지.
    finally:
        os.unlink(db_path)


def test_build_chat_history_scan_bounded_to_window() -> None:
    """AC-S1a2: WINDOW 밖의 오래된 chat 턴은 반환 안 함(전체 store 비의존·hang 가드)."""
    events: list[Any] = [
        TextMessage(run_id="discord-OLD", ts="t0", role="user", content="OLD_TURN"),
    ]
    # _HISTORY_SCAN_WINDOW 초과 노이즈로 OLD를 말미 윈도 밖으로 밀어낸다.
    for i in range(_HISTORY_SCAN_WINDOW + 10):
        events.append(RunStarted(run_id=f"noise{i}", ts=f"n{i}", workflow="crm_notify"))
    events.append(
        TextMessage(run_id="discord-NEW", ts="tz", role="user", content="NEW_TURN")
    )
    store, db_path = _seed_store(events)
    try:
        contents = [m["content"] for m in _build_chat_history(store)]
        assert "OLD_TURN" not in contents  # 윈도 밖 → 미반환(스캔 바운드).
        assert "NEW_TURN" in contents
    finally:
        os.unlink(db_path)


def test_chat_seed_messages_discord_no_dup() -> None:
    """AC-S1c: Discord — history 말미가 현재 query 에코면 그대로(현재 턴 정확히 1회)."""
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},  # inbound 에코가 이미 추가한 현재 턴.
    ]
    seeded = _chat_seed_messages(history, "q2")
    assert seeded == history
    assert sum(1 for m in seeded if m == {"role": "user", "content": "q2"}) == 1


def test_chat_seed_messages_web_cli_appends() -> None:
    """AC-S1d: web/CLI(에코 없음) — 현재 query append; 빈 history → 기존 단일 턴(후방호환)."""
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    assert _chat_seed_messages(history, "q2") == history + [
        {"role": "user", "content": "q2"}
    ]
    assert _chat_seed_messages([], "q1") == [{"role": "user", "content": "q1"}]


def test_history_seam_reaches_reply_input() -> None:
    """AC-S1b: 시드 history가 n2(reply) LLM 입력에 도달(이전 assistant + 현재 질문·순서)."""
    db_path = _tmp_db()
    try:
        llm = ChatRecordingLLM(reply="Aether Scourge는 아직 출시일 미정이에요.")
        engine, _store = _make_engine(llm, db_path, exists=True, content=_META_CONTENT)
        history = [
            {"role": "user", "content": "우리 게임 이름이 뭐였지?"},
            {"role": "assistant", "content": "저희 게임은 'Aether Scourge'입니다."},
        ]
        messages = _chat_seed_messages(history, "그거 언제 나와?")
        engine.run(
            nodes=list(NODES),
            run_id="chatmem1",
            workflow=WORKFLOW_NAME,
            initial_state={"messages": messages},
        )
        msgs = llm.calls[1]["messages"]
        joined = json.dumps(msgs, ensure_ascii=False)
        assert (
            "저희 게임은 'Aether Scourge'입니다." in joined
        )  # 이전 assistant 턴 회수.
        assert "그거 언제 나와?" in joined  # 현재 질문.
        prior_idx = next(
            i
            for i, m in enumerate(msgs)
            if m.get("content") == "우리 게임 이름이 뭐였지?"
        )
        cur_idx = next(
            i for i, m in enumerate(msgs) if m.get("content") == "그거 언제 나와?"
        )
        assert prior_idx < cur_idx  # 이전 턴이 현재보다 앞.
    finally:
        os.unlink(db_path)


def test_build_chat_history_single_source_limitation() -> None:
    """AC-S1f: 단일 인바운드 소스 불변식 박제 — 2번째 소스(web chat run)의 assistant가
    workflow=='chat' 때문에 섞인다(대화키 부재의 알려진 한계·silent 아님)."""
    store, db_path = _seed_store(
        [
            TextMessage(
                run_id="discord-1", ts="t1", role="user", content="디스코드 발화"
            ),
            # 별도 인바운드 소스(예: web /api/run chat)가 만든 chat run.
            RunStarted(run_id="webchat1", ts="t2", workflow="chat"),
            TextMessage(
                run_id="webchat1", ts="t3", role="assistant", content="웹에서 온 응답"
            ),
        ]
    )
    try:
        history = _build_chat_history(store)
        assert {
            "role": "assistant",
            "content": "웹에서 온 응답",
        } in history  # 한계 박제.
    finally:
        os.unlink(db_path)


def test_chat_persona_seed_prepends_role() -> None:
    """멀티봇: _chat_persona_seed가 지목 역할 페르소나를 맨 앞에 얹고 현재 턴은 1회 유지."""
    from office.run import PERSONA_PROMPTS, _chat_persona_seed

    history = [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "네"},
    ]
    seeded = _chat_persona_seed("dev", history, "빌드 어때?")
    assert seeded[0] == {"role": "user", "content": PERSONA_PROMPTS["dev"]}
    assert "한지훈" in seeded[0]["content"]
    assert seeded[-1] == {
        "role": "user",
        "content": "빌드 어때?",
    }  # 현재 턴 마지막 1회.
    # 미정의/None 역할 → 페르소나 없이 generic 폴백.
    plain = _chat_persona_seed(None, history, "질문")
    assert all(m["content"] != PERSONA_PROMPTS["dev"] for m in plain)
    assert plain[-1] == {"role": "user", "content": "질문"}
    assert _chat_persona_seed("ghost", [], "x") == [{"role": "user", "content": "x"}]
