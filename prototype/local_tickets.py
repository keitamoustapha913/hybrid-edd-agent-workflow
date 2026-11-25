"""Deterministic local project/child tickets; GitHub is strictly an optional mirror.

Agentflow owns only the marked managed section. Human notes outside it remain
untouched. Ticket contents describe observed local artifacts, not invented proof.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

if __package__:
    from . import mvp_progress
else:
    import mvp_progress


BEGIN = "<!-- AGENTFLOW_LOCAL_TICKET:BEGIN -->"
END = "<!-- AGENTFLOW_LOCAL_TICKET:END -->"
MVP_ID = re.compile(r"mvp-[0-9]{2,3}$")

REPORTS = [
    ("Brief", "brief.md"),
    ("Discovery", "discovery.md"),
    ("Prototype findings", "prototype-report.md"),
    ("G2 accepted prototype findings", "prototype-acceptance.md"),
    ("Product specification", "product-spec.md"),
    ("Product UI/UX contract", "product-ux-spec.md"),
    ("MVP roadmap", "mvp-roadmap.md"),
]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone())


def _row(conn: sqlite3.Connection, name: str) -> dict | None:
    if not _table_exists(conn, name):
        return None
    value = conn.execute(f"SELECT * FROM {name} WHERE singleton=1").fetchone()
    return dict(value) if value else None


def _artifact(source: Path, relative: str, *, from_dir: Path) -> str:
    file = source / relative
    if not file.is_file() or file.is_symlink() or source.resolve() not in file.resolve().parents:
        return "Not generated"
    data = file.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()[:12]
    display = Path(relative).name
    path = Path(os.path.relpath(file, from_dir)).as_posix()
    simulation = " · simulated" if data.startswith(b"<!-- AGENTFLOW_SIMULATED_OUTPUT -->") else ""
    return f"[{display}]({path}) · SHA-256 {checksum}{simulation}"


def _write_owned(path: Path, managed: str) -> None:
    content = f"{BEGIN}\n{managed.rstrip()}\n{END}"
    if path.is_symlink():
        raise ValueError(f"refusing to write symlinked local ticket: {path}")
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        left = existing.find(BEGIN)
        right = existing.find(END)
        if left < 0 or right < left:
            raise ValueError(f"refusing to overwrite a manually managed local ticket: {path}")
        result = existing[:left] + content + existing[right + len(END):]
    else:
        result = content + "\n\n## Human notes\n\n_Add project-specific decisions here. This section is preserved on refresh._\n"
    if not path.exists() or path.read_text(encoding="utf-8") != result:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result, encoding="utf-8")


def refresh(root: Path, initiative: str, conn: sqlite3.Connection, stages: list) -> None:
    """Keep one parent plus a ticket for each generated MVP specification."""
    source = root / "work" / initiative
    if not source.is_dir() or source.is_symlink():
        raise ValueError("initiative workspace must be a real directory")
    workflow = conn.execute("SELECT * FROM workflow WHERE singleton=1").fetchone()
    if workflow is None:
        raise ValueError("missing workflow state for local ticket")
    stage_index = workflow["stage_index"]
    state = workflow["status"]
    phase = stages[stage_index].name if stage_index < len(stages) else "done"
    approvals = conn.execute(
        "SELECT gate, sha, created_at FROM approvals ORDER BY id"
    ).fetchall()
    accepted = {row["gate"] for row in approvals}
    git = _row(conn, "git_binding")
    project = _row(conn, "project_config")
    children = []
    mvps = source / "mvps"
    if mvps.is_dir() and not mvps.is_symlink():
        children = sorted(
            item for item in mvps.iterdir()
            if item.is_dir() and not item.is_symlink()
            and MVP_ID.fullmatch(item.name) and (item / "spec.md").is_file()
            and not (item / "spec.md").is_symlink()
        )
    github_children = {}
    progress = {item.mvp_id: item for item in mvp_progress.collect(root, initiative, conn)}
    if _table_exists(conn, "mvp_issues"):
        github_children = {
            row["mvp_id"]: row["url"]
            for row in conn.execute("SELECT mvp_id, url FROM mvp_issues")
        }
    parent = source / "project-ticket.md"
    parent_lines = [
        f"# Project ticket — {initiative}",
        "",
        "> Local source of truth. GitHub Issues are optional mirrors.",
        "> The runnable prototype uses simulated agents; generated mock reports are not verified software evidence.",
        "",
        "## Overall progress",
        "",
        f"- Phase: **{phase}**",
        f"- State: **{state}**",
        f"- Steps completed in demo: {stage_index}/{len(stages)}",
        f"- Pending human gate: **{workflow['pending_gate'] or 'none'}**",
        f"- Approved gates: {', '.join(sorted(accepted)) or 'none'}",
        f"- Source branch: {git['selected_branch'] if git else 'not attached'}",
        f"- Integration branch: {project['integration_branch'] if project else 'not created'}",
        "- Integration revision: see project-status (tracked separately from this document)",
        f"- GitHub parent (optional): {project['parent_url'] if project and project['parent_url'] else 'not linked'}",
        "",
        "## Generated plans, specifications and reports",
        "",
        "| Deliverable | Local artifact |",
        "| --- | --- |",
    ]
    for label, relative in REPORTS:
        parent_lines.append(f"| {label} | {_artifact(source, relative, from_dir=source)} |")
    if _table_exists(conn, "prototype_iterations"):
        reviews = conn.execute(
            "SELECT iteration_id, status, report_sha, preview_sha, bundle_sha, created_at, decided_at "
            "FROM prototype_iterations ORDER BY iteration_id"
        ).fetchall()
        parent_lines.extend([
            "", "## Prototype G2 iterations", "",
            "> G2 accepts reviewed experiment findings, not prototype code or executed user tests.",
            "", "| Iteration | Decision | Preview | Review bundle |",
            "| --- | --- | --- | --- |",
        ])
        for review in reviews:
            iteration = review["iteration_id"]
            preview = _artifact(
                source, f"prototype-review/{iteration}/preview/index.html", from_dir=source,
            )
            bundle = _artifact(
                source, f"prototype-review/{iteration}/review-bundle.json", from_dir=source,
            )
            parent_lines.append(f"| {iteration} | {review['status']} | {preview} | {bundle} |")
        parent_lines.append("")
    parent_lines.extend([
        "", "## MVP sub-tickets", "",
        "| MVP | Local ticket | Scope | UX checkpoints | EDD progress |",
        "| --- | --- | --- | --- | --- |",
    ])
    for child in children:
        mvp = child.name
        child_dir = child
        item = progress[mvp]
        next_stage = item.active_stage.upper() if item.active_stage else "none"
        scope_display = "PENDING / NOT RECORDED" if item.scope == "PENDING" else item.scope
        summary = f"{item.completed}/6 done (mock) · {item.status} · next {next_stage}"
        parent_lines.append(
            f"| {mvp} | [ticket.md](mvps/{mvp}/ticket.md) | "
            f"{_artifact(source, f'mvps/{mvp}/spec.md', from_dir=source)} | "
            f"{item.ux_completed}/2 validated (mock) | {summary} |"
        )
        child_lines = [
            f"# MVP sub-ticket — {mvp}",
            "",
            f"- Parent: [project-ticket.md](../../project-ticket.md)",
            f"- Spec: {_artifact(source, f'mvps/{mvp}/spec.md', from_dir=child_dir)}",
            f"- UI/UX scope: {_artifact(source, f'mvps/{mvp}/ux/spec.md', from_dir=child_dir)}",
            f"- Scope approval: **{scope_display}**" + (f" · {item.scope_time}" if item.scope_time else ""),
            f"- MVP status: **{item.status}**",
            f"- Next stage: **{next_stage}**",
            f"- Validated EDD steps in the simulation: **{item.completed}/6**",
            f"- Recorded UI/UX checkpoints in the simulation: **{item.ux_completed}/2**",
            f"- Integration destination: {project['integration_branch'] if project else 'not created'}",
            f"- GitHub sub-issue (optional): {github_children.get(mvp, 'not linked')}",
            "",
            "## UI/UX and EDD stage-by-stage progress and evidence",
            "",
            "| Stage | Coordinator state | Last observed (UTC) | Report |",
            "| --- | --- | --- | --- |",
        ]
        child_lines.extend([
            "| UX DESIGN | " + item.ux_stages[0].status + " | "
            + (item.ux_stages[0].timestamp or "—") + " | "
            + _artifact(source, f"mvps/{mvp}/ux/design-review.md", from_dir=child_dir) + " |",
        ])
        for step in item.stages:
            if step.stage == "prove":
                inspect_step = item.ux_stages[1]
                child_lines.append(
                    "| UX INSPECT | " + inspect_step.status + " | "
                    + (inspect_step.timestamp or "—") + " | "
                    + _artifact(source, f"mvps/{mvp}/ux/inspection.md", from_dir=child_dir) + " |"
                )
            relative = f"mvps/{mvp}/edd/{step.stage}.md"
            child_lines.append(
                f"| {step.stage.upper()} | {step.status} | {step.timestamp or '—'} | "
                f"{_artifact(source, relative, from_dir=child_dir)} |"
            )
        child_lines.extend([
            "", "## UI/UX source and design artifacts", "",
            "- UI/UX design: " + _artifact(source, f"mvps/{mvp}/ux/design.md", from_dir=child_dir),
            "- UX DESIGN precedes REQUIREMENTS; UX INSPECT follows VERIFY and precedes PROVE.",
            "- The G5 scope gate binds the MVP spec and its companion UI/UX spec by hash.",
            "- Mock UX checks never constitute a real visual, keyboard, accessibility or usability inspection.",
        ])
        child_lines.extend([
            "",
            "Progress is updated by the local Coordinator after each transition, using its stage index and handoff log rather than report presence alone.",
            "UNSCHEDULED means the current prototype cannot run this MVP's UI/UX + EDD cycle yet; a report file does not change that status.",
            "Proof must be independently supported by real tests before claiming PASS.",
        ])
        _write_owned(child_dir / "ticket.md", "\n".join(child_lines))
    if not children:
        parent_lines.append("| — | MVP DESIGN has not generated any specifications yet | — | — | — |")
    parent_lines.extend([
        "",
        "## Recent Coordinator activity",
        "",
    ])
    activity = conn.execute(
        """
        SELECT created_at, kind, detail FROM events
        WHERE kind NOT IN (
            'PROJECT_ARTIFACTS_SNAPSHOTTED', 'INTEGRATION_PUSHED',
            'GITHUB_OPERATION_COMPLETED'
        )
        ORDER BY event_id DESC LIMIT 12
        """
    ).fetchall()
    for event in activity:
        try:
            detail = json.loads(event["detail"])
        except (ValueError, TypeError):
            detail = {}
        label = detail.get("gate", detail.get("role", detail.get("mvp", "")))
        parent_lines.append(f"- {event['created_at']} · {event['kind']} {label}".rstrip())
    _write_owned(parent, "\n".join(parent_lines))
