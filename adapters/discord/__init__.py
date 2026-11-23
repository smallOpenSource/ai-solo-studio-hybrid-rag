"""Discord 어댑터 (Phase 4b) — React Inbox와 대등한 '또 하나의 프론트엔드'.

별도 워커 프로세스(conda env one_man_corp-discord). web_server SSE를 소비해
직원(워크플로) 활동·승인 요청을 디스코드에 게시하고, 버튼 클릭을 승인 REST로
보낸다. office/web_server 비침투(import 0 — HTTP/SSE 클라이언트로만 소비).

모듈:
- mapping     : event_to_message(seq, payload) 순수 매핑(discord 무의존)
- sse_client  : httpx 스트리밍 SSE 소비(id:/data: 분리, last_event_id 재연결)
- cursor      : state/discord/cursor.json 멱등 커서(중복 게시 방지)
- rest_client : POST /api/inbox/{seq}/approve|reject (200·404 둘 다 멱등)
- bot         : discord.py Client(setup_hook+create_task, persistent View)
"""
