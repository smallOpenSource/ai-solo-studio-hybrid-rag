"""orch/digest.py — SR/renewal §20.3/§20.4b 개입 빈도 억제 + 일일 다이제스트.

§20.3 "per-decision 핑 금지": Notify/Autonomous 결정을 **즉시 핑하지 않고 누적**(비동기) →
묶음 **일일 다이제스트**(🟢 자율 N / 🟡 통지 M / 🔴 대기 K). §20.4b 포맷·1항목=1줄(ADR ref).

영속: REDIS_ENABLED 시 ``aigs:*`` 리스트(``store`` 주입·blackboard_sink 동형 패턴) + **vault 파일
폴백(항상)**. ``store=None``(미설정) 이면 파일만(graceful). 핫패스 LLM 0. 비밀 평문 0.
※ 발송 스케줄러(다이제스트를 실제 Discord/WA 로 push)는 운영/P6 — 본 모듈은 *누적+렌더*까지(정직 경계).
"""

from __future__ import annotations

import contextlib
from datetime import date as _date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]

_EMOJI = {"autonomous": "🟢", "notify": "🟡", "escalate": "🔴"}


def _tier_str(tier: Any) -> str:
    """Tier enum / 문자열 → 정규 tier 값(autonomous|notify|escalate)."""
    return getattr(tier, "value", str(tier))


def _format_entry(entry: dict[str, Any]) -> str:
    """다이제스트 1항목 = 1줄(§20.4b). 🟢 [role] summary confN [ref]."""
    tier = _tier_str(entry.get("tier"))
    emoji = _EMOJI.get(tier, "·")
    role = entry.get("role") or "?"
    summ = entry.get("summary") or ""
    conf = entry.get("confidence")
    conf_s = f" conf{conf:.2f}" if isinstance(conf, (int, float)) else ""
    ref = entry.get("ref") or ""
    ref_s = f" [{ref}]" if ref else ""
    return f"{emoji} [{role}] {summ}{conf_s}{ref_s}"


async def append_decision(
    tier: Any,
    summary: str,
    *,
    confidence: float | None = None,
    ref: str = "",
    role: str = "",
    store: Any = None,
    vault_dir: str | Path | None = None,
    today: str | None = None,
) -> Path:
    """결정 1건을 다이제스트에 누적(§20.3 per-decision 핑 0). REDIS(있으면)+vault 파일(항상).

    Returns 작성한 다이제스트 파일 경로.
    """
    day = today or _date.today().isoformat()
    entry = {
        "tier": _tier_str(tier),
        "summary": summary,
        "confidence": confidence,
        "ref": ref,
        "role": role,
    }
    # (1) REDIS 리스트(aigs:* · REDIS_ENABLED 시) — store.push_task(digest:<date>) 재사용.
    if store is not None:
        with contextlib.suppress(Exception):  # 누적 실패가 라이브 흐름을 막지 않게
            await store.push_task(f"digest:{day}", entry)
    # (2) vault 파일 폴백(항상·휴먼 미러).
    vdir = Path(vault_dir) if vault_dir is not None else (_REPO_ROOT / "vault")
    ddir = vdir / "digest"
    ddir.mkdir(parents=True, exist_ok=True)
    path = ddir / f"{day}.md"
    prior = (
        path.read_text(encoding="utf-8")
        if path.is_file()
        else f"# 일일 다이제스트 — {day}\n\n"
    )
    path.write_text(prior + _format_entry(entry) + "\n", encoding="utf-8")
    return path


def render_digest(entries: list[dict[str, Any]], day: str) -> str:
    """누적 결정 목록 → §20.4b 묶음 다이제스트 문자열(🟢 자율 N / 🟡 통지 M / 🔴 대기 K)."""
    auto = [e for e in entries if _tier_str(e.get("tier")) == "autonomous"]
    notify = [e for e in entries if _tier_str(e.get("tier")) == "notify"]
    escalate = [e for e in entries if _tier_str(e.get("tier")) == "escalate"]
    lines = [f"# 일일 다이제스트 — {day}", ""]
    lines.append(f"🟢 자율 결정 {len(auto)}건(가역·override 가능)")
    lines.extend(f"  · {_format_entry(e)}" for e in auto)
    lines.append(f"🟡 통지 {len(notify)}건 / 🔴 대기 중 승인 {len(escalate)}건")
    lines.extend(f"  · {_format_entry(e)}" for e in notify + escalate)
    return "\n".join(lines) + "\n"


async def read_digest_entries(day: str, *, store: Any) -> list[dict[str, Any]]:
    """REDIS 에 누적된 그날 항목(있으면). store=None/실패 → 빈 목록(graceful)."""
    if store is None:
        return []
    with contextlib.suppress(Exception):
        return await store.peek_tasks(f"digest:{day}", n=200)
    return []
