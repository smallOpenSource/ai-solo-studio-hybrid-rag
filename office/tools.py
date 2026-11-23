"""ms-01 CRM 워크플로 도구 스키마 + mock 구현 — US-008 (T7), 수용 기준 17.
US-006 (K7) 엔진 도구 연결: knowledge_search 스키마 + callable 추가.

설계:
- 도구 스키마(OpenAI function 형식)와 mock callable을 한 곳에 정의한다.
- mock은 결정론적 응답을 반환 — 실서버 접촉 없음(smoke 제외).
- error 반환 케이스 1개 포함 → NODE_FAILED 경로 실증.
- WorkflowEngine.tools에 주입해 Node.kind="tool" 실행 시 호출된다.
- knowledge_search callable은 지연 import(함수 내부 import knowledge.search)로
  engine env(qdrant-client 미설치)에서 import 시점 에러 없이 격리된다.
"""

from __future__ import annotations

from typing import Any

# Phase 8 P (옵션 B): REAL_TOOLS의 knowledge_search를 HTTP 클라이언트로 스왑한다.
# knowledge_client는 httpx만 의존(engine env OK·qdrant import 0)이라 모듈 상위 import 안전.
# ★MOCK_TOOLS와 knowledge_search 함수 본문은 무수정(아래 :208-222 lazy-import 디스패치 보존).
from office.knowledge_client import real_knowledge_search_http

# ── 도구 스키마 (OpenAI function 형식) ───────────────────────────────────────

CRM_GET_CUSTOMER_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "crm_get_customer",
        "description": "고객 ID 또는 이메일로 CRM에서 고객 정보를 조회합니다",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "고객 ID (예: C-2041)",
                },
                "email": {
                    "type": "string",
                    "description": "고객 이메일 주소",
                },
            },
        },
    },
}

ORDER_GET_LATEST_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "order_get_latest",
        "description": "고객 ID로 최근 주문 정보를 조회합니다",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "고객 ID",
                },
            },
            "required": ["customer_id"],
        },
    },
}

EMAIL_SEND_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "email_send",
        "description": "고객에게 이메일을 발송합니다 (비가역 — 사람 승인 필요)",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "수신자 이메일 주소",
                },
                "subject": {
                    "type": "string",
                    "description": "이메일 제목",
                },
                "body": {
                    "type": "string",
                    "description": "이메일 본문",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
}

KNOWLEDGE_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "knowledge_search",
        "description": "회사 내부 지식 베이스(위키 등)에서 질의와 의미가 가까운 문서 청크를 검색합니다",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색 질의",
                },
                "k": {
                    "type": "integer",
                    "description": "반환할 상위 결과 수 (기본 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

GAME_DATA_WRITE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "game_data_write",
        "description": (
            "게임 산출물 파일을 game/ 하위에 기록합니다 "
            "(가역 — 로컬 파일쓰기, 외부 부수효과 없음)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "game/ 하위 상대경로 (예: data/enemies.json)",
                },
                "content": {
                    "type": ["string", "object"],
                    "description": "JSON 직렬화 가능 내용 (dict/list면 직렬화, str이면 그대로)",
                },
            },
            "required": ["path", "content"],
        },
    },
}

GAME_DATA_READ_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "game_data_read",
        "description": (
            "이전 단계가 game/ 하위에 기록한 산출물 파일을 읽습니다 "
            "(읽기전용·가역 — 단계간 데이터 계약). required=True면 파일이 없을 때 "
            "예외를 던져 엔진이 명시 실패(NODE_FAILED/RUN_ERROR)로 드러냅니다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "game/ 하위 상대경로 (예: data/meta.json)",
                },
                "required": {
                    "type": "boolean",
                    "description": (
                        "True면 파일 부재 시 예외(단계 계약). False/생략이면 "
                        "exists=False로 조용히 반환"
                    ),
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
}

GAME_VALIDATE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "game_validate",
        "description": (
            "game/data 디렉터리의 6파일(meta/enemies/weapons/waves/upgrades/balance)을 "
            "스키마 검증한다 (읽기전용·가역 — QA 결정론 게이트). cross-file 참조 무결성"
            "(waves.spawn.enemyId ∈ enemies.id) 포함. {ok,errors,report} 반환."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "data_dir": {
                    "type": "string",
                    "description": (
                        "검증할 데이터 디렉터리(game/ 하위 상대경로). 생략 시 "
                        "기본 game/data."
                    ),
                },
            },
        },
    },
}

AGENT_ACTION_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "agent_action",
        "description": (
            "테스트 전용 — agent 격리 워크플로의 비가역 동작(needs-approval) mock. "
            "HITL suspend→resume 실증용이며 실제 부수효과는 없다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "동작 설명(임의)"},
            },
        },
    },
}

# 모든 스키마 목록 (WorkflowEngine.tools 주입용)
ALL_SCHEMAS: list[dict[str, Any]] = [
    CRM_GET_CUSTOMER_SCHEMA,
    ORDER_GET_LATEST_SCHEMA,
    EMAIL_SEND_SCHEMA,
    KNOWLEDGE_SEARCH_SCHEMA,
    AGENT_ACTION_SCHEMA,
    GAME_DATA_WRITE_SCHEMA,
    GAME_DATA_READ_SCHEMA,
    GAME_VALIDATE_SCHEMA,
]


# ── mock 구현 ────────────────────────────────────────────────────────────────
#
# ★도구 callable 시그니처(Phase 5 US-P5-1 AC3): 모든 도구는 (args, context=None)을
#   받는다. context={"run_id","node_id"}는 email_send가 멱등키 f"{run_id}:{node_id}"를
#   만드는 데만 쓰이고, 가역 도구(crm/order/knowledge)는 무시한다. engine은 context를
#   args와 **별도 인자**로 넘기므로 TOOL_CALL_START.args 이벤트엔 새지 않는다(계약 보존).


def mock_crm_get_customer(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """crm_get_customer 결정론적 mock.

    customer_id="error-id"이면 error를 반환 → NODE_FAILED 경로 실증 (수용 17).
    """
    customer_id = args.get("customer_id", "")
    email = args.get("email", "")

    # error 반환 케이스 (수용 17)
    if customer_id == "error-id":
        return {"error": "고객 ID를 찾을 수 없습니다", "code": "NOT_FOUND"}

    return {
        "customer_id": customer_id or "C-2041",
        "name": "김민준",
        "email": email or "minjun.kim@example.com",
        "tier": "gold",
        "phone": "010-1234-5678",
    }


def mock_order_get_latest(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """order_get_latest 결정론적 mock."""
    customer_id = args.get("customer_id", "C-2041")
    return {
        "order_id": "ORD-9981",
        "customer_id": customer_id,
        "product": "노트북 Pro 15",
        "amount": 1_850_000,
        "status": "배송완료",
        "ordered_at": "2026-05-28T14:30:00Z",
    }


def mock_email_send(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """email_send 결정론적 mock (실제 발송 없음)."""
    return {
        "sent": True,
        "to": args.get("to", ""),
        "message_id": "MOCK-MSG-0001",
    }


def mock_agent_action(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """agent_action 결정론적 mock (테스트 전용 — 실제 부수효과 없음).

    agent 격리 워크플로의 비가역(needs-approval) HITL 실증용. context(run_id·node_id)는
    멱등키 단언을 위해 message_id에 반영한다(email_send와 동형 — 정확히-1회 검증 수단).
    """
    ctx = context or {}
    key = f"{ctx.get('run_id', '')}:{ctx.get('node_id', '')}"
    return {
        "done": True,
        "note": args.get("note", ""),
        "action_id": f"AGENT-ACTION-{key}",
    }


def knowledge_search(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """knowledge_search callable — knowledge.search.search에 위임한다.

    지연 import: 함수 호출 시점에만 knowledge.search를 import한다.
    engine env에 qdrant-client가 없어도 import 시점에는 에러가 발생하지 않는다.
    실제 knowledge env 없이 테스트할 때는 이 함수를 직접 호출하지 않고
    mock callable을 MOCK_TOOLS에 주입해 우회한다.
    """
    query: str = args.get("query", "")
    k: int = int(args.get("k", 5))
    from knowledge.search import search  # 지연 import — engine env 격리

    return search(query, k=k)


# ── game_data_write — game/ 로컬 파일쓰기 도구 (G005 US-G5-1, 가역·always-allow) ──
#
# 게임 산출물(meta/enemies/weapons/... .json)을 game/ 하위에만 기록한다. 비가역 도구가
# 아니라(외부 부수효과 0·로컬 디스크만) 로컬 파일쓰기이므로 tools_policy.yaml에서
# always-allow로 등록된다. 멱등 기준은 **내용해시**(record_email INSERT OR IGNORE 정신):
# 같은 바이트를 다시 쓰면 디스크 미접촉·deduplicated=True. context 멱등키는 의도적으로
# 미사용(내용해시가 멱등 SSOT — game/ 산출물은 결정론·가역이라 내용 기준이 더 견고).


def game_data_write(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """game/ 하위에 게임 산출물 파일을 기록한다 (AC1·2·3, 가역).

    - 경로 화이트리스트(AC2): game/ 하위만 허용. `..`·절대경로·game/ 밖 탈출은
      거부하고 디스크에 손대지 않는다({"written": False, "error": ...}).
    - content 직렬화: dict/list면 결정론 JSON(sort_keys·indent=2·ensure_ascii=False),
      str이면 그대로.
    - 멱등(AC3): 대상이 이미 존재하고 기존 바이트 == 새 바이트면 쓰기 skip
      (deduplicated=True·중복쓰기 0). 다르거나 없으면 쓴다(deduplicated=False).
      반환 path는 game/ 기준 상대경로(이벤트 누출 안전).
    """
    import json as _json
    from pathlib import Path

    raw_path = args.get("path", "")
    content = args.get("content", "")

    # game/ 디렉터리 = office/의 부모(repo root)/game
    repo_root = Path(__file__).resolve().parent.parent
    game_dir = repo_root / "game"

    # ── 경로 화이트리스트(AC2) — 정규화 후 game/ 하위인지 검증, 아니면 디스크 미접촉 ──
    target = (game_dir / raw_path).resolve()
    try:
        rel = target.relative_to(game_dir.resolve())
    except ValueError:
        # `..`·절대경로 등으로 game/ 밖을 가리킴 → 거부(디스크 미접촉)
        return {
            "written": False,
            "error": "path outside game/",
            "path": raw_path,
        }

    # ── 자기참조/빈 경로 거부(Major-1) — game/ 자체나 하위 디렉터리를 파일로
    #    덮어써 트리를 막는 사고 방지(`""`·`.`·`data/..` 가 rel="." 로 귀결) ──
    if str(rel) == ".":
        return {
            "written": False,
            "error": "path must name a file under game/",
            "path": raw_path,
        }
    if target.is_dir():
        return {
            "written": False,
            "error": "target is a directory",
            "path": raw_path,
        }

    # ── content 직렬화(결정론) ──
    if isinstance(content, (dict, list)):
        text = _json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = str(content)
    new_bytes = text.encode("utf-8")
    rel_str = str(rel)

    # ── 멱등(AC3·내용해시 no-op) — 기존 바이트 == 새 바이트면 쓰기 skip ──
    if target.exists() and target.read_bytes() == new_bytes:
        return {
            "written": False,
            "path": rel_str,
            "bytes": len(new_bytes),
            "deduplicated": True,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(new_bytes)
    return {
        "written": True,
        "path": rel_str,
        "bytes": len(new_bytes),
        "deduplicated": False,
    }


# ── game_data_read — game/ 로컬 파일읽기 도구 (G005 US-G5-4, 읽기전용·always-allow) ──
#
# 단계간 계약(이전 단계가 game_data_write로 남긴 산출 파일 읽기)에 쓴다. game_data_write의
# 경로 화이트리스트(game/ 하위만)·자기참조/디렉터리 거부 로직을 그대로 재사용한다. 읽기는
# 가역이므로 tools_policy.yaml에서 always-allow. ★required=True인데 파일이 없으면 예외를
# raise한다(FileNotFoundError) — 엔진(error_fails_node=True)이 NODE_FAILED/RUN_ERROR로
# 단계 계약 위반(이전 단계 미완)을 명시 실패로 드러내게 한다(AC2 입력 계약 핵심).


def game_data_read(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """game/ 하위 게임 산출물 파일을 읽는다 (단계간 계약, 읽기전용·가역).

    - 경로 화이트리스트: game/ 하위만 허용. `..`·절대경로·game/ 밖 탈출은 거부하고
      디스크에 손대지 않는다(ValueError). game_data_write와 동일 규칙(자기참조/디렉터리도 거부).
    - 파일 있으면 {"exists": True, "content": str, "path": rel}, 없으면
      {"exists": False, "content": None, "path": rel}.
    - ★required=True인데 파일이 없으면 FileNotFoundError를 raise한다(단계 계약) — 엔진이
      명시 실패(NODE_FAILED/RUN_ERROR)로 드러낸다. required=False/생략이면 조용히 exists=False.
    """
    from pathlib import Path

    raw_path = args.get("path", "")
    required = bool(args.get("required", False))

    # game/ 디렉터리 = office/의 부모(repo root)/game (game_data_write와 동일)
    repo_root = Path(__file__).resolve().parent.parent
    game_dir = repo_root / "game"

    # ── 경로 화이트리스트 — 정규화 후 game/ 하위인지 검증, 아니면 디스크 미접촉 ──
    target = (game_dir / raw_path).resolve()
    try:
        rel = target.relative_to(game_dir.resolve())
    except ValueError as exc:
        # `..`·절대경로 등으로 game/ 밖을 가리킴 → 거부(디스크 미접촉).
        raise ValueError(f"path outside game/: {raw_path!r}") from exc

    # ── 자기참조/빈 경로·디렉터리 거부(game_data_write와 동일) ──
    if str(rel) == ".":
        raise ValueError(f"path must name a file under game/: {raw_path!r}")
    if target.is_dir():
        raise ValueError(f"target is a directory: {raw_path!r}")

    rel_str = str(rel)

    if not target.exists():
        if required:
            # ★단계 계약 위반: 이전 단계 산출 파일이 없음 → 예외로 명시 실패 유도.
            raise FileNotFoundError(
                f"required game data file not found: {rel_str} "
                f"(이전 단계 미완 — 단계간 계약 위반)"
            )
        return {"exists": False, "content": None, "path": rel_str}

    content = target.read_text(encoding="utf-8")
    return {"exists": True, "content": content, "path": rel_str}


# ── game_validate — game/data 스키마 검증 도구 (G005 US-G5-6, 읽기전용·always-allow) ──
#
# game_qa 워크플로의 결정론 게이트. office.game_schema.validate_all(데이터 로드 +
# 각 validate_* + cross-file 참조 무결성)을 실행해 {ok,errors,report}를 돌려준다.
# 경로 화이트리스트(game/ 하위만)는 game_data_read와 동일 — game/ 밖 디렉터리는 거부.
# 읽기전용·가역이므로 tools_policy.yaml에서 always-allow. ★베이스라인=ok:true, 고의
# 깨진 데이터=ok:false 결정론(빌드성공 ≠ 플레이가능의 데이터 측 게이트).


def game_validate(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """game/ 하위 데이터 디렉터리를 스키마 검증한다 (읽기전용·가역).

    - data_dir(생략 시 game/data): game/ 하위만 허용. `..`·절대경로·game/ 밖 탈출은
      거부({"ok": False, "errors": [...], "report": {}} — 디스크 미접촉).
    - office.game_schema.validate_all로 6파일 로드 + 각 validate_* + 참조 무결성 검증.
    - 반환 {"ok": bool, "errors": list[str], "report": {"valid": bool, "errorCount": int}}.
    """
    from pathlib import Path

    from office.game_schema import validate_all

    raw_dir = args.get("data_dir") or "data"

    # game/ 디렉터리 = office/의 부모(repo root)/game (game_data_read와 동일 화이트리스트).
    repo_root = Path(__file__).resolve().parent.parent
    game_dir = repo_root / "game"

    target = (game_dir / raw_dir).resolve()
    try:
        target.relative_to(game_dir.resolve())
    except ValueError:
        # `..`·절대경로 등으로 game/ 밖을 가리킴 → 거부(디스크 미접촉).
        return {
            "ok": False,
            "errors": [f"data_dir outside game/: {raw_dir!r}"],
            "report": {"valid": False, "errorCount": 1},
        }

    res = validate_all(target)
    errors = res.get("errors", [])
    return {
        "ok": res["ok"],
        "errors": errors,
        "report": {"valid": res["ok"], "errorCount": len(errors)},
    }


# 도구 이름 → callable 레지스트리 (WorkflowEngine.tools에 주입)
MOCK_TOOLS: dict[str, Any] = {
    "crm_get_customer": mock_crm_get_customer,
    "order_get_latest": mock_order_get_latest,
    "email_send": mock_email_send,
    "knowledge_search": knowledge_search,
    "agent_action": mock_agent_action,
    "game_data_write": game_data_write,
    "game_data_read": game_data_read,
    "game_validate": game_validate,
}


# ── 실도구 구현 (Phase 5 US-P5-1 — mock→real, AC1·2·3·4) ──────────────────────
#
# crm/order는 state/business.db 실조회(가역). email_send는 email_outbox에 멱등키
# UNIQUE로 기록(비가역, B2 토대). 시드가 기존 mock 값과 정합하므로 동일 입력에
# 동일 결과를 돌려준다 — 4a 게이트·crm_notify·web_runner 무회귀(AC15).
# 외부 SaaS·Google MCP 호출 0(온프레미스만, AC4). 신규 외부 의존성 0(stdlib만).

# 모듈 전역 단일 BusinessStore — 매 호출 재초기화(스키마/시드) 비용 회피.
# 테스트는 reset_business_store()로 임시 경로를 주입한다.
_BUSINESS_STORE: Any = None


def get_business_store() -> Any:
    """프로세스 단일 BusinessStore를 지연 생성해 돌려준다.

    지연 import + 지연 초기화 — tools.py import 시점에 DB 파일을 만들지 않는다
    (테스트가 reset_business_store로 임시 경로를 먼저 주입할 수 있게).
    """
    global _BUSINESS_STORE
    if _BUSINESS_STORE is None:
        from office.business import BusinessStore

        _BUSINESS_STORE = BusinessStore()
    return _BUSINESS_STORE


def reset_business_store(store: Any = None) -> None:
    """전역 BusinessStore를 교체한다(테스트 격리용 — 임시 DB 주입).

    store=None이면 다음 get_business_store() 호출 시 기본 경로로 재생성된다.
    """
    global _BUSINESS_STORE
    _BUSINESS_STORE = store


def real_crm_get_customer(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """crm_get_customer 실도구 — state/business.db 실조회 (AC1, 가역).

    미존재 customer_id(또는 기존 'error-id') → error dict(code=NOT_FOUND)로
    NODE_FAILED 경로 보존(수용 17 계승). 반환 형태는 mock과 동일.
    """
    customer_id = args.get("customer_id", "")

    # error 반환 케이스(기존 mock 계약 보존, 수용 17) — 명시적 에러 id.
    if customer_id == "error-id":
        return {"error": "고객 ID를 찾을 수 없습니다", "code": "NOT_FOUND"}

    store = get_business_store()
    row = store.get_customer(customer_id or "C-2041")
    if row is None:
        return {"error": "고객 ID를 찾을 수 없습니다", "code": "NOT_FOUND"}
    return row


def real_order_get_latest(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """order_get_latest 실도구 — state/business.db 실조회 (AC1, 가역).

    미존재 customer_id → error dict로 NODE_FAILED 경로 보존.
    """
    customer_id = args.get("customer_id", "C-2041")
    store = get_business_store()
    row = store.get_latest_order(customer_id)
    if row is None:
        return {"error": "주문을 찾을 수 없습니다", "code": "NOT_FOUND"}
    return row


def _now_iso() -> str:
    """현재 시각 ISO 8601 문자열 (UTC)."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _try_smtp_send(to: str, subject: str, body: str) -> bool:
    """자격증명(.env SMTP_*)이 있으면 stdlib smtplib로 실 발송한다 (C3 폴백 자리).

    자격증명 부재 시 False(=싱크 폴백, Discord "토큰 없으면 비활성" 패턴 계승).
    소스에 키 문자열 0 — .env의 SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS 자리만.
    connect 타임아웃 필수(hang 방지). 발송 실패해도 크래시 0(False 반환 → 싱크).
    """
    from core.config import load_env

    env = load_env()
    host = env.get("SMTP_HOST", "")
    user = env.get("SMTP_USER", "")
    password = env.get("SMTP_PASS", "")
    # 자격증명 미설정 → 실 발송 안 함(싱크 폴백). 값은 사용자 몫.
    if not host or not user or not password:
        return False

    import smtplib
    from email.message import EmailMessage

    port = int(env.get("SMTP_PORT", "587"))
    try:
        msg = EmailMessage()
        # From 분리(SMTP_FROM 우선·미설정 시 user): Brevo 모던 계정은 SMTP Login(=user)이
        # '{random}@<smtp-provider-login-domain>'라 verified sender 불가 → From은 SMTP_FROM(verified sender).
        # SMTP_FROM 미설정/공백이면 user 폴백(Gmail 등 login=From 자연일치 = 기존 동작 보존).
        msg["From"] = env.get("SMTP_FROM", "").strip() or user
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        # timeout 필수 — connect hang 차단(preflight.sh httpx 린트 정신).
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception:
        # 실 발송 실패가 워크플로를 죽여선 안 됨 — 싱크로 폴백.
        return False


def real_email_send(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """email_send 실도구 — email_outbox에 멱등키 UNIQUE로 기록 (AC2·3, 비가역).

    멱등키 = f"{run_id}:{node_id}"(context로 관통) + INSERT OR IGNORE → 이미 있으면
    행 추가 0(이중 발송 차단, B2). context 미주입(레거시 호출)이면 args 기반 폴백
    키를 써 크래시 0(단, run/node 결정론 키가 아니므로 멱등 보장은 약함).

    실 SMTP(C3)는 record 후 inserted(첫 발송)일 때만 발사 → 비가역 send at-most-once
    (resume/재진입 재발송 0). 자격증명 부재 시 싱크만(AC4). 반환 형태 mock 유지.
    """
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")

    ctx = context or {}
    run_id = ctx.get("run_id", "")
    node_id = ctx.get("node_id", "")
    if run_id or node_id:
        idem_key = f"{run_id}:{node_id}"
    else:
        # context 미주입 폴백 — 결정론 키 부재(멱등 약함, 크래시 회피용).
        idem_key = f"_noctx:{to}:{subject}"

    # ★send-reorder(라이브 SMTP 스모크 prerequisite): record 먼저 → inserted(첫 발송)
    # 일 때만 실 SMTP. 싱크 기록이 "정확히 1회"의 단일 진실(B2·멱등키)이고, 비가역
    # 실 발송은 inserted=True일 때만 1회 발사 → at-most-once. resume/재진입
    # (inserted=False)에선 재발송 0(크래시 창 이중발송 차단). 자격증명 부재면 no-op.
    store = get_business_store()
    inserted = store.record_email(
        to=to,
        subject=subject,
        body=body,
        idempotency_key=idem_key,
        sent_at=_now_iso(),
    )
    if inserted:
        _try_smtp_send(to, subject, body)
    # message_id = 멱등키 기반 결정론 — 같은 키면 같은 id(재호출도 동일).
    return {
        "sent": True,
        "to": to,
        "message_id": f"OUTBOX-{idem_key}",
        "deduplicated": not inserted,
    }


# 실도구 레지스트리 — run_full(웹/CLI 풀런)이 주입한다. ★Phase 8 P(옵션 B): knowledge_search는
# HTTP 클라이언트(real_knowledge_search_http)로 스왑 — engine env 워커가 디스크(qdrant) 대신
# knowledge 서비스에 위임한다(cross-env 해소·NIT-1: 바인딩 교체만, 함수 본문·MOCK_TOOLS 무수정).
REAL_TOOLS: dict[str, Any] = {
    "crm_get_customer": real_crm_get_customer,
    "order_get_latest": real_order_get_latest,
    "email_send": real_email_send,
    "knowledge_search": real_knowledge_search_http,
    "game_data_write": game_data_write,
    "game_data_read": game_data_read,
    "game_validate": game_validate,
}
