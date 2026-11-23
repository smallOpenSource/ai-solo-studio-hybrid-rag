#!/usr/bin/env python3
"""WA TLS 종단 리버스 프록시 — /etc/studio/ssl 인증서로 HTTPS 종단 후 평문 백엔드로 포워드.

★왜: 이 호스트엔 nginx/caddy/socat/stunnel 부재 + WA 브릭(play/map-storage/icon/uploader)은
평문 HTTP만 서빙 + traefik 제거(포트모델). Owner 가 /etc/studio/ssl/studio.example.com/ 인증서를 제공
+ "SSL 쓰라" 지시 → web_server(33006·uvicorn ssl)와 동일 인증서로 VM 에서 직접 TLS 종단한다.

설계: 공개 HTTPS 포트 ── TLS 종단 ──▶ 백엔드(127.0.0.1:평문). TLS 종단 후 **바이트 양방향 파이프**라
HTTP·WebSocket(wss→ws) 모두 투명 통과(헤더 변형 0). play 는 PUSHER_URL=https://… 환경값으로
자기 공개 스킴을 알므로 X-Forwarded-Proto 불요.

★play(33157)는 경로 분기: WA play 컨테이너는 HTTP(컨테이너 3000)와 WebSocket(컨테이너 3001)을
**별도 포트**로 서빙한다(로그: "Pusher web-server …3000" / "web-socket server …3001"). 프론트는
PUSHER_URL 하나로 HTTP·WS 둘 다 https://…:33157 로 접속하므로, 33157 종단 후 **첫 요청라인의 경로**로
`/ws*` → 3001(host 43159), 그 외(프론트·HTTP API) → 3000(host 43157) 로 분기한다. WA 레퍼런스
배포에서 traefik 이 하던 경로 라우팅과 같은 계층. (3000만 게시했을 때 /ws/room → Express 404 였음.)

★map(33158)도 경로 분기: WA 좌측 in-world 패널(office_panel.js 가 origin+/maps/agent_panel.html 로
임베드)을 renewal 택소노미+라이브 데이터로 살린다. Owner 정적호스트(:33150)·맵(office.tmj)은 비편집
이라, 패널 HTML 과 /agents 피드는 orch(:28202·canonical agent_status)가 제공하고 이 프록시가 :33158
에서 그 두 경로만 orch 로 라우팅한다(나머지=맵 리소스→:33150).

라우트(공개HTTPS → 백엔드平문):
  33157→play: /ws*→3001(43159) · else→3000(43157) ·
  33151→map-storage http(43151) · 33152→icon(43152) ·
  33153→uploader(43153) ·
  33158→map: /agents·/agent_panel.html·/maps/agent_panel.html → orch(127.0.0.1:28202) ·
         그 외 → 맵 정적호스트(Owner·127.0.0.1:33150)
종료성 P0: asyncio·연결당 독립 태스크·예외 격리·서버 무한 serve(데몬·systemd 관리).
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import os
import ssl
from pathlib import Path

_CERT = "/etc/studio/ssl/studio.example.com/fullchain.pem"
_KEY = "/etc/studio/ssl/studio.example.com/privkey.pem"

# play(33157) 경로 분기 백엔드
_PLAY_PUB = 33157
_PLAY_HTTP = ("127.0.0.1", 43157)  # 컨테이너 3000: front + pusher HTTP API
_PLAY_WS = ("127.0.0.1", 43159)  # 컨테이너 3001: pusher WebSocket(/ws/room)

# map(33158) 패널/agents 라우팅 백엔드 (orch·canonical agent_status)
_MAP_PUB = 33158
_MAP_STATIC = (
    "127.0.0.1",
    33150,
)  # Owner 정적 맵호스트(office.tmj·타일셋·office_panel.js)
_ORCH_BACKEND = ("127.0.0.1", 28202)  # orch core (renewal 패널 HTML + /agents 피드)
_PANEL_REQ_PATHS = (b"/maps/agent_panel.html", b"/agent_panel.html")

# 단순 종단 라우트 — 공개 HTTPS 포트 : (백엔드 host, 백엔드 port)
ROUTES: dict[int, tuple[str, int]] = {
    33151: ("127.0.0.1", 43151),  # map-storage HTTP
    33152: ("127.0.0.1", 43152),  # icon
    33153: ("127.0.0.1", 43153),  # uploader
}


# ── WA 접속 인증 게이트 (play :33157 진입) ──────────────────────────────────
# .env 의 HITL_OWNER_NAME/HITL_OWNER_PASSWORD 로 HTTP Basic 인증. ★진입점(play front+WS)
# 만 게이트한다 — 맵/자산 포트(33158/51/52/53)는 SPA 의 cross-origin fetch 가 인증 없이
# 접근해야 로드되므로 열어둔다(진입 자체가 play 에서 막히면 라이브 오피스 입장 불가).
# 시크릿 값은 절대 출력/로그하지 않는다(활성 여부만).
def _load_owner_basic() -> bytes | None:
    """.env 에서 기대 'Basic <b64>' 토큰을 만든다(없으면 None=게이트 비활성)."""
    creds: dict[str, str] = {}
    try:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            creds[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    name = os.environ.get("HITL_OWNER_NAME") or creds.get("HITL_OWNER_NAME")
    pw = os.environ.get("HITL_OWNER_PASSWORD") or creds.get("HITL_OWNER_PASSWORD")
    if not name or not pw:
        return None
    token = base64.b64encode(f"{name}:{pw}".encode()).decode()
    return b"Basic " + token.encode()


def _auth_ok(first: bytes, expected: bytes) -> bool:
    """첫 요청 헤더에서 Authorization 을 찾아 상수시간 비교."""
    for line in first.split(b"\r\n"):
        if line[:14].lower() == b"authorization:":
            val = line.split(b":", 1)[1].strip()
            return hmac.compare_digest(val, expected)
    return False


_UNAUTH_BODY = b"401 Authentication required"
_UNAUTH_RESPONSE = (
    b"HTTP/1.1 401 Unauthorized\r\n"
    b'WWW-Authenticate: Basic realm="AIGS Office", charset="UTF-8"\r\n'
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Length: " + str(len(_UNAUTH_BODY)).encode() + b"\r\n"
    b"Connection: close\r\n\r\n" + _UNAUTH_BODY
)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (OSError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def _bridge(
    client_r: asyncio.StreamReader,
    client_w: asyncio.StreamWriter,
    backend: tuple[str, int],
    preface: bytes = b"",
) -> None:
    try:
        back_r, back_w = await asyncio.open_connection(*backend)
    except OSError:
        client_w.close()
        return
    if preface:
        back_w.write(preface)
        try:
            await back_w.drain()
        except OSError:
            pass
    await asyncio.gather(_pipe(client_r, back_w), _pipe(back_r, client_w))


def _make_handler(backend: tuple[str, int]):
    async def handle(
        client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter
    ) -> None:
        await _bridge(client_r, client_w, backend)

    return handle


def _make_play_handler(expected_auth: bytes | None):
    """play 전용: TLS 종단 후 첫 요청라인의 경로로 3000(HTTP) vs 3001(WS) 분기.

    expected_auth 가 설정되면 진입을 HTTP Basic 으로 게이트한다(미인증=401)."""

    async def handle(
        client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter
    ) -> None:
        try:
            first = await client_r.read(8192)
        except OSError:
            client_w.close()
            return
        if not first:
            client_w.close()
            return
        if expected_auth is not None and not _auth_ok(first, expected_auth):
            try:
                client_w.write(_UNAUTH_RESPONSE)
                await client_w.drain()
            except OSError:
                pass
            client_w.close()
            return
        path = b""
        nl = first.find(b"\r\n")
        if nl != -1:
            parts = first[:nl].split(b" ")
            if len(parts) >= 2:
                path = parts[1]
        backend = _PLAY_WS if path.startswith(b"/ws") else _PLAY_HTTP
        await _bridge(client_r, client_w, backend, preface=first)

    return handle


def _route_maphost(
    first: bytes, default: tuple[str, int]
) -> tuple[tuple[str, int], bytes]:
    """첫 요청을 파싱해 (백엔드, Connection:close 강제+경로교정된 요청바이트)를 돌려준다.

    /agents·/agent_panel.html·/maps/agent_panel.html → orch, 그 외 → default(:33150).
    ★keep-alive 비활성(Connection: close): :33158 은 origin 하나(브라우저가 패널 iframe·맵
    리소스 요청을 한 풀에서 공유)라, 첫-요청 백엔드 라우팅은 keep-alive 연결이 재사용되면
    깨진다(맵용 연결에 /agents 가 실릴 수 있음). 모든 요청에 Connection: close 를 강제해
    **연결당 1요청**을 보장하면 첫-요청 라우팅이 항상 정확하다(작은 GET 위주·핸드셰이크 비용 무시).
    """
    nl = first.find(b"\r\n")
    if nl == -1:
        return (default, first)
    req_line = first[:nl]
    after = first[nl + 2 :]
    parts = req_line.split(b" ")
    path = parts[1] if len(parts) >= 2 else b"/"
    raw = path.split(b"?", 1)[0]
    backend = default
    if raw == b"/agents":
        backend = _ORCH_BACKEND
    elif raw in _PANEL_REQ_PATHS:
        backend = _ORCH_BACKEND
        new = b"/agent_panel.html"
        if b"?" in path:
            new = new + b"?" + path.split(b"?", 1)[1]
        parts[1] = new
        req_line = b" ".join(parts)
    # Connection: close 강제 — 기존 Connection 헤더 제거 후 요청라인 뒤에 삽입.
    kept = [h for h in after.split(b"\r\n") if not h.lower().startswith(b"connection:")]
    munged = req_line + b"\r\nConnection: close\r\n" + b"\r\n".join(kept)
    return (backend, munged)


def _make_maphost_handler(default: tuple[str, int]):
    """map(33158) 핸들러: 첫 요청으로 백엔드 라우팅(패널/agents→orch · 그외→맵호스트)."""

    async def handle(
        client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter
    ) -> None:
        try:
            first = await client_r.read(65536)
        except OSError:
            client_w.close()
            return
        if not first:
            client_w.close()
            return
        backend, preface = _route_maphost(first, default)
        await _bridge(client_r, client_w, backend, preface=preface)

    return handle


async def main() -> None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(_CERT, _KEY)
    servers = []

    owner_auth = _load_owner_basic()

    srv = await asyncio.start_server(
        _make_play_handler(owner_auth), "0.0.0.0", _PLAY_PUB, ssl=ctx
    )
    servers.append(srv)
    print(
        f"[wa_tls_proxy] HTTPS :{_PLAY_PUB} → /ws*→3001(43159) · else→3000(43157)"
        f" · auth={'ON(HITL_OWNER)' if owner_auth else 'OFF(creds 미설정)'}",
        flush=True,
    )

    # map(33158): 패널/agents → orch · 그 외 → 맵 정적호스트(:33150)
    srv = await asyncio.start_server(
        _make_maphost_handler(_MAP_STATIC), "0.0.0.0", _MAP_PUB, ssl=ctx
    )
    servers.append(srv)
    print(
        f"[wa_tls_proxy] HTTPS :{_MAP_PUB} → /agents·/agent_panel.html→orch{_ORCH_BACKEND} · "
        f"else→{_MAP_STATIC[0]}:{_MAP_STATIC[1]}",
        flush=True,
    )

    for pub_port, backend in ROUTES.items():
        srv = await asyncio.start_server(
            _make_handler(backend), "0.0.0.0", pub_port, ssl=ctx
        )
        servers.append(srv)
        print(
            f"[wa_tls_proxy] HTTPS :{pub_port} → {backend[0]}:{backend[1]}", flush=True
        )
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
