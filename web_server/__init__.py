"""web_server — Phase 4a 백엔드 웹 게이트웨이(얇음).

office/ 엔진이 SQLite events 테이블에 쌓는 이벤트를 브라우저로 실시간
흘려보내는 얇은 레이어. office를 import만 하고, office는 web_server를
import하지 않는다(엔진 비침투 — 단방향 의존).

LLM은 직접 호출하지 않는다(워크플로 = office 경유).
"""
