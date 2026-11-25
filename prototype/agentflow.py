#!/usr/bin/env python3
"""Hybrid + EDD commands: simulation, live Codex Coordinator and host evidence.

The default dashboard simulates new initiatives; existing live projects resume
the real guided console. Use `uv run workflow.py live <id>` for real Codex
without repeatedly entering hash, test, or screenshot flags.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import sys
import uuid

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from . import git_adapter, github_adapter, local_tickets, mvp_progress, project_artifacts, prototype_review
    from scripts.validate_handoff import validate
else:
    # Retain direct-script execution for existing installations and tests.
    import git_adapter
    import github_adapter
    import local_tickets
    import mvp_progress
    import project_artifacts
    import prototype_review

    sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))
    from validate_handoff import validate  # noqa: E402

SIMULATED_MARKER = "<!-- AGENTFLOW_SIMULATED_OUTPUT -->"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


@dataclass(frozen=True)
class Stage:
    name: str
    role: str | None
    status: str | None
    report: str | None
    gate: str | None = None


STAGES = [
    Stage("discovery", "DISCOVERY", "READY_FOR_REVIEW", "work/{id}/discovery.md", "G1_DISCOVERY"),
    Stage("prototype", "PROTOTYPE", "READY_FOR_REVIEW", "work/{id}/prototype-report.md", "G2_PROTOTYPE"),
    Stage("specification", "SPECIFICATION", "READY_FOR_REVIEW", "work/{id}/product-spec.md", "G3_PRODUCT_SPEC"),
    Stage("mvp_design", "MVP_DESIGN", "READY_FOR_REVIEW", "work/{id}/mvp-roadmap.md", "G4_ROADMAP"),
    Stage("mvp_scope", None, None, "work/{id}/mvps/mvp-01/spec.md", "G5_MVP_01"),
    Stage("ux_design", "UIUX", "DESIGN_READY", "work/{id}/mvps/mvp-01/ux/design-review.md"),
    Stage("requirements", "REQUIREMENTS", "READY", "work/{id}/mvps/mvp-01/edd/requirements.md"),
    Stage("red", "RED", "RED_CONFIRMED", "work/{id}/mvps/mvp-01/edd/red.md"),
    Stage("green", "GREEN", "GREEN_CONFIRMED", "work/{id}/mvps/mvp-01/edd/green.md"),
    Stage("refactor", "REFACTOR", "NO_REFACTOR_NEEDED", "work/{id}/mvps/mvp-01/edd/refactor.md"),
    Stage("verify", "VERIFY", "VERIFIED", "work/{id}/mvps/mvp-01/edd/verify.md"),
    Stage("ux_inspect", "UIUX", "INSPECTION_RECORDED", "work/{id}/mvps/mvp-01/ux/inspection.md"),
    Stage("prove", "PROVE", "PASS", "work/{id}/mvps/mvp-01/edd/prove.md"),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_id(initiative: str) -> None:
    if not IDENTIFIER.fullmatch(initiative):
        raise ValueError("initiative ID must be 2–41 lowercase letters, digits, or hyphens, starting with a letter")


def database_path(root: Path, initiative: str) -> Path:
    check_id(initiative)
    candidate = root / "work" / initiative / "agentflow.sqlite3"
    resolved_parent = candidate.parent.resolve(strict=False)
    resolved_root = root.resolve()
    if resolved_root not in resolved_parent.parents:
        raise ValueError("initiative path escapes the approved workspace root")
    return candidate


def open_db(root: Path, initiative: str) -> sqlite3.Connection:
    path = database_path(root, initiative)
    if not path.is_file():
        raise ValueError(f"unknown initiative: {initiative}; run init first")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def record(conn: sqlite3.Connection, kind: str, detail: dict) -> None:
    conn.execute(
        "INSERT INTO events (created_at, kind, detail) VALUES (?, ?, ?)",
        (utcnow(), kind, json.dumps(detail, sort_keys=True)),
    )


def current(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM workflow WHERE singleton = 1").fetchone()
    if row is None:
        raise ValueError("uninitialized workflow state")
    return row


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_mock_write(root: Path, path: Path, content: str) -> None:
    resolved_root = root.resolve()
    resolved_target = path.resolve(strict=False)
    if resolved_root not in resolved_target.parents:
        raise ValueError("refusing to write outside the approved workspace root")
    if path.exists() and not path.read_text(encoding="utf-8").startswith(SIMULATED_MARKER):
        raise ValueError(f"refusing to overwrite non-simulated file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SIMULATED_MARKER + "\n" + content, encoding="utf-8")


def initialize(root: Path, initiative: str) -> str:
    root = root.resolve()
    path = database_path(root, initiative)
    if path.exists():
        raise ValueError(f"initiative already initialized: {initiative}")
    if path.parent.exists() and any(path.parent.iterdir()):
        raise ValueError("initiative directory contains existing files; choose another ID")
    path.parent.mkdir(parents=True, exist_ok=True)
    brief = path.parent / "brief.md"
    shutil.copyfile(PACKAGE_ROOT / "templates/initiative-brief.md", brief)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE workflow (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                stage_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                pending_gate TEXT,
                pending_path TEXT,
                pending_sha TEXT,
                last_error TEXT,
                failures INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                detail TEXT NOT NULL
            );
            CREATE TABLE approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gate TEXT NOT NULL,
                path TEXT NOT NULL,
                sha TEXT NOT NULL,
                decision TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE git_binding (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                primary_root TEXT NOT NULL,
                selected_root TEXT NOT NULL,
                common_dir TEXT NOT NULL,
                selected_branch TEXT NOT NULL,
                attached_head TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE project_config (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                integration_root TEXT NOT NULL,
                integration_branch TEXT NOT NULL,
                integration_head TEXT NOT NULL,
                github_repo TEXT,
                parent_issue INTEGER,
                parent_database_id INTEGER,
                parent_url TEXT
            );
            CREATE TABLE mvp_issues (
                mvp_id TEXT PRIMARY KEY,
                issue_number INTEGER NOT NULL,
                database_id INTEGER NOT NULL,
                url TEXT NOT NULL
            );
            CREATE TABLE external_ops (
                op_key TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute("INSERT INTO workflow(singleton, stage_index, status) VALUES(1, 0, 'NOT_STARTED')")
        record(conn, "INITIALIZED", {"initiative": initiative, "mode": "SIMULATION_ONLY"})
    refresh_tickets(root, initiative)
    return (
        f"Initialized {initiative}. Local parent ticket: work/{initiative}/project-ticket.md. "
        f"Edit work/{initiative}/brief.md; optionally attach Git and create integration before running."
    )


def refresh_tickets(root: Path, initiative: str) -> None:
    with open_db(root, initiative) as conn:
        local_tickets.refresh(root, initiative, conn, STAGES)


def _ensure_git_table(conn: sqlite3.Connection) -> None:
    # An existing prototype database may predate Git-aware support.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS git_binding (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            primary_root TEXT NOT NULL,
            selected_root TEXT NOT NULL,
            common_dir TEXT NOT NULL,
            selected_branch TEXT NOT NULL,
            attached_head TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _ensure_project_tables(conn: sqlite3.Connection) -> None:
    # Older prototypes did not have integration or GitHub issue tracking.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_config (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            integration_root TEXT NOT NULL,
            integration_branch TEXT NOT NULL,
            integration_head TEXT NOT NULL,
            github_repo TEXT,
            parent_issue INTEGER,
            parent_database_id INTEGER,
            parent_url TEXT
        );
        CREATE TABLE IF NOT EXISTS mvp_issues (
            mvp_id TEXT PRIMARY KEY,
            issue_number INTEGER NOT NULL,
            database_id INTEGER NOT NULL,
            url TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS external_ops (
            op_key TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def _project_record(conn: sqlite3.Connection) -> sqlite3.Row | None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_config'"
    ).fetchone():
        return None
    return conn.execute("SELECT * FROM project_config WHERE singleton=1").fetchone()


def _git_binding(conn: sqlite3.Connection) -> sqlite3.Row | None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='git_binding'"
    ).fetchone()
    if not exists:
        return None
    return conn.execute("SELECT * FROM git_binding WHERE singleton=1").fetchone()


def _require_unstarted(conn: sqlite3.Connection) -> None:
    row = current(conn)
    if row["status"] != "NOT_STARTED" or row["stage_index"] != 0:
        raise ValueError(
            "repository/worktree changes require an unstarted initiative; "
            "create a new initiative to avoid invalidating previous approvals"
        )


def _select_git_root(conn: sqlite3.Connection, info: git_adapter.RepositoryInfo) -> None:
    if not info.branch:
        raise ValueError("select a named Git branch before binding this worktree")
    _ensure_git_table(conn)
    current_binding = _git_binding(conn)
    if current_binding and Path(current_binding["common_dir"]) != info.common_dir:
        raise ValueError("selected worktree belongs to a different Git repository")
    primary = info.worktrees[0].path
    conn.execute(
        """
        INSERT INTO git_binding(
            singleton, primary_root, selected_root, common_dir,
            selected_branch, attached_head, updated_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(singleton) DO UPDATE SET
            primary_root=excluded.primary_root,
            selected_root=excluded.selected_root,
            common_dir=excluded.common_dir,
            selected_branch=excluded.selected_branch,
            attached_head=excluded.attached_head,
            updated_at=excluded.updated_at
        """,
        (
            str(primary), str(info.root), str(info.common_dir),
            info.branch, info.head, utcnow(),
        ),
    )


def attach_repo(root: Path, initiative: str, path: Path) -> str:
    info = git_adapter.inspect(path)
    state_dir = (root / "work" / initiative).resolve(strict=False)
    if info.root == state_dir or info.root in state_dir.parents:
        raise ValueError("Agentflow state would be inside the attached Git checkout; keep the workflow kit outside the target repository")
    with open_db(root, initiative) as conn:
        _require_unstarted(conn)
        _ensure_git_table(conn)
        binding = _git_binding(conn)
        if binding and Path(binding["common_dir"]) != info.common_dir:
            raise ValueError("a different repository is already attached; create a new initiative")
        _select_git_root(conn, info)
        record(conn, "GIT_REPO_ATTACHED", {
            "worktree": str(info.root), "branch": info.branch, "head": info.head,
        })
    refresh_tickets(root, initiative)
    return f"Attached Git repository: {info.root} ({info.branch}, {info.head[:12]})"


def _require_binding(conn: sqlite3.Connection) -> sqlite3.Row:
    binding = _git_binding(conn)
    if not binding:
        raise ValueError("no Git repository attached; use repo-attach first")
    return binding


def select_worktree(root: Path, initiative: str, path: Path) -> str:
    with open_db(root, initiative) as conn:
        _require_unstarted(conn)
        binding = _require_binding(conn)
        info = git_adapter.inspect(path)
        if info.common_dir != Path(binding["common_dir"]):
            raise ValueError("selected path belongs to a different Git repository")
        if info.root not in {item.path for item in info.worktrees}:
            raise ValueError("path is not a registered Git worktree")
        _select_git_root(conn, info)
        record(conn, "GIT_WORKTREE_SELECTED", {
            "worktree": str(info.root), "branch": info.branch, "head": info.head,
        })
    refresh_tickets(root, initiative)
    return f"Selected worktree: {info.root} ({info.branch})"


def create_worktree(
    root: Path, initiative: str, mvp: str, *,
    base: str = "HEAD", path: Path | None = None,
) -> str:
    # All preflight checks happen before the single Git-mutating operation.
    with open_db(root, initiative) as conn:
        _require_unstarted(conn)
        binding = _require_binding(conn)
        source = git_adapter.inspect(Path(binding["selected_root"]))
        if source.common_dir != Path(binding["common_dir"]) or source.branch != binding["selected_branch"]:
            raise ValueError("attached repository identity has changed")
        if source.dirty:
            raise ValueError("selected worktree is dirty; uncommitted changes would not be included in the new branch")
    if base == "HEAD":
        project = project_context(root, initiative)
        if project:
            if project["error"] or project["dirty"]:
                raise ValueError("integration must be valid and clean to base a new MVP on it")
            base = project["integration_head"]
    created = git_adapter.create_mvp_worktree(
        source.root, initiative=initiative, mvp=mvp, base=base, target=path,
    )
    with open_db(root, initiative) as conn:
        _require_unstarted(conn)
        _select_git_root(conn, created)
        record(conn, "GIT_WORKTREE_CREATED", {
            "worktree": str(created.root), "branch": created.branch,
            "head": created.head, "base": base,
        })
    refresh_tickets(root, initiative)
    return f"Created and selected {created.root} ({created.branch}, {created.head[:12]})"


def git_context(root: Path, initiative: str) -> dict | None:
    with open_db(root, initiative) as conn:
        binding = _git_binding(conn)
        if not binding:
            return None
        saved = dict(binding)
    context = {
        "primary_root": saved["primary_root"],
        "selected_root": saved["selected_root"],
        "expected_branch": saved["selected_branch"],
        "attached_head": saved["attached_head"],
        "error": None,
    }
    try:
        info = git_adapter.inspect(Path(saved["selected_root"]))
        if info.common_dir != Path(saved["common_dir"]):
            raise ValueError("selected path no longer belongs to the attached Git repository")
        if info.branch != saved["selected_branch"]:
            raise ValueError(
                f"branch changed since selection: expected {saved['selected_branch']}, "
                f"found {info.branch or 'DETACHED'}"
            )
        context.update({
            "branch": info.branch,
            "head": info.head,
            "dirty": info.dirty,
            "change_count": info.change_count,
            "origin": info.origin,
            "worktrees": [
                {"path": str(item.path), "branch": item.branch, "head": item.head,
                 "locked": item.locked, "prunable": item.prunable}
                for item in info.worktrees
            ],
        })
    except (ValueError, OSError) as error:
        context["error"] = str(error)
    return context


def git_status(root: Path, initiative: str) -> str:
    context = git_context(root, initiative)
    if not context:
        return "Git: no repository attached. Use repo-attach to bind a local checkout."
    lines = [
        f"Repository: {context['primary_root']}",
        f"Selected worktree: {context['selected_root']}",
        f"Attached branch: {context['expected_branch']}",
    ]
    if context["error"]:
        lines.append(f"INVALID BINDING: {context['error']}")
        return "\n".join(lines)
    lines.extend([
        f"Origin: {context['origin']}",
        f"HEAD: {context['head']}",
        f"Working tree: {'DIRTY' if context['dirty'] else 'CLEAN'} ({context['change_count']} status entries)",
        "Registered worktrees:",
    ])
    for tree in context["worktrees"]:
        tag = " [selected]" if tree["path"] == context["selected_root"] else ""
        lines.append(f"  {tree['path']}  {tree['branch'] or 'DETACHED'}{tag}")
    return "\n".join(lines)


def create_integration(
    root: Path, initiative: str, *, base: str = "HEAD", path: Path | None = None,
) -> str:
    """Create a persistent project integration worktree without selecting it for EDD."""
    with open_db(root, initiative) as conn:
        _require_unstarted(conn)
        binding = _require_binding(conn)
        _ensure_project_tables(conn)
        previous = _project_record(conn)
        source = git_adapter.inspect(Path(binding["selected_root"]))
        if source.common_dir != Path(binding["common_dir"]) or source.branch != binding["selected_branch"]:
            raise ValueError("attached repository identity changed")
        if previous:
            target = git_adapter.inspect(Path(previous["integration_root"]))
            if target.branch != previous["integration_branch"] or target.common_dir != source.common_dir:
                raise ValueError("existing integration worktree identity changed")
            if path and path.resolve(strict=False) != target.root:
                raise ValueError("integration worktree already exists at a different path")
            return f"Integration already configured: {target.root} ({target.branch})"
    created = git_adapter.create_integration_worktree(
        source.root, initiative=initiative, base=base, target=path,
    )
    with open_db(root, initiative) as conn:
        _require_unstarted(conn)
        _ensure_project_tables(conn)
        if _project_record(conn):
            raise ValueError("integration was concurrently configured; inspect before continuing")
        binding = _require_binding(conn)
        if created.common_dir != Path(binding["common_dir"]):
            raise ValueError("integration was created in a different repository")
        conn.execute(
            "INSERT INTO project_config(singleton, integration_root, integration_branch, integration_head) VALUES (1, ?, ?, ?)",
            (str(created.root), created.branch, created.head),
        )
        record(conn, "INTEGRATION_CREATED", {
            "worktree": str(created.root), "branch": created.branch, "head": created.head,
        })
    refresh_tickets(root, initiative)
    return f"Created integration worktree: {created.root} ({created.branch}, {created.head[:12]})"


def project_context(root: Path, initiative: str) -> dict | None:
    with open_db(root, initiative) as conn:
        project = _project_record(conn)
        if not project:
            return None
        config = dict(project)
        issues = conn.execute(
            "SELECT mvp_id, issue_number, url FROM mvp_issues ORDER BY mvp_id"
        ).fetchall()
        binding = _git_binding(conn)
    context = {
        **config,
        "mvp_issues": [dict(row) for row in issues],
        "error": None,
        "dirty": None,
        "head": None,
    }
    try:
        info = git_adapter.inspect(Path(config["integration_root"]))
        if not binding or info.common_dir != Path(binding["common_dir"]):
            raise ValueError("integration worktree belongs to a different repository")
        if info.branch != config["integration_branch"]:
            raise ValueError("integration worktree branch has changed")
        if info.head != config["integration_head"]:
            raise ValueError("integration HEAD changed outside Coordinator; reconcile before publishing")
        context.update({"head": info.head, "dirty": info.dirty, "origin": info.origin})
    except (ValueError, OSError) as error:
        context["error"] = str(error)
    return context


def _verified_integration(root: Path, initiative: str) -> tuple[dict, git_adapter.RepositoryInfo]:
    context = project_context(root, initiative)
    if not context:
        raise ValueError("create an integration worktree first")
    if context["error"]:
        raise ValueError(f"integration binding is invalid: {context['error']}")
    info = git_adapter.inspect(Path(context["integration_root"]))
    if info.dirty:
        raise ValueError("integration worktree is dirty; review and commit/restore your changes first")
    return context, info


def promote_mvp(root: Path, initiative: str, *, mvp: str, source: Path) -> str:
    context, integration = _verified_integration(root, initiative)
    updated = git_adapter.promote_fast_forward(
        integration.root, source, initiative=initiative, mvp=mvp,
    )
    with open_db(root, initiative) as conn:
        conn.execute("UPDATE project_config SET integration_head=? WHERE singleton=1", (updated.head,))
        record(conn, "INTEGRATION_PROMOTED", {
            "mvp": mvp, "source": str(source.resolve()), "head": updated.head,
        })
    refresh_tickets(root, initiative)
    return f"Integration contains {mvp}: {updated.branch} @ {updated.head[:12]} (local, not pushed)"


def snapshot_project_artifacts(root: Path, initiative: str) -> str:
    context, integration = _verified_integration(root, initiative)
    docs = project_artifacts.snapshot(
        root / "work" / initiative, integration.root, initiative=initiative,
    )
    with open_db(root, initiative) as conn:
        conn.execute(
            "UPDATE project_config SET integration_head=? WHERE singleton=1",
            (docs.head,),
        )
        record(conn, "PROJECT_ARTIFACTS_SNAPSHOTTED", {
            "artifacts": docs.artifact_count, "index_path": docs.index_path,
            "head": docs.head, "committed": docs.committed,
        })
    refresh_tickets(root, initiative)
    change = "committed" if docs.committed else "already current"
    return (
        f"Project artifacts {change}: {docs.artifact_count} Markdown files; "
        f"{docs.index_path} @ {docs.head[:12]} (local, not pushed)"
    )


def push_project(root: Path, initiative: str, *, remote: str = "origin") -> str:
    context, integration = _verified_integration(root, initiative)
    if context["github_repo"] and integration.origin.lower() != f"github.com/{context['github_repo'].lower()}":
        raise ValueError("integration Git remote does not match the bound GitHub issue repository")
    updated = git_adapter.push_integration(integration.root, initiative=initiative, remote=remote)
    with open_db(root, initiative) as conn:
        record(conn, "INTEGRATION_PUSHED", {
            "branch": updated.branch, "head": updated.head, "remote": remote,
        })
    refresh_tickets(root, initiative)
    return f"Pushed {updated.branch} @ {updated.head[:12]} to remote {remote} (non-force)"


def project_status(root: Path, initiative: str) -> str:
    context = project_context(root, initiative)
    local = root / "work" / initiative
    lines = [
        f"Local parent ticket: {local / 'project-ticket.md'}",
        "Local MVP tickets:",
    ]
    for ticket in sorted((local / "mvps").glob("mvp-*/ticket.md")):
        if ticket.is_file() and not ticket.is_symlink():
            lines.append(f"  {ticket}")
    if len(lines) == 2:
        lines.append("  none until MVP DESIGN generates specifications")
    if not context:
        lines.append("Integration: not created yet (use integration-create)")
        lines.append("GitHub mirror: disabled (optional)")
        return "\n".join(lines)
    lines.extend([
        f"Integration: {context['integration_branch']}",
        f"Worktree: {context['integration_root']}",
        f"HEAD: {context['head'] or context['integration_head']}",
        f"State: {'INVALID — ' + context['error'] if context['error'] else 'DIRTY' if context['dirty'] else 'CLEAN'}",
        f"GitHub mirror: {context['github_repo'] or 'disabled (optional)'}",
        f"GitHub parent: {context['parent_url'] or 'not created'}",
        "Optional GitHub MVP sub-issues:",
    ])
    lines.extend(
        f"  {issue['mvp_id']}: #{issue['issue_number']} {issue['url']}"
        for issue in context["mvp_issues"]
    )
    if not context["mvp_issues"]:
        lines.append("  none yet")
    return "\n".join(lines)


def _external_once(root: Path, initiative: str, key: str, operation) -> dict:
    """Persist an outbox reservation before any GitHub write.

    A crash or ambiguous API failure is never retried automatically, preventing
    duplicate tickets/comments. A human must reconcile the STARTED operation.
    """
    with open_db(root, initiative) as conn:
        _ensure_project_tables(conn)
        previous = conn.execute(
            "SELECT state, detail FROM external_ops WHERE op_key=?", (key,),
        ).fetchone()
        if previous:
            if previous["state"] == "DONE":
                return json.loads(previous["detail"])
            raise ValueError(
                f"GitHub operation {key} has an uncertain outcome; "
                "inspect the remote issue before retrying or manually reconcile"
            )
        conn.execute(
            "INSERT INTO external_ops(op_key, state, detail, created_at) VALUES (?, 'STARTED', '{}', ?)",
            (key, utcnow()),
        )
    result = operation()
    with open_db(root, initiative) as conn:
        conn.execute(
            "UPDATE external_ops SET state='DONE', detail=? WHERE op_key=?",
            (json.dumps(result, sort_keys=True), key),
        )
        record(conn, "GITHUB_OPERATION_COMPLETED", {"key": key})
    return result


def _require_github_origin(context: dict, repo: str) -> None:
    github_adapter.validate_repo(repo)
    expected = f"github.com/{repo}".lower()
    origin = context.get("origin", "").lower()
    if origin != expected:
        raise ValueError(
            f"GitHub issue repository {repo} does not match Git origin ({context.get('origin', 'unknown')}); "
            "configure the intended GitHub origin first"
        )


def create_parent_ticket(
    root: Path, initiative: str, *, repo: str, title: str | None = None,
) -> str:
    context, _ = _verified_integration(root, initiative)
    _require_github_origin(context, repo)
    if context["parent_issue"] and context["github_repo"] == repo:
        return f"Parent ticket already linked: {context['parent_url']}"
    if context["parent_issue"] or (context["github_repo"] and context["github_repo"] != repo):
        raise ValueError("a different GitHub parent ticket is already bound")
    title = title or f"[Agentflow] {initiative} — project delivery"
    body = (
        f"# Project: {initiative}\n\n"
        "One parent issue coordinates discovery, prototype, product specification, "
        "MVP roadmap, implementation, verification, and proof.\n\n"
        f"- Integration branch: {context['integration_branch']}\n"
        f"- Published artifacts index: docs/agentflow/{initiative}/INDEX.md\n"
        "- MVP work is tracked in native GitHub sub-issues.\n"
        "- Progress is appended as auditable comments after source/artifact validation.\n"
        "- No simulated completion is represented as real verified software.\n"
    )
    info = _external_once(
        root, initiative, f"create-parent:{repo}",
        lambda: asdict(github_adapter.create_parent(
            repo, initiative=initiative, title=title, body=body,
        )),
    )
    with open_db(root, initiative) as conn:
        conn.execute(
            "UPDATE project_config SET github_repo=?, parent_issue=?, parent_database_id=?, parent_url=? WHERE singleton=1",
            (repo, info["number"], info["database_id"], info["url"]),
        )
        record(conn, "GITHUB_PARENT_LINKED", {"repo": repo, "number": info["number"], "url": info["url"]})
    refresh_tickets(root, initiative)
    return f"GitHub parent ticket linked: {info['url']}"


def create_mvp_ticket(
    root: Path, initiative: str, *, mvp: str, title: str | None = None,
) -> str:
    if not git_adapter.MVP_ID.fullmatch(mvp):
        raise ValueError("MVP ID must be mvp-01 or mvp-001")
    context, _ = _verified_integration(root, initiative)
    if not context["parent_issue"] or not context["github_repo"]:
        raise ValueError("create the GitHub parent ticket before MVP sub-issues")
    spec = root / "work" / initiative / "mvps" / mvp / "spec.md"
    if not spec.is_file() or spec.is_symlink():
        raise ValueError(f"no generated MVP spec exists for {mvp}; run MVP DESIGN first")
    ux_spec = root / "work" / initiative / "mvps" / mvp / "ux" / "spec.md"
    if not ux_spec.is_file() or ux_spec.is_symlink():
        raise ValueError(f"no generated MVP UI/UX spec exists for {mvp}; run MVP DESIGN first")
    with open_db(root, initiative) as conn:
        record_row = conn.execute(
            "SELECT url FROM mvp_issues WHERE mvp_id=?", (mvp,),
        ).fetchone()
        if record_row:
            return f"MVP sub-issue already linked: {record_row['url']}"
    repo = context["github_repo"]
    body = (
        f"# {mvp} — {initiative}\n\n"
        f"- Approved scope source: work/{initiative}/mvps/{mvp}/spec.md\n"
        f"- Current spec SHA-256: {sha256(spec)}\n"
        f"- UI/UX scope source: work/{initiative}/mvps/{mvp}/ux/spec.md\n"
        f"- UI/UX spec SHA-256: {sha256(ux_spec)}\n"
        f"- Integration target: {context['integration_branch']}\n"
        "- Track UX DESIGN → REQUIREMENTS → RED → GREEN → REFACTOR → VERIFY → UX INSPECT → PROVE here.\n"
        "- The MVP implementation is not authorized until its G5 human approval.\n"
    )
    info = _external_once(
        root, initiative, f"create-subissue:{mvp}",
        lambda: asdict(github_adapter.create_mvp_subissue(
            repo, initiative=initiative, parent_database_id=context["parent_database_id"],
            mvp=mvp, title=title or f"[{mvp}] {initiative} deliverable", body=body,
        )),
    )
    with open_db(root, initiative) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mvp_issues(mvp_id, issue_number, database_id, url) VALUES (?, ?, ?, ?)",
            (mvp, info["number"], info["database_id"], info["url"]),
        )
        record(conn, "GITHUB_MVP_LINKED", {"mvp": mvp, "issue_number": info["number"], "url": info["url"]})
    refresh_tickets(root, initiative)
    return f"Linked {mvp} under project parent #{context['parent_issue']}: {info['url']}"


def _progress_message(root: Path, initiative: str, context: dict, *, mvp: str | None) -> str:
    with open_db(root, initiative) as conn:
        row = current(conn)
        num_approvals = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        pushed = conn.execute(
            "SELECT detail FROM events WHERE kind='INTEGRATION_PUSHED' ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
    stage = STAGES[row["stage_index"]].name if row["stage_index"] < len(STAGES) else "done"
    lines = [
        f"## Agentflow progress: {initiative}" + (f" / {mvp}" if mvp else ""),
        "",
        f"- Stage: {stage}",
        f"- Workflow state: {row['status']} (SIMULATION ONLY in this prototype)",
        f"- Human approvals recorded: {num_approvals}",
        f"- Pending gate: {row['pending_gate'] or 'none'}",
        f"- Integration branch: {context['integration_branch']}",
        f"- Integration HEAD: {context['integration_head']}",
    ]
    if row["pending_path"]:
        artifact = root / row["pending_path"]
        if artifact.is_file():
            lines.append(f"- Current report: {row['pending_path']} (SHA-256 {sha256(artifact)})")
    source = root / "work" / initiative
    artifacts = project_artifacts.collect(source)
    if mvp:
        artifacts = [item for item in artifacts if item[0].parts[:2] == ("mvps", mvp)]
    lines.extend(["", "### Generated artifact inventory", ""])
    for relative, _path, content in artifacts:
        lines.append(f"- {relative.as_posix()} — SHA-256 {hashlib.sha256(content).hexdigest()[:16]}")
    if not artifacts:
        lines.append("- No generated Markdown artifacts for this scope yet.")
    pushed_head = json.loads(pushed["detail"]).get("head") if pushed else None
    index = Path(context["integration_root"]) / "docs" / "agentflow" / initiative / "INDEX.md"
    if pushed_head == context["integration_head"] and index.is_file():
        lines.extend([
            "",
            "Published documentation (revision-specific):",
            f"https://github.com/{context['github_repo']}/blob/{pushed_head}/docs/agentflow/{initiative}/INDEX.md",
        ])
    else:
        lines.extend(["", "Documentation is not yet published at the current integration revision."])
    marker = f"<!-- agentflow-progress:{initiative}:{mvp or 'parent'} -->"
    return "\n".join([*lines, "", marker, ""])


def sync_project_tickets(root: Path, initiative: str) -> str:
    """Post one idempotency-tracked progress comment per issue for the current state."""
    context, _ = _verified_integration(root, initiative)
    if not context["parent_issue"] or not context["github_repo"]:
        raise ValueError("create the GitHub parent ticket before syncing progress")
    repo = context["github_repo"]
    targets = [(None, context["parent_issue"])]
    targets.extend((item["mvp_id"], item["issue_number"]) for item in context["mvp_issues"])
    posted = 0
    for mvp, issue in targets:
        body = _progress_message(root, initiative, context, mvp=mvp)
        digest = hashlib.sha256(body.encode()).hexdigest()[:20]
        key = f"comment:{issue}:{digest}"
        with open_db(root, initiative) as conn:
            earlier = conn.execute("SELECT state FROM external_ops WHERE op_key=?", (key,)).fetchone()
        if earlier and earlier["state"] == "DONE":
            continue
        _external_once(
            root, initiative, key,
            lambda issue=issue, body=body: {
                "comment_id": github_adapter.post_comment(repo, issue, body),
            },
        )
        posted += 1
    if posted:
        refresh_tickets(root, initiative)
    return f"Synced progress to {posted} GitHub issue(s); unchanged updates were skipped"


def _gate_git_identity(conn: sqlite3.Connection) -> dict | None:
    binding = _git_binding(conn)
    if not binding:
        return None
    info = git_adapter.inspect(Path(binding["selected_root"]))
    if info.common_dir != Path(binding["common_dir"]) or info.branch != binding["selected_branch"]:
        raise ValueError("Git binding changed; reselect an approved worktree in a new initiative")
    if info.dirty:
        raise ValueError("Git worktree contains uncommitted changes; use a clean worktree before advancing")
    return {"worktree": str(info.root), "branch": info.branch, "head": info.head}


def _record_gate_request(conn: sqlite3.Connection, gate: str, path: str, digest: str,
                         *, companion: dict | None = None) -> None:
    detail = {"gate": gate, "path": path, "sha": digest}
    if companion:
        detail.update(companion)
    identity = _gate_git_identity(conn)
    if identity:
        detail["git"] = identity
    record(conn, "GATE_REQUESTED", detail)


def _check_gate_git_identity(conn: sqlite3.Connection, gate: str, digest: str) -> None:
    current_identity = _gate_git_identity(conn)
    if not current_identity:
        return
    records = conn.execute(
        "SELECT detail FROM events WHERE kind='GATE_REQUESTED' ORDER BY event_id DESC"
    ).fetchall()
    for event in records:
        detail = json.loads(event["detail"])
        if detail.get("gate") == gate and detail.get("sha") == digest:
            if detail.get("git") != current_identity:
                raise ValueError("Git worktree revision changed since this approval gate was requested")
            return
    raise ValueError("missing Git-bound approval request; approval rejected")


def _ux_companion(root: Path, initiative: str, gate: str) -> dict | None:
    """G3/G5 decisions bind their companion UX contracts by source hash."""
    if gate == "G3_PRODUCT_SPEC":
        relative = f"work/{initiative}/product-ux-spec.md"
    elif gate == "G5_MVP_01":
        relative = f"work/{initiative}/mvps/mvp-01/ux/spec.md"
    else:
        return None
    path = root / relative
    if (path.is_symlink() or not path.is_file() or
            root.resolve() not in path.resolve().parents):
        raise ValueError(f"UI/UX specification missing or unsafe: {relative}; return to its owning specification agent")
    return {"ux_spec_path": relative, "ux_spec_sha": sha256(path)}


def _check_ux_companion(conn: sqlite3.Connection, root: Path, initiative: str,
                        gate: str, digest: str) -> None:
    if gate not in {"G3_PRODUCT_SPEC", "G5_MVP_01"}:
        return
    current_ux = _ux_companion(root, initiative, gate)
    records = conn.execute(
        "SELECT detail FROM events WHERE kind='GATE_REQUESTED' ORDER BY event_id DESC"
    ).fetchall()
    for row in records:
        detail = json.loads(row["detail"])
        if detail.get("gate") == gate and detail.get("sha") == digest:
            if any(detail.get(key) != value for key, value in current_ux.items()):
                raise ValueError(f"stale UI/UX specification; {gate} approval rejected until renewed review")
            return
    raise ValueError(f"missing UI/UX-bound {gate} approval request")


def _check_approved_ux_contracts(conn: sqlite3.Connection, root: Path, initiative: str) -> None:
    """Recheck prior G3/G5 UX scope, including on retry/resume after a blocker."""
    checked = set()
    for row in conn.execute(
        "SELECT gate, sha FROM approvals WHERE gate IN ('G3_PRODUCT_SPEC', 'G5_MVP_01') ORDER BY id DESC"
    ):
        if row["gate"] not in checked:
            _check_ux_companion(conn, root, initiative, row["gate"], row["sha"])
            checked.add(row["gate"])


def mock_specialist(root: Path, initiative: str, stage: Stage, invalid_handoff: bool) -> tuple[str, dict]:
    assert stage.role is not None and stage.status is not None and stage.report is not None
    report_path = stage.report.format(id=initiative)
    extra_output: list[str] = []
    body = (
        f"# {stage.role} — SIMULATED OUTPUT\n\n"
        "This report is generated by the CLI prototype. It is **not a real agent result**.\n\n"
        f"- Stage: {stage.name}\n- Simulated status: {stage.status}\n"
        "- Tests and code changes: **NOT EXECUTED**\n"
        "- Human evaluation: **NOT PERFORMED**\n"
    )
    if stage.name == "prototype":
        iteration = prototype_review.next_iteration(root, initiative)
        body += (
            f"- Review iteration: **{iteration}**\n"
            f"- Preview (generated after valid handoff): "
            f"`work/{initiative}/prototype-review/{iteration}/preview/index.html`\n"
            "- Prototype delivery: **static synthetic demonstration only**; human feedback must be reviewed manually.\n"
        )
    if stage.name == "specification":
        acceptance = root / "work" / initiative / "prototype-acceptance.md"
        if not acceptance.is_file() or acceptance.is_symlink():
            raise ValueError("Specification requires recorded G2 prototype acceptance")
        body += (
            "\n## Approved prototype inputs (simulation handoff)\n\n"
            f"- G2 acceptance: `work/{initiative}/prototype-acceptance.md`\n"
            f"- Acceptance SHA-256: `{sha256(acceptance)}`\n"
            f"- Findings: `work/{initiative}/prototype-report.md`\n"
            "- Carry forward *reviewed findings*, not unverified prototype code.\n"
        )
        ux_spec = f"work/{initiative}/product-ux-spec.md"
        safe_mock_write(root, root / ux_spec, (
            "# Product UI/UX contract — SIMULATED EXAMPLE\n\n"
            "- Interface surface: synthetic order-search web UI (actual scope requires human confirmation).\n"
            "- Target users/tasks: find an order, understand its status, export results.\n"
            "- UX-001: find an order with a visible search control; check empty and error results.\n"
            "- UX-002: export action has a clear label and visible outcome or failure feedback.\n"
            "- UX-003: keyboard and assistive-technology navigation are observable.\n"
            "- Accessibility: meaningful labels/semantics, keyboard focus visibility and contrast.\n"
            "- UX-004: layouts remain usable at narrow and wide viewport widths.\n"
            "- States: default, loading, empty, invalid, permission-denied, error, success.\n"
            "- Visual criteria: information hierarchy, consistent spacing/type, contrast and focus.\n"
            "- Localized copy and reduced-motion behavior: specify before implementing.\n"
            "- Evidence: NONE. These are example requirements, **not tested UX findings**.\n"
        ))
        extra_output.append(ux_spec)
        body += f"- Product UI/UX contract: `{ux_spec}` (unverified example)\n"
    safe_mock_write(root, root / report_path, body)
    if stage.role == "MVP_DESIGN":
        spec = f"work/{initiative}/mvps/mvp-01/spec.md"
        safe_mock_write(root, root / spec, "# MVP-01 — simulated specification\n\n- Acceptance ID: MVP-01-AC-001\n")
        extra_output.append(spec)
        ux_spec = f"work/{initiative}/mvps/mvp-01/ux/spec.md"
        safe_mock_write(root, root / ux_spec, (
            "# MVP-01 UI/UX specification — SIMULATED EXAMPLE\n\n"
            "- Parent contract: `work/" + initiative + "/product-ux-spec.md`\n"
            "- Target task: search synthetic orders and export the visible set.\n"
            "- MVP-01-UX-001 → UX-001 / MVP-01-AC-001: labelled search; results update and empty results are explained.\n"
            "- MVP-01-UX-002 → UX-002: CSV export affordance and success/error feedback.\n"
            "- MVP-01-UX-003 → UX-003: keyboard focus order and semantic control labels.\n"
            "- MVP-01-UX-004 → UX-004: small/medium/large viewport layouts.\n"
            "- Screen inventory: search, result list, empty state, denied, error, loading, success, focused and disabled.\n"
            "- Interaction details: entry, exit, disabled, focus, validation, confirmation and recovery.\n"
            "- Design tokens: type scale, spacing, contrast, colors, responsive boundaries.\n"
            "- Proof required later: browser/device observations and actual screenshot/trace paths.\n"
            "- Current sample: NOT VERIFIED by a UI/UX agent or user.\n"
        ))
        extra_output.append(ux_spec)
    if stage.name == "ux_design":
        ux_input = root / f"work/{initiative}/mvps/mvp-01/ux/spec.md"
        if not ux_input.is_file() or ux_input.is_symlink():
            raise ValueError("UI/UX Design requires the approved MVP UX specification")
        design = f"work/{initiative}/mvps/mvp-01/ux/design.md"
        safe_mock_write(root, root / design, (
            "# UI/UX Design — SIMULATED EXAMPLE\n\n"
            "- Based on: MVP-01 UX-001 through UX-004 (sample contract).\n"
            "- Journey: search → results / empty state → export / error recovery.\n"
            "- Layout states: narrow/mobile, medium, wide; loading/empty/denied/errors.\n"
            "- Interaction inventory: focus, keyboard, disabled, hover, validation, recovery.\n"
            "- Design choices and wireframes: NOT CREATED by a live designer.\n"
            "- Screenshot or usability evidence: NOT EXECUTED.\n"
        ))
        extra_output.append(design)
    if stage.name == "ux_inspect":
        required = (root / f"work/{initiative}/mvps/mvp-01/ux/design.md",
                    root / f"work/{initiative}/mvps/mvp-01/edd/verify.md")
        if any(not file.is_file() or file.is_symlink() for file in required):
            raise ValueError("UI/UX Inspection requires the design and independent VERIFY report")
    criterion_ids = ["MVP-01-AC-001"] if stage.name in {
        "requirements", "red", "green", "refactor", "verify", "prove"
    } else []
    if stage.name in {"ux_design", "ux_inspect"}:
        criterion_ids = ["MVP-01-UX-001", "MVP-01-UX-002", "MVP-01-UX-003", "MVP-01-UX-004"]
        mode = "design coverage" if stage.name == "ux_design" else "post-build inspection"
        body_extra = (
            f"\n## UI/UX {mode} checklist (NOT EXECUTED)\n\n"
            "- Layout, responsive breakpoints and visual hierarchy: NOT EXECUTED\n"
            "- Focus order, keyboard, semantics and contrast: NOT EXECUTED\n"
            "- Loading, empty, invalid, success, denied and error states: NOT EXECUTED\n"
            "- Screenshots, device matrix and usability observations: NOT EXECUTED\n"
            "- Defect disposition / severity: UNKNOWN; independent inspection required\n"
            "- Simulation must not be interpreted as UX sign-off.\n"
        )
        safe_mock_write(root, root / report_path, body + body_extra)
    run_id = "mock-" + uuid.uuid4().hex[:12]
    record_root = root / "work" / initiative / "handoffs" / run_id
    if root.resolve() not in record_root.resolve(strict=False).parents:
        raise ValueError("handoff output directory escapes workspace root")
    record_root.mkdir(parents=True)
    producer = {
        "schema_version": 1,
        "role": stage.role,
        "run_id": run_id,
        "mvp_id": "mvp-01" if criterion_ids else "",
        "status": stage.status,
        "report_path": report_path,
        "output_paths": extra_output,
        "criterion_ids": criterion_ids,
        "summary": "SIMULATED ONLY; no Codex session or software checks ran",
    }
    handoff = {
        "schema_version": 1,
        "producer_role": stage.role,
        "producer_run_id": run_id,
        "mvp_id": producer["mvp_id"],
        "reported_status": stage.status,
        "producer_report_path": report_path,
        "output_paths": extra_output,
        "evidence_paths": [],
        "criterion_ids": criterion_ids,
        "issues": [],
        "recommendation": "READY_FOR_VALIDATION",
    }
    if invalid_handoff:
        handoff["producer_report_path"] = report_path.removesuffix(".md") + "-missing.md"
        handoff["issues"] = [
            {"code": "PATH_REMAP", "severity": "WARNING", "detail": "Intentionally missing path for demo"}
        ]
    (record_root / "producer.json").write_text(json.dumps(producer, indent=2) + "\n", encoding="utf-8")
    (record_root / "normalized-handoff.json").write_text(json.dumps(handoff, indent=2) + "\n", encoding="utf-8")
    result = validate(root, producer, handoff, [f"work/{initiative}"])
    (record_root / "validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return run_id, result


def gate_message(gate: str, path: str, digest: str, initiative: str, root: Path) -> str:
    command = ["uv", "run", "workflow.py"]
    if root.resolve() != PACKAGE_ROOT.resolve():
        command.extend(["--root", str(root)])
    command.extend(["approve", initiative, "--gate", gate, "--sha", digest])
    invoke = " ".join(
        shlex.quote(part) for part in command
    )
    prototype_note = (
        "\n  Preview: work/" + initiative + "/prototype-review/current.json"
        " (resolve iteration; press [O] in the TUI to open the HTML preview)"
        if gate == "G2_PROTOTYPE" else ""
    )
    ux_note = (
        f"\n  UX scope: work/{initiative}/mvps/mvp-01/ux/spec.md"
        " (also bound to G5 by SHA-256; review both specs)"
        if gate == "G5_MVP_01" else
        f"\n  UX contract: work/{initiative}/product-ux-spec.md"
        " (also bound to G3 by SHA-256; review both specifications)"
        if gate == "G3_PRODUCT_SPEC" else ""
    )
    return (
        f"PAUSED — human approval required: {gate}\n"
        f"  Artifact: {path}\n  SHA-256: {digest}{prototype_note}{ux_note}\n"
        f"  Review it, then run:\n"
        f"  {invoke}"
    )


def advance(root: Path, initiative: str, *, invalid_handoff: bool = False) -> list[str]:
    messages: list[str] = []
    with open_db(root, initiative) as conn:
        _gate_git_identity(conn)
        while True:
            row = current(conn)
            if row["status"] == "WAITING_FOR_HUMAN":
                messages.append(gate_message(
                    row["pending_gate"], row["pending_path"], row["pending_sha"], initiative, root))
                break
            if row["status"] == "BLOCKED":
                messages.append(f"BLOCKED — {row['last_error']}\nUse uv run workflow.py retry {initiative} after review.")
                break
            if row["status"] == "SIMULATED_COMPLETE":
                messages.append("SIMULATED_COMPLETE — workflow demo finished; no real code or tests verified.")
                break
            try:
                _check_approved_ux_contracts(conn, root, initiative)
            except ValueError as error:
                conn.execute("UPDATE workflow SET status='BLOCKED',last_error=? WHERE singleton=1", (str(error),))
                record(conn, "UX_CONTRACT_CHANGED", {"error": str(error)})
                messages.append(f"BLOCKED — approved UX contract changed: {error}")
                break
            index = row["stage_index"]
            if index >= len(STAGES):
                conn.execute("UPDATE workflow SET status='SIMULATED_COMPLETE' WHERE singleton=1")
                record(conn, "SIMULATED_COMPLETE", {"initiative": initiative})
                messages.append("SIMULATED_COMPLETE — all 12 specialist invocations exercised in mock mode (11 distinct roles).")
                break
            stage = STAGES[index]
            if stage.role is None:
                path = stage.report.format(id=initiative)
                digest = sha256(root / path)
                try:
                    companion = _ux_companion(root, initiative, stage.gate)
                except ValueError as error:
                    conn.execute(
                        "UPDATE workflow SET status='BLOCKED',last_error=?,failures=failures+1 WHERE singleton=1",
                        (str(error),),
                    )
                    record(conn, "UX_SCOPE_MISSING", {"error": str(error)})
                    messages.append(f"BLOCKED — {error}")
                    break
                conn.execute(
                    "UPDATE workflow SET status='WAITING_FOR_HUMAN', pending_gate=?, pending_path=?, pending_sha=? WHERE singleton=1",
                    (stage.gate, path, digest),
                )
                _record_gate_request(conn, stage.gate, path, digest, companion=companion)
                messages.append(gate_message(stage.gate, path, digest, initiative, root))
                break
            display_role = ("UIUX " + stage.name.removeprefix("ux_").upper()
                            if stage.role == "UIUX" else stage.role)
            messages.append(f"RUN  {display_role} [SIMULATED]")
            try:
                run_id, result = mock_specialist(root, initiative, stage, invalid_handoff)
            except (OSError, ValueError) as error:
                result = {"valid": False, "errors": [str(error)]}
                run_id = "not-started"
            record(conn, "HANDOFF_CHECKED", {
                "role": stage.role, "stage": stage.name, "run_id": run_id,
                "mvp_id": "mvp-01" if stage.name in (*mvp_progress.EDD_STAGES, *mvp_progress.UX_STAGES) else "",
                "valid": result["valid"], "errors": result["errors"],
            })
            if not result["valid"]:
                failure_reason = "; ".join(result["errors"])
                conn.execute(
                    "UPDATE workflow SET status='BLOCKED', last_error=?, failures=failures+1 WHERE singleton=1",
                    (failure_reason,),
                )
                messages.append(f"BLOCKED — handoff validation rejected: {failure_reason}")
                break
            messages.append(f"OK   Handoff validated for {stage.role} [SIMULATED]")
            invalid_handoff = False
            if stage.name == "prototype":
                try:
                    review = prototype_review.create_iteration(root, initiative, conn)
                except (OSError, ValueError, sqlite3.Error) as error:
                    conn.execute(
                        "UPDATE workflow SET status='BLOCKED', last_error=?, failures=failures+1 WHERE singleton=1",
                        (f"prototype review generation failed: {error}",),
                    )
                    record(conn, "PROTOTYPE_REVIEW_FAILED", {"error": str(error)})
                    messages.append(f"BLOCKED — prototype review generation failed: {error}")
                    break
                record(conn, "PROTOTYPE_PREVIEW_READY", review)
                messages.append(f"PREVIEW {review['iteration_id']}  {review['preview_path']} [SIMULATED]")
            conn.execute(
                "UPDATE workflow SET stage_index=?, status='RUNNING', failures=0, last_error=NULL WHERE singleton=1",
                (index + 1,),
            )
            if stage.gate:
                digest = sha256(root / stage.report.format(id=initiative))
                try:
                    companion = _ux_companion(root, initiative, stage.gate)
                except ValueError as error:
                    conn.execute("UPDATE workflow SET status='BLOCKED',last_error=? WHERE singleton=1", (str(error),))
                    record(conn, "UX_SCOPE_MISSING", {"error": str(error)})
                    messages.append(f"BLOCKED — {error}")
                    break
                conn.execute(
                    "UPDATE workflow SET status='WAITING_FOR_HUMAN', pending_gate=?, pending_path=?, pending_sha=? WHERE singleton=1",
                    (stage.gate, stage.report.format(id=initiative), digest),
                )
                _record_gate_request(conn, stage.gate, stage.report.format(id=initiative), digest, companion=companion)
                messages.append(gate_message(stage.gate, stage.report.format(id=initiative), digest, initiative, root))
                break
    refresh_tickets(root, initiative)
    return messages


def approve(root: Path, initiative: str, gate: str, digest: str) -> list[str]:
    with open_db(root, initiative) as conn:
        row = current(conn)
        if row["status"] != "WAITING_FOR_HUMAN" or row["pending_gate"] != gate:
            raise ValueError("no matching pending approval gate")
        path = root / row["pending_path"]
        observed = sha256(path)
        if digest != row["pending_sha"] or observed != digest:
            raise ValueError("stale or mismatched artifact hash; approval rejected")
        _check_gate_git_identity(conn, gate, digest)
        _check_approved_ux_contracts(conn, root, initiative)
        _check_ux_companion(conn, root, initiative, gate, digest)
        review = None
        if gate == "G2_PROTOTYPE":
            review = prototype_review.validate_for_approval(root, initiative, conn, digest)
            # The human approves the exact report + preview + review evidence bundle.
            prototype_review.record_approval(root, initiative, conn, digest)
        conn.execute(
            "INSERT INTO approvals(gate, path, sha, decision, source, created_at) VALUES(?, ?, ?, 'APPROVED', 'SIMULATED_LOCAL_CLI', ?)",
            (gate, row["pending_path"], digest, utcnow()),
        )
        detail = {"gate": gate, "sha": digest, "source": "SIMULATED_LOCAL_CLI"}
        if review:
            detail.update({"iteration_id": review["iteration_id"], "review_bundle_sha256": review["bundle_sha"]})
        record(conn, "GATE_APPROVED", detail)
        # G5 is a gate-only stage; the other gates follow an already-advanced specialist stage.
        next_index = row["stage_index"] + (1 if gate == "G5_MVP_01" else 0)
        conn.execute(
            "UPDATE workflow SET status='RUNNING', stage_index=?, pending_gate=NULL, pending_path=NULL, pending_sha=NULL WHERE singleton=1",
            (next_index,),
        )
    return [f"APPROVED {gate} [local CLI demo only]", *advance(root, initiative)]


def request_changes(root: Path, initiative: str, reason: str) -> str:
    if not reason.strip():
        raise ValueError("provide an explanation for the requested changes")
    with open_db(root, initiative) as conn:
        row = current(conn)
        if row["status"] != "WAITING_FOR_HUMAN":
            raise ValueError("no pending human review")
        previous = max(0, row["stage_index"] - 1)
        feedback = (prototype_review.record_feedback(root, initiative, conn, reason)
                    if row["pending_gate"] == "G2_PROTOTYPE" else None)
        detail = {
            "gate": row["pending_gate"], "reason": reason, "return_to": STAGES[previous].name,
        }
        if feedback:
            detail.update({"feedback_id": feedback["feedback_id"], "iteration_id": feedback["iteration_id"]})
        record(conn, "CHANGES_REQUESTED", detail)
        conn.execute(
            "UPDATE workflow SET stage_index=?, status='RUNNING', pending_gate=NULL, pending_path=NULL, pending_sha=NULL WHERE singleton=1",
            (previous,),
        )
    refresh_tickets(root, initiative)
    return f"Changes requested; next run will return to {STAGES[previous].role} [simulated]."


def retry(root: Path, initiative: str) -> list[str]:
    with open_db(root, initiative) as conn:
        row = current(conn)
        if row["status"] != "BLOCKED":
            raise ValueError("workflow is not blocked")
        if row["failures"] > 2:
            raise ValueError("automatic retry budget exhausted; request human review")
        record(conn, "RETRY", {"stage": STAGES[row["stage_index"]].name, "failures": row["failures"]})
        conn.execute("UPDATE workflow SET status='RUNNING', last_error=NULL WHERE singleton=1")
    return [f"RETRY {STAGES[row['stage_index']].role} [SIMULATED]", *advance(root, initiative)]


def status(root: Path, initiative: str) -> str:
    with open_db(root, initiative) as conn:
        row = current(conn)
        stage = STAGES[row["stage_index"]].name if row["stage_index"] < len(STAGES) else "done"
        approvals = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        mvps = mvp_progress.collect(root, initiative, conn)
        prototype = prototype_review.inspect(root, initiative, conn)
    lines = [
        f"Initiative: {initiative}",
        "Mode: SIMULATION_ONLY (no live Codex calls)",
        f"State: {row['status']}",
        f"Stage: {stage} ({row['stage_index']}/{len(STAGES)})",
        f"Local parent ticket: work/{initiative}/project-ticket.md",
        f"Recorded approvals: {approvals} | Events: {events}",
    ]
    if mvps:
        lines.append("MVPs (local-first, simulated UX + EDD only for mvp-01):")
        for item in mvps:
            next_label = item.active_stage.upper() if item.active_stage else "none scheduled"
            lines.append(
                f"  {item.mvp_id}: {item.status}; scope {item.scope}; "
                f"UX {item.ux_completed}/2; EDD {item.completed}/6; next {next_label}"
            )
    if row["pending_gate"]:
        lines.append(gate_message(row["pending_gate"], row["pending_path"], row["pending_sha"], initiative, root))
    if prototype:
        lines.append(
            f"Prototype G2: {prototype['iteration_id']} · {prototype['status']} · "
            f"review integrity {prototype['integrity']}"
        )
        lines.append(f"Prototype preview (local): {prototype['preview_path']}")
        lines.append(f"Review bundle SHA-256: {prototype['bundle_sha']}")
        if prototype["error"]:
            lines.append(f"Prototype review warning: {prototype['error']}")
    if row["last_error"]:
        lines.append(f"Last error: {row['last_error']}")
    context = git_context(root, initiative)
    if context:
        lines.append(f"Git: {context['expected_branch']}  ·  {context['selected_root']}")
        if context["error"]:
            lines.append(f"Git warning: {context['error']}")
        else:
            lines.append(f"Git HEAD: {context['head'][:12]}  ·  {'DIRTY' if context['dirty'] else 'CLEAN'}")
    project = project_context(root, initiative)
    if project:
        lines.append(f"Integration: {project['integration_branch']} @ {project['integration_head'][:12]}")
        lines.append(f"Parent issue: {project['parent_url'] or 'not created'}")
        lines.append(f"MVP sub-issues: {len(project['mvp_issues'])}")
    return "\n".join(lines)


def history(root: Path, initiative: str) -> str:
    with open_db(root, initiative) as conn:
        events = conn.execute("SELECT created_at, kind, detail FROM events ORDER BY event_id").fetchall()
    return "\n".join(f"{x['created_at']} {x['kind']} {x['detail']}" for x in events)


def guided_start(root: Path, *, input_fn=None, launch_fn=None) -> int:
    """Single-command, local-first onboarding for an interactive terminal."""
    ask = input_fn or input
    print("\n  ◆ HYBRID → EDD WORKFLOW  ·  LOCAL-FIRST  ·  GUIDED START")
    print("  Local parent/MVP tickets are automatic. GitHub Issues are optional.\n")
    work = root / "work"
    initiatives: list[str] = []
    if work.is_dir():
        initiatives = sorted(
            item.name for item in work.iterdir()
            if item.is_dir() and not item.is_symlink()
            and IDENTIFIER.fullmatch(item.name)
            and (item / "agentflow.sqlite3").is_file()
        )
    if initiatives:
        print("  Existing projects: " + ", ".join(initiatives))
        default = initiatives[0] if len(initiatives) == 1 else ""
    else:
        print("  Start a new project.")
        default = "my-project"
    prompt = f"  Project ID [{default}]: " if default else "  Project ID: "
    try:
        initiative = ask(prompt).strip() or default
        if not initiative:
            raise ValueError("enter an initiative ID; existing projects are listed above")
        check_id(initiative)
        if not database_path(root, initiative).is_file():
            print("  " + initialize(root, initiative))
        else:
            print(f"  Resuming {initiative}.")
        binding = git_context(root, initiative)
        if binding:
            print(f"  Git worktree: {binding['selected_root']}")
        else:
            repo_path = ask("  Git checkout path (optional; Enter to skip): ").strip()
            if repo_path:
                print("  " + attach_repo(root, initiative, Path(repo_path)))
                binding = git_context(root, initiative)
        project = project_context(root, initiative)
        if binding and not project:
            answer = ask("  Create the local integration branch? [y/N]: ").strip().lower()
            if answer in {"y", "yes"}:
                print("  " + create_integration(root, initiative))
        print(f"\n  Project ticket: work/{initiative}/project-ticket.md\n")
    except (EOFError, KeyboardInterrupt):
        print("\n  Setup cancelled; existing project files were preserved.")
        return 130
    if launch_fn is None:
        # Resume existing REAL initiatives in the guided live interface. The
        # simulation TUI remains available for projects without live state.
        with open_db(root, initiative) as conn:
            has_live = bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_workflow'"
            ).fetchone() and conn.execute(
                "SELECT 1 FROM live_workflow WHERE singleton=1"
            ).fetchone())
        if has_live:
            if __package__:
                from . import live_console
            else:
                from prototype import live_console
            print("  Resuming the real human-gated Codex Coordinator.\n")
            return live_console.launch(root, initiative, ask=ask)
        print("  SIMULATION: [V] MVPs  [P] Project  [G] Git  [N] Start  [A] Approve  [Q] Quit\n")
        if __package__:
            from . import dashboard
        else:
            import dashboard
        launch_fn = dashboard.launch_tui
    return launch_fn(root, initiative, sys.modules[__name__])


def main() -> int:
    parser = argparse.ArgumentParser(description="Hybrid + EDD Coordinator (simulation, live Codex, MVP 04 evidence + UX review)")
    parser.add_argument("--root", type=Path, default=PACKAGE_ROOT, help="Working repository root")
    sub = parser.add_subparsers(dest="command")
    for command in (
        "init", "run", "status", "retry", "history", "approve", "changes", "tui",
        "repo-attach", "repo-status", "worktree-create", "worktree-select",
        "project-status", "tickets-status", "tickets-refresh", "integration-create", "integration-promote",
        "integration-snapshot", "integration-push", "tracker-create",
        "tracker-mvp", "tracker-sync", "codex-stage",
        "live-init", "live-run", "live-status", "live-approve", "live-changes",
        "live-retry", "live-reconcile", "live-preview", "live-evidence", "live-next", "live",
    ):
        p = sub.add_parser(command)
        if command == "live":
            p.add_argument("initiative", nargs="?")
            p.add_argument("--snapshot", action="store_true", help="Read-only live dashboard for scripts/CI")
            p.add_argument("--codex-bin", default="codex", help="Codex executable (default: codex)")
            p.add_argument("--model", help="Optional Codex model")
            p.add_argument("--timeout", type=int, default=900)
        else:
            p.add_argument("initiative")
        if command == "run":
            p.add_argument("--simulate-invalid-handoff", action="store_true")
        if command == "codex-stage":
            p.add_argument("--stage", required=True, help="One specialist stage (e.g. discovery, ux_design, red, verify)")
            p.add_argument("--model", help="Optional Codex model; otherwise use configured default")
            p.add_argument("--timeout", type=int, default=900, help="Max seconds for each of two fresh Codex sessions")
            p.add_argument("--codex-bin", default="codex", help="Codex CLI executable (default: codex)")
            p.add_argument("--dry-run", action="store_true", help="Read-only preflight; do not start an agent")
            p.add_argument("--confirm", action="store_true", help="Explicitly authorize this live Codex stage")
        if command in {"live-run", "live-approve"}:
            p.add_argument("--model", help="Optional configured Codex model")
            p.add_argument("--timeout", type=int, default=900, help="Max seconds per specialist/Handoff session")
            p.add_argument("--codex-bin", default="codex", help="Codex CLI executable")
        if command == "live-run":
            p.add_argument("--confirm", action="store_true", help="Authorize Codex sessions until next gate")
        if command == "live-approve":
            p.add_argument("--gate", required=True, help="Exact human gate ID")
            p.add_argument("--sha", required=True, help="Exact pending report SHA-256")
            p.add_argument("--waive-preview", action="store_true", help="G2 only: record missing live preview")
            p.add_argument("--reason", default="", help="Explanation for G2 preview waiver")
            p.add_argument("--bundle-sha", help="G2 preview or G6/G7 host-evidence bundle SHA-256")
            p.add_argument("--review-notes", default="", help="Actual manual G2/G6/G7 observations")
            p.add_argument("--continue", dest="continue_run", action="store_true",
                           help="Authorize automatic Codex sessions after this approval")
        if command == "live-changes":
            p.add_argument("--reason", required=True, help="Actionable human feedback")
            p.add_argument("--target", choices=["green", "evidence", "ux_inspect", "prove"],
                           help="For G6/G7: green fixes code; evidence reruns checks; ux_inspect re-reviews")
        if command == "live-preview":
            p.add_argument("--open", action="store_true", help="Open the loopback preview URL in the host browser")
            p.add_argument("--check-only", action="store_true", help="Check frozen preview integrity without starting HTTP")
        if command == "live-evidence":
            p.add_argument("--test", action="append", default=[],
                           help="Required host-selected functional test command; repeatable")
            p.add_argument("--ux-check", action="append", default=[],
                           help="Required UI/UX test/capture command; repeatable")
            p.add_argument("--ux-artifact", action="append", default=[],
                           help="Required screenshot produced by --ux-check, relative to isolated checkout")
            p.add_argument("--timeout", type=int, default=120, help="Max seconds per evidence check (1–300)")
            p.add_argument("--confirm", action="store_true", help="Authorize these exact commands to run locally")
        if command == "live-next":
            p.add_argument("--mvp", help="Exactly the next sequential, G4-listed MVP (e.g. mvp-02)")
            p.add_argument("--confirm", action="store_true", help="Explicitly schedule the next MVP after G7")
        if command == "approve":
            p.add_argument("--gate", required=True)
            p.add_argument("--sha", required=True)
        if command == "changes":
            p.add_argument("--reason", required=True)
        if command == "tui":
            p.add_argument("--snapshot", action="store_true", help="Print a dashboard preview without curses")
            p.add_argument("--width", type=int, default=80, help="Snapshot width in columns (default: 80)")
            p.add_argument("--height", type=int, default=24, help="Snapshot height in rows (default: 24)")
            views = p.add_mutually_exclusive_group()
            views.add_argument("--git-panel", action="store_true", help="Show Git details in a dashboard snapshot")
            views.add_argument("--project-panel", action="store_true", help="Show project ticket and integration summary")
            views.add_argument("--mvp-panel", action="store_true", help="Show MVP scope, two UI/UX checkpoints and six EDD stages")
            p.add_argument("--repo", type=Path, help="Attach this local checkout when opening a new or unstarted initiative")
        if command in ("repo-attach", "worktree-select"):
            p.add_argument("--path", type=Path, required=True, help="Existing local Git checkout path")
        if command == "worktree-create":
            p.add_argument("--mvp", required=True, help="MVP ID, such as mvp-01")
            p.add_argument("--base", default="HEAD", help="Existing base revision (default: HEAD)")
            p.add_argument("--path", type=Path, help="Optional new worktree destination")
        if command == "integration-create":
            p.add_argument("--base", default="HEAD", help="Base commit for new integration branch")
            p.add_argument("--path", type=Path, help="Optional integration worktree directory")
        if command == "integration-promote":
            p.add_argument("--mvp", required=True)
            p.add_argument("--source", required=True, type=Path, help="Clean MVP worktree to integrate")
        if command == "integration-push":
            p.add_argument("--remote", default="origin")
        if command == "tracker-create":
            p.add_argument("--repo", required=True, help="GitHub OWNER/REPO matching origin")
            p.add_argument("--title", help="Optional parent ticket title")
        if command == "tracker-mvp":
            p.add_argument("--mvp", required=True)
            p.add_argument("--title", help="Optional child ticket title")
        if command in {"integration-snapshot", "integration-push", "tracker-create", "tracker-mvp", "tracker-sync"}:
            p.add_argument("--confirm", action="store_true", help="Confirm explicit commit, push, or GitHub write")
    args = parser.parse_args()
    try:
        root = args.root.resolve(strict=True)
        if args.command is None:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError(
                    "guided setup needs an interactive terminal; "
                    "run uv run workflow.py --help for scripted commands"
                )
            return guided_start(root)
        if args.command == "live":
            if __package__:
                from . import live_console
            else:
                from prototype import live_console
            if not args.snapshot and (not sys.stdin.isatty() or not sys.stdout.isatty()):
                raise ValueError("guided live interface needs a terminal; use live <id> --snapshot for read-only view")
            return live_console.launch(
                root, args.initiative, codex_bin=args.codex_bin, model=args.model,
                timeout=args.timeout, check_only=args.snapshot,
            )
        if args.command == "codex-stage":
            if __package__:
                from . import codex_runtime
            else:
                from prototype import codex_runtime
            plan = codex_runtime.build_plan(
                root, args.initiative, args.stage, codex_bin=args.codex_bin, model=args.model,
            )
            if args.dry_run:
                print("LIVE CODEX PREFLIGHT (no agents launched)\n" + codex_runtime.describe_plan(plan))
                return 0
            if not args.confirm:
                raise ValueError("live codex-stage requires --confirm; use --dry-run to inspect its scope first")
            print("LIVE CODEX — starting an isolated specialist and handoff session", flush=True)
            print(codex_runtime.describe_plan(plan), flush=True)
            result = codex_runtime.run_stage(plan, timeout=args.timeout)
            print("Run record: " + result["run_dir"])
            print("Result: " + result["state"])
            if result.get("error"):
                print("Reason: " + result["error"])
            print("Candidate only — simulation gates and local tickets were not changed.")
            return 0 if result["state"] == "CANDIDATE_VALIDATED" else 2
        if args.command.startswith("live-"):
            if __package__:
                from . import live_coordinator
            else:
                from prototype import live_coordinator
            if args.command == "live-init":
                output = live_coordinator.initialize(root, args.initiative)
            elif args.command == "live-run":
                if not args.confirm:
                    raise ValueError("live-run requires --confirm before Codex can start")
                output = "\n".join(live_coordinator.run(
                    root, args.initiative, codex_bin=args.codex_bin,
                    model=args.model, timeout=args.timeout,
                ))
            elif args.command == "live-status":
                output = live_coordinator.status(root, args.initiative)
            elif args.command == "live-approve":
                output = live_coordinator.approve(
                    root, args.initiative, args.gate, args.sha,
                    waive_preview=args.waive_preview, reason=args.reason,
                    bundle_sha=args.bundle_sha, review_notes=args.review_notes,
                )
                if args.continue_run:
                    output += "\n" + "\n".join(live_coordinator.run(
                        root, args.initiative, codex_bin=args.codex_bin,
                        model=args.model, timeout=args.timeout,
                    ))
            elif args.command == "live-changes":
                output = live_coordinator.changes(
                    root, args.initiative, args.reason, target_stage=args.target
                )
            elif args.command == "live-evidence":
                if not args.confirm:
                    raise ValueError("live-evidence requires --confirm before executing local commands")
                if __package__:
                    from . import live_evidence
                else:
                    from prototype import live_evidence
                output = live_evidence.collect(
                    root, args.initiative, tests=args.test, ux_checks=args.ux_check,
                    ux_artifacts=args.ux_artifact, timeout=args.timeout,
                )
            elif args.command == "live-next":
                if not args.confirm:
                    raise ValueError("use the guided live interface [M] or pass --confirm to schedule an MVP")
                output = live_coordinator.schedule_next(root, args.initiative, mvp_id=args.mvp)
            elif args.command == "live-preview":
                if __package__:
                    from . import live_prototype
                else:
                    from prototype import live_prototype
                if args.check_only:
                    review = live_prototype.describe(root, args.initiative)
                    output = (
                        f"Frozen Codex preview {review['iteration']} · {review['status']}\n"
                        f"HTML: {review['preview_path']}\n"
                        f"Preview SHA-256: {review['preview_sha']}\n"
                        f"Bundle SHA-256: {review['bundle_sha']}\n"
                        "Visual interactions: NOT_TESTED until human review."
                    )
                else:
                    output = live_prototype.serve(root, args.initiative, open_browser=args.open)
            elif args.command == "live-retry":
                output = live_coordinator.retry(root, args.initiative)
            else:
                output = live_coordinator.reconcile(root, args.initiative)
            print(output)
            return 0
        if args.command == "tui":
            if __package__:
                from . import dashboard
            else:
                import dashboard

            if args.snapshot and args.repo:
                raise ValueError("--snapshot is read-only; attach a Git repository with repo-attach first")
            if not args.snapshot and (not sys.stdin.isatty() or not sys.stdout.isatty()):
                raise ValueError("interactive dashboard needs a terminal; use 'tui <id> --snapshot' for plain output")
            if not args.snapshot and not database_path(root, args.initiative).is_file():
                print(initialize(root, args.initiative))
            if args.repo:
                print(attach_repo(root, args.initiative, args.repo))
            if args.snapshot:
                if not (20 <= args.width <= 240 and 8 <= args.height <= 100):
                    raise ValueError("snapshot dimensions must be 20–240 columns and 8–100 rows")
                print(dashboard.snapshot_text(
                    root, args.initiative, sys.modules[__name__],
                    width=args.width, height=args.height,
                    show_git=args.git_panel, show_project=args.project_panel,
                    show_mvp=args.mvp_panel,
                ))
                return 0
            return dashboard.launch_tui(root, args.initiative, sys.modules[__name__])
        if args.command == "init":
            output = initialize(root, args.initiative)
        elif args.command == "run":
            output = "\n".join(advance(root, args.initiative, invalid_handoff=args.simulate_invalid_handoff))
        elif args.command == "status":
            output = status(root, args.initiative)
        elif args.command == "retry":
            output = "\n".join(retry(root, args.initiative))
        elif args.command == "history":
            output = history(root, args.initiative)
        elif args.command == "repo-attach":
            output = attach_repo(root, args.initiative, args.path)
        elif args.command == "repo-status":
            output = git_status(root, args.initiative)
        elif args.command == "project-status":
            output = project_status(root, args.initiative)
        elif args.command == "tickets-status":
            output = (
                f"Parent ticket: {root / 'work' / args.initiative / 'project-ticket.md'}\n"
                f"Project status: {status(root, args.initiative)}"
            )
        elif args.command == "tickets-refresh":
            refresh_tickets(root, args.initiative)
            output = f"Refreshed local parent and MVP tickets for {args.initiative}"
        elif args.command == "worktree-create":
            output = create_worktree(root, args.initiative, args.mvp, base=args.base, path=args.path)
        elif args.command == "worktree-select":
            output = select_worktree(root, args.initiative, args.path)
        elif args.command == "integration-create":
            output = create_integration(root, args.initiative, base=args.base, path=args.path)
        elif args.command == "integration-promote":
            output = promote_mvp(root, args.initiative, mvp=args.mvp, source=args.source)
        elif args.command in {"integration-snapshot", "integration-push", "tracker-create", "tracker-mvp", "tracker-sync"}:
            if not args.confirm:
                raise ValueError(f"{args.command} requires --confirm; review the files, target and external effects first")
            if args.command == "integration-snapshot":
                output = snapshot_project_artifacts(root, args.initiative)
            elif args.command == "integration-push":
                output = push_project(root, args.initiative, remote=args.remote)
            elif args.command == "tracker-create":
                output = create_parent_ticket(root, args.initiative, repo=args.repo, title=args.title)
            elif args.command == "tracker-mvp":
                output = create_mvp_ticket(root, args.initiative, mvp=args.mvp, title=args.title)
            else:
                output = sync_project_tickets(root, args.initiative)
        elif args.command == "approve":
            output = "\n".join(approve(root, args.initiative, args.gate, args.sha))
        else:
            output = request_changes(root, args.initiative, args.reason)
        print(output)
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
