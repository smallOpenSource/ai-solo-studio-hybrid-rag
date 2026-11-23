"""web_server 실행 진입점 — uvicorn 단일 워커(--workers 1) 고정.

사용법:
    python -m web_server            # endpoint.yaml web 블록의 host/port로 기동

★단일 워커 고정(M2, 수용 20 — 계획 §4.9):
- workers=1을 코드로 못 박는다. 멀티워커면 web_server.runner의 전역 단일 실행 락
  (_workflow_lock)·전용 executor·web_server.sse의 인메모리 fan-out 큐가 프로세스별로
  쪼개져 **동시성 1·단일 실행·구독이 깨진다**. 4a 게이트는 단일 워커를 전제한다.
- run_full을 띄우는 것은 runner의 전용 스레드(ThreadPoolExecutor)지 uvicorn 워커가
  아니다 — 이벤트루프 비블로킹(F1′). 여기 workers=1은 "그 전용 스레드/락/큐가 한
  프로세스에 모여 있게" 하는 전제다.

host/port는 config/endpoint.yaml의 web 블록에서 읽는다(코드 하드코딩 0, 수용 18).
"""

from __future__ import annotations

import os

import uvicorn

from core.config import get_web_config
from web_server.app import create_app


def main() -> None:
    """endpoint.yaml web 블록의 host/port로 uvicorn을 단일 워커로 기동한다."""
    cfg = get_web_config()
    # ★프로덕션은 로그인 세션 게이트 ON(item5). 암호 미설정 시 잠금(silent-open보다 안전).
    app = create_app(auth_required=True)
    # ★workers=1 고정 — 멀티워커 금지(M2). reload도 끔(다중 프로세스 회피).
    kwargs = {"host": cfg["host"], "port": int(cfg["port"]), "workers": 1}
    cert, key = cfg.get("ssl_certfile"), cfg.get("ssl_keyfile")
    if cert and key:
        if os.access(cert, os.R_OK) and os.access(key, os.R_OK):
            kwargs["ssl_certfile"], kwargs["ssl_keyfile"] = cert, key
        else:
            print(f"[web_server] ssl 파일 읽기 불가({cert}/{key}) — HTTP 폴백")
    uvicorn.run(app, **kwargs)


if __name__ == "__main__":
    main()
