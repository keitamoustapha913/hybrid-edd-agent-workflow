"""One-command, local-first interactive front end for the real Codex Coordinator.

All decisions still use live_coordinator's hash/source checks. The terminal
interface never fabricates approvals or turns a model report into evidence.
Evidence commands are configured once and confirmed in the interface, instead
of being passed as a long list of CLI flags for each verification run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import textwrap
import uuid
import webbrowser
from threading import Thread

from . import agentflow, git_adapter, git_agent, live_coordinator, live_delivery, live_evidence, live_prototype


PLAN_NAME = "evidence-plan.json"
PLAN_LIMIT = 20_000
MAX_REPORT = 200_000
AUTO_TABLE = "live_console_preferences"


def auto_enabled(root: Path, initiative: str) -> bool:
    """Read a project-local preference without migrating a read-only dashboard."""
    with agentflow.open_db(root, initiative) as db:
        return _auto_enabled(db)


def _auto_enabled(db) -> bool:
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (AUTO_TABLE,)
    ).fetchone():
        return True
    row = db.execute(f"SELECT auto_continue FROM {AUTO_TABLE} WHERE singleton=1").fetchone()
    return bool(row[0]) if row else True


def toggle_auto(root: Path, initiative: str) -> bool:
    """Persist manual mode without authorizing any real Codex session."""
    with agentflow.open_db(root, initiative) as db:
        db.execute("BEGIN IMMEDIATE")
        enabled = not _auto_enabled(db)
        db.execute(
            f"CREATE TABLE IF NOT EXISTS {AUTO_TABLE}("
            "singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
            "auto_continue INTEGER NOT NULL CHECK(auto_continue IN (0,1)))"
        )
        db.execute(
            f"INSERT INTO {AUTO_TABLE}(singleton,auto_continue) VALUES(1,?) "
            "ON CONFLICT(singleton) DO UPDATE SET auto_continue=excluded.auto_continue",
            (int(enabled),),
        )
        live_coordinator._event(db, "AUTO_CONTINUATION_PREFERENCE", {"enabled": enabled})
    live_coordinator.refresh_tickets(root, initiative)
    return enabled


def _dispatch(root: Path, initiative: str, say, *, trigger: str,
              codex_bin: str, model: str | None, timeout: int,
              automatic: bool) -> None:
    """Dispatch only an explicitly authorized stretch, up to the next barrier."""
    with agentflow.open_db(root, initiative) as db:
        state = live_coordinator._state(db)
        if state["status"] != "READY":
            return
        if automatic:
            db.execute("BEGIN IMMEDIATE")
            state = live_coordinator._state(db)
            if state["status"] != "READY":
                return
            live_coordinator._event(db, "AUTO_CONTINUATION_AUTHORIZED", {
                "trigger": trigger,
                "mvp_id": live_coordinator._active_mvp(state),
                "stage_index": state["stage_index"],
                "stage": live_coordinator._stage_for_target(
                    state["stage_index"], live_coordinator._active_mvp(state)
                ).name,
                "authority": "human-confirmed terminal action",
            })
    say(("Auto-continuing" if automatic else "Running") +
        " eligible Codex specialists until the next human/evidence gate...")
    for line in live_coordinator.run(
        root, initiative, codex_bin=codex_bin, model=model, timeout=timeout,
    ):
        say(line)


def _after_human_action(root: Path, initiative: str, say, *, trigger: str,
                        codex_bin: str, model: str | None, timeout: int) -> None:
    if auto_enabled(root, initiative):
        _dispatch(root, initiative, say, trigger=trigger, codex_bin=codex_bin,
                  model=model, timeout=timeout, automatic=True)


def _choose(root: Path, initiative: str | None, ask, say) -> str:
    if initiative is not None:
        agentflow.check_id(initiative)
        return initiative
    projects = []
    directory = root / "work"
    if directory.is_dir():
        projects = sorted(
            path.name for path in directory.iterdir()
            if path.is_dir() and not path.is_symlink()
            and agentflow.IDENTIFIER.fullmatch(path.name)
            and (path / "agentflow.sqlite3").is_file()
        )
    if len(projects) == 1:
        return projects[0]
    say("Projects: " + (", ".join(projects) if projects else "none yet"))
    selected = ask("Project name: ").strip()
    agentflow.check_id(selected)
    return selected


def _configured_project(root: Path, initiative: str, ask, say) -> None:
    if not agentflow.database_path(root, initiative).is_file():
        say(agentflow.initialize(root, initiative))
    with agentflow.open_db(root, initiative) as db:
        present = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_workflow'"
        ).fetchone()
        initialized = (db.execute("SELECT 1 FROM live_workflow WHERE singleton=1").fetchone()
                       if present else None)
    if initialized:
        return
    binding = agentflow.git_context(root, initiative)
    if not binding:
        repo = ask("Existing clean Git checkout path (required for live Codex): ").strip()
        if not repo:
            raise ValueError("live workflow needs a selected Git checkout; none was supplied")
        say(agentflow.attach_repo(root, initiative, Path(repo)))
    say(live_coordinator.initialize(root, initiative))


def _plan_path(root: Path, initiative: str) -> Path:
    mvp = "mvp-01"
    with agentflow.open_db(root, initiative) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='live_workflow'").fetchone():
            state = db.execute("SELECT * FROM live_workflow WHERE singleton=1").fetchone()
            if state:
                mvp = live_coordinator._active_mvp(state)
    relative = (f"work/{initiative}/{PLAN_NAME}" if mvp == "mvp-01" else
                f"work/{initiative}/mvps/{mvp}/{PLAN_NAME}")
    return live_coordinator._file(root, relative)


def _check_plan(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("evidence plan must be a schema_version=1 JSON object")
    commands = [value.get("tests"), value.get("ux_checks"), value.get("ux_artifacts")]
    if any(not isinstance(items, list) or not items or any(not isinstance(x, str) for x in items)
           for items in commands):
        raise ValueError("evidence plan requires nonempty test, UX check and screenshot lists")
    if len(commands[0]) + len(commands[1]) > live_evidence.MAX_CHECKS:
        raise ValueError("evidence plan has too many test/UX commands")
    if len(commands[2]) > live_evidence.MAX_IMAGE_COUNT or len(set(commands[2])) != len(commands[2]):
        raise ValueError("evidence plan has duplicate or excessive screenshot paths")
    for command in commands[0] + commands[1]:
        live_evidence._argv(command)
    for relative in commands[2]:
        live_evidence._relative(Path("/tmp/evidence-plan-preview"), relative)
        if Path(relative).suffix.lower() not in live_evidence.IMAGE_EXTENSIONS:
            raise ValueError("evidence plan screenshot must be PNG, JPEG or WebP")
    timeout = value.get("timeout", 120)
    if type(timeout) is not int or not 1 <= timeout <= live_evidence.MAX_SECONDS:
        raise ValueError("evidence plan timeout must be between 1 and 300 seconds")
    return {"schema_version": 1, "tests": commands[0], "ux_checks": commands[1],
            "ux_artifacts": commands[2], "timeout": timeout}


def load_plan(root: Path, initiative: str) -> dict | None:
    path = _plan_path(root, initiative)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > PLAN_LIMIT:
        raise ValueError("saved evidence plan is unsafe or oversized")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("evidence plan is not valid UTF-8 JSON") from error
    return _check_plan(value)


def configure_plan(root: Path, initiative: str, ask, say) -> dict | None:
    """Prompt once, then persist a human-entered plan without Codex authority."""
    path = _plan_path(root, initiative)
    before = path.read_bytes() if path.is_file() and not path.is_symlink() else None
    current = load_plan(root, initiative)
    if current:
        say("Current verification plan:")
        describe_plan(current, say)
        if ask("Replace this saved plan? [y/N]: ").strip().lower() not in {"y", "yes"}:
            return current
    say("Configure once for this project. Use trusted commands already available in its Git checkout.")
    say("You will review these exact commands before every execution.")
    test = ask("Functional test command (e.g. uv run --offline pytest): ").strip()
    ux = ask("UX capture command (runs your app/journey and saves an image): ").strip()
    image = ask("Screenshot output path relative to checkout (e.g. captures/mobile.png): ").strip()
    if not test or not ux or not image:
        say("Evidence configuration cancelled; no incomplete plan saved.")
        return None
    plan = _check_plan({
        "schema_version": 1, "tests": [test], "ux_checks": [ux],
        "ux_artifacts": [image], "timeout": 120,
    })
    describe_plan(plan, say)
    if ask("Save this local evidence plan? [y/N]: ").strip().lower() not in {"y", "yes"}:
        say("Plan not saved.")
        return None
    if path.is_symlink() or (path.read_bytes() if path.is_file() else None) != before:
        raise ValueError("evidence plan changed while being edited; refusing to overwrite")
    payload = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temp = path.with_name(".evidence-plan-" + uuid.uuid4().hex + ".tmp")
    with temp.open("xb") as destination:
        destination.write(payload)
    os.replace(temp, path)
    say(f"Saved local evidence configuration: {path.relative_to(root)}")
    return plan


def describe_plan(plan: dict, say) -> None:
    for command in plan["tests"]:
        say("  Functional test: " + command)
    for command in plan["ux_checks"]:
        say("  UI/UX capture:   " + command)
    for file in plan["ux_artifacts"]:
        say("  Expected image:  " + file)
    say(f"  Timeout: {plan['timeout']}s per command")


def _report(root: Path, initiative: str, state: dict) -> tuple[str, str]:
    path = state.get("pending_path")
    if not path:
        index = state["stage_index"]
        mvp = state.get("active_mvp", "mvp-01")
        for i in reversed(range(min(index + 1, len(agentflow.STAGES)))):
            stage = live_coordinator._stage_for_target(i, mvp)
            if stage.report:
                candidate = stage.report.format(id=initiative)
                if live_coordinator._file(root, candidate).is_file():
                    path = candidate
                    break
    if not path:
        return "No report yet", "The next specialist has not produced a report."
    file = live_coordinator._file(root, path)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_REPORT:
        return path, "Report unavailable, missing, symlinked or oversized."
    try:
        return path, file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return path, "Report could not be read."


def snapshot(root: Path, initiative: str) -> str:
    """Read-only live status view; never uses the simulation's state table."""
    with agentflow.open_db(root, initiative) as db:
        state = dict(live_coordinator._state(db))
        mvp = state.get("active_mvp", "mvp-01")
        approvals = [row[0] for row in db.execute(
            "SELECT gate FROM live_approvals WHERE revoked_at IS NULL ORDER BY id"
        )]
        completions = ([row[0] for row in db.execute(
            "SELECT mvp_id FROM live_mvp_completions ORDER BY mvp_id"
        )] if db.execute("SELECT 1 FROM sqlite_master WHERE name='live_mvp_completions'").fetchone() else [])
        evidence = live_evidence.latest(db)
        automatic = _auto_enabled(db)
    index = state["stage_index"]
    active_stage = live_coordinator._stage_for_target(index, mvp)
    stage = active_stage.name.upper() if active_stage else "DONE"
    owner = ("HUMAN REVIEW" if state["status"] == "WAITING_FOR_HUMAN" else
             "EVIDENCE COLLECTION" if state["status"] == live_coordinator.EVIDENCE_BARRIER else
             stage)
    report_path, report = _report(root, initiative, state)
    preview = "\n".join((textwrap.shorten(line, width=104, placeholder=" …")
                          for line in (report.splitlines() or ["[empty report]"])[:8]))
    bar = "■" * min(index, len(agentflow.STAGES)) + "·" * max(len(agentflow.STAGES) - index, 0)
    width = 78

    def frame(content: str) -> str:
        # Do not let a long local branch, agent ID, or list of gates destroy
        # the fixed-width panel; detailed values remain available in [S].
        if len(content) > width - 2:
            content = content[:width - 4] + "…"
        return "│ " + content.ljust(width - 2) + " │"

    def phase_line(i: int) -> str:
        marker = "✓" if i < index or state["status"] == "COMPLETED" else (
            "▶" if i == index else "·"
        )
        step = live_coordinator._stage_for_target(i, mvp)
        return f"{marker} {step.name.upper().replace('_', ' ')}"

    plan_status = "configured" if _plan_path(root, initiative).is_file() else "not configured"
    project = agentflow.project_context(root, initiative)
    delivery = live_delivery.delivery(root, initiative, mvp)
    lines = [
        "╭" + "─" * width + "╮",
        frame("HYBRID → EDD  ·  LIVE COORDINATOR  ·  LOCAL FIRST"),
        frame(""),
        frame(f"Project       {initiative}"),
        frame(f"Active MVP    {mvp.upper()}"),
        frame(f"Status        {state['status']}"),
        frame(f"Auto-continue {'ON' if automatic else 'OFF'} · after confirmed human action only"),
        frame(f"Active        {owner}"),
        frame(f"Progress      {index}/{len(agentflow.STAGES)}  {bar}"),
        frame(f"Source        {state['expected_branch']} @ {state['expected_head'][:12]}"),
        frame(f"Human gate    {state['pending_gate'] or 'none'}"),
        frame(f"Approved      {', '.join(approvals) or 'none'}"),
        frame(f"Completed MVPs {', '.join(completions) or 'none'}"),
        frame(f"Evidence      {evidence['status'] if evidence else 'not collected'}"),
        frame(f"Evidence plan {plan_status}"),
        frame(f"Integration   {project['integration_branch'] if project else 'not configured · [I] setup'}"),
        frame(f"Git delivery  {delivery['status'] if delivery else 'not started'}"),
        frame(""),
        frame("PROJECT · DISCOVERY → PROTOTYPE → SPECIFICATION → MVP DESIGN"),
    ]
    for first in range(0, 5, 2):
        pair = phase_line(first).ljust(35)
        if first + 1 < 5:
            pair += phase_line(first + 1)
        lines.append(frame("  " + pair))
    lines.append(frame(f"{mvp.upper()} · UX DESIGN → REQUIREMENTS → RED → GREEN → REFACTOR → VERIFY"))
    for first in range(5, len(agentflow.STAGES), 2):
        pair = phase_line(first).ljust(35)
        if first + 1 < len(agentflow.STAGES):
            pair += phase_line(first + 1)
        lines.append(frame("  " + pair))
    lines += [
        "╰" + "─" * width + "╯",
        "", "Current report: " + report_path,
        preview, "",
    ]
    if state["last_error"]:
        lines.append("Note: " + state["last_error"])
    if state["status"] == live_coordinator.EVIDENCE_BARRIER:
        lines.append("Next: Review configured checks and collect host evidence.")
    elif state["status"] == "WAITING_FOR_HUMAN":
        lines.append("Next: Review this report; approve the bound version or request changes.")
    elif state["status"] == "READY":
        lines.append("Next: Launch the eligible specialist(s) when authorized.")
        if state["stage_index"] == 0 and not project:
            lines.append("Tip: [I] Git delivery creates integration and a dedicated MVP worktree before Codex runs.")
    elif state["status"] == "COMPLETED":
        following = live_coordinator.available_next(root, initiative)
        lines.append(f"{mvp.upper()} completed locally. No Git push or deployment was triggered.")
        if project and not (delivery and delivery["status"] in live_delivery.PUBLISHED):
            lines.append("Next: [I] Git delivery · commit reviewed code → promote → snapshot → optional push.")
        if following:
            lines.append(f"Next: schedule {following.upper()} from the approved roadmap with [M].")
    lines.append("[I] Git delivery is available before the first Codex run and after human G7.")
    lines.append("[T] Toggle automatic continuation; reopening the console never starts agents by itself.")
    return "\n".join(lines)


def _approve(root: Path, initiative: str, state: dict, ask, say) -> bool:
    gate = state["pending_gate"]
    bundle = None
    notes = ""
    if gate == "G2_PROTOTYPE":
        bundle = live_prototype.describe(root, initiative)["bundle_sha"]
        notes = ask("What did you actually observe in the browser? (required): ").strip()
    elif gate in {"G6_UX_INSPECTION", "G7_PROVE"}:
        with agentflow.open_db(root, initiative) as db:
            bundle = live_evidence.validate(db, root, initiative)["bundle_sha"]
        notes = ask("What evidence and limitations did you personally review? (required): ").strip()
    if gate in {"G2_PROTOTYPE", "G6_UX_INSPECTION", "G7_PROVE"} and not notes:
        say("No approval recorded: review observations are required.")
        return False
    if ask(f"Approve {gate} for the displayed report/source and resume eligible agents if Auto is ON? [y/N]: ").strip().lower() not in {"y", "yes"}:
        say("Approval cancelled.")
        return False
    say(live_coordinator.approve(
        root, initiative, gate, state["pending_sha"],
        bundle_sha=bundle, review_notes=notes,
    ))
    return True


def _changes(root: Path, initiative: str, state: dict, ask, say) -> bool:
    reason = ask("What needs to change? (saved feedback; Auto ON reruns the next stage): ").strip()
    if not reason:
        say("No change request recorded.")
        return False
    target = None
    if state["pending_gate"] in {"G6_UX_INSPECTION", "G7_PROVE"} or (
        state["status"] == "BLOCKED" and state["stage_index"] == 11
    ):
        options = ("green / evidence" if state["status"] == "BLOCKED" else
                   "green / evidence / ux_inspect" + (" / prove" if state["pending_gate"] == "G7_PROVE" else ""))
        target = ask(f"Return to [{options}]: ").strip().lower()
        if not target:
            say("Choose a concrete return stage; no change recorded.")
            return False
    say(live_coordinator.changes(root, initiative, reason, target_stage=target))
    return True


class _Preview:
    def __init__(self):
        self.server = None
        self.thread = None
        self.url = None

    def open(self, root: Path, initiative: str, say) -> None:
        info = live_prototype.describe(root, initiative)
        if info["status"] != "READY":
            raise ValueError("the current G2 preview is not awaiting review")
        if self.server:
            self.stop()
        html = live_coordinator._file(root, info["preview_path"]).read_bytes()
        self.server = live_prototype.make_server(html, info["preview_sha"])
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        say("Frozen prototype: " + self.url)
        say("Browser review is manual; press A only after trying the experiment.")
        if not webbrowser.open(self.url):
            say("No browser handler opened. Open the URL on this same computer.")

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.thread = None
            self.url = None


def _integration_menu(root: Path, initiative: str, ask, say, *,
                      codex_bin: str = "codex", model: str | None = None,
                      timeout: int = 900) -> None:
    """One place to inspect local Git handoffs and approve each mutation."""

    def approved_for_review(action: str) -> dict | None:
        say(f"Git Agent: inspecting {action.lower()} in a separate read-only Codex session...")
        result = git_agent.review(root, initiative, action=action,
                                  codex_bin=codex_bin, model=model,
                                  timeout=min(timeout, 180))
        say("Git Agent report: " + result["summary"])
        for risk in result["risks"]:
            say("  Risk: " + risk)
        for check in result["checks"]:
            say("  Check: " + check)
        say("Local review: " + result["run_dir"])
        if result["decision"] != "READY":
            say("Git Agent blocked this action; no human confirmation or Git mutation was requested.")
            return None
        return result

    while True:
        with agentflow.open_db(root, initiative) as db:
            state = dict(live_coordinator._state(db))
        mvp = state.get("active_mvp", "mvp-01")
        receipt = live_delivery.delivery(root, initiative, mvp)
        say("\nGIT DELIVERY · " + mvp.upper())
        say(live_delivery.status(root, initiative))
        if state["status"] == "READY" and state["stage_index"] == 0:
            say("[1] Setup integration + MVP worktree  [B] Back")
        elif state["status"] == "COMPLETED":
            say("[2] Commit G7-approved source  [3] Promote to integration")
            say("[4] Snapshot local reports  [5] Push integration (optional)")
            say("[6] Reconcile uncertain commit  [B] Back")
        else:
            say("Delivery awaits a completed G7-approved MVP. [B] Back")
        choice = ask("Git action: ").strip().lower()
        if choice in {"b", "back", "q", "quit"}:
            return
        try:
            if choice == "1" and state["status"] == "READY" and state["stage_index"] == 0:
                say("Creates local agentflow/<project>/integration and a dedicated MVP-01 worktree.")
                review = approved_for_review("PREPARE")
                if review is None:
                    continue
                if ask("Create these Git branches/worktrees? [y/N]: ").strip().lower() in {"y", "yes"}:
                    git_agent.assert_fresh(root, initiative, review)
                    say(live_delivery.prepare(root, initiative))
            elif choice == "2" and state["status"] == "COMPLETED":
                if receipt and receipt["status"] not in {"RETRYABLE"}:
                    say("Existing delivery receipt must be reconciled/completed first.")
                    continue
                context = agentflow.git_context(root, initiative)
                selected = git_adapter.inspect(Path(context["selected_root"]))
                say(f"Reviewed source: {selected.branch} @ {selected.head[:12]}")
                say("Changes that would be committed (includes untracked non-ignored files):")
                changes = git_adapter._git(selected.root, "status", "--short", "--untracked-files=normal").stdout
                say(changes[:8000] if changes else "(none; the reviewed HEAD is already clean)")
                say("Inspect filenames/content for private data. No hooks will be bypassed.")
                review = approved_for_review("COMMIT")
                if review is None:
                    continue
                if ask("Commit exactly the G7-approved source on its MVP branch? [y/N]: ").strip().lower() in {"y", "yes"}:
                    git_agent.assert_fresh(root, initiative, review)
                    say(live_delivery.commit(root, initiative))
            elif choice == "3" and state["status"] == "COMPLETED":
                review = approved_for_review("PROMOTE")
                if review is None:
                    continue
                if ask(f"Fast-forward the integration worktree to approved {mvp.upper()}? [y/N]: ").strip().lower() in {"y", "yes"}:
                    git_agent.assert_fresh(root, initiative, review)
                    say(live_delivery.promote(root, initiative))
            elif choice == "4" and state["status"] == "COMPLETED":
                say("This copies local Markdown plans, specs, reports and tickets into a local Git commit.")
                say("Review these documents for secrets before authorizing publication.")
                review = approved_for_review("SNAPSHOT")
                if review is None:
                    continue
                if ask("Commit the local project documentation to integration? [y/N]: ").strip().lower() in {"y", "yes"}:
                    git_agent.assert_fresh(root, initiative, review)
                    say(live_delivery.snapshot(root, initiative))
            elif choice == "5" and state["status"] == "COMPLETED":
                project = agentflow.project_context(root, initiative)
                say(f"Remote: origin · branch: {project['integration_branch'] if project else 'unconfigured'}")
                say("This is an external network write; it uses a non-force push only.")
                review = approved_for_review("PUSH")
                if review is None:
                    continue
                if ask("PUSH the reviewed integration revision to origin? [y/N]: ").strip().lower() in {"y", "yes"}:
                    git_agent.assert_fresh(root, initiative, review)
                    say(live_delivery.push(root, initiative))
            elif choice == "6" and state["status"] == "COMPLETED":
                if ask("Inspect/reconcile an uncertain source commit? [y/N]: ").strip().lower() in {"y", "yes"}:
                    say(live_delivery.reconcile_commit(root, initiative))
            else:
                say("Choose an available, authorized Git action.")
        except (ValueError, OSError) as error:
            say("Blocked: " + str(error))


def launch(root: Path, initiative: str | None = None, *, ask=None, say=None,
           codex_bin: str = "codex", model: str | None = None,
           timeout: int = 900, check_only: bool = False) -> int:
    ask = ask or input
    say = say or print
    root = root.resolve(strict=True)
    try:
        name = _choose(root, initiative, ask, say)
        if check_only:
            say(snapshot(root, name))
            return 0
        _configured_project(root, name, ask, say)
        preview = _Preview()
        try:
            while True:
                with agentflow.open_db(root, name) as db:
                    state = dict(live_coordinator._state(db))
                say("\n" + snapshot(root, name))
                status = state["status"]
                if status == "COMPLETED":
                    following = live_coordinator.available_next(root, name)
                    options = ("[I] Git delivery  [M] Next MVP  [V] Report  [S] Status  [Q] Quit" if following else
                               "[I] Git delivery  [V] Report  [S] Status  [Q] Quit")
                elif status == live_coordinator.EVIDENCE_BARRIER:
                    options = "[E] Collect evidence  [F] Configure checks  [V] Report  [Q] Quit"
                elif status == "WAITING_FOR_HUMAN":
                    options = "[A] Approve  [C] Changes  [V] Report  [S] Status  [Q] Quit"
                    if state["pending_gate"] == "G2_PROTOTYPE":
                        options = "[O] Open preview  " + options
                elif status == "READY":
                    options = ("[I] Git delivery  " if state["stage_index"] == 0 else "") + "[N] Run next agents  [V] Report  [S] Status  [Q] Quit"
                elif status == "BLOCKED":
                    options = ("[R] Retry  [C] Fix UX  [V] Report  [Q] Quit"
                               if state["stage_index"] == 11 else
                               "[R] Retry  [V] Report  [S] Status  [Q] Quit")
                else:
                    options = "[R] Reconcile  [S] Status  [Q] Quit"
                say(options)
                say(f"[T] Auto-continue {'ON' if auto_enabled(root, name) else 'OFF'} (toggle)")
                choice = ask("Choose: ").strip().lower()
                if choice in {"q", "quit", "exit"}:
                    break
                try:
                    if choice == "t":
                        enabled = toggle_auto(root, name)
                        say(f"Auto-continuation {'ON' if enabled else 'OFF'}. "
                            "Only an explicit human action can authorize the next Codex stretch.")
                    elif choice == "v":
                        say(_report(root, name, state)[1])
                    elif choice == "s":
                        say(live_coordinator.status(root, name))
                    elif choice == "i" and (status == "COMPLETED" or
                                             (status == "READY" and state["stage_index"] == 0)):
                        _integration_menu(root, name, ask, say, codex_bin=codex_bin,
                                          model=model, timeout=timeout)
                    elif choice == "o" and state["pending_gate"] == "G2_PROTOTYPE":
                        preview.open(root, name, say)
                    elif choice == "a" and status == "WAITING_FOR_HUMAN":
                        if _approve(root, name, state, ask, say):
                            _after_human_action(root, name, say, trigger="GATE_APPROVED",
                                                codex_bin=codex_bin, model=model, timeout=timeout)
                    elif choice == "c" and (status == "WAITING_FOR_HUMAN" or (
                        status == "BLOCKED" and state["stage_index"] == 11
                    )):
                        if _changes(root, name, state, ask, say):
                            _after_human_action(root, name, say, trigger="CHANGES_REQUESTED",
                                                codex_bin=codex_bin, model=model, timeout=timeout)
                    elif choice == "f" and status == live_coordinator.EVIDENCE_BARRIER:
                        configure_plan(root, name, ask, say)
                    elif choice == "e" and status == live_coordinator.EVIDENCE_BARRIER:
                        plan = load_plan(root, name) or configure_plan(root, name, ask, say)
                        if plan is None:
                            continue
                        say("Host will run these trusted commands against a disposable source copy:")
                        describe_plan(plan, say)
                        if ask("Run these exact checks now? (Auto ON resumes agents only after PASS) [y/N]: ").strip().lower() in {"y", "yes"}:
                            say(live_evidence.collect(
                                root, name, tests=plan["tests"], ux_checks=plan["ux_checks"],
                                ux_artifacts=plan["ux_artifacts"], timeout=plan["timeout"],
                            ))
                            _after_human_action(root, name, say, trigger="HOST_EVIDENCE_PASS",
                                                codex_bin=codex_bin, model=model, timeout=timeout)
                    elif choice == "m" and status == "COMPLETED" and following:
                        if ask(f"Schedule {following.upper()} from the approved roadmap and run to G5 if Auto ON? [y/N]: ").strip().lower() in {"y", "yes"}:
                            say(live_coordinator.schedule_next(root, name, mvp_id=following))
                            _after_human_action(root, name, say, trigger="NEXT_MVP_SCHEDULED",
                                                codex_bin=codex_bin, model=model, timeout=timeout)
                    elif choice == "n" and status == "READY":
                        if ask("Authorize the next real Codex sessions? [y/N]: ").strip().lower() in {"y", "yes"}:
                            _dispatch(root, name, say, trigger="EXPLICIT_RUN",
                                      codex_bin=codex_bin, model=model, timeout=timeout,
                                      automatic=False)
                    elif choice == "r" and status == "BLOCKED":
                        say(live_coordinator.retry(root, name))
                        _after_human_action(root, name, say, trigger="HUMAN_RETRY",
                                            codex_bin=codex_bin, model=model, timeout=timeout)
                    elif choice == "r" and status in {"RUNNING", "UNCERTAIN"}:
                        say(live_coordinator.reconcile(root, name))
                    else:
                        say("Choose one of the available actions above.")
                except (ValueError, OSError) as error:
                    say("Blocked: " + str(error))
        finally:
            preview.stop()
        return 0
    except (EOFError, KeyboardInterrupt):
        say("\nLive session closed. Project state and reports were preserved.")
        return 130
