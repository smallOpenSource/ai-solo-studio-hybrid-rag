# SearXNG 메타검색 배포 (§8·P9 US-9a-1)

**상태(2026-06-23·라이브 검증)**: `aigs-searxng` 컨테이너 **가동 중** — `127.0.0.1:2815→8080`·`GET /search?q=...&format=json` → **HTTP 200 application/json**(실결과). 검색 클라이언트 `orch/searxng.py` 라이브 왕복 확인(`search('python asyncio')`→3 실결과).

## 1. 배포 방식 (OD-P9-A = A-1 — P1에서 실현됨)
- **결정 A-1**: 스펙 §8(line 372)은 SearXNG를 rootless Podman **Quadlet**(`~/.config/containers/systemd/aigs-searxng.container`)으로 규정. 그러나 RHEL8/cgroup-v1 rootless에서 Quadlet 생성 유닛(`podman run --cgroups=split`)이 실패(P1 선례·`wiki/12_renewal_p1_foundation.md`).
- **실현**: §23.2(spec line 924)가 **명시 인가**한 **플레인 systemd `--user` 유닛 폴백**으로 배포됨 — `aigs-searxng.service`("Path A · plain-unit fallback"·`aigs-qdrant`/`aigs-phoenix`와 동일 패턴). **P1 토대 단계에서 이미 가동**(44h+ uptime). 따라서 P9는 추가 컨테이너 배포 불요 — **검색 클라이언트 코드**만 신규.
- 이미지: `docker.io/searxng/searxng:latest`. 포트: `127.0.0.1:2815:8080`(레지스트리 2815).

## 2. 설정 (`state/podman/data/searxng/settings.yml` → 바인드 `/etc/searxng`)
§23.5 핵심 필드(P1 배치·라이브 검증):
- `formats: [html, json]` — **API 필수**. JSON 200 응답으로 활성 실증(2026-06-23).
- `limiter: false` — 사설 단일 사용자.
- `secret_key` — P1 생성(`openssl rand -hex 32`·값 미기록·시크릿).
- 변경 시: `systemctl --user restart aigs-searxng`.

## 3. 검색 클라이언트 (`orch/searxng.py` — NET-NEW·P9)
- `search(query, *, config, max_results, transport) -> list[SearxResult]` — `GET /search?q=...&format=json` → 정규화(`url`/`title`/`content`/`engine`·빈 url 제외).
- 종료성 P0: HTTP 타임아웃 **바운드**(connect 5s + read 10s·`SEARXNG_TIMEOUT_S` 오버라이드·무한/0 차단).
- 시크릿: 로컬 무인증·쿼리/값 로깅 0. 테스트 주입 seam(`httpx.MockTransport`).
- 소비처: 연구 파이프라인 RPI Research → 결과 URL을 `knowledge.crawl`로 넘겨 **비신뢰 인입**(`capture_and_index(provenance="untrusted", source_url=url)`·§11.1·US-9a-2/US-9c-3).

## 4. ADR (리터럴 vs RHEL8 현실)
- **결정**: Quadlet(스펙 리터럴) → RHEL8 cgroup-v1 rootless 실패 → §23.2 인가 **플레인 systemd `--user` 폴백**.
- **근거**: 기능 본질 = `GET /search?format=json` 200 JSON(컨테이너 오케스트레이션 기제 무관). P1에서 동일 패턴(Qdrant/Phoenix)으로 실증. §23.2가 폴백을 *명시 인가*하므로 리터럴 위반 아님.
- **결과**: 컨테이너 라이브·재기동 `systemctl --user restart aigs-searxng`. Quadlet 자동재기동 의미론 차이는 `Restart=on-failure`(플레인 유닛)로 보전.

## 5. 검증 (US-9a-1)
- **[unit u1]** `orch/tests/test_searxng.py` 5 passed(파싱·max_results·HTTP 에러·타임아웃 바운드·빈 결과·MockTransport).
- **[라이브 e1]** `search('python asyncio')` → docs.python.org 등 3 실결과(2026-06-23·`CLIENT-LIVE-OK`).
- ruff clean.
