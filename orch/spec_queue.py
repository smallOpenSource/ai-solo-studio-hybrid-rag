"""SR/renewal P5a — spec-queue 메커니즘 (§7.2·§7.5·net-new·사람=비동기 브리지).

스펙 권위(renewal-spec-is-authority · SR/renewal/AI_GAME_STUDIO_IMPLEMENTATION_SPEC.md):
  - §7.2(339): DEV 에이전트는 작업을 *큐에 쌓아 사람에게 넘기고*, 산출(commit/diff)을
    수집해 보고한다.
  - §7.5(357-366): 개입 3트리거(구현/정밀검수/인프라) + 무손실 비동기(큐·스펙·상태가
    git/Redis 영속이라 Owner 가 언제 개시하든 중단 지점에서 재개).
  - §27.3(1859-1871): Task 큐 — 키 ``aigs:task:<task_id>``(status·handoff_ref) +
    큐 ``aigs:queue:<agent>``(List)에 push/pop.

★§7.1 HARD 절대 경계(renewal-spec-is-authority · 계획 §1.1 원칙2):
  이 모듈은 로컬 에이전트를 **프로그램으로 호출하지 않는다**. 비대화형 플래그·
  에이전트 SDK·제공자 API 키·PTY 자동구동 같은 자동호출 리터럴이 소스에 **부재**
  해야 한다(정적 grep 가드 = ``orch/tests/test_spec_queue.py`` — 금지 리터럴은
  *테스트에만* 둔다). 사람이 큐를 드레인하는 *비동기 브리지* 모델만 구현 — 순수
  큐 연산(enqueue/peek/harvest)뿐, 능동 실행 표면(다음 액션 트리거)이 **0** 이다
  (harvest 는 status *갱신*만).

★C1(계획 §1.5·시나리오4): enqueue 개시 = DEV 도구(``tools/p5_make_brief.py``)의
  *명시적* 호출이다 — cycle/org graph 에는 DEV-핸드오프(HandoffBrief) 산출 노드가
  없으므로 app.py 자동 sink 는 영구 0회 죽은 분기(green-fake)다. 따라서 app.py 무수정.

이 모듈은 orch 코어 그래프(graph.py / org_graph.py / outbound.py / app.py)와 **독립한
신규 표면**(동결셋 무관·코어 0수정). git 은 읽기 전용 조회(``git log``)로만 쓴다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from orch.handoff import HandoffBrief, check_dod, write_brief

# §7.5 개입 3트리거(SPEC:357-363) — 큐 항목의 처리 유형(리터럴 계약).
Trigger = Literal["impl", "audit", "infra"]

# §27.3 큐 — Claude Code(사람 드레인) 전용 List 큐 이름.
_CLAUDE_QUEUE = "claude_code"

# §7.5 3-신호 접두사(직원 발화와 구분·결정론·LLM 0). emit_text_message BLUF 본문용.
_SIGNAL_PREFIX: dict[str, str] = {
    "impl": "🔧 [구현 대기]",
    "audit": "🔍 [정밀검수 요청]",
    "infra": "⚙️ [인프라·수정]",
}


@dataclass
class HarvestResult:
    """harvest 판정 결과(falsifiable 4-AND).

    ``done`` 이면 4조건 전부 충족(status=done 로 갱신됨), 아니면 ``missing`` 에
    미충족 사유. harvest 는 **status 갱신만** 한다(다음 액션 트리거 0·m8).
    """

    task_id: str
    done: bool
    missing: list[str] = field(default_factory=list)


def _store_active(store: Any) -> bool:
    """store 가 실제 큐/키 연산을 받을 수 있는지(REDIS 미설정 → False·graceful)."""
    return store is not None


async def enqueue_spec(
    store: Any,
    brief: HandoffBrief,
    *,
    trigger: Trigger,
) -> str:
    """브리프를 vault/specs 에 기록하고 Claude Code 큐에 적재(§7.2·§27.3).

    DEV 도구/배관이 **명시적으로** 호출한다(graph 자동 sink 아님·C1). 동작:
      1. ``write_brief`` 로 ``<vault>/specs/<task_id>.md`` 기록(항상·git 영속).
      2. ``set_task(task_id, {status:queued, handoff_ref, assignee, trigger})``.
      3. ``push_task("claude_code", {task_id})`` — List 큐 적재.

    REDIS 미설정(store=None) 시 vault 기록만 하고 큐 연산은 graceful no-op
    (§7.5 무손실: 스펙은 git 영속이라 큐 부재여도 재개 가능). 반환 = task_id.

    ★vault_dir = repo 루트의 ``vault/``(handoff.write_brief 가 ``specs/`` 하위에 기록).
    """
    out = write_brief(brief, _vault_dir())
    handoff_ref = str(out)
    if _store_active(store):
        await store.set_task(
            brief.task_id,
            {
                "status": "queued",
                "handoff_ref": handoff_ref,
                "assignee": brief.to,
                "trigger": trigger,
            },
        )
        await store.push_task(_CLAUDE_QUEUE, {"task_id": brief.task_id})
    return brief.task_id


async def peek_pending(store: Any, n: int = 10) -> list[dict[str, Any]]:
    """``aigs:queue:claude_code`` 큐의 RO 뷰(§27.3·peek_tasks 위임).

    REDIS 미설정(store=None) → 빈 목록(graceful·503 아님). 능동 부작용 0.
    """
    if not _store_active(store):
        return []
    return await store.peek_tasks(_CLAUDE_QUEUE, n)


def _git_log_has_task(repo_root: Path | str, task_id: str) -> bool:
    """``git log --all --grep=<task_id>`` 로 task_id 가 커밋 메시지에 1건+ 있는지.

    읽기 전용 조회뿐(쓰기/체크아웃 0). git 부재/비-repo 면 False(예외 흡수).
    """
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                "--all",
                f"--grep={task_id}",
                "--format=%H",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    return any(line.strip() for line in proc.stdout.splitlines())


def _git_commit_ts(repo_root: Path | str, task_id: str) -> int | None:
    """task_id 를 가진 가장 이른 커밋의 author timestamp(epoch). 없으면 None.

    시계열 brief_sha⊰commit_sha 판정용(d): 브리프 파일 mtime ⊰ 게임 commit ts.
    """
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                "--all",
                f"--grep={task_id}",
                "--format=%at",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    stamps = [int(x) for x in proc.stdout.split() if x.strip().isdigit()]
    return min(stamps) if stamps else None


def _resolve_brief(
    store_task: dict[str, Any] | None, brief: HandoffBrief | None
) -> HandoffBrief | None:
    """harvest 가 쓸 brief 복원: 인자 우선, 없으면 get_task 의 handoff_ref 파일 파싱."""
    if brief is not None:
        return brief
    if not store_task:
        return None
    ref = store_task.get("handoff_ref")
    if not ref:
        return None
    path = Path(ref)
    if not path.is_file():
        return None
    from orch.handoff import parse_brief

    try:
        return parse_brief(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


async def harvest(
    store: Any,
    task_id: str,
    repo_root: Path | str,
    *,
    brief: HandoffBrief | None = None,
) -> HarvestResult:
    """falsifiable 4-AND 수확 판정(§7.2 산출 수집). **status 갱신만**(능동 트리거 0·m8).

    조건(전부 AND):
      (a) ``git log --all --grep=<task_id>`` 1건+ (사람-브리지가 task_id 를 커밋
          메시지에 삽입 = 추적성·m7).
      (b) ``brief.artifacts_expected`` 경로 전부 실파일 존재.
      (c) ``check_dod`` 4구성(artifact/research_evidence/worklog/handoff) 충족
          (m6 4경로 소싱).
      (d) 시계열: 브리프 파일 mtime ⊰ task_id 커밋 timestamp(brief 생성 ⊰ game commit).

    4 AND → ``get_task`` 로 기존 payload 복원 → status 만 ``done`` 으로 변경 →
    ``set_task``(RMW·handoff_ref/assignee/trigger 보존·full 덮어쓰기 금지·LOW-1) +
    ``pop_task`` 큐 제거. 아니면 status 유지 + ``missing`` 에 미충족 사유.

    ★harvest 는 다음 액션을 트리거하지 않는다 — 재큐·emit·구현 호출 0(순수 판정).
    """
    store_task = await store.get_task(task_id) if _store_active(store) else None
    resolved = _resolve_brief(store_task, brief)

    missing: list[str] = []

    # (a) task_id 커밋 존재
    if not _git_log_has_task(repo_root, task_id):
        missing.append("no_commit_with_task_id")

    # (b) artifacts_expected 전부 실파일
    if resolved is None:
        missing.append("brief_unresolved")
        artifacts: list[str] = []
    else:
        artifacts = list(resolved.artifacts_expected)
        for rel in artifacts:
            if not _artifact_path(repo_root, rel).is_file():
                missing.append(f"artifact_missing:{rel}")

    # (c) DoD 4구성(m6 4경로 소싱)
    dod_paths = _dod_paths(repo_root, task_id, resolved)
    dod = check_dod(**dod_paths)
    if not dod.ok:
        missing.extend(f"dod_missing:{name}" for name in dod.missing)

    # (d) 시계열 brief ⊰ commit. brief 파일 mtime 소스 = store 의 handoff_ref(실파일이면)
    # 우선, 아니면 §27.1 정본 경로(vault/specs/<id>.md = dod_paths["handoff"]) 폴백.
    commit_ts = _git_commit_ts(repo_root, task_id)
    brief_ref = store_task.get("handoff_ref") if store_task else None
    brief_mtime = _file_mtime(brief_ref)
    if brief_mtime is None:
        brief_mtime = _file_mtime(dod_paths["handoff"])
    if commit_ts is None:
        missing.append("no_commit_timestamp")
    elif brief_mtime is None:
        missing.append("no_brief_timestamp")
    elif not (brief_mtime <= commit_ts):
        missing.append("temporal_order_violated")

    if missing:
        return HarvestResult(task_id=task_id, done=False, missing=missing)

    # 4 AND 충족 — RMW status=done(기존 payload 보존) + 큐 제거.
    if _store_active(store):
        existing = store_task if isinstance(store_task, dict) else {}
        updated = dict(existing)
        updated["status"] = "done"
        await store.set_task(task_id, updated)
        await store.pop_task(_CLAUDE_QUEUE)
    return HarvestResult(task_id=task_id, done=True, missing=[])


def make_claude_signal(trigger: Trigger, task_id: str, brief: HandoffBrief) -> str:
    """§7.5 3-신호 = ``emit_text_message`` 로 방출할 BLUF **문자열**(결정론·LLM 0).

    신규 이벤트 타입 0(동결 events.ts 준수·outbound.py 는 TEXT_MESSAGE 만 제공).
    접두사로 직원 발화와 구분 — impl/audit/infra. 형식:
      ``<접두사> <objective> · spec: <handoff_ref> · /claude 세션 개시 요망``

    handoff_ref = §27.1 저장 위치(``vault/specs/<task_id>.md``)를 리터럴로 구성
    (write_brief 와 동일 규칙). ★이 함수는 *알림 텍스트*만 만든다 — 자동 호출 아님.
    """
    prefix = _SIGNAL_PREFIX[trigger]
    handoff_ref = f"vault/specs/{task_id}.md"
    return f"{prefix} {brief.objective} · spec: {handoff_ref} · /claude 세션 개시 요망"


# ── 내부 경로 헬퍼 (m6 DoD 4경로 소싱·repo 루트 상대) ───────────────────────────
def _vault_dir() -> Path:
    """repo 루트의 ``vault/``(이 파일 = ``orch/spec_queue.py`` → parents[1])."""
    return Path(__file__).resolve().parents[1] / "vault"


def _artifact_path(repo_root: Path | str, rel: str) -> Path:
    """artifacts_expected 항목(repo 상대 경로)을 절대경로로."""
    p = Path(rel)
    return p if p.is_absolute() else Path(repo_root) / rel


def _dod_paths(
    repo_root: Path | str,
    task_id: str,
    brief: HandoffBrief | None,
) -> dict[str, str]:
    """§18.5 DoD 4구성 경로(m6 4경로 소싱·vault/P2.5 규약).

    artifact = brief.artifacts_expected[0](게임 index.html) · handoff = write_brief
    경로(vault/specs/<id>.md) · research_evidence = vault/research/<task>-notes.md ·
    worklog = vault/quality/worklog-<id>.md. 결손이면 check_dod 가 미충족으로 잡는다.
    """
    root = Path(repo_root)
    vault = root / "vault"
    artifact = ""
    if brief is not None and brief.artifacts_expected:
        artifact = str(_artifact_path(repo_root, brief.artifacts_expected[0]))
    return {
        "artifact": artifact,
        "handoff": str(vault / "specs" / f"{task_id}.md"),
        "research_evidence": str(vault / "research" / f"{task_id}-notes.md"),
        "worklog": str(vault / "quality" / f"worklog-{task_id}.md"),
    }


def _file_mtime(path: str | None) -> int | None:
    """파일 mtime(epoch int). 없으면 None."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    return int(p.stat().st_mtime)
