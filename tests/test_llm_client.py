"""H-02 — LLMClient HTTP 타임아웃 구성 단위 테스트.

오프라인 결정론(실서버 0). LLMClient.__init__만 실행해 httpx.Client의
connect/read 타임아웃이 분리 구성되는지 단언한다(실호출·models/chat 없음).

배경: connect=10s로 서버 다운을 빠르게 감지하되, read는 LLM 생성 시간을
위해 기본 120s를 유지해야 한다(단축 시 정상 호출이 잘림 — PM-1).
"""

from __future__ import annotations

from core.llm_client import LLMClient


def test_llm_client_timeout_connect_read_split() -> None:
    """__init__ 후 _http.timeout이 connect=10s·read=120s로 분리되어야 한다."""
    # base_url·api_key를 직접 주입 → .env/실서버 의존 0, __init__만 실행.
    client = LLMClient(base_url="http://localhost:9", api_key="dummy-key")
    try:
        timeout = client._http.timeout
        assert timeout.connect == 10.0
        assert timeout.read == 120.0
    finally:
        client.close()


def test_llm_client_read_follows_custom_timeout() -> None:
    """timeout 인자를 주면 read에 매핑되고 connect는 10s 상한을 유지한다."""
    client = LLMClient(base_url="http://localhost:9", api_key="dummy-key", timeout=45.0)
    try:
        timeout = client._http.timeout
        assert timeout.read == 45.0
        assert timeout.connect == 10.0
    finally:
        client.close()
