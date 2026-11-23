"""pytest 공용 픽스처."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

# tests/ 디렉토리를 sys.path에 추가 — mock_server 등 직접 임포트 허용
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# web_server 테스트(fastapi/starlette 의존)는 백엔드 전용 env(one_man_corp-engine)에서만 돈다.
# Phase 0 벤치 전용 env(one_man_corp-bench)에는 fastapi가 없어 수집 단계에서 ImportError가
# 나므로, starlette 미설치 환경에서는 해당 모듈만 수집에서 제외한다(나머지 회귀는 그대로 통과).
# test_web_sse.py는 EventFanout 순수 로직이라 fastapi 불필요 → 제외 대상 아님.
import importlib.util  # noqa: E402

collect_ignore: list[str] = []

if importlib.util.find_spec("starlette") is None:
    collect_ignore += [
        "test_web_app.py",
        "test_web_inbox.py",
        "test_web_runner.py",
        "test_web_e2e.py",
        # Phase 5 US-P5-3/US-P5-4: 재시작 복구·L3 캡스톤은 web_server(starlette
        # TestClient/create_app)를 import한다 → starlette 미설치 env(bench 등)에서
        # 수집 단계 ImportError 방지(나머지 회귀는 그대로 통과 — engine env에서만 실행).
        "test_restart_recovery.py",
        "test_phase5_e2e.py",
    ]

# knowledge 테스트(qdrant-client 의존)는 지식 전용 env(one_man_corp-knowledge)에서만 돈다.
# 엔진·벤치 env에는 qdrant-client가 없어(의존성 격리) 수집 단계에서 ImportError가 나므로,
# qdrant-client 미설치 환경에서는 해당 모듈만 수집에서 제외한다(나머지 회귀는 그대로 통과).
if importlib.util.find_spec("qdrant_client") is None:
    collect_ignore += [
        "test_knowledge_store.py",
        "test_knowledge_search.py",
        "test_knowledge_eval.py",
        "test_knowledge_ingest.py",
        # Phase 9 크롤 테스트는 KnowledgeStore(=qdrant) 의존이라 engine/bench env에서
        # 제외한다. ★crawl4ai용 별도 가드는 만들지 않는다 — 그러면 crawl4ai 없는
        # 결정론 게이트(one_man_corp-knowledge)에서 mock 크롤 테스트가 통째 제외돼
        # 게이트가 크롤을 안 보게 된다(치명). 실 crawl4ai 의존은 파일 내부
        # importorskip + @pytest.mark.slow로 처리한다(FakeCrawler만 top-level 사용).
        "test_knowledge_crawl.py",
    ]

# Phase 4b Discord 어댑터 테스트(discord.py 의존)는 어댑터 전용 env(one_man_corp-discord)
# 에서만 돈다. 엔진·벤치·지식 env에는 discord.py가 없어 bot 모듈 수집 단계에서 ImportError가
# 나므로, discord 미설치 환경에서는 해당 모듈만 수집에서 제외한다(나머지 회귀는 그대로 통과).
if importlib.util.find_spec("discord") is None:
    collect_ignore += [
        "test_discord_mapping.py",
        "test_discord_sse_client.py",
        "test_discord_cursor.py",
        "test_discord_rest_client.py",
        "test_discord_bot.py",
        # G006 신규(US-G6-0/1): bot 모듈을 import(모듈 top 또는 런타임)하므로 discord
        # 미설치 env에서는 제외. 실 게이트/enqueue 통합은 test_g6_*_integration.py가
        # (discord 무의존·httpx만·importorskip fastapi) 엔진 env에서 따로 검증한다.
        "test_discord_auth.py",
        "test_discord_commands.py",
    ]


@pytest.fixture
def env() -> dict[str, str]:
    """프로젝트 루트 .env + os.environ 병합 dict.

    기존 test_embedding.py / test_rerank.py 가 이 픽스처를 사용한다.
    os.environ이 .env보다 우선한다.
    """
    from pathlib import Path

    env_path = Path(__file__).parent.parent / ".env"
    file_values: dict[str, str | None] = (
        dotenv_values(env_path) if env_path.exists() else {}
    )
    merged: dict[str, str] = {}
    for k, v in file_values.items():
        if v is not None:
            merged[k] = v
    merged.update({k: v for k, v in os.environ.items()})
    return merged


# ── ★무한 루프 hang 방지: 모든 테스트에 기본 타임아웃 자동 강제 ────────────────────
# Phase 4b에서 무한 재연결 루프 테스트가 시스템을 hang시킨 사고가 있었다
# (memory/discord-adapter-test-hang-guard.md). 어댑터는 SSE 재연결·gateway·이벤트
# 소비 등 "의도적 무한 루프"가 본질이라, 그 루프를 건드리는 async 테스트가 종료 조건
# 없이 돌면 시스템이 hang/메모리 폭주한다. CLI `--timeout` 이나 사람의 기억에 의존하지
# 않도록, pytest-timeout 이 설치된 env에서는 **모든 비-slow 테스트에 기본 타임아웃을
# 자동 적용**해 어떤 무한 루프 테스트도 N초 후 강제 종료되게 한다(시간·메모리 양쪽 차단).
# pytest-timeout 미설치 env에서는 no-op(무해).
_DEFAULT_TEST_TIMEOUT_SEC = 30


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    if importlib.util.find_spec("pytest_timeout") is None:
        return
    for item in items:
        # 개별 지정 timeout 마커가 있거나 slow(실서버, 정당히 김) 테스트는 건너뜀.
        if item.get_closest_marker("timeout") is not None:
            continue
        if item.get_closest_marker("slow") is not None:
            continue
        item.add_marker(pytest.mark.timeout(_DEFAULT_TEST_TIMEOUT_SEC))
