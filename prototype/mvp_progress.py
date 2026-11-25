"""Read-only, evidence-aware EDD and independent UI/UX progress per MVP.

The current simulated runner executes UX + EDD only for mvp-01. Other MVPs are
discoverable in local files, but their stages must remain UNSCHEDULED until a
runner explicitly records progress for them. File existence is not proof.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3


MVP_ID = re.compile(r"mvp-[0-9]{2,3}$")
EDD_STAGES = ("requirements", "red", "green", "refactor", "verify", "prove")
UX_STAGES = ("ux_design", "ux_inspect")
# UI/UX DESIGN precedes REQUIREMENTS; UI/UX INSPECTION follows VERIFY.
# EDD retains its six canonical steps, with the independent UX checks between.
STAGE_POSITIONS = {
    "ux_design": 5, "requirements": 6, "red": 7, "green": 8,
    "refactor": 9, "verify": 10, "ux_inspect": 11, "prove": 12,
}
FIRST_EDD_INDEX = STAGE_POSITIONS["requirements"]


@dataclass(frozen=True)
class EDDProgress:
    stage: str
    status: str
    timestamp: str | None
    report_path: str
    report_exists: bool


@dataclass(frozen=True)
class MVPProgress:
    mvp_id: str
    scope: str
    scope_time: str | None
    status: str
    active_stage: str | None
    completed: int
    stages: tuple[EDDProgress, ...]
    ux_completed: int
    ux_stages: tuple[EDDProgress, ...]


def collect(root: Path, initiative: str, conn: sqlite3.Connection) -> tuple[MVPProgress, ...]:
    """Return observed MVP states, with validation distinct from mere files.

    Existing handoff events without an MVP ID belong to mvp-01 in this v1
    prototype. This compatibility path must not be used for future multi-MVP
    execution where explicit MVP identity is required for every handoff.
    """
    work = root / "work" / initiative
    mvp_root = work / "mvps"
    if not mvp_root.is_dir() or mvp_root.is_symlink():
        return ()
    directories = sorted(
        child for child in mvp_root.iterdir()
        if child.is_dir() and not child.is_symlink()
        and MVP_ID.fullmatch(child.name)
        and (child / "spec.md").is_file()
        and not (child / "spec.md").is_symlink()
    )
    if not directories:
        return ()

    state = conn.execute("SELECT * FROM workflow WHERE singleton=1").fetchone()
    if state is None:
        raise ValueError("uninitialized workflow progress")
    stage_index = state["stage_index"]
    workflow_status = state["status"]
    pending_gate = state["pending_gate"]
    approvals = {
        row["gate"]: row["created_at"]
        for row in conn.execute("SELECT gate, created_at FROM approvals ORDER BY id")
    }
    requested = {}
    checked = {}
    for event in conn.execute(
        "SELECT created_at, kind, detail FROM events "
        "WHERE kind IN ('GATE_REQUESTED', 'HANDOFF_CHECKED') ORDER BY event_id"
    ):
        try:
            detail = json.loads(event["detail"])
        except (ValueError, TypeError):
            continue
        if not isinstance(detail, dict):
            continue
        if event["kind"] == "GATE_REQUESTED":
            requested[detail.get("gate")] = event["created_at"]
            continue
        role_value = detail.get("role", "")
        role = role_value.lower() if isinstance(role_value, str) else ""
        step = detail.get("stage") if role == "uiux" else role
        if step not in STAGE_POSITIONS:
            continue
        mvp = detail.get("mvp_id") or "mvp-01"
        if isinstance(mvp, str) and MVP_ID.fullmatch(mvp):
            checked[(mvp, step)] = (detail.get("valid") is True, event["created_at"])

    results = []
    for directory in directories:
        mvp = directory.name
        gate = "G5_" + mvp.upper().replace("-", "_")
        if gate in approvals:
            scope = "APPROVED"
            scope_time = approvals[gate]
        elif pending_gate == gate:
            scope = "REVIEW"
            scope_time = requested.get(gate)
        else:
            scope = "PENDING"
            scope_time = requested.get(gate)

        def stage_progress(name: str) -> EDDProgress:
            position = STAGE_POSITIONS[name]
            latest = checked.get((mvp, name))
            if mvp != "mvp-01":
                stage_status = "UNSCHEDULED"
            elif scope != "APPROVED":
                stage_status = "WAITING_SCOPE"
            elif stage_index > position:
                # Advancement requires a valid handoff. Do not claim a pass
                # if an older/incomplete DB has no matching valid event.
                stage_status = "DONE" if latest and latest[0] else "UNCONFIRMED"
            elif stage_index == position and workflow_status == "BLOCKED":
                stage_status = "BLOCKED"
            elif stage_index == position:
                stage_status = "NEXT"
            else:
                stage_status = "QUEUED"
            if name == "ux_design":
                report_path = f"work/{initiative}/mvps/{mvp}/ux/design-review.md"
            elif name == "ux_inspect":
                report_path = f"work/{initiative}/mvps/{mvp}/ux/inspection.md"
            else:
                report_path = f"work/{initiative}/mvps/{mvp}/edd/{name}.md"
            report = root / report_path
            report_exists = (
                report.is_file() and not report.is_symlink()
                and work.resolve() in report.resolve().parents
            )
            timestamp = latest[1] if latest and (
                stage_status == "BLOCKED" or (stage_status == "DONE" and latest[0])
            ) else None
            return EDDProgress(name, stage_status, timestamp, report_path, report_exists)

        steps = [stage_progress(name) for name in EDD_STAGES]
        ux_steps = [stage_progress(name) for name in UX_STAGES]

        done = sum(step.status == "DONE" for step in steps)
        ux_done = sum(step.status == "DONE" for step in ux_steps)
        ordered = sorted((*steps, *ux_steps), key=lambda s: STAGE_POSITIONS[s.stage])
        active = next((s.stage for s in ordered if s.status in {"NEXT", "BLOCKED", "UNCONFIRMED"}), None)
        if mvp != "mvp-01":
            status = "UNSCHEDULED"
            active = None
        elif scope == "REVIEW":
            status = "SCOPE REVIEW"
            active = "scope approval"
        elif scope != "APPROVED":
            status = "AWAITING SCOPE"
        elif workflow_status == "SIMULATED_COMPLETE" and done == len(EDD_STAGES) and ux_done == len(UX_STAGES):
            status = "DONE (MOCK)"
        elif workflow_status == "BLOCKED" and 5 <= stage_index <= 12:
            status = "BLOCKED"
        else:
            status = "UX / EDD IN PROGRESS"
        results.append(MVPProgress(mvp, scope, scope_time, status, active, done,
                                   tuple(steps), ux_done, tuple(ux_steps)))
    return tuple(results)
