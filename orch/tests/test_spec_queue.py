"""orch.spec_queue 단위 (P5a A1+A2·fake in-mem store·tmp git repo·네트워크 0).

검증:
  (a) enqueue→peek→harvest 라운드트립(fake store·tmp git repo·실 커밋).
  (b) harvest 부정케이스 3종(task_id 미존재 커밋·artifacts_expected 결손·DoD 미충족)
      → status≠done(실제 FAIL 경로 입증·green-theater 차단).
  (c) §7.1 HARD grep 가드(소스에 자동호출 리터럴 0·선례 test_evals_runner 모방).
  (d) make_claude_signal 3-신호(impl/audit/infra 접두사·결정론).
  (e) m8 harvest 순수성 spy(harvest 가 push_task/emit/기타 부작용 0·status set 만).

orch env = pytest-asyncio 미설치 → async 는 ``asyncio.run``(test_state_store 동형).
"""

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

import orch.spec_queue as sq
from orch.handoff import HandoffBrief


# ── fake in-mem store (set_task/get_task/push_task/peek_tasks/pop_task) ───────
class _SpyStore:
    """모든 메서드 호출을 기록하는 in-mem store(harvest 순수성 spy·m8)."""

    def __init__(self) -> None:
        self.kv: dict[str, dict] = {}
        self.queues: dict[str, list[dict]] = {}
        self.calls: list[str] = []

    async def set_task(self, task_id, payload):
        self.calls.append("set_task")
        self.kv[task_id] = dict(payload)

    async def get_task(self, task_id):
        self.calls.append("get_task")
        v = self.kv.get(task_id)
        return dict(v) if v is not None else None

    async def push_task(self, queue, item):
        self.calls.append("push_task")
        self.queues.setdefault(queue, []).insert(0, dict(item))

    async def peek_tasks(self, queue, n=10):
        self.calls.append("peek_tasks")
        return [dict(x) for x in self.queues.get(queue, [])[:n]]

    async def pop_task(self, queue):
        self.calls.append("pop_task")
        lst = self.queues.get(queue)
        return lst.pop() if lst else None


def _make_brief(task_id: str, *, artifacts: list[str]) -> HandoffBrief:
    return HandoffBrief(
        task_id=task_id,
        from_="DEV",
        to="Claude Code",
        objective="단일화면 슬라이딩 퍼즐 구현",
        context_refs=["vault/game-design/gdd.md"],
        acceptance_criteria=["state_shape: tiles,moves,solved,size", "move: moves+=1"],
        constraints=["vanilla JS", "window.__gameState 노출"],
        reversibility="two-way",
        confidence=0.8,
        priority="high",
        artifacts_expected=artifacts,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@local")
    _git(repo, "config", "user.name", "test")


def _seed_dod_and_artifacts(repo: Path, task_id: str, artifacts: list[str]) -> None:
    """DoD 4경로(artifact/handoff/research/worklog) + artifacts_expected 실파일 생성."""
    vault = repo / "vault"
    (vault / "specs").mkdir(parents=True, exist_ok=True)
    (vault / "research").mkdir(parents=True, exist_ok=True)
    (vault / "quality").mkdir(parents=True, exist_ok=True)
    (vault / "specs" / f"{task_id}.md").write_text("handoff", encoding="utf-8")
    (vault / "research" / f"{task_id}-notes.md").write_text("notes", encoding="utf-8")
    (vault / "quality" / f"worklog-{task_id}.md").write_text(
        "worklog", encoding="utf-8"
    )
    for rel in artifacts:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("artifact", encoding="utf-8")


# ── (a) enqueue→peek→harvest 라운드트립 ──────────────────────────────────────
def test_enqueue_peek_harvest_roundtrip(tmp_path, monkeypatch):
    """DEV 도구 명시 enqueue → peek_pending 항목 → 4-AND harvest → status=done."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    task_id = "p5-rt-1"
    artifacts = ["games/slide-puzzle/index.html", "games/slide-puzzle/PROVENANCE"]
    brief = _make_brief(task_id, artifacts=artifacts)

    # enqueue 가 vault/specs 에 쓰는 위치를 tmp repo 로 (real vault 무오염).
    monkeypatch.setattr(sq, "_vault_dir", lambda: repo / "vault")

    store = _SpyStore()

    async def run():
        out_id = await sq.enqueue_spec(store, brief, trigger="impl")
        assert out_id == task_id
        # peek_pending 에 항목 존재(명시 enqueue 입증).
        pending = await sq.peek_pending(store)
        assert pending == [{"task_id": task_id}]
        # set_task 상태 = queued + handoff_ref/assignee/trigger.
        rec = await store.get_task(task_id)
        assert rec["status"] == "queued"
        assert rec["assignee"] == "Claude Code"
        assert rec["trigger"] == "impl"
        assert rec["handoff_ref"].endswith(f"specs/{task_id}.md")

        # DoD + artifacts 실파일 + task_id 커밋(시계열: brief 먼저, commit 나중).
        _seed_dod_and_artifacts(repo, task_id, artifacts)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"feat: slide puzzle ({task_id})")

        res = await sq.harvest(store, task_id, repo, brief=brief)
        assert res.done is True, res.missing
        assert res.missing == []
        # status=done RMW(보존).
        done = await store.get_task(task_id)
        assert done["status"] == "done"
        assert done["handoff_ref"].endswith(f"specs/{task_id}.md")
        assert done["assignee"] == "Claude Code"
        assert done["trigger"] == "impl"
        # 큐에서 제거(pop_task).
        assert await sq.peek_pending(store) == []

    asyncio.run(run())


# ── (b) harvest 부정케이스 3종 → status≠done (실 FAIL 경로) ────────────────────
def test_harvest_negative_no_commit(tmp_path):
    """task_id 가 커밋 메시지에 없으면 done=False(추적성 a 위반)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    task_id = "p5-neg-commit"
    artifacts = ["games/x/index.html"]
    brief = _make_brief(task_id, artifacts=artifacts)
    _seed_dod_and_artifacts(repo, task_id, artifacts)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: unrelated commit (no task id)")

    store = _SpyStore()
    store.kv[task_id] = {
        "status": "queued",
        "handoff_ref": "x",
        "assignee": "Claude Code",
        "trigger": "impl",
    }

    async def run():
        res = await sq.harvest(store, task_id, repo, brief=brief)
        assert res.done is False
        assert "no_commit_with_task_id" in res.missing
        assert store.kv[task_id]["status"] == "queued"  # 갱신 안 됨

    asyncio.run(run())


def test_harvest_negative_artifact_missing(tmp_path):
    """artifacts_expected 경로가 실파일이 아니면 done=False(b 위반)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    task_id = "p5-neg-artifact"
    artifacts = ["games/x/index.html", "games/x/MISSING.html"]
    brief = _make_brief(task_id, artifacts=artifacts)
    # 첫 아티팩트만 생성(둘째 결손) + DoD.
    _seed_dod_and_artifacts(repo, task_id, ["games/x/index.html"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"feat: partial ({task_id})")

    store = _SpyStore()
    store.kv[task_id] = {
        "status": "queued",
        "handoff_ref": "x",
        "assignee": "Claude Code",
        "trigger": "impl",
    }

    async def run():
        res = await sq.harvest(store, task_id, repo, brief=brief)
        assert res.done is False
        assert any(m.startswith("artifact_missing:") for m in res.missing)
        assert store.kv[task_id]["status"] == "queued"

    asyncio.run(run())


def test_harvest_negative_dod_unmet(tmp_path):
    """DoD 4구성(worklog 결손) 미충족이면 done=False(c 위반)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    task_id = "p5-neg-dod"
    artifacts = ["games/x/index.html"]
    brief = _make_brief(task_id, artifacts=artifacts)
    _seed_dod_and_artifacts(repo, task_id, artifacts)
    # worklog 제거 → DoD 미충족.
    (repo / "vault" / "quality" / f"worklog-{task_id}.md").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"feat: missing worklog ({task_id})")

    store = _SpyStore()
    store.kv[task_id] = {
        "status": "queued",
        "handoff_ref": "x",
        "assignee": "Claude Code",
        "trigger": "impl",
    }

    async def run():
        res = await sq.harvest(store, task_id, repo, brief=brief)
        assert res.done is False
        assert any(m.startswith("dod_missing:") for m in res.missing)
        assert store.kv[task_id]["status"] == "queued"

    asyncio.run(run())


# ── (c) §7.1 HARD grep 가드(소스 자동호출 리터럴 0·선례 모방) ──────────────────
def test_spec_queue_no_autocall_literals_static_guard():
    """spec_queue.py 소스에 자동호출 리터럴이 부재(§7.1 HARD·test_graph_unit 소스풀스캔 모방).

    금지 리터럴(비대화형 플래그/제공자 API 키/에이전트 SDK/claude 부르는 subprocess)을
    *테스트 코드에만* 두고 소스에서 grep — offenders 가 있으면 FAIL. self-test #3 의
    ``grep -nE '\\-p |subprocess.*claude|anthropic|ANTHROPIC_API_KEY'`` 와 동치.
    """
    src_path = Path(sq.__file__)
    lines = src_path.read_text(encoding="utf-8").splitlines()
    forbidden = re.compile(r"-p |subprocess.*claude|anthropic|ANTHROPIC_API_KEY")
    offenders = [
        f"{src_path.name}:{i}"
        for i, line in enumerate(lines, 1)
        if forbidden.search(line)
    ]
    assert not offenders, f"§7.1 HARD 위반 리터럴 발견: {offenders}"
    # 추가 금지 토큰(Agent SDK 영문 표기).
    assert "Agent SDK" not in "\n".join(lines)
    # 양성 단언: 순수 큐 함수 3종이 실제로 존재(모듈이 비지 않음).
    assert "def enqueue_spec" in "\n".join(lines)
    assert "def peek_pending" in "\n".join(lines)
    assert "def harvest" in "\n".join(lines)


# ── (d) make_claude_signal 3-신호(접두사·결정론) ──────────────────────────────
def test_make_claude_signal_three_prefixes_deterministic():
    """impl/audit/infra 각 접두사 + objective + spec 경로 + 결정론(동일입력 동일출력)."""
    brief = _make_brief("p5-sig", artifacts=["games/x/index.html"])
    cases = {
        "impl": "🔧 [구현 대기]",
        "audit": "🔍 [정밀검수 요청]",
        "infra": "⚙️ [인프라·수정]",
    }
    for trigger, prefix in cases.items():
        s = sq.make_claude_signal(trigger, brief.task_id, brief)
        assert s.startswith(prefix), s
        assert brief.objective in s
        assert "vault/specs/p5-sig.md" in s
        assert "/claude 세션 개시 요망" in s
        # 결정론: 재호출 동일.
        assert s == sq.make_claude_signal(trigger, brief.task_id, brief)
    # 세 신호가 서로 구별됨(접두사 분기).
    distinct = {sq.make_claude_signal(t, "p5-sig", brief) for t in cases}
    assert len(distinct) == 3


def test_make_claude_signal_is_text_only_no_call():
    """신호는 *문자열*만 반환(자동 호출 객체 아님·emit_text_message content 인자 정합)."""
    brief = _make_brief("p5-sig2", artifacts=["games/x/index.html"])
    s = sq.make_claude_signal("audit", brief.task_id, brief)
    assert isinstance(s, str)


# ── (e) m8 harvest 순수성 spy(부작용 0·status set 만) ──────────────────────────
def test_harvest_purity_no_side_effects_on_success(tmp_path):
    """성공 harvest 가 호출하는 store 메서드 = {get_task, set_task, pop_task} 뿐.

    재큐(push_task)·emit·구현 트리거 등 *능동 실행 표면* 0(m8). push_task 가 호출되면
    FAIL — harvest 는 status 갱신만 한다는 불변식을 spy 로 양성 입증.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    task_id = "p5-purity"
    artifacts = ["games/p/index.html", "games/p/PROVENANCE"]
    brief = _make_brief(task_id, artifacts=artifacts)
    _seed_dod_and_artifacts(repo, task_id, artifacts)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"feat: puzzle ({task_id})")

    store = _SpyStore()
    store.kv[task_id] = {
        "status": "queued",
        "handoff_ref": "x",
        "assignee": "Claude Code",
        "trigger": "impl",
    }
    store.queues[sq._CLAUDE_QUEUE] = [{"task_id": task_id}]

    async def run():
        res = await sq.harvest(store, task_id, repo, brief=brief)
        assert res.done is True, res.missing
        # ★순수성: push_task(재큐)·peek_tasks 등 능동 표면 부재.
        assert "push_task" not in store.calls
        assert "peek_tasks" not in store.calls
        # 허용된 호출만(get_task→set_task→pop_task).
        assert set(store.calls) <= {"get_task", "set_task", "pop_task"}
        # set_task 는 정확히 1회(status=done RMW).
        assert store.calls.count("set_task") == 1

    asyncio.run(run())


def test_harvest_purity_no_side_effects_on_failure(tmp_path):
    """실패 harvest 는 store 를 *쓰지* 않는다(set_task/pop_task 0·읽기만)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    task_id = "p5-purity-fail"
    artifacts = ["games/p/index.html"]
    brief = _make_brief(task_id, artifacts=artifacts)
    # 커밋 없음 → 실패.
    store = _SpyStore()
    store.kv[task_id] = {"status": "queued"}

    async def run():
        res = await sq.harvest(store, task_id, repo, brief=brief)
        assert res.done is False
        assert "set_task" not in store.calls
        assert "pop_task" not in store.calls
        assert "push_task" not in store.calls

    asyncio.run(run())


# ── graceful: REDIS 미설정(store=None) ────────────────────────────────────────
def test_peek_pending_graceful_when_no_store():
    """store=None → peek_pending 빈 목록(503 아님·graceful)."""
    assert asyncio.run(sq.peek_pending(None)) == []


def test_enqueue_graceful_when_no_store(tmp_path, monkeypatch):
    """store=None → vault/specs 기록은 되고 큐 graceful no-op(§7.5 무손실)."""
    monkeypatch.setattr(sq, "_vault_dir", lambda: tmp_path / "vault")
    brief = _make_brief("p5-graceful", artifacts=["games/x/index.html"])

    async def run():
        out_id = await sq.enqueue_spec(None, brief, trigger="impl")
        assert out_id == "p5-graceful"
        # vault/specs 기록됨.
        spec = tmp_path / "vault" / "specs" / "p5-graceful.md"
        assert spec.is_file()

    asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
