"""멀티 프론트 어댑터 패키지 (Phase 4b~).

각 어댑터는 web_server가 노출한 SSE(/events/stream)·승인 REST(/api/inbox)
계약의 HTTP/SSE 클라이언트 소비자다. office/web_server 파이썬은 한 줄도 고치지
않는다(비침투). 4b = discord/.
"""
