"""P2-rest-B §18.6 — _persona_prompt 비파괴 플레이북 주입.

플레이북 파일 없으면 byte-identical(회귀 0), 있으면 절차기억 append. orch env·LLM 0.
"""

from __future__ import annotations

from orch.org_graph import _PERSONA_PROMPTS, _persona_prompt

# vault/playbook/<id>.md 가 없는(플레이북 미보유) 역할들 — byte-identical 이어야 함.
_NO_PLAYBOOK = ("coo", "librarian", "intake", "dev_lead", "ceo")


def test_byte_identical_when_no_playbook():
    for pid in _NO_PLAYBOOK:
        expected = _PERSONA_PROMPTS.get(pid, f"# {pid}\n(역할 프롬프트 없음)")
        assert _persona_prompt(pid) == expected


def test_unknown_pid_default_byte_identical():
    assert (
        _persona_prompt("nonexistent_role")
        == "# nonexistent_role\n(역할 프롬프트 없음)"
    )


def test_injects_playbook_when_present():
    # research 는 vault/playbook/research.md 존재 → 절차기억 주입(§18.6).
    out = _persona_prompt("research")
    assert "## 플레이북(절차기억)" in out
    # base(역할 프롬프트)가 prefix 로 보존됨.
    assert out.startswith(_PERSONA_PROMPTS["research"])
    # 플레이북 고유 토큰이 포함됨.
    assert "scope" in out or "출처" in out


def test_all_scaffolded_roles_inject():
    for pid in ("research", "dev", "art", "qa"):
        assert "## 플레이북(절차기억)" in _persona_prompt(pid)
