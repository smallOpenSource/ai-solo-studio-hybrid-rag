"""US-8c-1 u5 — .claude/settings.json 권한 정책 (§23.6 deny>ask>allow·B-1).

§11.1 최소권한·§7.3/§23.6: 사람-운용 Claude Code 권한. deny=시크릿(.env)·sudo·rm-rf,
ask=git push·podman·systemctl·loginctl, allow=Read/Edit/dev 도구, defaultMode=default
(미목록 명령은 기본 동작 → 운용 CC 루틴 도구[grep/ls 등] 무파손·deny만 차단·ask만 프롬프트).
정책 드리프트 회귀 lock(향후 deny 약화/제거 시 FAIL).
"""

from __future__ import annotations

import json
from pathlib import Path

_SETTINGS = Path(__file__).resolve().parents[2] / ".claude" / "settings.json"


def _perm() -> dict:
    data = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    return data["permissions"]


def test_settings_is_valid_json_and_has_permissions():
    data = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    assert "permissions" in data
    # ★add-only: 기존 hooks(start_guard) 블록 보존
    assert "hooks" in data and data["hooks"].get("SessionStart")


def test_deny_blocks_secrets_and_destructive():
    deny = set(_perm()["deny"])
    # §23.6:1057-1058 — 두 .env deny 리터럴 모두 + sudo/rm-rf
    assert "Read(**/*.env)" in deny
    assert "Read(/srv/office/config/env/**)" in deny
    assert "Bash(sudo *)" in deny
    assert "Bash(rm -rf *)" in deny


def test_ask_gates_sensitive_ops():
    ask = set(_perm()["ask"])
    assert "Bash(git push *)" in ask  # git push=Owner 게이트
    for cmd in ("Bash(podman *)", "Bash(systemctl *)", "Bash(loginctl *)"):
        assert cmd in ask


def test_allow_covers_operating_cc_core():
    allow = set(_perm()["allow"])
    # 운용 CC 핵심: 읽기/검색/편집/커밋/dev 도구
    for rule in (
        "Read(**)",
        "Glob(**)",
        "Grep(**)",
        "WebFetch",
        "Bash(git status)",
        "Bash(git commit *)",
        "Bash(python *)",
        "Bash(conda *)",
    ):
        assert rule in allow


def test_default_mode_is_default():
    # defaultMode=default → 미목록 명령은 기본 동작(운용 루틴 도구 무파손·deny/ask만 특별 처리)
    assert _perm()["defaultMode"] == "default"


def test_priority_deny_over_allow_for_env():
    # deny>ask>allow: Read(**) allow + Read(**/*.env) deny → .env 는 deny 우선(시크릿 보호)
    perm = _perm()
    assert "Read(**)" in perm["allow"]
    assert "Read(**/*.env)" in perm["deny"]
