"""Dependency-free terminal dashboard for the simulated Agentflow Coordinator.

All workflow decisions are delegated to the existing engine. This module only
reads state, renders it, and dispatches explicit keyboard actions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import sys
import textwrap
from types import ModuleType
import webbrowser

if __package__:
    from . import mvp_progress, prototype_review
else:
    import mvp_progress
    import prototype_review


@dataclass(frozen=True)
class DashboardView:
    initiative: str
    state: str
    stage_index: int
    total_stages: int
    active_agent: str
    last_agent: str
    approvals: int
    pending_gate: str | None
    pending_path: str | None
    pending_sha: str | None
    artifact_integrity: str | None
    report_path: str | None
    report_text: str
    last_error: str | None
    events: tuple[str, ...]
    stages: tuple[str, ...]
    git: dict | None
    project: dict | None
    mvps: tuple[mvp_progress.MVPProgress, ...]
    active_mvp: str | None
    active_edd_stage: str | None
    local_parent_ticket: str
    local_mvp_tickets: tuple[str, ...]
    prototype: dict | None
    ux_scope: dict | None
    ux_report_text: str | None


def _safe_report(root: Path, initiative: str, relative: str) -> tuple[str, Path | None]:
    """Read only markdown from inside this initiative; DB paths are untrusted."""
    initiative_dir = (root / "work" / initiative).resolve()
    candidate = (root / relative).resolve(strict=False)
    if candidate.suffix != ".md" or initiative_dir not in candidate.parents:
        return "[Report path is outside the permitted initiative directory]", None
    if not candidate.is_file():
        return "[Report is not available yet]", candidate
    try:
        # Avoid loading unexpectedly large or attacker-supplied files into the UI.
        if candidate.stat().st_size > 256 * 1024:
            return "[Report exceeds the 256 KiB dashboard preview limit]", candidate
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "[Report could not be read]", candidate
    if text.startswith("<!-- AGENTFLOW_SIMULATED_OUTPUT -->\n"):
        text = text.split("\n", 1)[1]
    return text.strip() or "[Report is empty]", candidate


def _event_label(row: sqlite3.Row) -> str:
    try:
        detail = json.loads(row["detail"])
    except (ValueError, TypeError):
        detail = {}
    kind = row["kind"]
    summary = ""
    if kind == "HANDOFF_CHECKED":
        summary = f"{detail.get('role', '?')} / {'valid' if detail.get('valid') else 'rejected'}"
    elif kind in {"GATE_REQUESTED", "GATE_APPROVED", "CHANGES_REQUESTED"}:
        summary = str(detail.get("gate", ""))
    elif kind == "RETRY":
        summary = str(detail.get("stage", ""))
    elif kind == "INITIALIZED":
        summary = str(detail.get("initiative", ""))
    elif kind.startswith("GIT_"):
        summary = str(detail.get("branch", ""))
    elif kind.startswith("INTEGRATION_"):
        summary = str(detail.get("mvp", detail.get("branch", "")))
    elif kind.startswith("GITHUB_"):
        summary = str(detail.get("mvp", detail.get("url", "")))
    return f"{row['created_at'][11:19]}  {kind.replace('_', ' ')}  {summary}".rstrip()


def load_view(root: Path, initiative: str, engine: ModuleType, *, history_limit: int = 5) -> DashboardView:
    with engine.open_db(root, initiative) as conn:
        row = engine.current(conn)
        approvals = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        events = conn.execute(
            "SELECT created_at, kind, detail FROM events ORDER BY event_id DESC LIMIT ?",
            (history_limit,),
        ).fetchall()
        mvps = mvp_progress.collect(root, initiative, conn)
        prototype = prototype_review.inspect(root, initiative, conn)
        prior_ux_error = None
        try:
            engine._check_approved_ux_contracts(conn, root, initiative)
        except ValueError as error:
            prior_ux_error = str(error)
        ux_scope = None
        if row["pending_gate"] in {"G3_PRODUCT_SPEC", "G5_MVP_01"}:
            for event in conn.execute(
                "SELECT detail FROM events WHERE kind='GATE_REQUESTED' ORDER BY event_id DESC"
            ):
                try:
                    detail = json.loads(event["detail"])
                except (ValueError, TypeError):
                    continue
                if detail.get("gate") == row["pending_gate"] and detail.get("sha") == row["pending_sha"]:
                    ux_scope = {"path": detail.get("ux_spec_path"), "sha": detail.get("ux_spec_sha")}
                    break

    state = row["status"]
    index = row["stage_index"]
    total = len(engine.STAGES)
    last_role_index = min(index - 1, total - 1)
    while last_role_index >= 0 and engine.STAGES[last_role_index].role is None:
        last_role_index -= 1
    if last_role_index >= 0:
        last_stage = engine.STAGES[last_role_index]
        last_agent = ("UIUX " + last_stage.name.removeprefix("ux_").upper()
                      if last_stage.role == "UIUX" else last_stage.role)
    else:
        last_agent = "NONE YET"

    if state == "WAITING_FOR_HUMAN":
        active_agent = "HUMAN REVIEW"
    elif state == "SIMULATED_COMPLETE":
        active_agent = "NONE — DEMO COMPLETE"
    elif index < total:
        upcoming = engine.STAGES[index]
        active_agent = ("UIUX " + upcoming.name.removeprefix("ux_").upper()
                        if upcoming.role == "UIUX" else upcoming.role or "MVP SCOPE REVIEW")
    else:
        active_agent = "NONE"

    report_path = row["pending_path"]
    if not report_path:
        for stage in reversed(engine.STAGES[: min(index + 1, total)]):
            if stage.report and (root / stage.report.format(id=initiative)).exists():
                report_path = stage.report.format(id=initiative)
                break
    report_text, _ = _safe_report(root, initiative, report_path) if report_path else (
        "[No report yet. Press N to start the simulated workflow.]", None
    )
    ux_report_text = (_safe_report(root, initiative, ux_scope["path"])[0]
                      if ux_scope and isinstance(ux_scope.get("path"), str) else None)

    integrity = None
    if row["pending_path"]:
        _, candidate = _safe_report(root, initiative, row["pending_path"])
        if candidate is None or not candidate.is_file():
            integrity = "MISSING / INVALID — approval blocked"
        else:
            try:
                integrity = (
                    "UNCHANGED — matches review request"
                    if engine.sha256(candidate) == row["pending_sha"]
                    else "CHANGED — approval blocked until re-reviewed"
                )
            except OSError:
                integrity = "UNREADABLE — approval blocked"
        if row["pending_gate"] == "G2_PROTOTYPE":
            if not prototype:
                integrity = "MISSING G2 REVIEW — approval blocked"
            elif prototype["integrity"] != "UNCHANGED":
                integrity = "CHANGED PREVIEW / REVIEW — approval blocked: " + str(prototype["error"])
        elif row["pending_gate"] in {"G3_PRODUCT_SPEC", "G5_MVP_01"}:
            companion = ux_scope or {}
            expected_path = (f"work/{initiative}/product-ux-spec.md" if row["pending_gate"] == "G3_PRODUCT_SPEC"
                             else f"work/{initiative}/mvps/mvp-01/ux/spec.md")
            ux_path = root / expected_path
            if companion.get("path") != expected_path or not companion.get("sha"):
                integrity = "MISSING UI/UX scope binding — approval blocked"
            elif (ux_path.is_symlink() or not ux_path.is_file() or
                  (root / "work" / initiative).resolve() not in ux_path.resolve().parents):
                integrity = "MISSING UI/UX scope — approval blocked"
            elif engine.sha256(ux_path) != companion["sha"]:
                integrity = "CHANGED UI/UX scope — approval blocked"
        if prior_ux_error:
            integrity = f"CHANGED APPROVED UI/UX contract — approval blocked: {prior_ux_error}"

    ticket_root = root / "work" / initiative
    mvp_root = ticket_root / "mvps"
    mvp_tickets = tuple(
        f"work/{initiative}/mvps/{directory.name}/ticket.md"
        for directory in sorted(mvp_root.glob("mvp-*"))
        if directory.is_dir() and (directory / "ticket.md").is_file()
    ) if mvp_root.is_dir() else ()

    active_mvp = next((item for item in mvps if item.status in {
        "SCOPE REVIEW", "UX / EDD IN PROGRESS", "BLOCKED",
    }), None)

    return DashboardView(
        initiative=initiative,
        state=state,
        stage_index=index,
        total_stages=total,
        active_agent=active_agent,
        last_agent=last_agent,
        approvals=approvals,
        pending_gate=row["pending_gate"],
        pending_path=row["pending_path"],
        pending_sha=row["pending_sha"],
        artifact_integrity=integrity,
        report_path=report_path,
        report_text=report_text,
        last_error=row["last_error"],
        events=tuple(_event_label(event) for event in events),
        stages=tuple({
            "mvp_design": "MVP Design",
            "mvp_scope": "MVP Scope",
            "ux_design": "UX Design",
            "ux_inspect": "UX Inspect",
            "red": "RED",
            "green": "GREEN",
            "verify": "VERIFY",
            "prove": "PROVE",
        }.get(stage.name, stage.name.replace("_", " ").title()) for stage in engine.STAGES),
        git=engine.git_context(root, initiative),
        project=engine.project_context(root, initiative),
        mvps=mvps,
        active_mvp=active_mvp.mvp_id if active_mvp else None,
        active_edd_stage=active_mvp.active_stage if active_mvp else None,
        local_parent_ticket=f"work/{initiative}/project-ticket.md",
        local_mvp_tickets=mvp_tickets,
        prototype=prototype,
        ux_scope=ux_scope,
        ux_report_text=ux_report_text,
    )


def available_actions(view: DashboardView) -> str:
    if view.state == "WAITING_FOR_HUMAN":
        if view.pending_gate == "G2_PROTOTYPE":
            return "[O] Open preview  [A] Approve G2  [C] Request changes  [P] Project  [V] MVPs  [Q] Quit"
        return "[A] Approve  [C] Changes  [V] MVPs  [P] Project  [H] Events  [G] Git  [Q] Quit"
    if view.state == "BLOCKED":
        return "[R] Retry handoff  [V] MVPs  [P] Project  [H] Events  [G] Git  [Q] Quit"
    if view.state == "SIMULATED_COMPLETE":
        return "[V] MVPs  [P] Project  [H] Events  [G] Git  [Q] Quit"
    return "[N / Enter] Start / continue  [V] MVPs  [P] Project  [H] Events  [G] Git  [Q] Quit"


def _wrap(lines: list[str], label: str, width: int, *, indent: str = "  ") -> None:
    max_width = max(8, width - len(indent))
    chunks = textwrap.wrap(label, width=max_width, break_long_words=True, break_on_hyphens=False)
    lines.extend(indent + chunk for chunk in (chunks or [""]))


def render_lines(view: DashboardView, *, width: int = 80, expanded_history: bool = False) -> list[str]:
    width = max(30, width)
    completed = min(view.stage_index, view.total_stages)
    percent = round(completed * 100 / view.total_stages)
    bar_width = max(8, min(24, width - 34))
    filled = round(completed * bar_width / view.total_stages)
    progress_bar = "█" * filled + "░" * (bar_width - filled)
    divider = "─" * min(width - 1, 72)
    lines = [
        f"AGENTFLOW  /  {view.initiative}  [SIMULATION ONLY]",
        divider,
        f"ACTIVE AGENT    {view.active_agent}",
        f"STATE           {view.state}",
        f"PROGRESS        {completed:02d}/{view.total_stages:02d}  [{progress_bar}] {percent}%",
        f"LAST AGENT      {view.last_agent}    |    APPROVALS {view.approvals}",
        f"ACTIVE MVP      {view.active_mvp or 'not yet scheduled'}"
        + (f"  /  {view.active_edd_stage.upper()}" if view.active_edd_stage else ""),
        divider,
    ]

    if view.pending_gate:
        lines.append(f"HUMAN DECISION REQUIRED  ·  {view.pending_gate}")
        _wrap(lines, f"Artifact: {view.pending_path}", width)
        _wrap(lines, f"SHA-256: {view.pending_sha}", width)
        _wrap(lines, f"Integrity: {view.artifact_integrity}", width)
        if view.pending_gate in {"G3_PRODUCT_SPEC", "G5_MVP_01"} and view.ux_scope:
            _wrap(lines, f"Companion UX scope: {view.ux_scope['path']}", width)
            _wrap(lines, f"UX SHA-256: {view.ux_scope['sha']}", width)
        if view.pending_gate == "G2_PROTOTYPE" and view.prototype:
            _wrap(lines, f"Iteration: {view.prototype['iteration_id']}", width)
            _wrap(lines, f"Preview: {view.prototype['preview_path']}", width)
            _wrap(lines, f"Review bundle: {view.prototype['bundle_sha']}", width)
            lines.append("  Press O to open the local example. Test it before G2 approval.")
        lines.append("  Review the report below before pressing A or C.")
    elif view.last_error:
        lines.append("HANDOFF BLOCKED  ·  Manual review needed")
        _wrap(lines, view.last_error, width)
    elif view.state == "SIMULATED_COMPLETE":
        lines.append("DEMO COMPLETE  ·  No real Codex runs, tests, or proof were executed")
    else:
        lines.append("READY  ·  Press N or Enter to run the next simulated agent")

    lines.extend([divider, "LATEST EVENTS"])
    displayed = view.events if expanded_history else view.events[:3]
    for event in displayed:
        _wrap(lines, event, width)
    if not displayed:
        lines.append("  [No events yet]")

    lines.extend([divider, f"CURRENT REPORT  ·  {view.report_path or 'not generated'}"])
    for paragraph in view.report_text.splitlines():
        if not paragraph.strip():
            lines.append("")
        else:
            _wrap(lines, paragraph, width, indent="")
    if view.ux_report_text:
        lines.extend([divider, "COMPANION UI/UX SPECIFICATION"])
        for paragraph in view.ux_report_text.splitlines():
            _wrap(lines, paragraph, width, indent="")
    lines.extend([divider, f"ACTIONS  {available_actions(view)}", "Scroll: ↑ / ↓ / PageUp / PageDown"])
    # Snapshot output is usable in narrow terminals as well as the curses view.
    wrapped_lines: list[str] = []
    for line in lines:
        if len(line) <= width:
            wrapped_lines.append(line)
        else:
            wrapped_lines.extend(textwrap.wrap(
                line, width=width, break_long_words=True, break_on_hyphens=False,
            ))
    return wrapped_lines


# A small, testable cell canvas keeps the snapshot and interactive layout identical.
# One-character box glyphs work in standard UTF-8 terminal fonts.
BASE, MUTED, BORDER, ACCENT, TITLE, SUCCESS, WARNING, ERROR, BUTTON = range(9)


class Canvas:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.cells = [[" "] * width for _ in range(height)]
        self.styles = [[BASE] * width for _ in range(height)]
        self.max_scroll = 0

    def put(self, y: int, x: int, value: str, style: int = BASE, *, limit: int | None = None) -> None:
        if y < 0 or y >= self.height or x >= self.width:
            return
        value = "".join(c if c.isprintable() else " " for c in str(value))
        allowed = min(self.width - x, limit if limit is not None else self.width)
        if allowed <= 0:
            return
        if len(value) > allowed:
            value = value[: max(0, allowed - 1)] + "…"
        for index, char in enumerate(value):
            column = x + index
            if 0 <= column < self.width:
                self.cells[y][column] = char
                self.styles[y][column] = style

    def frame(self, x: int, y: int, width: int, height: int, title: str = "", style: int = BORDER) -> None:
        if width < 4 or height < 2:
            return
        self.put(y, x, "╭" + "─" * (width - 2) + "╮", style)
        for middle in range(y + 1, y + height - 1):
            self.put(middle, x, "│", style)
            self.put(middle, x + width - 1, "│", style)
        self.put(y + height - 1, x, "╰" + "─" * (width - 2) + "╯", style)
        if title:
            self.put(y, x + 2, f" {title} ", TITLE, limit=width - 4)

    def render(self) -> list[str]:
        return ["".join(row).rstrip() for row in self.cells]


def _report_rows(text: str, width: int) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for source in text.splitlines():
        source = source.replace("\t", "    ").strip().replace("**", "").replace("`", "")
        if not source:
            rows.append(("", BASE))
            continue
        style = BASE
        if source.startswith("#"):
            source = source.lstrip("# ").strip()
            style = TITLE
        elif source.startswith("- "):
            source = "• " + source[2:]
        elif source.startswith("["):
            style = MUTED
        for line in textwrap.wrap(
            source, width=max(8, width), break_long_words=True, break_on_hyphens=False,
        ) or [""]:
            rows.append((line, style))
    return rows or [("[No report content]", MUTED)]


def _git_rows(view: DashboardView, width: int) -> list[tuple[str, int]]:
    info = view.git
    if not info:
        return [
            ("NO REPOSITORY ATTACHED", WARNING),
            ("", BASE),
            ("Press [B] to attach a local Git checkout.", BASE),
            ("This does not modify repository files.", MUTED),
            ("", BASE),
            ("Then press [W] to create an isolated MVP worktree.", MUTED),
        ]
    lines: list[tuple[str, int]] = []

    def add(label: str, value: str, style: int = BASE) -> None:
        for part in textwrap.wrap(
            f"{label}  {value}", width=max(8, width),
            break_long_words=True, break_on_hyphens=False,
        ) or [""]:
            lines.append((part, style))

    add("Repository", str(info["primary_root"]), TITLE)
    add("Worktree", str(info["selected_root"]))
    if info["error"]:
        add("ERROR", str(info["error"]), ERROR)
        add("Action", "Reopen with an approved worktree", WARNING)
        return lines
    add("Origin", str(info["origin"]), MUTED)
    add("Branch", str(info["branch"]), ACCENT)
    add("HEAD", str(info["head"])[:16] + "…", MUTED)
    add("Changes", "DIRTY — run/approval blocked" if info["dirty"] else "CLEAN", ERROR if info["dirty"] else SUCCESS)
    lines.append(("", BASE))
    lines.append(("REGISTERED WORKTREES  ·  [S] select by number", TITLE))
    for index, tree in enumerate(info["worktrees"], 1):
        marker = "✓" if tree["path"] == info["selected_root"] else "○"
        add(f"{index}. {marker}", f"{tree['branch'] or 'DETACHED'}  ·  {tree['path']}", ACCENT if marker == "✓" else BASE)
    lines.append(("", BASE))
    lines.append(("[B] Attach  ·  [W] Create MVP worktree", MUTED))
    return lines


def _project_rows(view: DashboardView, width: int) -> list[tuple[str, int]]:
    info = view.project
    lines: list[tuple[str, int]] = []

    def add(value: str, style: int = BASE) -> None:
        for part in textwrap.wrap(
            value, width=max(8, width),
            break_long_words=True, break_on_hyphens=False,
        ) or [""]:
            lines.append((part, style))

    add("LOCAL PROJECT TICKET  ·  source of truth", TITLE)
    add(view.local_parent_ticket)
    lines.append(("", BASE))
    add("MVP DELIVERY STATUS", TITLE)
    if not view.mvps:
        add("No MVP specifications generated yet.", MUTED)
    for item in view.mvps:
        add(f"{item.mvp_id}  {item.status}  ·  scope {item.scope}  ·  UX {item.ux_completed}/2  ·  EDD {item.completed}/6",
            WARNING if item.status in {"BLOCKED", "SCOPE REVIEW"} else SUCCESS if item.status == "DONE (MOCK)" else BASE)
    add("Press [V] for UX Design, EDD and independent UX Inspection.", ACCENT)
    lines.append(("", BASE))
    add("LOCAL MVP SUB-TICKETS", TITLE)
    for ticket in view.local_mvp_tickets:
        add(ticket, SUCCESS)
    if not view.local_mvp_tickets:
        add("Created automatically when MVP specs are generated.", MUTED)
    lines.append(("", BASE))
    add("LOCAL INTEGRATION ACTIONS", TITLE)
    add("[I] Create integration   [J] Promote selected MVP")
    add("[F] Snapshot reports   [O] Push integration")
    add("GitHub mirror (optional): [T] Parent [M] Child [U] Sync", MUTED)
    lines.append(("", BASE))
    if not info:
        add("NO PROJECT INTEGRATION BRANCH", WARNING)
        add("Press [I] to create a dedicated integration worktree.")
        add("One integration branch belongs to the entire initiative.", MUTED)
    else:
        add(f"Integration  {info['integration_branch']}", TITLE)
        add(f"Worktree  {info['integration_root']}")
        add(f"HEAD  {(info['head'] or info['integration_head'])[:16]}…", MUTED)
        add(
            "Working tree  INVALID — " + info["error"] if info["error"] else
            "Working tree  DIRTY" if info["dirty"] else
            "Working tree  CLEAN",
            ERROR if info["error"] or info["dirty"] else SUCCESS,
        )
        add(f"GitHub mirror  {info['github_repo'] or 'disabled (optional)'}", MUTED)
        add(f"GitHub parent  {info['parent_url'] or 'not created'}", MUTED)
        lines.append(("", BASE))
        add("MVP SUB-ISSUES", TITLE)
        for issue in info["mvp_issues"]:
            add(f"{issue['mvp_id']}  #{issue['issue_number']}  {issue['url']}", ACCENT)
        if not info["mvp_issues"]:
            add("No MVP sub-issues yet.", MUTED)
    lines.append(("", BASE))
    add("Local tickets update automatically. Press [P] to return.", MUTED)
    return lines


def _mvp_rows(view: DashboardView, width: int) -> list[tuple[str, int]]:
    """Per-MVP scope, stage, and observed-time matrix; never infer passes from files."""
    rows: list[tuple[str, int]] = []

    def add(value: str, style: int = BASE) -> None:
        for part in textwrap.wrap(
            value, width=max(8, width), break_long_words=True,
            break_on_hyphens=False,
        ) or [""]:
            rows.append((part, style))

    add("MVP DELIVERY TRACKER  ·  LOCAL SOURCE OF TRUTH", TITLE)
    add("Each MVP has two UX checkpoints around its six EDD steps.", MUTED)
    add("✓ validated mock handoff   → next   ! blocked   ○ queued", MUTED)
    rows.append(("", BASE))
    if not view.mvps:
        add("NO MVP SPECS YET", WARNING)
        add("Run and approve Discovery, Prototype and Specification, then review MVP Design.")
        add("MVP-01, MVP-02 etc. will appear after their local spec.md files are generated.", MUTED)
        return rows

    for item in view.mvps:
        state_style = (SUCCESS if item.status == "DONE (MOCK)" else
                       ERROR if item.status == "BLOCKED" else
                       WARNING if item.status in {"SCOPE REVIEW", "AWAITING SCOPE"} else ACCENT)
        add(f"{item.mvp_id.upper()}  ·  {item.status}", state_style)
        add(f"Scope: {item.scope}  ·  UX: {item.ux_completed}/2  ·  EDD: {item.completed}/6 validated (mock)")
        if item.scope_time:
            add(f"Scope event: {item.scope_time[:19].replace('T', ' ')} UTC", MUTED)
        if item.active_stage:
            add(f"CURRENT / NEXT: {item.active_stage.upper()}", WARNING if item.status == "BLOCKED" else ACCENT)
        ordered_steps = sorted((*item.stages, *item.ux_stages),
                               key=lambda step: mvp_progress.STAGE_POSITIONS[step.stage])
        for step in ordered_steps:
            marker, style = {
                "DONE": ("✓", SUCCESS),
                "NEXT": ("→", ACCENT),
                "BLOCKED": ("!", ERROR),
                "UNCONFIRMED": ("?", WARNING),
                "QUEUED": ("○", MUTED),
                "WAITING_SCOPE": ("○", MUTED),
                "UNSCHEDULED": ("·", MUTED),
            }[step.status]
            when = f"  {step.timestamp[:19].replace('T', ' ')} UTC" if step.timestamp else ""
            label = step.stage.upper().replace("_", " ")
            add(f" {marker} {label:14}  {step.status}{when}", style)
            if step.report_exists and step.status not in {"DONE", "BLOCKED"}:
                add("   Report exists, but no validated handoff is recorded.", WARNING)
        add(f"UX contract: work/{view.initiative}/mvps/{item.mvp_id}/ux/spec.md", MUTED)
        add(f"Ticket: work/{view.initiative}/mvps/{item.mvp_id}/ticket.md", MUTED)
        if item.status == "UNSCHEDULED":
            add("Not dispatched: the current demo runner executes only MVP-01.", WARNING)
        rows.append(("", BASE))
    add("Every DONE above means simulated handoff validation, NOT real UX inspection or software proof.", MUTED)
    add("Press [V] to return, or [P] for project/integration.", MUTED)
    return rows


def _prototype_rows(view: DashboardView, width: int) -> list[tuple[str, int]]:
    """Show a reproducible preview review, not merely the prose report."""
    rows: list[tuple[str, int]] = []

    def add(value: str, style: int = BASE) -> None:
        for part in textwrap.wrap(value, width=max(8, width), break_long_words=True, break_on_hyphens=False) or [""]:
            rows.append((part, style))

    add("G2 PROTOTYPE REVIEW  ·  HUMAN GATE", TITLE)
    review = view.prototype
    if not review:
        add("No versioned preview is available; request changes to rerun Prototype.", WARNING)
        return rows
    add(f"Iteration: {review['iteration_id']}   State: {review['status']}", ACCENT)
    add(f"Evidence: {review['integrity']}", SUCCESS if review["integrity"] == "UNCHANGED" else ERROR)
    if review["error"]:
        add(f"Integrity error: {review['error']}", ERROR)
    add("", BASE)
    add("LOCAL WORKING PREVIEW", TITLE)
    add(review["preview_path"])
    add("[O] Open HTML preview in your browser", ACCENT)
    add("Search synthetic orders and try Export CSV. Observe usability manually.", MUTED)
    add("Sample preview only: not real Codex output or software verification.", WARNING)
    add("", BASE)
    add("REVIEW CONTRACT", TITLE)
    add(f"Bundle SHA-256: {review['bundle_sha']}", MUTED)
    add("Report, preview, checks and previous feedback hashes must all match.", BASE)
    add("Interactive behavior: NOT EXECUTED by automated checks.", WARNING)
    if review["feedback_id"]:
        add("", BASE)
        add(f"Previous feedback: {review['feedback_id']}", WARNING)
        add(str(review["feedback_text"] or ""), BASE)
    add("", BASE)
    add("[A] Accept reviewed findings → Specification (G3)", SUCCESS)
    add("[C] Describe changes → Prototype rerun → NEW preview → NEW G2", WARNING)
    add("An old G2 decision never authorizes a later iteration.", MUTED)
    add("", BASE)
    add("PROTOTYPE AGENT REPORT  ·  SCROLL TO REVIEW", TITLE)
    rows.extend(_report_rows(view.report_text, width))
    return rows


def _pane_rows(
    view: DashboardView, width: int, show_history: bool,
    show_git: bool = False, show_project: bool = False, show_mvp: bool = False,
) -> list[tuple[str, int]]:
    if show_mvp:
        return _mvp_rows(view, width)
    if show_project:
        return _project_rows(view, width)
    if show_git:
        return _git_rows(view, width)
    if show_history:
        return [(event, MUTED if "INITIALIZED" in event else BASE)
                for event in view.events] or [("No activity recorded yet.", MUTED)]
    if view.pending_gate == "G2_PROTOTYPE":
        return _prototype_rows(view, width)
    if view.pending_gate in {"G3_PRODUCT_SPEC", "G5_MVP_01"} and view.ux_report_text:
        return [*_report_rows(view.report_text, width), ("", BASE),
                ("COMPANION UI/UX SPEC  ·  HASH-BOUND TO THIS GATE", TITLE),
                *_report_rows(view.ux_report_text, width)]
    return _report_rows(view.report_text, width)


def _stage_marker(view: DashboardView, index: int) -> tuple[str, int]:
    review_index = view.stage_index if view.pending_gate == "G5_MVP_01" else view.stage_index - 1
    if view.state == "WAITING_FOR_HUMAN" and index == review_index:
        return "◆", WARNING
    if view.state == "BLOCKED" and index == view.stage_index:
        return "!", ERROR
    if index < view.stage_index:
        return "✓", SUCCESS
    if index == view.stage_index and view.state in {"NOT_STARTED", "RUNNING"}:
        return "●", ACCENT
    return "○", MUTED


def _decision_panel(canvas: Canvas, view: DashboardView, x: int, y: int, width: int, height: int) -> None:
    if view.pending_gate:
        title = "APPROVAL REQUIRED"
    elif view.state == "BLOCKED":
        title = "ATTENTION REQUIRED"
    elif view.state == "SIMULATED_COMPLETE":
        title = "WORKFLOW FINISHED"
    else:
        title = "NEXT ACTION"
    canvas.frame(x, y, width, height, title)
    inner = width - 4
    if view.pending_gate:
        canvas.put(y + 1, x + 2, f"◆  {view.pending_gate}", WARNING, limit=inner)
        title = (f"Preview  {view.prototype['iteration_id']} · [O] open"
                 if view.pending_gate == "G2_PROTOTYPE" and view.prototype
                 else "Feature spec + UX spec · [D] inspect"
                 if view.pending_gate in {"G3_PRODUCT_SPEC", "G5_MVP_01"}
                 else f"Report  {Path(view.pending_path).name}")
        canvas.put(y + 2, x + 2, title, BASE, limit=inner)
        integrity_good = (view.artifact_integrity or "").startswith("UNCHANGED")
        git_invalid = bool(view.git and (view.git["error"] or view.git.get("dirty")))
        message = (
            "!  Git state changed — approval blocked" if git_invalid else
            "✓  Preview + report verified  ·  [D] info" if integrity_good and view.pending_gate == "G2_PROTOTYPE" else
            "✓  Artifact verified  ·  [D] details" if integrity_good else
            "!  Preview changed — approval blocked" if view.pending_gate == "G2_PROTOTYPE" else
            "!  Artifact changed — approval blocked"
        )
        canvas.put(y + 3, x + 2, message, ERROR if git_invalid or not integrity_good else SUCCESS, limit=inner)
    elif view.state == "BLOCKED":
        canvas.put(y + 1, x + 2, "!  Handoff validation failed", ERROR, limit=inner)
        short_error = " ".join((view.last_error or "Review the error and retry.").split())
        canvas.put(y + 2, x + 2, short_error, MUTED, limit=inner)
        canvas.put(y + 3, x + 2, "Press [R] to retry after reviewing.", WARNING, limit=inner)
    elif view.state == "SIMULATED_COMPLETE":
        canvas.put(y + 1, x + 2, "✓  All mock stages exercised", SUCCESS, limit=inner)
        canvas.put(y + 2, x + 2, "No real tests or Codex calls occurred.", MUTED, limit=inner)
    else:
        canvas.put(y + 1, x + 2, f"●  Next: {view.active_agent}", ACCENT, limit=inner)
        canvas.put(y + 2, x + 2, "Press [N] or Enter to continue.", BASE, limit=inner)
        git_invalid = bool(view.git and (view.git["error"] or view.git.get("dirty")))
        canvas.put(y + 3, x + 2,
                   "!  Git worktree must be clean/valid" if git_invalid else "Simulated execution; no real agents.",
                   ERROR if git_invalid else MUTED, limit=inner)


def _content_panel(
    canvas: Canvas, view: DashboardView, x: int, y: int, width: int, height: int,
    *, scroll: int, show_history: bool, show_git: bool = False, show_project: bool = False,
    show_mvp: bool = False,
) -> int:
    title = (
        "MVP / EDD TRACKER" if show_mvp else
        "PROJECT / INTEGRATION" if show_project else
        "GIT / WORKTREES" if show_git else
        "EVENT HISTORY" if show_history else
        "PROTOTYPE / G2 REVIEW" if view.pending_gate == "G2_PROTOTYPE" else
        "FEATURE + UI/UX SPECS" if view.pending_gate in {"G3_PRODUCT_SPEC", "G5_MVP_01"} else
        "CURRENT REPORT"
    )
    canvas.frame(x, y, width, height, title)
    inner_width = width - 4
    body = _pane_rows(view, inner_width, show_history, show_git, show_project, show_mvp)
    visible = max(0, height - 2)
    max_scroll = max(0, len(body) - visible)
    offset = max(0, min(scroll, max_scroll))
    if not show_history and not show_git and not show_project and not show_mvp and view.pending_gate not in {
        "G2_PROTOTYPE", "G3_PRODUCT_SPEC", "G5_MVP_01",
    }:
        filename = Path(view.report_path).name if view.report_path else "awaiting first run"
        label_x = max(20, min(22, width // 3))
        canvas.put(y, x + label_x, f" · {filename}", MUTED, limit=max(1, width - label_x - 12))
    for row, (line, style) in enumerate(body[offset : offset + visible]):
        canvas.put(y + row + 1, x + 2, line, style, limit=inner_width)
    if max_scroll:
        canvas.put(y + height - 1, x + width - 15, f" {offset + 1}-{min(offset + visible, len(body))}/{len(body)} ↑↓ ",
                   ACCENT, limit=13)
    return max_scroll


def _activity_panel(canvas: Canvas, view: DashboardView, y: int, height: int) -> None:
    branch = ""
    if view.git:
        branch = f"  ·  GIT: {view.git.get('branch') or view.git['expected_branch']}"
    canvas.frame(0, y, canvas.width, height, "RECENT ACTIVITY" + branch[: max(0, canvas.width - 23)])
    for row, event in enumerate(view.events[: max(0, height - 2)]):
        canvas.put(y + row + 1, 2, event, MUTED, limit=canvas.width - 4)


def _footer(
    canvas: Canvas, view: DashboardView, message: str,
    show_history: bool, show_git: bool, show_project: bool, show_mvp: bool,
) -> None:
    y = canvas.height - 2
    if show_mvp:
        actions = []
        if view.state == "WAITING_FOR_HUMAN":
            actions.extend([("[A] Approve", SUCCESS), ("[C] Changes", WARNING)])
        actions.extend([("[V] Back", ACCENT), ("[P] Project", WARNING), ("[Q] Quit", MUTED)])
    elif show_project:
        actions = [
            ("[I] Branch", ACCENT), ("[T] Ticket", WARNING), ("[M] MVP", SUCCESS),
            ("[V] EDD stages", ACCENT), ("[P] Back", MUTED), ("[Q] Quit", MUTED),
        ]
    elif show_git:
        actions = [
            ("[B] Bind", ACCENT), ("[W] New", SUCCESS), ("[S] Select", WARNING),
            ("[G] Back", MUTED), ("[Q] Quit", MUTED),
        ]
    else:
        if canvas.width < 70 and view.state == "WAITING_FOR_HUMAN":
            actions = [("[A] Approve", SUCCESS), ("[C] Changes", WARNING)]
        elif view.state == "WAITING_FOR_HUMAN":
            actions = [("[A] Approve", SUCCESS), ("[C] Changes", WARNING), ("[D] Info", ACCENT)]
        elif view.state == "BLOCKED":
            actions = [("[R] Retry", ERROR), ("[D] Info", ACCENT)]
        elif view.state == "SIMULATED_COMPLETE":
            actions = [("[D] Info", ACCENT)]
        else:
            actions = [("[N] Start / Continue", ACCENT)]
        if view.pending_gate == "G2_PROTOTYPE":
            actions.insert(0, ("[O] Preview", ACCENT))
        if canvas.width >= 70:
            actions.extend([("[H] Report" if show_history else "[H] Events", MUTED)])
        actions.extend([("[V] MVPs", ACCENT), ("[G] Git", ACCENT), ("[P] Project", WARNING), ("[Q] Quit", MUTED)])
    quit_label, quit_style = actions.pop()
    last_start = canvas.width - len(quit_label) - 1
    x = 1
    for label, style in actions:
        if x + len(label) + 1 >= last_start:
            break
        canvas.put(y, x, label, style)
        x += len(label) + 2
    canvas.put(y, last_start, quit_label, quit_style)
    canvas.put(canvas.height - 1, 1, message or "↑ / ↓ scroll   •   Actions run only in SIMULATION mode.",
               MUTED, limit=canvas.width - 2)


def compose_screen(
    view: DashboardView, *, width: int = 80, height: int = 24,
    scroll: int = 0, show_history: bool = False, show_git: bool = False,
    show_project: bool = False, show_mvp: bool = False, message: str = "",
) -> Canvas:
    """Render a complete fixed-height dashboard. Safe for snapshots and curses."""
    width, height = max(1, width), max(1, height)
    canvas = Canvas(width, height)
    if width < 48 or height < 19:
        canvas.put(0, 0, "AGENTFLOW  •  SIMULATION ONLY", TITLE)
        canvas.put(2, 0, "Resize terminal to at least 48 columns x 19 rows.", WARNING)
        canvas.put(height - 1, 0, "[Q] Quit", MUTED)
        return canvas

    canvas.frame(0, 0, width, 3, "◆ AGENTFLOW", ACCENT)
    canvas.put(0, width - 19, " SIMULATION MODE ", WARNING, limit=18)
    status = (
        "REVIEW NEEDED" if view.state == "WAITING_FOR_HUMAN" else
        "BLOCKED" if view.state == "BLOCKED" else
        "COMPLETE" if view.state == "SIMULATED_COMPLETE" else
        "READY"
    )
    status_style = WARNING if view.pending_gate else ERROR if view.state == "BLOCKED" else SUCCESS if view.state == "SIMULATED_COMPLETE" else ACCENT
    progress = min(view.stage_index, view.total_stages)
    percent = round(progress * 100 / view.total_stages)
    bar_width = 9 if width < 70 else 13
    filled = round(bar_width * progress / view.total_stages)
    metric = f"{progress:02d}/{view.total_stages:02d}  {'━' * filled}{'─' * (bar_width - filled)}  {percent:>3}%"
    if width < 70:
        short_status = "REVIEW" if view.pending_gate else status
        canvas.put(1, 2, f"{short_status}  ·  {view.initiative}", status_style,
                   limit=width - len(metric) - 5)
    else:
        canvas.put(1, 2, f"● {status}", status_style, limit=19)
        mvp_label = f" · {view.active_mvp} / {view.active_edd_stage}" if view.active_mvp and view.active_edd_stage else ""
        canvas.put(1, 21, view.initiative + mvp_label, TITLE, limit=max(8, width - 52))
    canvas.put(1, width - len(metric) - 2, metric, ACCENT, limit=len(metric))

    activity_height = 4 if height >= 28 else 3
    activity_y = height - 2 - activity_height
    middle_y, middle_h = 3, activity_y - 3
    split = width >= 76 and middle_h >= 15
    if split:
        left_width = 26 if width < 104 else 30
        right_x, right_width = left_width + 1, width - left_width - 1
        canvas.frame(0, middle_y, left_width, middle_h, "WORKFLOW STAGES")
        # Keep the active UX/EDD step visible even in a 24-line terminal.
        slots = max(1, middle_h - 6)
        active_index = view.stage_index - (1 if view.pending_gate and view.pending_gate != "G5_MVP_01" else 0)
        active_index = max(0, min(active_index, len(view.stages) - 1))
        start = max(0, min(active_index - slots // 2, len(view.stages) - slots))
        canvas.put(middle_y + 1, 2, f"Stages {start + 1}-{min(len(view.stages), start + slots)}/{len(view.stages)}",
                   MUTED, limit=left_width - 4)
        for offset, i in enumerate(range(start, min(len(view.stages), start + slots))):
            name = view.stages[i]
            row = middle_y + 2 + offset
            marker, style = _stage_marker(view, i)
            canvas.put(row, 2, f"{i + 1:02d}", MUTED)
            canvas.put(row, 6, marker, style)
            canvas.put(row, 9, name, style if style != MUTED else BASE, limit=left_width - 11)
        if middle_h >= 16:
            canvas.put(middle_y + middle_h - 2, 2, f"Approvals recorded  {view.approvals}", MUTED, limit=left_width - 4)
            git_label = view.git["expected_branch"] if view.git else "not attached"
            canvas.put(middle_y + middle_h - 3, 2, f"Git  {git_label}", ACCENT if view.git else MUTED, limit=left_width - 4)
            project_branch = view.project["integration_branch"] if view.project else "not initialized"
            canvas.put(middle_y + middle_h - 4, 2, f"Project  {project_branch}", ACCENT if view.project else MUTED, limit=left_width - 4)
        decision_height = 5
        _decision_panel(canvas, view, right_x, middle_y, right_width, decision_height)
        canvas.max_scroll = _content_panel(
            canvas, view, right_x, middle_y + decision_height,
            right_width, middle_h - decision_height, scroll=scroll,
            show_history=show_history, show_git=show_git, show_project=show_project,
            show_mvp=show_mvp,
        )
    else:
        # Compact terminals get the same decision/report actions in stacked cards.
        stage_height, decision_height = 3, 4
        canvas.frame(0, middle_y, width, stage_height, "WORKFLOW")
        active = view.stage_index
        if view.pending_gate and view.pending_gate != "G5_MVP_01":
            active -= 1
        active = max(0, min(active, len(view.stages) - 1))
        marker, _ = _stage_marker(view, active)
        canvas.put(middle_y + 1, 2, f"{view.stage_index:02d}/{view.total_stages:02d} steps  •  {marker} {view.stages[active]}",
                   TITLE, limit=width - 4)
        decision_y = middle_y + stage_height
        _decision_panel(canvas, view, 0, decision_y, width, decision_height + 1)
        report_y = decision_y + decision_height + 1
        canvas.max_scroll = _content_panel(
            canvas, view, 0, report_y, width,
            max(3, activity_y - report_y), scroll=scroll,
            show_history=show_history, show_git=show_git, show_project=show_project,
            show_mvp=show_mvp,
        )

    _activity_panel(canvas, view, activity_y, activity_height)
    _footer(canvas, view, message, show_history, show_git, show_project, show_mvp)
    return canvas


def snapshot_text(
    root: Path, initiative: str, engine: ModuleType, *, width: int = 80, height: int = 24,
    show_git: bool = False, show_project: bool = False, show_mvp: bool = False,
) -> str:
    view = load_view(root, initiative, engine)
    return "\n".join(compose_screen(
        view, width=width, height=height, show_git=show_git, show_project=show_project,
        show_mvp=show_mvp,
    ).render())


def perform_action(root: Path, initiative: str, engine: ModuleType, action: str, reason: str = "") -> str:
    """Route TUI actions through the same state machine as regular CLI commands."""
    view = load_view(root, initiative, engine)
    if action == "run" and view.state in {"NOT_STARTED", "RUNNING"}:
        result = engine.advance(root, initiative)
    elif action == "approve" and view.state == "WAITING_FOR_HUMAN":
        result = engine.approve(root, initiative, view.pending_gate, view.pending_sha)
    elif action == "changes" and view.state == "WAITING_FOR_HUMAN":
        response = engine.request_changes(root, initiative, reason)
        result = [response, *engine.advance(root, initiative)]
    elif action == "retry" and view.state == "BLOCKED":
        result = engine.retry(root, initiative)
    else:
        return f"Action '{action}' is not available in state {view.state}."
    return next((message for message in reversed(result) if message.startswith("BLOCKED")), result[0])


def open_preview(root: Path, view: DashboardView, *, opener=None) -> str:
    """Explicit, local-only UI launch. Never follow untrusted manifest URLs."""
    if view.pending_gate != "G2_PROTOTYPE" or not view.prototype:
        raise ValueError("no pending G2 prototype preview")
    if view.prototype["integrity"] != "UNCHANGED" or not (view.artifact_integrity or "").startswith("UNCHANGED"):
        raise ValueError("preview integrity changed; request a new preview before opening")
    relative = view.prototype["preview_path"]
    path = root / relative
    safe_root = (root / "work" / view.initiative).resolve()
    if path.is_symlink() or not path.is_file() or safe_root not in path.resolve().parents:
        raise ValueError("prototype preview is outside the local initiative workspace")
    try:
        launched = (opener or webbrowser.open_new_tab)(path.resolve().as_uri())
    except (OSError, RuntimeError, webbrowser.Error) as error:
        raise ValueError(f"browser could not be launched; open manually: {path} ({error})") from error
    if not launched:
        return f"No browser handler found. Open this file manually: {path}"
    return f"Opened local preview {view.prototype['iteration_id']} · inspect before approving G2."


def launch_tui(root: Path, initiative: str, engine: ModuleType) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("dashboard needs an interactive terminal; use 'tui <id> --snapshot' for plain output")
    try:
        import curses
    except ImportError as error:
        raise ValueError("curses is unavailable on this platform; use --snapshot") from error

    colors: dict[int, int] = {}

    def _set_colors() -> None:
        if not curses.has_colors():
            return
        try:
            curses.start_color()
            curses.use_default_colors()
            for index, foreground in (
                (MUTED, curses.COLOR_BLUE),
                (BORDER, curses.COLOR_BLUE),
                (ACCENT, curses.COLOR_CYAN),
                (TITLE, curses.COLOR_WHITE),
                (SUCCESS, curses.COLOR_GREEN),
                (WARNING, curses.COLOR_YELLOW),
                (ERROR, curses.COLOR_RED),
                (BUTTON, curses.COLOR_CYAN),
            ):
                curses.init_pair(index, foreground, -1)
                colors[index] = curses.color_pair(index)
        except curses.error:
            colors.clear()

    def _paint(stdscr, canvas: Canvas) -> None:
        stdscr.erase()
        emphasis = {ACCENT, TITLE, SUCCESS, WARNING, ERROR, BUTTON}
        for y in range(canvas.height):
            column = 0
            while column < canvas.width:
                style = canvas.styles[y][column]
                end = column + 1
                while end < canvas.width and canvas.styles[y][end] == style:
                    end += 1
                segment = "".join(canvas.cells[y][column:end])
                attr = colors.get(style, curses.A_DIM if style in (MUTED, BORDER) else 0)
                if style in emphasis:
                    attr |= curses.A_BOLD
                try:
                    stdscr.addnstr(y, column, segment, max(0, canvas.width - column), attr)
                except curses.error:
                    pass  # Writing the bottom-right cell can raise on some terminals.
                column = end
        stdscr.refresh()

    def _overlay(canvas: Canvas, title: str, body: list[str], footer: str) -> tuple[int, int, int, int]:
        dialog_width = min(72, canvas.width - 6)
        inner_width = dialog_width - 4
        wrapped = []
        for value in body:
            wrapped.extend(textwrap.wrap(
                value, width=inner_width, break_long_words=True, break_on_hyphens=False,
            ) or [""])
        dialog_height = min(canvas.height - 4, max(7, len(wrapped) + 4))
        x, y = (canvas.width - dialog_width) // 2, (canvas.height - dialog_height) // 2
        for row in range(y, y + dialog_height):
            canvas.put(row, x, " " * dialog_width, BASE)
        canvas.frame(x, y, dialog_width, dialog_height, title, ACCENT)
        for index, line in enumerate(wrapped[: dialog_height - 3]):
            canvas.put(y + index + 1, x + 2, line, TITLE if index == 0 else BASE, limit=inner_width)
        canvas.put(y + dialog_height - 2, x + 2, footer, ACCENT, limit=inner_width)
        return x, y, dialog_width, dialog_height

    def _confirm_dialog(stdscr, view: DashboardView, title: str, body: list[str]) -> bool:
        while True:
            height, width = stdscr.getmaxyx()
            canvas = compose_screen(view, width=width, height=height)
            _overlay(canvas, title, body, "[ Y ] Confirm    [ N / Esc ] Cancel")
            _paint(stdscr, canvas)
            key = stdscr.getch()
            if key in (ord("y"), ord("Y")):
                return True
            if key in (ord("n"), ord("N"), 27, 10, 13, ord("q")):
                return False

    def _input_dialog(
        stdscr, view: DashboardView, title: str, question: str, *,
        initial: str = "", max_length: int = 240,
    ) -> str | None:
        value = initial
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                height, width = stdscr.getmaxyx()
                canvas = compose_screen(view, width=width, height=height)
                modal_width = min(72, width - 6)
                input_width = modal_width - 8
                visible = value[-input_width:]
                x, y, _, _ = _overlay(canvas, title, [
                    question,
                    "› " + visible,
                ], "Enter: confirm   Esc: cancel   Ctrl-U: clear")
                _paint(stdscr, canvas)
                try:
                    stdscr.move(y + 2, x + 4 + len(visible))
                except curses.error:
                    pass
                stdscr.refresh()
                key = stdscr.get_wch()
                if key in ("\n", "\r", curses.KEY_ENTER):
                    return value.strip() or None
                if key == "\x1b":
                    return None
                if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    value = value[:-1]
                elif key == "\x15":
                    value = ""
                elif isinstance(key, str) and key.isprintable() and len(value) < max_length:
                    value += key
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def _details(stdscr, view: DashboardView) -> None:
        while True:
            height, width = stdscr.getmaxyx()
            canvas = compose_screen(view, width=width, height=height)
            info = [
                f"Active: {view.active_agent}   State: {view.state}",
                f"Report: {view.pending_path or view.report_path or 'not generated'}",
                f"SHA-256: {view.pending_sha or 'not pending'}",
                f"Integrity: {view.artifact_integrity or 'not awaiting approval'}",
            ]
            if view.last_error:
                info.append(f"Error: {view.last_error}")
            if view.prototype:
                info.extend([
                    f"Prototype iteration: {view.prototype['iteration_id']}",
                    f"Preview: {view.prototype['preview_path']}",
                    f"Review bundle: {view.prototype['bundle_sha']}",
                    f"Review integrity: {view.prototype['integrity']}",
                ])
            if view.ux_scope:
                info.extend([
                    f"UX companion: {view.ux_scope['path']}",
                    f"UX SHA-256: {view.ux_scope['sha']}",
                ])
            _overlay(canvas, "ARTIFACT / AUDIT DETAILS", info, "Press any key to return")
            _paint(stdscr, canvas)
            if stdscr.getch() != curses.KEY_RESIZE:
                return

    def _loop(stdscr) -> None:
        _set_colors()
        stdscr.keypad(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        scroll = 0
        expanded_history = False
        show_git = False
        show_project = False
        show_mvp = False
        message = "↑ / ↓ scroll report  •  All execution is simulated."
        while True:
            height, width = stdscr.getmaxyx()
            view = load_view(root, initiative, engine, history_limit=30 if expanded_history else 5)
            canvas = compose_screen(
                view, width=width, height=height, scroll=scroll,
                show_history=expanded_history, show_git=show_git,
                show_project=show_project, show_mvp=show_mvp, message=message,
            )
            scroll = max(0, min(scroll, canvas.max_scroll))
            _paint(stdscr, canvas)
            key = stdscr.getch()

            if key in (ord("q"), ord("Q")):
                return
            if height < 19 or width < 48:
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                scroll = min(scroll + 1, canvas.max_scroll)
                continue
            if key in (curses.KEY_UP, ord("k")):
                scroll = max(0, scroll - 1)
                continue
            if key == curses.KEY_NPAGE:
                scroll = min(scroll + max(1, height - 14), canvas.max_scroll)
                continue
            if key == curses.KEY_PPAGE:
                scroll = max(0, scroll - max(1, height - 14))
                continue
            if key == curses.KEY_HOME:
                scroll = 0
                continue
            if key == curses.KEY_END:
                scroll = canvas.max_scroll
                continue
            if key in (ord("h"), ord("H")):
                expanded_history = not expanded_history
                show_git = False
                show_project = False
                show_mvp = False
                scroll = 0
                message = "Viewing event history." if expanded_history else "Viewing latest report."
                continue
            if key in (ord("g"), ord("G")):
                show_git = not show_git
                expanded_history = False
                show_project = False
                show_mvp = False
                scroll = 0
                message = "Git worktrees and source identity." if show_git else "Viewing latest report."
                continue
            if key in (ord("p"), ord("P")):
                show_project = not show_project
                show_git = False
                expanded_history = False
                show_mvp = False
                scroll = 0
                message = "Local project ticket and integration branch." if show_project else "Viewing latest report."
                continue
            if key in (ord("v"), ord("V")):
                show_mvp = not show_mvp
                show_git = False
                show_project = False
                expanded_history = False
                scroll = 0
                message = "Per-MVP scope, EDD stage progress and UTC timestamps." if show_mvp else "Viewing latest report."
                continue
            try:
                if key in (ord("n"), ord("N"), 10, 13):
                    message = perform_action(root, initiative, engine, "run")
                elif key in (ord("a"), ord("A")) and view.state == "WAITING_FOR_HUMAN":
                    if not (view.artifact_integrity or "").startswith("UNCHANGED"):
                        message = "Artifact integrity check failed. Approval disabled."
                    elif view.git and (view.git["error"] or view.git.get("dirty")):
                        message = "Git worktree must be valid and clean before approving."
                    elif _confirm_dialog(stdscr, view, "CONFIRM APPROVAL", [
                        f"Approve {view.pending_gate}?",
                        "The next simulated agent will run automatically.",
                        f"Feature SHA-256: {(view.pending_sha or '')[:20]}…",
                        *([f"UX spec: {view.ux_scope['path']}",
                           f"UX SHA-256: {str(view.ux_scope['sha'])[:20]}…"] if view.ux_scope else []),
                    ]):
                        message = perform_action(root, initiative, engine, "approve")
                    else:
                        message = "Approval cancelled."
                elif key in (ord("c"), ord("C")) and view.state == "WAITING_FOR_HUMAN":
                    reason = _input_dialog(
                        stdscr, view, "REQUEST CHANGES", "What must the agent improve?",
                        max_length=3000 if view.pending_gate == "G2_PROTOTYPE" else 240,
                    )
                    message = (perform_action(root, initiative, engine, "changes", reason)
                               if reason else "Change request cancelled; reason required.")
                elif key in (ord("o"), ord("O")) and view.pending_gate == "G2_PROTOTYPE" and not show_project:
                    message = open_preview(root, view)
                elif key in (ord("r"), ord("R")) and view.state == "BLOCKED":
                    message = perform_action(root, initiative, engine, "retry")
                elif key in (ord("b"), ord("B")):
                    path = _input_dialog(
                        stdscr, view, "ATTACH REPOSITORY",
                        "Absolute local path to an existing Git checkout:",
                        max_length=512,
                    )
                    if path:
                        message = engine.attach_repo(root, initiative, Path(path))
                        show_git, expanded_history = True, False
                    else:
                        message = "Repository attachment cancelled."
                elif key in (ord("w"), ord("W")):
                    if not view.git:
                        message = "Attach a local Git checkout first: [G] then [B]."
                    else:
                        mvp = _input_dialog(
                            stdscr, view, "CREATE MVP WORKTREE",
                            "MVP ID (branch will be agentflow/<initiative>/<mvp>):",
                            initial="mvp-01", max_length=16,
                        )
                        if mvp:
                            main = Path(view.git["primary_root"])
                            destination = main.parent / f"{main.name}-agentflow-{initiative}-{mvp}"
                            if _confirm_dialog(stdscr, view, "CONFIRM GIT WORKTREE", [
                                f"Create branch agentflow/{initiative}/{mvp} from HEAD?",
                                f"New directory: {destination}",
                                "Existing branches and worktrees will not be overwritten.",
                            ]):
                                message = engine.create_worktree(root, initiative, mvp)
                                show_git, expanded_history = True, False
                            else:
                                message = "Worktree creation cancelled."
                        else:
                            message = "Worktree creation cancelled."
                elif key in (ord("s"), ord("S")):
                    if not view.git or view.git["error"]:
                        message = "Attach a valid repository before selecting worktrees."
                    else:
                        choice = _input_dialog(
                            stdscr, view, "SELECT WORKTREE",
                            "Registered worktree number (see [G] Git panel):",
                            initial="1", max_length=5,
                        )
                        if choice:
                            if not choice.isdecimal() or not (1 <= int(choice) <= len(view.git["worktrees"])):
                                message = "Invalid worktree number; open [G] to inspect the list."
                            else:
                                destination = Path(view.git["worktrees"][int(choice) - 1]["path"])
                                message = engine.select_worktree(root, initiative, destination)
                                show_git, expanded_history = True, False
                        else:
                            message = "Worktree selection cancelled."
                elif show_project and key in (ord("i"), ord("I")):
                    if not view.git:
                        message = "Attach a local repository first using [G] then [B]."
                    elif _confirm_dialog(stdscr, view, "CREATE INTEGRATION BRANCH", [
                        f"Create project integration branch agentflow/{initiative}/integration?",
                        "A new sibling Git worktree will be created without overwriting files.",
                        "GitHub is not required; this remains local until an explicit push.",
                    ]):
                        message = engine.create_integration(root, initiative)
                    else:
                        message = "Integration creation cancelled."
                elif show_project and key in (ord("j"), ord("J")):
                    branch = view.git.get("branch", "") if view.git else ""
                    prefix = f"agentflow/{initiative}/"
                    mvp = branch[len(prefix):] if branch.startswith(prefix) else ""
                    if not view.project or not mvp.startswith("mvp-"):
                        message = "Select an MVP worktree in [G] first. Integration must already exist."
                    elif _confirm_dialog(stdscr, view, "PROMOTE MVP TO INTEGRATION", [
                        f"Fast-forward integration to {branch}?",
                        "Requires committed, clean worktrees and compatible Git history.",
                        "This changes only the local integration branch; it does not push.",
                    ]):
                        message = engine.promote_mvp(
                            root, initiative, mvp=mvp, source=Path(view.git["selected_root"]),
                        )
                    else:
                        message = "MVP promotion cancelled."
                elif show_project and key in (ord("f"), ord("F")):
                    if not view.project:
                        message = "Create the integration branch first using [I]."
                    elif _confirm_dialog(stdscr, view, "SNAPSHOT LOCAL REPORTS", [
                        "Copy local project/MVP tickets, plans, specs and reports to integration docs?",
                        "A local Git commit will be made. Review these documents for secrets",
                        "and private information before using [O] to push them.",
                    ]):
                        message = engine.snapshot_project_artifacts(root, initiative)
                    else:
                        message = "Artifact snapshot cancelled."
                elif show_project and key in (ord("o"), ord("O")):
                    if not view.project:
                        message = "Create an integration branch first."
                    elif _confirm_dialog(stdscr, view, "PUSH INTEGRATION TO ORIGIN", [
                        f"Push {view.project['integration_branch']} to Git remote origin?",
                        "This publishes local commits. No force push will be attempted.",
                        "The origin must be configured and you must have push permission.",
                    ]):
                        message = engine.push_project(root, initiative)
                    else:
                        message = "Integration push cancelled."
                elif show_project and key in (ord("t"), ord("T")):
                    if not view.project:
                        message = "Create a local integration branch before enabling the optional GitHub mirror."
                    else:
                        origin = view.project.get("origin", "")
                        default_repo = origin.removeprefix("github.com/") if origin.startswith("github.com/") else ""
                        repo = _input_dialog(
                            stdscr, view, "OPTIONAL GITHUB PARENT",
                            "GitHub OWNER/REPO matching the configured origin:",
                            initial=default_repo, max_length=180,
                        )
                        if repo and _confirm_dialog(stdscr, view, "CREATE GITHUB ISSUE", [
                            f"Create one parent issue in {repo} for {initiative}?",
                            "External publication is optional; local tickets remain authoritative.",
                        ]):
                            message = engine.create_parent_ticket(root, initiative, repo=repo)
                        else:
                            message = "GitHub parent creation cancelled."
                elif show_project and key in (ord("m"), ord("M")):
                    if not view.project or not view.project["parent_issue"]:
                        message = "Link the optional GitHub parent first using [T]."
                    else:
                        mvp = _input_dialog(
                            stdscr, view, "OPTIONAL MVP SUB-ISSUE",
                            "MVP ID with an existing generated spec (for example mvp-01):",
                            initial="mvp-01", max_length=16,
                        )
                        if mvp and _confirm_dialog(stdscr, view, "CREATE GITHUB SUB-ISSUE", [
                            f"Create native GitHub sub-issue for {mvp}?",
                            "The local MVP ticket will remain the authoritative record.",
                        ]):
                            message = engine.create_mvp_ticket(root, initiative, mvp=mvp)
                        else:
                            message = "GitHub sub-issue creation cancelled."
                elif show_project and key in (ord("u"), ord("U")):
                    if not view.project or not view.project["parent_issue"]:
                        message = "The optional GitHub parent is not configured."
                    elif _confirm_dialog(stdscr, view, "PUBLISH GITHUB PROGRESS", [
                        "Post a progress summary to the parent and linked MVP sub-issues?",
                        "Only observed status, paths and hashes are published, not raw report bodies.",
                        "Mock progress is explicitly labeled SIMULATION ONLY.",
                    ]):
                        message = engine.sync_project_tickets(root, initiative)
                    else:
                        message = "Progress publication cancelled."
                elif key in (ord("d"), ord("D")):
                    _details(stdscr, view)
                elif key == curses.KEY_RESIZE:
                    continue
                else:
                    message = "Choose an action from the bottom bar; ↑/↓ scrolls the report."
            except (ValueError, OSError, sqlite3.Error) as error:
                message = f"ERROR: {error}"
            scroll = 0

    curses.wrapper(_loop)
    return 0
