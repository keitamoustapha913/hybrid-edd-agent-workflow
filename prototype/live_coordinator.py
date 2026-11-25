"""Durable live Coordinator with per-MVP Codex, UI/UX and evidence gates.

The simulation tables are deliberately untouched. A live workflow has its own
dispatch ledger, hash-bound approvals and owned canonical artifacts. A
validated Handoff is structural validation, not independent software proof.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from . import agentflow, codex_runtime, git_adapter, live_evidence, live_prototype
from scripts.validate_handoff import validate


BEGIN = "<!-- AGENTFLOW_LIVE:BEGIN -->"
END = "<!-- AGENTFLOW_LIVE:END -->"
MAX_FEEDBACK = 3_000
MAX_RETRIES = 2
EVIDENCE_BARRIER = "AWAITING_EVIDENCE"
MVP_ID = re.compile(r"mvp-[0-9]{2,3}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_workflow(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    stage_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    active_run_id TEXT,
    pending_gate TEXT,
    pending_path TEXT,
    pending_sha TEXT,
    pending_ux_sha TEXT,
    expected_head TEXT NOT NULL,
    expected_branch TEXT NOT NULL,
    expected_common_dir TEXT NOT NULL,
    expected_fingerprint TEXT NOT NULL,
    last_error TEXT,
    failures INTEGER NOT NULL DEFAULT 0,
    feedback_path TEXT,
    feedback_stage TEXT,
    active_mvp TEXT NOT NULL DEFAULT 'mvp-01'
);
CREATE TABLE IF NOT EXISTS live_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_approvals(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gate TEXT NOT NULL,
    path TEXT NOT NULL,
    sha TEXT NOT NULL,
    companion_path TEXT,
    companion_sha TEXT,
    run_id TEXT,
    preview_status TEXT,
    reason TEXT,
    at TEXT NOT NULL,
    revoked_at TEXT,
    mvp_id TEXT NOT NULL DEFAULT 'mvp-01'
);
CREATE TABLE IF NOT EXISTS live_artifacts(
    path TEXT PRIMARY KEY,
    sha TEXT NOT NULL,
    run_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_mvp_completions(
    mvp_id TEXT PRIMARY KEY,
    proof_approval_id INTEGER NOT NULL,
    source_head TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    completed_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _event(conn: sqlite3.Connection, kind: str, detail: dict) -> None:
    conn.execute("INSERT INTO live_events(at,kind,detail) VALUES(?,?,?)",
                 (_now(), kind, json.dumps(detail, sort_keys=True)))


def _state(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM live_workflow WHERE singleton=1"
    ).fetchone()
    if row is None:
        raise ValueError("live Coordinator not initialized; run live-init first")
    return row


def _active_mvp(row: sqlite3.Row) -> str:
    # Existing MVP-01 databases remain readable without an implicit migration.
    return row["active_mvp"] if "active_mvp" in row.keys() else "mvp-01"


def _ensure_mvp_schema(conn: sqlite3.Connection) -> None:
    """Additive migration, called only on an explicitly mutating operation."""
    for table, column, ddl in (
        ("live_workflow", "active_mvp", "TEXT NOT NULL DEFAULT 'mvp-01'"),
        ("live_approvals", "mvp_id", "TEXT NOT NULL DEFAULT 'mvp-01'"),
    ):
        columns = {item[1] for item in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS live_mvp_completions ("
        "mvp_id TEXT PRIMARY KEY, proof_approval_id INTEGER NOT NULL, "
        "source_head TEXT NOT NULL, source_fingerprint TEXT NOT NULL, "
        "completed_at TEXT NOT NULL)"
    )


def _stage_for_target(index: int, mvp: str) -> agentflow.Stage | None:
    """Resolve the canonical stage for this MVP without changing role identity."""
    if index >= len(agentflow.STAGES):
        return None
    base = agentflow.STAGES[index]
    if index == 4 and mvp != "mvp-01":
        return agentflow.Stage(
            "mvp_scope", "MVP_DESIGN", "READY_FOR_REVIEW",
            f"work/{{id}}/mvps/{mvp}/scope-review.md",
            "G5_" + mvp.upper().replace("-", "_"),
        )
    if index >= 5 and mvp != "mvp-01":
        return replace(base, report=base.report.replace("mvp-01", mvp))
    return base


def _stage(row: sqlite3.Row) -> agentflow.Stage | None:
    return _stage_for_target(row["stage_index"], _active_mvp(row))


def _file(root: Path, relative: str) -> Path:
    return codex_runtime._relative_file(root.resolve(), relative)


def _approved_contracts(conn: sqlite3.Connection, root: Path, initiative: str) -> None:
    """An approved contract is immutable even if the current stage is later."""
    for approval in conn.execute(
        "SELECT * FROM live_approvals WHERE revoked_at IS NULL ORDER BY id"
    ):
        approved_mvp = approval["mvp_id"] if "mvp_id" in approval.keys() else "mvp-01"
        path = _file(root, approval["path"])
        if not path.is_file() or _hash(path) != approval["sha"]:
            raise ValueError(f"approved gate {approval['gate']} changed: {approval['path']}")
        if approval["companion_path"]:
            companion = _file(root, approval["companion_path"])
            if not companion.is_file() or _hash(companion) != approval["companion_sha"]:
                raise ValueError(
                    f"approved UI/UX contract changed at {approval['gate']}: {approval['companion_path']}"
                )
        if approval["gate"] == "G2_PROTOTYPE" and approval["preview_status"] == "REVIEWED":
            live_prototype.validate_approved(conn, root, initiative, approval["id"])
        acceptance_path = {
            "G2_PROTOTYPE": f"work/{initiative}/prototype-acceptance.md",
            "G6_UX_INSPECTION": f"work/{initiative}/mvps/{approved_mvp}/ux/inspection-acceptance.md",
            "G7_PROVE": f"work/{initiative}/mvps/{approved_mvp}/edd/proof-acceptance.md",
        }.get(approval["gate"])
        if acceptance_path:
            owned = conn.execute(
                "SELECT sha FROM live_artifacts WHERE path=?", (acceptance_path,)
            ).fetchone()
            saved = _file(root, acceptance_path)
            if owned is None or not saved.is_file() or saved.is_symlink() or _hash(saved) != owned["sha"]:
                raise ValueError(f"approved human acceptance changed: {acceptance_path}")
    state = _state(conn)
    if state["stage_index"] >= 11 and state["status"] != EVIDENCE_BARRIER:
        # The actual host-run test logs and screenshots are a continuously
        # checked prerequisite, not an agent-authored VERIFY claim.
        # A G7-approved working tree may subsequently become a Git commit.
        # A separate, content-checked delivery receipt bridges that new HEAD
        # to the original human approval without falsifying the old evidence.
        delivered = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='live_deliveries'").fetchone():
            delivered = conn.execute(
                "SELECT * FROM live_deliveries WHERE mvp_id=?",
                (_active_mvp(state),),
            ).fetchone()
        bridged = (state["status"] == "COMPLETED" and delivered is not None
                   and delivered["status"] in {"COMMITTED", "PROMOTED", "SNAPSHOTTED", "PUSHED"})
        if bridged:
            completion = conn.execute(
                "SELECT * FROM live_mvp_completions WHERE mvp_id=?",
                (_active_mvp(state),),
            ).fetchone()
            if (completion is None or completion["proof_approval_id"] != delivered["proof_approval_id"]
                    or completion["source_head"] != delivered["approved_head"]
                    or completion["source_fingerprint"] != delivered["approved_fingerprint"]
                    or delivered["source_commit"] != state["expected_head"]):
                raise ValueError("delivered source is not bound to the original G7 proof")
        live_evidence.validate(conn, root, initiative, mvp_id=_active_mvp(state),
                               require_current=not bridged)
    # Earlier MVP receipts must remain intact after source code evolves. Their
    # frozen hashes still matter, but their source is intentionally historical.
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_mvp_completions'"
    ).fetchone():
        for item in conn.execute(
            "SELECT mvp_id FROM live_mvp_completions WHERE mvp_id != ?",
            (_active_mvp(state),),
        ):
            live_evidence.validate(conn, root, initiative, mvp_id=item["mvp_id"],
                                   require_current=False)


def _source(conn: sqlite3.Connection, row: sqlite3.Row, root: Path,
            initiative: str) -> Path:
    context = agentflow.git_context(root, initiative)
    if not context or context["error"]:
        raise ValueError(f"selected Git worktree is unavailable: {context['error'] if context else 'missing'}")
    repo = Path(context["selected_root"])
    info = git_adapter.inspect(repo)
    if (info.head != row["expected_head"]
            or info.branch != row["expected_branch"]
            or str(info.common_dir) != row["expected_common_dir"]):
        raise ValueError("Git HEAD, branch or repository identity differs from the saved live baseline")
    if codex_runtime._git_fingerprint(repo) != row["expected_fingerprint"]:
        raise ValueError("Git worktree changed outside the Coordinator; inspect before continuing")
    _approved_contracts(conn, root, initiative)
    return repo


def _gate_companion(initiative: str, gate: str, mvp_id: str = "mvp-01") -> str | None:
    if gate == "G3_PRODUCT_SPEC":
        return f"work/{initiative}/product-ux-spec.md"
    if gate == "G5_" + mvp_id.upper().replace("-", "_"):
        return f"work/{initiative}/mvps/{mvp_id}/ux/spec.md"
    if gate == "G6_UX_INSPECTION":
        return f"work/{initiative}/mvps/{mvp_id}/edd/verification-evidence.md"
    if gate == "G7_PROVE":
        return f"work/{initiative}/mvps/{mvp_id}/ux/inspection-acceptance.md"
    return None


def _queue_gate(conn: sqlite3.Connection, root: Path, initiative: str,
                stage: agentflow.Stage) -> str:
    assert stage.gate and stage.report
    mvp = _active_mvp(_state(conn))
    path = stage.report.format(id=initiative)
    primary = _file(root, path)
    if not primary.is_file():
        raise ValueError(f"gate {stage.gate} requires a real artifact: {path}")
    digest = _hash(primary)
    review = None
    if stage.gate == "G2_PROTOTYPE":
        review = live_prototype.review_ready(conn, root, initiative, digest)
    if stage.gate in {"G6_UX_INSPECTION", "G7_PROVE"}:
        evidence = live_evidence.validate(conn, root, initiative, mvp_id=mvp)
    else:
        evidence = None
    companion = _gate_companion(initiative, stage.gate, mvp)
    ux_sha = None
    if companion:
        spec = _file(root, companion)
        if not spec.is_file():
            raise ValueError(f"gate {stage.gate} requires UI/UX contract: {companion}")
        ux_sha = _hash(spec)
    conn.execute(
        "UPDATE live_workflow SET status='WAITING_FOR_HUMAN', "
        "pending_gate=?,pending_path=?,pending_sha=?,pending_ux_sha=? WHERE singleton=1",
        (stage.gate, path, digest, ux_sha),
    )
    _event(conn, "GATE_REQUESTED", {
        "gate": stage.gate, "path": path, "sha": digest,
        "companion": companion, "companion_sha": ux_sha,
        "preview_iteration": review["iteration"] if review else None,
        "preview_bundle_sha": review["bundle_sha"] if review else None,
        "verification_bundle_sha": evidence["bundle_sha"] if evidence else None,
    })
    return f"PAUSED {stage.gate}: review {path} (SHA-256 {digest})"


def _update_owned(path: Path, body: str) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to update symlinked ticket: {path}")
    if not path.is_file():
        raise ValueError(f"local ticket is missing: {path}")
    current = path.read_text(encoding="utf-8")
    marker = f"{BEGIN}\n{body.rstrip()}\n{END}"
    left, right = current.find(BEGIN), current.find(END)
    if left >= 0 and right > left:
        next_content = current[:left] + marker + current[right + len(END):]
    elif left < 0 and right < 0:
        next_content = current.rstrip() + "\n\n" + marker + "\n"
    else:
        raise ValueError(f"partial live ticket markers: {path}")
    if current != next_content:
        path.write_text(next_content, encoding="utf-8")


def refresh_tickets(root: Path, initiative: str) -> None:
    """Separate live-owned sections preserve both simulation and human notes."""
    agentflow.refresh_tickets(root, initiative)
    with agentflow.open_db(root, initiative) as conn:
        state = _state(conn)
        mvp = _active_mvp(state)
        approvals = conn.execute(
            "SELECT * FROM live_approvals "
            "WHERE revoked_at IS NULL ORDER BY id"
        ).fetchall()
        artifacts = conn.execute(
            "SELECT path,sha,run_id FROM live_artifacts ORDER BY path"
        ).fetchall()
        events = conn.execute(
            "SELECT at,kind,detail FROM live_events ORDER BY id DESC LIMIT 8"
        ).fetchall()
        completions = (conn.execute(
            "SELECT * FROM live_mvp_completions ORDER BY mvp_id"
        ).fetchall() if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='live_mvp_completions'"
        ).fetchone() else [])
        deliveries = ({row["mvp_id"]: dict(row) for row in conn.execute(
            "SELECT * FROM live_deliveries ORDER BY mvp_id")}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='live_deliveries'").fetchone()
            else {})
        git_reviews = (conn.execute(
            "SELECT * FROM live_git_agent_reviews ORDER BY at,request_id"
        ).fetchall() if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='live_git_agent_reviews'"
        ).fetchone() else [])
        auto_settings = (conn.execute(
            "SELECT auto_continue FROM live_console_preferences WHERE singleton=1"
        ).fetchone() if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='live_console_preferences'"
        ).fetchone() else None)
    project = agentflow.project_context(root, initiative)
    index = state["stage_index"]
    current_stage = _stage(state)
    name = current_stage.name if current_stage else "done"
    parent = [
        "## Live Codex Coordinator — MVP 07",
        "",
        "> Live Codex results, separate from the simulation. Handoff validity is not software verification.",
        "",
        f"- Current stage: **{name}** ({index}/{len(agentflow.STAGES)})",
        f"- Active MVP: **{mvp}**",
        f"- Status: **{state['status']}**",
        f"- Auto-continue after confirmed human actions: **{'ON' if auto_settings is None or auto_settings[0] else 'OFF'}** (never on session reopen)",
        f"- Active/last reserved run: {state['active_run_id'] or 'none'}",
        f"- Human gate: {state['pending_gate'] or 'none'}",
        f"- Source HEAD: {state['expected_head'][:12]} · {state['expected_branch']}",
        f"- Last error: {state['last_error'] or 'none'}",
        f"- Approvals: {', '.join(row['gate'] for row in approvals) or 'none'}",
        f"- Completed MVPs (human G7): {', '.join(item['mvp_id'] for item in completions) or 'none'}",
        f"- Integration branch: {project['integration_branch'] if project else 'not configured (local-only)'}",
        f"- Git delivery: {', '.join(k + ' ' + v['status'] for k, v in deliveries.items()) or 'not started'}",
        "",
        "### Live MVP roll-up",
        "",
        "| MVP | Status | Scope G5 | UX + EDD steps | Git delivery | Local sub-ticket |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    mvp_root = root / "work" / initiative / "mvps"
    planned = (
        {item.name for item in mvp_root.iterdir()
         if item.is_dir() and not item.is_symlink() and MVP_ID.fullmatch(item.name)
         and (item / "spec.md").is_file() and not (item / "spec.md").is_symlink()}
        if mvp_root.is_dir() and not mvp_root.is_symlink() else set()
    )
    completed = {item["mvp_id"] for item in completions}
    for item_id in sorted(planned | completed | {mvp}):
        is_current = item_id == mvp
        approved_scope = any(
            row["gate"] == "G5_" + item_id.upper().replace("-", "_")
            and (row["mvp_id"] if "mvp_id" in row.keys() else "mvp-01") == item_id
            for row in approvals
        )
        status = ("HUMAN G7 COMPLETE" if item_id in completed else
                  f"{state['status']} · {name.upper()}" if is_current else "NOT SCHEDULED")
        done = (8 if item_id in completed else
                min(8, max(0, index - 5)) if is_current and approved_scope else 0)
        link = (f"[ticket.md](mvps/{item_id}/ticket.md)" if
                (mvp_root / item_id / "ticket.md").is_file() else "not yet generated")
        git_progress = deliveries.get(item_id, {}).get("status", "NOT DELIVERED")
        parent.append(f"| {item_id} | {status} | {'APPROVED' if approved_scope else 'PENDING'} | "
                      f"{done}/8 adopted/reviewed | {git_progress} | {link} |")
    if deliveries:
        parent.extend(["", "### Git delivery receipts", "",
                       "> Human-reviewed G7 source is committed locally, then promoted to integration. "
                       "Remote push is separately authorized.", ""])
        for item_id, receipt in deliveries.items():
            prefix = "GIT DELIVERED" if receipt["status"] in {"SNAPSHOTTED", "PUSHED"} else "GIT PENDING"
            parent.append(f"- **{prefix}** {item_id}: **{receipt['status']}** · "
                          f"commit {(receipt['source_commit'] or 'not committed')[:12]} · "
                          f"G7 approval #{receipt['proof_approval_id']}")
    if git_reviews:
        parent.extend(["", "### Git Agent read-only reviews", "",
                       "> Review is not authorization. Git changes require a separate human decision.", ""])
        for review in git_reviews:
            path = f"git-agent-reviews/{review['request_id']}/validated.json"
            parent.append(f"- [{review['mvp_id']} {review['action']}]({path}) · "
                          f"**{review['decision']}** · SHA-256 {review['result_sha'][:12]}")
    parent.extend(["", "### Live artifacts (content-addressed)", ""])
    parent.extend(
        f"- [{Path(row['path']).name}]({Path(row['path']).relative_to('work/' + initiative).as_posix()})"
        f" · SHA-256 {row['sha'][:12]} · {row['run_id']}"
        for row in artifacts
    )
    parent.extend(["", "### Recent live activity", ""])
    with agentflow.open_db(root, initiative) as conn:
        iteration_rows = (
            conn.execute("SELECT * FROM live_preview_iterations ORDER BY iteration").fetchall()
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_preview_iterations'"
            ).fetchone() else []
        )
        evidence = live_evidence.latest(conn, mvp)
    if evidence:
        parent.extend([
            f"", f"### Host-observed {mvp.upper()} verification", "",
            f"- Latest run: {evidence['run_id']} · **{evidence['status']}**",
            f"- Frozen evidence bundle SHA-256: {evidence['bundle_sha']}",
            f"- Source fingerprint: {evidence['source_fingerprint']}",
            "- Next review: independent UIUX INSPECT and human G6, then PROVE and G7.",
        ])
    if iteration_rows:
        parent.extend([
            "", "### Real Codex prototype G2 iterations", "",
            "> Each HTML preview came from the actual Codex specialist output, not the synthetic TUI.",
            "", "| Iteration | Review status | Offline preview | Bundle SHA-256 |",
            "| --- | --- | --- | --- |",
        ])
        for iteration in iteration_rows:
            label = f"iteration-{iteration['iteration']:03d}"
            directory = f"prototype-review-live/{label}"
            parent.append(
                f"| {label} | {iteration['status']} | "
                f"[HTML]({directory}/preview/index.html) | {iteration['bundle_sha'][:12]} |"
            )
        parent.append("")
    for item in events:
        parent.append(f"- {item['at']}: {item['kind']}")
        if item["kind"] == "HUMAN_CHANGES_REQUESTED":
            detail = json.loads(item["detail"])
            feedback = Path(detail["feedback_file"]).relative_to(f"work/{initiative}")
            note = detail["reason"].replace("\n", " ").replace("<", "&lt;")[:200]
            parent.append(f"  - Feedback: [{feedback.name}]({feedback.as_posix()}) · {note}")
    _update_owned(root / "work" / initiative / "project-ticket.md", "\n".join(parent))
    child = root / "work" / initiative / f"mvps/{mvp}/ticket.md"
    if child.is_file():
        mvp_stages = [
            stage for stage in agentflow.STAGES[5:]
        ]
        rows = [
            f"## Live {mvp.upper()} sub-stages",
            "",
            "> Recorded Codex handoffs; VERIFY/UX/PROVE claims require independent evidence.",
            "",
            "| Phase | Live state | Report |",
            "| --- | --- | --- |",
        ]
        for stage in mvp_stages:
            stage_index = agentflow.STAGES.index(stage)
            step_status = (
                "G7 HUMAN-REVIEWED" if stage.name == "prove" and any(
                    approval["gate"] == "G7_PROVE" and
                    (approval["mvp_id"] if "mvp_id" in approval.keys() else "mvp-01") == mvp
                    for approval in approvals
                ) else
                "G6 HUMAN-REVIEWED" if stage.name == "ux_inspect" and any(
                    approval["gate"] == "G6_UX_INSPECTION" and
                    (approval["mvp_id"] if "mvp_id" in approval.keys() else "mvp-01") == mvp
                    for approval in approvals
                ) else
                "HOST CHECKS RECORDED" if stage.name == "verify" and evidence and evidence["status"] == "PASS" else
                "ADOPTED (not independently verified)" if stage_index < index else
                "AWAITING INDEPENDENT EVIDENCE" if stage_index == index and state["status"] == EVIDENCE_BARRIER else
                "BLOCKED" if stage_index == index and state["status"] in {"BLOCKED", "UNCERTAIN"} else
                "ACTIVE" if stage_index == index and state["status"] in {"READY", "RUNNING"} else
                "PENDING"
            )
            report = stage.report.format(id=initiative).replace("mvps/mvp-01/", f"mvps/{mvp}/")
            rows.append(f"| {stage.name.upper()} | {step_status} | {Path(report).name} |")
        receipt = deliveries.get(mvp)
        rows.extend(["", "### Git delivery", "",
                     f"- Status: **{receipt['status'] if receipt else 'NOT DELIVERED'}**",
                     f"- Approved code commit: **{receipt['source_commit'][:12] if receipt and receipt['source_commit'] else 'none'}**",
                     "- Local reports remain authoritative. GitHub Issues are optional."])
        _update_owned(child, "\n".join(rows))


def initialize(root: Path, initiative: str) -> str:
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        sim = agentflow.current(conn)
        if sim["stage_index"] != 0 or sim["status"] != "NOT_STARTED":
            raise ValueError("live and simulation workflows cannot share an active initiative")
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_workflow'"
        ).fetchone() and conn.execute(
            "SELECT 1 FROM live_workflow WHERE singleton=1"
        ).fetchone():
            raise ValueError("live Coordinator already initialized")
        context = agentflow.git_context(root, initiative)
        if not context or context["error"]:
            raise ValueError("attach an existing named-branch Git worktree before live-init")
        repo = Path(context["selected_root"])
        info = git_adapter.inspect(repo)
        if codex_runtime._git_status(repo):
            raise ValueError("live-init requires a clean selected Git worktree")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO live_workflow(singleton,stage_index,status,expected_head,"
            "expected_branch,expected_common_dir,expected_fingerprint) "
            "VALUES(1,0,'READY',?,?,?,?)",
            (info.head, info.branch, str(info.common_dir),
             codex_runtime._git_fingerprint(repo)),
        )
        _event(conn, "LIVE_INITIALIZED", {
            "repo": str(info.root), "branch": info.branch, "head": info.head,
        })
    refresh_tickets(root, initiative)
    return "Live Coordinator initialized. Review the brief, then press [N] in the live interface."


def _checked_result(root: Path, initiative: str, stage: agentflow.Stage,
                    run_id: str, record: dict,
                    mvp_id: str = "mvp-01") -> tuple[Path, list[tuple[str, str]]]:
    """Revalidate quarantined bytes before any canonical artifact write."""
    if (record.get("run_id") != run_id or record.get("stage") != stage.name
            or record.get("role") != stage.role or record.get("state") != "CANDIDATE_VALIDATED"):
        raise ValueError("reserved dispatch ID/stage or validated run state does not match")
    run_dir = _file(root, f"work/{initiative}/codex-runs/{run_id}")
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ValueError("Codex run directory invalid")
    producer = codex_runtime._read_json(run_dir / "producer.json")
    handoff = codex_runtime._read_json(run_dir / "normalized-handoff.json")
    recorded = codex_runtime._read_json(run_dir / "validation.json")
    checked = validate(run_dir / "candidate-root", producer, handoff, [f"work/{initiative}"])
    if not recorded.get("valid") or not checked["valid"] or checked != recorded:
        raise ValueError("quarantined candidate or normalized handoff was altered")
    report = stage.report.format(id=initiative)
    names = {report}
    names.update(
        f"work/{initiative}/{name.replace('mvps/mvp-01/', f'mvps/{mvp_id}/')}"
        for name in codex_runtime.ADDITIONAL_OUTPUTS.get(stage.name, ())
    )
    if (producer["run_id"] != run_id or producer["role"] != stage.role
            or producer["mvp_id"] != (mvp_id if stage.name in {
                "mvp_scope", "ux_design", "requirements", "red", "green",
                "refactor", "verify", "ux_inspect", "prove"
            } else "")
            or producer["report_path"] != report
            or producer["status"] not in codex_runtime.SUCCESS_STATUSES[stage.name]
            or set(checked["checked_files"]) != names):
        raise ValueError("candidate inventory, role, status or report path does not match its stage")
    items = []
    for relative in sorted(names):
        file = _file(run_dir / "candidate-root", relative)
        if not file.is_file() or file.is_symlink():
            raise ValueError(f"candidate artifact missing: {relative}")
        if _hash(file) != checked["checked_files"][relative]:
            raise ValueError(f"candidate digest changed: {relative}")
        if file.stat().st_size > codex_runtime.MAX_ARTIFACT_BYTES:
            raise ValueError("oversized live artifact")
        items.append((relative, checked["checked_files"][relative]))
    return run_dir, items


def _atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temp.open("xb") as output:
        output.write(data)
    os.replace(temp, path)


def _adopt(conn: sqlite3.Connection, root: Path, initiative: str, stage: agentflow.Stage,
           run_id: str, record: dict) -> None:
    row = _state(conn)
    mvp = _active_mvp(row)
    if record.get("mvp_id") != (mvp if stage.name in {
        "mvp_scope", "ux_design", "requirements", "red", "green", "refactor",
        "verify", "ux_inspect", "prove"
    } else ""):
        raise ValueError("run belongs to a different active MVP")
    run_dir, inventory = _checked_result(root, initiative, stage, run_id, record, mvp)
    if record.get("head_before") != row["expected_head"]:
        raise ValueError("run started against a different source revision")
    if record.get("source_digest_before") != row["expected_fingerprint"]:
        raise ValueError("run started against a different source-content fingerprint")
    if record.get("final_head") != row["expected_head"] or not record.get("final_source_digest"):
        raise ValueError("Codex mutated Git revision or final source fingerprint is missing")
    repo = _source_identity_only(row, root, initiative)
    if codex_runtime._git_fingerprint(repo) != record["final_source_digest"]:
        raise ValueError("source repository changed after Handoff; candidate cannot be adopted")
    if stage.name not in codex_runtime.WRITE_SOURCE_STAGES and (
            record["final_source_digest"] != row["expected_fingerprint"]):
        raise ValueError("read-only specialist changed Git worktree")
    if stage.name == "prototype":
        live_prototype.freeze(
            conn, root, initiative, run_id, run_dir / "candidate-root",
            head=row["expected_head"], branch=row["expected_branch"],
            prior_feedback=row["feedback_path"] if row["feedback_stage"] == "prototype" else None,
        )
    manifest_path = run_dir / "adoption-manifest.json"
    expected = [{"path": path, "sha": digest} for path, digest in inventory]
    manifest = {"run_id": run_id, "stage": stage.name, "artifacts": expected}
    manifest_exists = manifest_path.exists()
    if manifest_exists:
        if codex_runtime._read_json(manifest_path) != manifest:
            raise ValueError("existing adoption manifest does not match candidate")
    # Preflight ALL targets before writing any; existing user-owned files never
    # become Coordinator-owned merely because contents happen to match. A
    # previously persisted manifest authorizes exact-byte recovery of files
    # written by a prior interrupted adoption transaction.
    updates = []
    for relative, digest in inventory:
        destination = _file(root, relative)
        prior = conn.execute("SELECT * FROM live_artifacts WHERE path=?", (relative,)).fetchone()
        observed = _hash(destination) if destination.is_file() else None
        if destination.exists() and (destination.is_symlink() or not destination.is_file()):
            raise ValueError(f"unsafe existing artifact: {relative}")
        if observed is not None and prior is None and not (manifest_exists and observed == digest):
            raise ValueError(f"refusing to overwrite pre-existing user/simulation artifact: {relative}")
        if prior and observed not in {prior["sha"], digest}:
            raise ValueError(f"live-owned artifact edited outside Coordinator: {relative}")
        if prior and observed is None:
            raise ValueError(f"live-owned artifact unexpectedly removed: {relative}")
        source = _file(run_dir / "candidate-root", relative)
        updates.append((relative, source, destination, prior, observed, digest))
    if not manifest_exists:
        with manifest_path.open("x", encoding="utf-8") as output:
            json.dump(manifest, output, indent=2)
    for relative, source, destination, prior, observed, digest in updates:
        if observed != digest:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if prior:
                archived = _file(
                    root, f"work/{initiative}/live-revisions/{run_id}/"
                    + relative.removeprefix(f"work/{initiative}/")
                )
                archived.parent.mkdir(parents=True, exist_ok=True)
                if archived.exists():
                    if _hash(archived) != prior["sha"]:
                        raise ValueError(f"revision backup was modified: {relative}")
                else:
                    with archived.open("xb") as backup:
                        backup.write(destination.read_bytes())
            _atomic_write(destination, source.read_bytes())
        conn.execute(
            "INSERT INTO live_artifacts(path,sha,run_id) VALUES(?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET sha=excluded.sha,run_id=excluded.run_id",
            (relative, digest, run_id),
        )
    conn.execute(
        "UPDATE live_workflow SET stage_index=stage_index+?,status='READY',"
        "active_run_id=NULL,expected_fingerprint=?,last_error=NULL,failures=0 "
        "WHERE singleton=1",
        (0 if stage.name == "mvp_scope" else 1, record["final_source_digest"]),
    )
    _event(conn, "STAGE_ADOPTED", {"stage": stage.name, "run_id": run_id,
                                  "artifacts": [p for p, _ in inventory],
                                  "source_changed": record.get("source_changed")})
    # A verified Handoff does not mean that VERIFY or PROVE evidence exists.
    if stage.name == "verify":
        conn.execute(
            "UPDATE live_workflow SET status=?,last_error=? WHERE singleton=1",
            (EVIDENCE_BARRIER, "VERIFY report is a model claim. Open the guided live "
             "interface and choose [E] to run your reviewed host checks and capture UI evidence."),
        )
        _event(conn, "INDEPENDENT_EVIDENCE_REQUIRED", {"after": "verify"})
    elif stage.name == "ux_inspect":
        _queue_gate(conn, root, initiative, replace(stage, gate="G6_UX_INSPECTION"))
    elif stage.name == "prove":
        _queue_gate(conn, root, initiative, replace(stage, gate="G7_PROVE"))
    elif stage.gate:
        # Queue the human gate in the same transaction as artifact adoption.
        # Reconciliation after a crash therefore cannot skip G1/G2/G3/G4.
        _queue_gate(conn, root, initiative, stage)


def _source_identity_only(row: sqlite3.Row, root: Path, initiative: str) -> Path:
    context = agentflow.git_context(root, initiative)
    if not context or context["error"]:
        raise ValueError("Git selection was lost or changed")
    repo = Path(context["selected_root"])
    info = git_adapter.inspect(repo)
    if (info.head != row["expected_head"] or info.branch != row["expected_branch"]
            or str(info.common_dir) != row["expected_common_dir"]):
        raise ValueError("Git identity changed while specialist was running")
    _approved_contracts_from_root(root, initiative)
    return repo


def _approved_contracts_from_root(root: Path, initiative: str) -> None:
    with agentflow.open_db(root, initiative) as view:
        _approved_contracts(view, root, initiative)


def _finalize(root: Path, initiative: str, run_id: str) -> str:
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_mvp_schema(conn)
        row = _state(conn)
        mvp = _active_mvp(row)
        if row["status"] != "RUNNING" or row["active_run_id"] != run_id:
            raise ValueError("run ID no longer owns this live dispatch")
        stage = _stage(row)
        if stage is None:
            raise ValueError("no active specialist for completed MVP")
        record_path = _file(root, f"work/{initiative}/codex-runs/{run_id}/run.json")
        if not record_path.is_file():
            raise ValueError("reserved run has no terminal run.json; outcome is uncertain")
        record = codex_runtime._read_json(record_path)
        if record.get("state") == "RUNNING":
            raise ValueError("Codex run remains in RUNNING state; do not replay it")
        if record.get("state") == "BLOCKED":
            conn.execute(
                "UPDATE live_workflow SET status='BLOCKED',active_run_id=NULL,"
                "last_error=?,failures=failures+1 WHERE singleton=1",
                (record.get("error", "Codex run blocked"),),
            )
            _event(conn, "DISPATCH_BLOCKED", {"stage": stage.name, "run_id": run_id,
                                             "error": record.get("error")})
            return f"BLOCKED {stage.name}: {record.get('error', 'unknown Codex error')}"
        try:
            _adopt(conn, root, initiative, stage, run_id, record)
        except live_prototype.PreviewRejected as error:
            # Preview preflight runs before canonical file adoption; this is a
            # terminal specialist correction, not an uncertain partial write.
            conn.execute(
                "UPDATE live_workflow SET status='BLOCKED',active_run_id=NULL,"
                "last_error=?,failures=failures+1 WHERE singleton=1",
                (f"unsafe/invalid prototype preview: {error}",),
            )
            _event(conn, "PROTOTYPE_PREVIEW_REJECTED", {"run_id": run_id, "error": str(error)})
            return f"BLOCKED prototype: unsafe preview — {error}"
        record["adopted_by_coordinator"] = True
        record["adopted_at"] = _now()
        # DB is authoritative. If interrupted here, reconciliation sees next
        # stage and will not re-run a completed dispatch.
        _atomic_write(record_path, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
        return f"ADOPTED {stage.name} (Codex + Handoff) · {run_id}"


def _block_uncertain(root: Path, initiative: str, run_id: str, reason: str) -> None:
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _state(conn)
        if row["status"] == "RUNNING" and row["active_run_id"] == run_id:
            conn.execute(
                "UPDATE live_workflow SET status='UNCERTAIN',last_error=? WHERE singleton=1",
                (reason,),
            )
            _event(conn, "DISPATCH_UNCERTAIN", {"run_id": run_id, "reason": reason})


def _reserve(root: Path, initiative: str, *, codex_bin: str,
             model: str | None) -> tuple[agentflow.Stage | None, str | None,
                                        codex_runtime.StagePlan | None, str | None]:
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _state(conn)
        if row["status"] != "READY":
            raise ValueError(f"live workflow is {row['status']}; inspect live-status first")
        _source(conn, row, root, initiative)
        stage = _stage(row)
        if stage is None:
            raise ValueError("all stages completed; a new MVP needs explicit scheduling")
        if stage.name == "mvp_scope" and row["feedback_stage"] != "mvp_scope":
            # An upstream MVP DESIGN session may have already produced both
            # scope documents. G5 still requires a human decision on their
            # exact bytes, but no redundant Codex scope call is necessary.
            mvp = _active_mvp(row)
            feature = _file(root, f"work/{initiative}/mvps/{mvp}/spec.md")
            ux = _file(root, f"work/{initiative}/mvps/{mvp}/ux/spec.md")
            if feature.is_file() != ux.is_file():
                raise ValueError("next MVP feature and UX scope must both exist or both be generated")
            if feature.is_file():
                if feature.is_symlink() or ux.is_symlink():
                    raise ValueError("MVP scope files may not be symlinks")
                if max(feature.stat().st_size, ux.stat().st_size) > codex_runtime.MAX_ARTIFACT_BYTES:
                    raise ValueError("pre-existing MVP scope is oversized; review and reduce it")
                if (feature.read_bytes().startswith(agentflow.SIMULATED_MARKER.encode())
                        or ux.read_bytes().startswith(agentflow.SIMULATED_MARKER.encode())):
                    raise ValueError("simulated MVP scope is not eligible for a real live G5")
                gate_stage = replace(stage, role=None,
                                     report=f"work/{{id}}/mvps/{mvp}/spec.md")
                return None, None, None, _queue_gate(conn, root, initiative, gate_stage)
        if stage.role is None:
            return None, None, None, _queue_gate(conn, root, initiative, stage)
        plan = codex_runtime.build_plan(
            root, initiative, stage.name, codex_bin=codex_bin, model=model,
            allow_dirty=True, mvp_id=_active_mvp(row),
        )
        if stage.name == row["feedback_stage"] and row["feedback_path"]:
            feedback_path = _file(root, row["feedback_path"])
            if not feedback_path.is_file() or feedback_path.stat().st_size > 12_000:
                raise ValueError("prior Prototype feedback is missing or oversized")
            data = feedback_path.read_bytes()
            plan = replace(plan, inputs=plan.inputs + ({
                "path": row["feedback_path"], "sha256": hashlib.sha256(data).hexdigest(),
                "content": data.decode("utf-8"),
            },))
        dispatch_id = "codex-" + uuid.uuid4().hex
        conn.execute(
            "UPDATE live_workflow SET status='RUNNING',active_run_id=?,last_error=NULL WHERE singleton=1",
            (dispatch_id,),
        )
        _event(conn, "DISPATCH_RESERVED", {
            "stage": stage.name, "run_id": dispatch_id, "sandbox": plan.sandbox,
            "head": row["expected_head"], "baseline": row["expected_fingerprint"],
        })
        return stage, dispatch_id, plan, row["expected_fingerprint"]


def run(root: Path, initiative: str, *, codex_bin: str = "codex",
        model: str | None = None, timeout: int = 900) -> list[str]:
    """One approved invocation auto-dispatches until a human/evidence gate."""
    if not 1 <= timeout <= 3600:
        raise ValueError("timeout must be 1–3600 seconds per Codex session")
    root = root.resolve(strict=True)
    messages = []
    while True:
        with agentflow.open_db(root, initiative) as conn:
            row = _state(conn)
            if row["status"] != "READY":
                messages.append(f"PAUSED {row['status']}: {row['pending_gate'] or row['last_error'] or 'inspect live-status'}")
                break
            if row["stage_index"] >= len(agentflow.STAGES):
                messages.append("All stages reached. Independent release approval remains required.")
                break
        stage, run_id, plan, baseline = _reserve(
            root, initiative, codex_bin=codex_bin, model=model
        )
        if stage is None:
            messages.append(str(baseline))
            refresh_tickets(root, initiative)
            break
        messages.append(f"RUN {stage.name} · {run_id}")
        try:
            codex_runtime.run_stage(
                plan, timeout=timeout, run_id=run_id,
                allow_dirty=True, expected_fingerprint=baseline,
            )
            messages.append(_finalize(root, initiative, run_id))
        except (ValueError, OSError, sqlite3.Error) as error:
            _block_uncertain(root, initiative, run_id, str(error))
            messages.append(f"UNCERTAIN {stage.name}: {error}. Run live-reconcile after inspecting logs.")
            break
        finally:
            refresh_tickets(root, initiative)
        with agentflow.open_db(root, initiative) as conn:
            state = _state(conn)
            if state["status"] != "READY":
                messages.append(
                    f"PAUSED {state['status']}: {state['pending_gate'] or state['last_error'] or 'review required'}"
                )
                break
    return messages


def _owned_write(conn: sqlite3.Connection, root: Path, relative: str,
                 content: str, run_id: str) -> str:
    target = _file(root, relative)
    owned = conn.execute("SELECT sha FROM live_artifacts WHERE path=?", (relative,)).fetchone()
    if target.is_symlink() or (target.exists() and owned is None):
        raise ValueError(f"refusing to overwrite non-live acceptance file: {relative}")
    if owned and (not target.is_file() or _hash(target) != owned["sha"]):
        raise ValueError(f"previous acceptance was edited: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, content.encode("utf-8"))
    digest = _hash(target)
    conn.execute(
        "INSERT INTO live_artifacts(path,sha,run_id) VALUES(?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET sha=excluded.sha,run_id=excluded.run_id",
        (relative, digest, run_id),
    )
    return digest


def approve(root: Path, initiative: str, gate: str, digest: str,
            *, waive_preview: bool = False, reason: str = "",
            bundle_sha: str | None = None, review_notes: str = "") -> str:
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_mvp_schema(conn)
        row = _state(conn)
        if row["status"] != "WAITING_FOR_HUMAN" or row["pending_gate"] != gate:
            raise ValueError("no matching live human gate is pending")
        mvp = _active_mvp(row)
        _source(conn, row, root, initiative)
        if row["pending_sha"] != digest or _hash(_file(root, row["pending_path"])) != digest:
            raise ValueError("stale or mismatched artifact SHA-256: approval rejected")
        companion = _gate_companion(initiative, gate, mvp)
        if companion and _hash(_file(root, companion)) != row["pending_ux_sha"]:
            raise ValueError("companion UI/UX scope hash changed: approval rejected")
        preview_status = None
        preview_iteration = None
        preview_run_id = row["active_run_id"]
        if gate == "G2_PROTOTYPE":
            existing = live_prototype.latest(conn)
            if existing is not None:
                if waive_preview:
                    raise ValueError("a real G2 preview exists; inspect it or request changes, not a waiver")
                if not bundle_sha:
                    raise ValueError("G2 requires --bundle-sha from live-status after reviewing live-preview")
                preview_iteration, acceptance = live_prototype.approve_preflight(
                    conn, root, initiative, digest, bundle_sha, review_notes,
                    row["expected_head"],
                )
                preview_status = "REVIEWED"
                preview_run_id = existing["run_id"]
                reason = review_notes.strip()
            else:
                # Backward compatibility for an already-running MVP02 workflow
                # without a generated preview. New MVP03 runs always freeze
                # a real preview before G2 can be requested.
                if not waive_preview or not reason.strip():
                    raise ValueError("no real G2 preview; legacy waiver requires --waive-preview --reason")
                if len(reason) > MAX_FEEDBACK:
                    raise ValueError("G2 waiver reason exceeds 3000 characters")
                preview_status = "WAIVED"
                acceptance = (
                    "# Prototype findings acceptance (legacy preview waiver)\n\n"
                    "> No live preview or interaction was shown or tested.\n\n"
                    f"- Accepted report: {row['pending_path']}\n"
                    f"- Report SHA-256: {digest}\n"
                    f"- Git HEAD: {row['expected_head']}\n"
                    f"- Decision: APPROVED_WITH_PREVIEW_WAIVER\n"
                    f"- Reason: {reason.strip()}\n"
                    f"- Recorded at: {_now()}\n\n"
                    "Specification may use these findings, not infer working prototype code.\n"
                )
            _owned_write(
                conn, root, f"work/{initiative}/prototype-acceptance.md",
                acceptance, preview_run_id or "HUMAN_G2",
            )
        elif gate in {"G6_UX_INSPECTION", "G7_PROVE"}:
            if waive_preview or reason.strip():
                raise ValueError("G6/G7 cannot use a preview waiver or waiver reason")
            evidence = live_evidence.validate(conn, root, initiative, mvp_id=mvp)
            if not bundle_sha or bundle_sha != evidence["bundle_sha"]:
                raise ValueError(f"{gate} requires the exact --bundle-sha from live-status")
            if not review_notes.strip() or len(review_notes) > MAX_FEEDBACK:
                raise ValueError(f"{gate} requires actual human observations (1–3000 characters)")
            # Humans review independent UIUX observations AND the actual
            # host-executed checks; model output alone cannot close either gate.
            if gate == "G6_UX_INSPECTION":
                relative = f"work/{initiative}/mvps/{mvp}/ux/inspection-acceptance.md"
                heading = "# Human UI/UX inspection acceptance — G6"
            else:
                relative = f"work/{initiative}/mvps/{mvp}/edd/proof-acceptance.md"
                heading = "# Human evidence/proof review — G7"
            acceptance = (
                heading + "\n\n"
                f"- Reviewed report: {row['pending_path']}\n"
                f"- Reviewed report SHA-256: {digest}\n"
                f"- Host verification receipt SHA-256: {evidence['receipt_sha']}\n"
                f"- Host evidence bundle SHA-256: {bundle_sha}\n"
                f"- Git HEAD: {row['expected_head']}\n"
                f"- Source fingerprint: {row['expected_fingerprint']}\n"
                f"- Recorded: {_now()}\n\n"
                f"## Human observations\n\n{review_notes.strip()}\n\n"
                "This is a local human decision on recorded evidence, not an authenticated "
                "signature or an automatic software-quality certification.\n"
            )
            _owned_write(conn, root, relative, acceptance, evidence["run_id"])
            preview_status = "EVIDENCE_REVIEWED"
            preview_run_id = evidence["run_id"]
            reason = review_notes.strip()
        elif waive_preview or bundle_sha or review_notes:
            raise ValueError("review bundle, notes and waiver options apply only to G2/G6/G7")
        decision = conn.execute(
            "INSERT INTO live_approvals(gate,path,sha,companion_path,companion_sha,"
            "run_id,preview_status,reason,at,mvp_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (gate, row["pending_path"], digest, companion, row["pending_ux_sha"],
             preview_run_id, preview_status,
             reason.strip() if gate in {"G2_PROTOTYPE", "G6_UX_INSPECTION", "G7_PROVE"} else None,
             _now(), mvp),
        )
        if preview_iteration is not None:
            live_prototype.record_approval(
                conn, preview_iteration, decision.lastrowid, bundle_sha, review_notes
            )
        _event(conn, "HUMAN_APPROVED", {
            "gate": gate, "mvp_id": mvp, "sha": digest, "companion_sha": row["pending_ux_sha"],
            "preview_status": preview_status or "NOT_APPLICABLE",
            "preview_iteration": f"iteration-{preview_iteration:03d}" if preview_iteration else None,
            "preview_bundle_sha": bundle_sha if preview_iteration else None,
            "evidence_bundle_sha": bundle_sha if gate in {"G6_UX_INSPECTION", "G7_PROVE"} else None,
        })
        next_index = row["stage_index"] + (1 if gate == "G5_" + mvp.upper().replace("-", "_") else 0)
        next_state = "COMPLETED" if gate == "G7_PROVE" else "READY"
        if gate == "G7_PROVE":
            conn.execute(
                "INSERT INTO live_mvp_completions(mvp_id,proof_approval_id,source_head,"
                "source_fingerprint,completed_at) VALUES(?,?,?,?,?)",
                (mvp, decision.lastrowid, row["expected_head"], row["expected_fingerprint"], _now()),
            )
        conn.execute(
            "UPDATE live_workflow SET status=?,stage_index=?,pending_gate=NULL,"
            "pending_path=NULL,pending_sha=NULL,pending_ux_sha=NULL WHERE singleton=1",
            (next_state, next_index),
        )
    refresh_tickets(root, initiative)
    if gate == "G7_PROVE":
        return (f"COMPLETED {mvp.upper()} G7_PROVE · independent evidence and human decision "
                "bound. Next roadmap MVP requires explicit scheduling; no Git push or deployment.")
    return f"APPROVED {gate} · exact SHA-256 bound. Next stage ready (no Codex started)."


def _next_id(current: str) -> str | None:
    number = int(current.removeprefix("mvp-")) + 1
    return f"mvp-{number:02d}" if number <= 999 else None


def _roadmap_lists(root: Path, initiative: str, mvp_id: str) -> bool:
    path = _file(root, f"work/{initiative}/mvp-roadmap.md")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 350_000:
        return False
    # Accept customary headings "MVP-02" and "MVP 2", but not a bare index
    # or an arbitrary new feature outside the G4-approved roadmap.
    ordinal = int(mvp_id[4:])
    return bool(re.search(rf"(?i)\bmvp[\s_-]*0*{ordinal}\b", path.read_text(encoding="utf-8")))


def available_next(root: Path, initiative: str) -> str | None:
    """Read-only availability hint; scheduling rechecks every invariant."""
    with agentflow.open_db(root, initiative) as conn:
        state = _state(conn)
        if state["status"] != "COMPLETED":
            return None
        mvp = _active_mvp(state)
        next_id = _next_id(mvp)
        if next_id is None or not _roadmap_lists(root, initiative, next_id):
            return None
        # When integration is enabled, do not advertise an unsafe next-MVP
        # transition until the last approved code and reports have been
        # published locally to the shared integration branch.
        if agentflow.project_context(root, initiative):
            from . import live_delivery
            receipt = live_delivery.delivery(root, initiative, mvp)
            if not receipt or receipt["status"] not in live_delivery.PUBLISHED:
                return None
        return next_id


def schedule_next(root: Path, initiative: str, *, mvp_id: str | None = None) -> str:
    """Move to the next sequential, G4-listed MVP only after human G7."""
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_mvp_schema(conn)
        state = _state(conn)
        prior = _active_mvp(state)
        expected = _next_id(prior)
        if state["status"] != "COMPLETED" or expected is None:
            raise ValueError("finish the current MVP through human G7 before scheduling another")
        next_id = mvp_id or expected
        if not MVP_ID.fullmatch(next_id) or next_id != expected:
            raise ValueError(f"only the next sequential MVP ({expected}) can be scheduled")
        _source(conn, state, root, initiative)
        if not _roadmap_lists(root, initiative, next_id):
            raise ValueError(f"{next_id} is not named in the approved G4 roadmap")
        proof = conn.execute(
            "SELECT id,at FROM live_approvals WHERE gate='G7_PROVE' AND mvp_id=? "
            "AND revoked_at IS NULL ORDER BY id DESC LIMIT 1", (prior,),
        ).fetchone()
        if proof is None:
            raise ValueError("previous MVP lacks a valid human G7 proof approval")
        if conn.execute(
            "SELECT 1 FROM live_mvp_completions WHERE mvp_id=?", (next_id,),
        ).fetchone():
            raise ValueError("next MVP is already recorded as completed; cannot replay it")
        from . import live_delivery
        next_source = live_delivery.next_worktree(root, initiative, next_id)
        conn.execute(
            "INSERT OR IGNORE INTO live_mvp_completions("
            "mvp_id,proof_approval_id,source_head,source_fingerprint,completed_at) "
            "VALUES(?,?,?,?,?)",
            (prior, proof["id"], state["expected_head"], state["expected_fingerprint"], proof["at"]),
        )
        conn.execute(
            "UPDATE live_workflow SET active_mvp=?,stage_index=4,status='READY',"
            "active_run_id=NULL,pending_gate=NULL,pending_path=NULL,pending_sha=NULL,"
            "pending_ux_sha=NULL,last_error=NULL,failures=0,"
            "feedback_path=NULL,feedback_stage=NULL WHERE singleton=1",
            (next_id,),
        )
        if next_source is not None:
            # Both the Git binding and live baseline move together. An old
            # G7 receipt remains bound to the finished, unmodified worktree.
            agentflow._select_git_root(conn, next_source)
            conn.execute(
                "UPDATE live_workflow SET expected_head=?,expected_branch=?,"
                "expected_common_dir=?,expected_fingerprint=? WHERE singleton=1",
                (next_source.head, next_source.branch, str(next_source.common_dir),
                 codex_runtime._git_fingerprint(next_source.root)),
            )
        _event(conn, "NEXT_MVP_SCHEDULED", {
            "prior": prior, "mvp_id": next_id, "approved_roadmap": f"work/{initiative}/mvp-roadmap.md",
            "worktree": str(next_source.root) if next_source else "unchanged (local-only)",
        })
    refresh_tickets(root, initiative)
    return (f"SCHEDULED {next_id.upper()} · fresh MVP DESIGN scope + human G5 before UX/EDD. "
            "No Codex run, Git push or deployment was started.")


def changes(root: Path, initiative: str, reason: str,
            *, target_stage: str | None = None) -> str:
    if not reason.strip() or len(reason) > MAX_FEEDBACK:
        raise ValueError("provide 1–3000 characters of actionable change feedback")
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_mvp_schema(conn)
        row = _state(conn)
        mvp = _active_mvp(row)
        blocked_ux = (
            row["status"] == "BLOCKED" and row["stage_index"] == 11
            and target_stage in {"green", "evidence"}
        )
        if row["status"] != "WAITING_FOR_HUMAN" and not blocked_ux:
            raise ValueError("no live human gate is waiting for feedback; blocked UX may target green")
        _source(conn, row, root, initiative)
        gate, index = row["pending_gate"] or "BLOCKED_UX", row["stage_index"]
        target = (3 if gate == "G5_MVP_01" else 4 if gate == (
            "G5_" + mvp.upper().replace("-", "_")
        ) else index - 1)
        if target_stage:
            if gate not in {"G6_UX_INSPECTION", "G7_PROVE", "BLOCKED_UX"}:
                raise ValueError("--target only applies to G6, G7 or a BLOCKED UIUX Inspector")
            permitted = {"green", "evidence", "ux_inspect"} if gate == "G6_UX_INSPECTION" else (
                {"green", "evidence", "ux_inspect", "prove"} if gate == "G7_PROVE" else {"green", "evidence"}
            )
            if target_stage not in permitted:
                raise ValueError(f"--target {target_stage} is not a safe return stage for {gate}")
            target = (11 if target_stage == "evidence" else
                      next(i for i, stage in enumerate(agentflow.STAGES) if stage.name == target_stage))
        if target < 0:
            raise ValueError("invalid stage to rerun")
        feedback_path = f"work/{initiative}/live-feedback/{uuid.uuid4().hex}.md"
        feedback = _file(root, feedback_path)
        feedback.parent.mkdir(parents=True, exist_ok=True)
        with feedback.open("x", encoding="utf-8") as output:
            output.write(
                f"# Human change request — {gate}\n\n"
                f"Prior report: {row['pending_path'] or 'failed inspector run'}\n"
                f"Prior report SHA-256: {row['pending_sha'] or 'not adopted'}\n\n"
                f"## Required changes\n\n{reason.strip()}\n"
            )
        preview_iteration = None
        if gate == "G2_PROTOTYPE" and live_prototype.latest(conn) is not None:
            review = live_prototype.record_feedback(
                conn, root, initiative, row["pending_sha"], feedback_path
            )
            preview_iteration = review["iteration"]
        # Returning to an earlier stage invalidates this gate and all
        # downstream approvals, without deleting audit history.
        invalidated = [item.gate for item in agentflow.STAGES[target:] if item.gate]
        if target <= 4:
            invalidated.append("G5_" + mvp.upper().replace("-", "_"))
        if target <= 11:
            invalidated.append("G6_UX_INSPECTION")
        if target <= 12:
            invalidated.append("G7_PROVE")
        for invalid in invalidated:
            conn.execute(
                "UPDATE live_approvals SET revoked_at=? WHERE gate=? AND mvp_id=? AND revoked_at IS NULL",
                (_now(), invalid, mvp),
            )
        if target <= 10 or target_stage == "evidence":
            live_evidence.invalidate(conn)
        next_status = EVIDENCE_BARRIER if target_stage == "evidence" else "READY"
        conn.execute(
            "UPDATE live_workflow SET status=?,stage_index=?,"
            "pending_gate=NULL,pending_path=NULL,pending_sha=NULL,pending_ux_sha=NULL,"
            "feedback_path=?,feedback_stage=?,failures=0 WHERE singleton=1",
            (next_status, target, feedback_path, _stage_for_target(target, mvp).name),
        )
        _event(conn, "HUMAN_CHANGES_REQUESTED", {
            "gate": gate, "mvp_id": mvp, "target": _stage_for_target(target, mvp).name,
            "reason": reason.strip(), "feedback_file": feedback_path,
            "rejected_preview_iteration": preview_iteration,
        })
    refresh_tickets(root, initiative)
    return (f"Changes recorded in {feedback_path}; ready to rerun "
            f"{_stage_for_target(target, mvp).name} with authorized continuation "
            "(automatically in live mode when enabled, otherwise [N]).")


def retry(root: Path, initiative: str) -> str:
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _state(conn)
        if row["status"] != "BLOCKED":
            raise ValueError("only a terminal BLOCKED run can be retried; UNCERTAIN requires inspection")
        if row["failures"] > MAX_RETRIES:
            raise ValueError("automatic retry budget exhausted; manual review required")
        _source(conn, row, root, initiative)
        conn.execute("UPDATE live_workflow SET status='READY',last_error=NULL WHERE singleton=1")
        _event(conn, "HUMAN_RETRY", {"stage": agentflow.STAGES[row["stage_index"]].name,
                                     "failures": row["failures"]})
    refresh_tickets(root, initiative)
    return ("Retry authorized after review; live Auto-continue resumes when enabled, "
            "otherwise press [N].")


def reconcile(root: Path, initiative: str) -> str:
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        row = _state(conn)
        if row["status"] not in {"RUNNING", "UNCERTAIN"} or not row["active_run_id"]:
            return f"No unresolved live dispatch; state is {row['status']}."
        run_id = row["active_run_id"]
        record_path = _file(root, f"work/{initiative}/codex-runs/{run_id}/run.json")
        if not record_path.is_file():
            _block_uncertain(root, initiative, run_id, "reserved dispatch has no run.json")
            return "UNCERTAIN: no completed run.json; inspect process and logs. No automatic replay."
        record = codex_runtime._read_json(record_path)
        if record.get("state") not in {"BLOCKED", "CANDIDATE_VALIDATED"}:
            _block_uncertain(root, initiative, run_id, "run.json not terminal")
            return "UNCERTAIN: Codex run not terminal. No automatic replay."
        if row["status"] == "UNCERTAIN":
            # Reconciliation is safe only for a *terminal* record, not a
            # potentially-running process with its results still changing.
            with agentflow.open_db(root, initiative) as write:
                write.execute("BEGIN IMMEDIATE")
                write.execute(
                    "UPDATE live_workflow SET status='RUNNING' "
                    "WHERE singleton=1 AND status='UNCERTAIN' AND active_run_id=?",
                    (run_id,),
                )
    try:
        result = _finalize(root, initiative, run_id)
    except (ValueError, OSError, sqlite3.Error) as error:
        _block_uncertain(root, initiative, run_id, str(error))
        result = f"UNCERTAIN: adoption rejected: {error}. Manual review required."
    refresh_tickets(root, initiative)
    return result


def status(root: Path, initiative: str) -> str:
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        row = _state(conn)
        mvp = _active_mvp(row)
        index = row["stage_index"]
        stage = _stage(row)
        approvals = conn.execute(
            "SELECT gate,at,preview_status FROM live_approvals WHERE revoked_at IS NULL ORDER BY id"
        ).fetchall()
        activity = conn.execute(
            "SELECT at,kind,detail FROM live_events ORDER BY id DESC LIMIT 6"
        ).fetchall()
        has_frozen_preview = (
            live_prototype.latest(conn) is not None
            if row["pending_gate"] == "G2_PROTOTYPE" else False
        )
        evidence_row = live_evidence.latest(conn, mvp)
        evidence_review = None
        evidence_warning = None
        if evidence_row and evidence_row["status"] == "PASS":
            try:
                evidence_review = live_evidence.validate(conn, root, initiative)
            except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
                evidence_warning = str(error)
    waiting_human = row["status"] == "WAITING_FOR_HUMAN"
    active_owner = (
        "HOST EVIDENCE REVIEW" if row["status"] == EVIDENCE_BARRIER else
        "HUMAN REVIEW" if waiting_human else
        stage.role if stage and stage.role else "HUMAN GATE"
    )
    lines = [
        f"LIVE CODEX COORDINATOR · {initiative}",
        f"State: {row['status']}",
        f"Active MVP: {mvp}",
        f"Next stage: {stage.name.upper() if stage else 'DONE'} ({index}/{len(agentflow.STAGES)})",
        f"Active owner: {active_owner}",
        f"Last/reserved run: {row['active_run_id'] or 'none'}",
        f"Git branch/HEAD: {row['expected_branch']} @ {row['expected_head'][:12]}",
        "Local parent: work/" + initiative + "/project-ticket.md",
        f"{mvp.upper()} stages: UX DESIGN → REQUIREMENTS → RED → GREEN → REFACTOR → "
        "VERIFY → UX INSPECT → PROVE",
        "Approved gates: " + (", ".join(item["gate"] for item in approvals) or "none"),
    ]
    preceding = _stage_for_target(index - 1, mvp) if index else None
    if preceding and preceding.report:
        last_path = preceding.report.format(id=initiative)
        if _file(root, last_path).is_file():
            lines.append(f"Latest report: {last_path} · SHA-256 {_hash(_file(root, last_path))[:12]}")
    lines.extend([f"{mvp.upper()} sub-stages:"])
    for i, sub_stage in enumerate(agentflow.STAGES[5:], start=5):
        progress = ("ADOPTED (not proven)" if i < index else
                    "AWAITING EVIDENCE" if i == index and row["status"] == EVIDENCE_BARRIER else
                    "ACTIVE" if i == index and row["status"] in {"READY", "RUNNING"} else "PENDING")
        lines.append(f"  {sub_stage.name.upper():12} {progress}")
    if evidence_review:
        lines.extend([
            f"{mvp.upper()} host evidence: {evidence_review['run_id']} · PASS",
            f"Independent test+UX checks: {evidence_review['checks']}",
            f"Captured UX screenshots: {evidence_review['screenshots']}",
            f"Frozen evidence bundle SHA-256: {evidence_review['bundle_sha']}",
            f"Evidence receipt: work/{initiative}/mvps/{mvp}/edd/verification-evidence.md",
        ])
    if evidence_warning:
        lines.append(f"{mvp.upper()} EVIDENCE INVALID: {evidence_warning}")
    if row["pending_gate"]:
        lines += [
            f"GATE: {row['pending_gate']} · {row['pending_path']}",
            f"Review SHA-256: {row['pending_sha']}",
        ]
        if row["pending_ux_sha"]:
            lines.append(f"UI/UX contract SHA-256: {row['pending_ux_sha']}")
        if row["pending_gate"] == "G2_PROTOTYPE":
            try:
                review = live_prototype.describe(root, initiative)
            except ValueError as error:
                lines.append(f"G2 PREVIEW WARNING: {error}")
                if has_frozen_preview:
                    lines.append(
                        "Frozen G2 preview is invalid: do not approve or waive; "
                        "inspect artifacts and reconcile before continuing."
                    )
                    suffix = None
                else:
                    suffix = " --waive-preview --reason 'documented legacy exception'"
            else:
                lines.extend([
                    f"Real G2 preview: {review['iteration']} · {review['status']}",
                    f"Frozen HTML: {review['preview_path']}",
                    f"Preview SHA-256: {review['preview_sha']}",
                    f"Review bundle SHA-256: {review['bundle_sha']}",
                    f"Open preview: start 'uv run workflow.py live {initiative}', then press [O]"
                    " (advanced CLI: live-preview)",
                    "Review and interact in your browser; manual observation is required.",
                ])
                suffix = (
                    f" --bundle-sha {review['bundle_sha']}"
                    " --review-notes 'what I actually tried and observed'"
                )
        elif row["pending_gate"] in {"G6_UX_INSPECTION", "G7_PROVE"}:
            if not evidence_review:
                lines.append("G6/G7 cannot be approved until frozen host evidence passes integrity checks.")
                suffix = None
            else:
                suffix = (
                    f" --bundle-sha {evidence_review['bundle_sha']}"
                    " --review-notes 'screenshots, checks, behavior and limitations reviewed'"
                )
        else:
            suffix = ""
        if suffix is not None:
            lines.append(f"Approve after review: open guided live interface and press [A] for {row['pending_gate']}")
        lines.append("Request changes: press [C] in the guided live interface, then describe the issue")
    if row["status"] == "READY":
        lines.append(f"Continue: uv run workflow.py live {initiative} — press [N] and authorize Codex")
    if row["status"] == "COMPLETED":
        candidate = available_next(root, initiative)
        lines.append(f"{mvp.upper()} human G7 complete; earlier evidence remains immutable.")
        if candidate:
            lines.append(f"Next roadmap MVP: {candidate.upper()} — press [M] in the live interface")
    if row["status"] == EVIDENCE_BARRIER:
        lines.append(f"Collect evidence: uv run workflow.py live {initiative} — press [E]")
        lines.append("On the first run, enter actual test, UX-capture and screenshot settings once.")
        lines.append("Run trusted, project-specific commands; host checks do not prove UX by themselves.")
    if row["status"] == "BLOCKED":
        lines.append(f"Review logs, then press [R] in uv run workflow.py live {initiative}")
        if index == 11:
            lines.append("UI fix path: press [C] and choose green, evidence or ux_inspect")
    if row["status"] in {"UNCERTAIN", "RUNNING"}:
        lines.append(f"Inspect logs, then press [R] in uv run workflow.py live {initiative}")
    if row["last_error"]:
        lines.append(f"Note: {row['last_error']}")
    lines += ["Recent live events:"]
    lines += [f"  {item['at']} {item['kind']}" for item in activity]
    return "\n".join(lines)
