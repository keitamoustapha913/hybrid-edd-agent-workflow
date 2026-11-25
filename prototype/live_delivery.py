"""MVP 06: human-authorized Git delivery of a real, G7-approved MVP.

Only local Markdown/SQLite are required. GitHub Issues are never created here.
Each mutation is a separate terminal decision; the remote push is non-force.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import subprocess

from . import agentflow, codex_runtime, git_adapter, live_coordinator, live_evidence


TABLE = """
CREATE TABLE IF NOT EXISTS live_deliveries (
    mvp_id TEXT PRIMARY KEY,
    proof_approval_id INTEGER NOT NULL,
    approved_head TEXT NOT NULL,
    approved_fingerprint TEXT NOT NULL,
    approved_manifest TEXT NOT NULL,
    source_tree TEXT,
    source_commit TEXT,
    status TEXT NOT NULL,
    integration_head TEXT,
    docs_head TEXT,
    pushed_head TEXT,
    updated_at TEXT NOT NULL
)
"""
COMMITTED = {"COMMITTED", "PROMOTED", "SNAPSHOTTED", "PUSHED"}
PUBLISHED = {"SNAPSHOTTED", "PUSHED"}


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute(TABLE)


def _row(conn: sqlite3.Connection, mvp: str) -> sqlite3.Row | None:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_deliveries'").fetchone():
        return None
    return conn.execute("SELECT * FROM live_deliveries WHERE mvp_id=?", (mvp,)).fetchone()


def delivery(root: Path, initiative: str, mvp: str | None = None) -> dict | None:
    """Read-only view of a durable per-MVP delivery receipt."""
    with agentflow.open_db(root, initiative) as conn:
        mvp_id = mvp or live_coordinator._active_mvp(live_coordinator._state(conn))
        found = _row(conn, mvp_id)
        return dict(found) if found else None


def _proof(conn: sqlite3.Connection, state: sqlite3.Row) -> sqlite3.Row:
    mvp = live_coordinator._active_mvp(state)
    if state["status"] != "COMPLETED":
        raise ValueError("Git delivery requires a completed MVP and human G7 proof approval")
    approval = conn.execute(
        "SELECT id FROM live_approvals WHERE gate='G7_PROVE' AND mvp_id=? "
        "AND revoked_at IS NULL ORDER BY id DESC LIMIT 1", (mvp,),
    ).fetchone()
    completion = conn.execute(
        "SELECT * FROM live_mvp_completions WHERE mvp_id=?", (mvp,),
    ).fetchone()
    if not approval or not completion or completion["proof_approval_id"] != approval["id"]:
        raise ValueError("G7 completion and its human proof approval are missing or disagree")
    return completion


def _approved_source(conn: sqlite3.Connection, root: Path, initiative: str,
                     state: sqlite3.Row) -> tuple[git_adapter.RepositoryInfo, sqlite3.Row]:
    completion = _proof(conn, state)
    mvp = live_coordinator._active_mvp(state)
    receipt = _row(conn, mvp)
    try:
        source = live_coordinator._source(conn, state, root, initiative)
    except ValueError:
        # After a failed commit, `git add` can move an untracked file into
        # the index without changing any reviewed file bytes. Reconciliation
        # may explicitly permit retry only if the complete manifest matches.
        if not receipt or receipt["status"] != "RETRYABLE":
            raise
        binding = agentflow.git_context(root, initiative)
        if not binding or binding["error"]:
            raise
        source = Path(binding["selected_root"])
        git_info = git_adapter.inspect(source)
        if (git_info.head != state["expected_head"] or
                _manifest(source) != receipt["approved_manifest"] or
                receipt["approved_fingerprint"] != state["expected_fingerprint"]):
            raise ValueError("retried commit source differs from the G7-approved manifest")
        live_coordinator._approved_contracts(conn, root, initiative)
    info = git_adapter.inspect(source)
    expected = f"agentflow/{initiative}/{mvp}"
    if info.branch != expected:
        raise ValueError(f"delivery requires dedicated {expected} worktree; use [I] Setup before starting the MVP")
    original = (completion["source_head"] == state["expected_head"] and
                completion["source_fingerprint"] == state["expected_fingerprint"])
    bridged = (receipt is not None and receipt["status"] in COMMITTED and
               receipt["approved_head"] == completion["source_head"] and
               receipt["approved_fingerprint"] == completion["source_fingerprint"] and
               receipt["source_commit"] == state["expected_head"] and
               receipt["proof_approval_id"] == completion["proof_approval_id"])
    if not (original or bridged):
        raise ValueError("G7 source identity no longer matches the approved baseline")
    return info, completion


def _git(repo: Path, *args: str, check: bool = True) -> str:
    return git_adapter._git(repo, *args, check=check).stdout.strip()


def _current_tree(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD^{tree}")


def _manifest(repo: Path) -> str:
    """Stage-independent digest of all tracked/untracked, non-ignored files.

    Listing HEAD paths as well records deletions even after `git add -A`
    removes the path from the index. No file contents are logged or exported.
    """
    paths: set[bytes] = set()
    for args in (("ls-tree", "-r", "--name-only", "-z", "HEAD"),
                 ("ls-files", "--cached", "-z"),
                 ("ls-files", "--others", "--exclude-standard", "-z")):
        # Byte-safe subprocess read for filenames that are not valid UTF-8.
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                timeout=30, check=False)
        if result.returncode:
            raise ValueError("cannot inspect full Git content manifest")
        paths.update(p for p in result.stdout.split(b"\0") if p)
    digest = hashlib.sha256()
    for filename in sorted(paths):
        candidate = repo / os.fsdecode(filename)
        for parent in candidate.parents:
            if parent == repo:
                break
            if parent.is_symlink():
                raise ValueError("source manifest path traverses a symlinked directory")
        digest.update(filename + b"\0")
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            digest.update(b"deleted\0")
            continue
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(b"link\0" + os.fsencode(os.readlink(candidate)))
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(b"regular\0" + str(metadata.st_mode & 0o111).encode())
            with candidate.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            raise ValueError(f"Git source has an unsupported special path: {candidate}")
    return digest.hexdigest()


def prepare(root: Path, initiative: str) -> str:
    """One-time local integration + MVP worktree setup before any Codex run."""
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        state = live_coordinator._state(conn)
        if state["status"] != "READY" or state["stage_index"] != 0:
            raise ValueError("Git setup must happen before the first Codex session; existing approvals cannot be rebound")
        if conn.execute("SELECT 1 FROM live_approvals LIMIT 1").fetchone() or conn.execute(
            "SELECT 1 FROM live_events WHERE kind='CODEX_RESERVED' LIMIT 1"
        ).fetchone():
            raise ValueError("Git setup cannot rebind a workflow after Codex or human approvals")
        source = live_coordinator._source(conn, state, root, initiative)
        info = git_adapter.inspect(source)
        if info.dirty:
            raise ValueError("Git setup requires a clean source worktree")
    if not agentflow.project_context(root, initiative):
        agentflow.create_integration(root, initiative)
    context = agentflow.git_context(root, initiative)
    expected_branch = f"agentflow/{initiative}/mvp-01"
    if context["branch"] != expected_branch:
        agentflow.create_worktree(root, initiative, "mvp-01")
    selected = agentflow.git_context(root, initiative)
    if selected["error"]:
        raise ValueError(f"new MVP worktree binding is invalid: {selected['error']}")
    chosen = git_adapter.inspect(Path(selected["selected_root"]))
    if chosen.branch != expected_branch or chosen.dirty:
        raise ValueError("selected MVP worktree has an unexpected branch or dirty files")
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = live_coordinator._state(conn)
        if state["stage_index"] != 0 or state["status"] != "READY":
            raise ValueError("live stage changed during Git setup; inspect before continuing")
        already = (state["expected_head"] == chosen.head and
                   state["expected_branch"] == chosen.branch and
                   state["expected_common_dir"] == str(chosen.common_dir))
        if not already:
            conn.execute(
                "UPDATE live_workflow SET expected_head=?, expected_branch=?, "
                "expected_common_dir=?, expected_fingerprint=? WHERE singleton=1",
                (chosen.head, chosen.branch, str(chosen.common_dir),
                 codex_runtime._git_fingerprint(chosen.root)),
            )
            live_coordinator._event(conn, "LIVE_INITIAL_MVP_WORKTREE_BOUND", {
                "branch": chosen.branch, "head": chosen.head, "worktree": str(chosen.root),
            })
    live_coordinator.refresh_tickets(root, initiative)
    return ("Git delivery already prepared: " if already else "Prepared Git integration + MVP source: ") + (
        f"{chosen.branch} @ {chosen.head[:12]}. No remote push or Codex run."
    )


def commit(root: Path, initiative: str) -> str:
    """Commit exactly the worktree bytes approved at G7 after human confirmation.

    A write-ahead record permits explicit recovery if Git succeeds but the
    Coordinator loses control before recording the new HEAD.
    """
    root = root.resolve(strict=True)
    agentflow._verified_integration(root, initiative)
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = live_coordinator._state(conn)
        mvp = live_coordinator._active_mvp(state)
        ensure(conn)
        previous = _row(conn, mvp)
        if previous and previous["status"] != "RETRYABLE":
            raise ValueError(f"delivery already recorded as {previous['status']}; inspect instead of repeating")
        info, proof = _approved_source(conn, root, initiative, state)
        # Refuse to create a commit with an invented identity.
        _git(info.root, "var", "GIT_AUTHOR_IDENT")
        _git(info.root, "var", "GIT_COMMITTER_IDENT")
        if previous:
            conn.execute("UPDATE live_deliveries SET status='COMMITTING',source_tree=NULL,updated_at=? "
                         "WHERE mvp_id=? AND status='RETRYABLE'",
                         (live_coordinator._now(), mvp))
        else:
            conn.execute(
                "INSERT INTO live_deliveries(mvp_id,proof_approval_id,approved_head,"
                "approved_fingerprint,approved_manifest,status,updated_at) "
                "VALUES(?,?,?,?,?,'COMMITTING',?)",
                (mvp, proof["proof_approval_id"], state["expected_head"],
                 state["expected_fingerprint"], _manifest(info.root), live_coordinator._now()),
            )
        live_coordinator._event(conn, "GIT_COMMIT_RESERVED", {"mvp": mvp, "head": info.head})

    # The original reviewed content is checked again after staging. Git hooks
    # may stop or alter a commit; any uncertain outcome remains explicitly held.
    _git(info.root, "add", "-A", "--")
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = live_coordinator._state(conn)
        recorded = _row(conn, mvp)
        if (git_adapter.inspect(info.root).head != info.head or
                _manifest(info.root) != recorded["approved_manifest"]):
            raise ValueError("source changed while staging; commit held for manual reconciliation")
        tree = _git(info.root, "write-tree")
        conn.execute("UPDATE live_deliveries SET source_tree=?,updated_at=? WHERE mvp_id=?",
                     (tree, live_coordinator._now(), mvp))
    if git_adapter.inspect(info.root).dirty:
        _git(info.root, "commit", "-m", f"feat(agentflow): verified {mvp}")
    return _complete_commit(root, initiative, mvp)


def _complete_commit(root: Path, initiative: str, mvp: str) -> str:
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = live_coordinator._state(conn)
        record = _row(conn, mvp)
        if not record or record["status"] != "COMMITTING" or not record["source_tree"]:
            raise ValueError("there is no reviewable pending Git commit")
        context = agentflow.git_context(root, initiative)
        info = git_adapter.inspect(Path(context["selected_root"]))
        if (info.branch != state["expected_branch"] or info.dirty or
                _current_tree(info.root) != record["source_tree"]):
            raise ValueError("commit result is dirty, modified, or on the wrong branch; reconcile manually")
        parents = _git(info.root, "rev-list", "--parents", "-n", "1", "HEAD").split()
        if (info.head != record["approved_head"] and
                (len(parents) != 2 or parents[1] != record["approved_head"])):
            raise ValueError("commit is not a direct child of the human-reviewed source HEAD")
        if (state["expected_head"] != record["approved_head"] or
                state["expected_fingerprint"] != record["approved_fingerprint"]):
            raise ValueError("G7 source baseline changed during Git delivery")
        fingerprint = codex_runtime._git_fingerprint(info.root)
        conn.execute("UPDATE live_workflow SET expected_head=?, expected_fingerprint=? WHERE singleton=1",
                     (info.head, fingerprint))
        conn.execute(
            "UPDATE live_deliveries SET status='COMMITTED',source_commit=?,updated_at=? WHERE mvp_id=?",
            (info.head, live_coordinator._now(), mvp),
        )
        live_coordinator._event(conn, "GIT_MVP_COMMITTED", {
            "mvp": mvp, "source_head": info.head, "source_tree": record["source_tree"],
            "proof_approval_id": record["proof_approval_id"],
        })
    live_coordinator.refresh_tickets(root, initiative)
    return f"Committed approved {mvp.upper()} source at {info.head[:12]} (local, not pushed)."


def reconcile_commit(root: Path, initiative: str) -> str:
    """Explicitly inspect and recover only an exact, previously reserved commit."""
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        state = live_coordinator._state(conn)
        mvp = live_coordinator._active_mvp(state)
        row = _row(conn, mvp)
        if not row or row["status"] != "COMMITTING":
            raise ValueError("there is no uncertain commit to reconcile")
        context = agentflow.git_context(root, initiative)
        info = git_adapter.inspect(Path(context["selected_root"]))
        if (info.head == row["approved_head"] and
                _manifest(info.root) == row["approved_manifest"]):
            # No new commit happened. The operator can retry after reviewing
            # the current stage/index, without resetting user content.
            with agentflow.open_db(root, initiative) as transaction:
                transaction.execute("UPDATE live_deliveries SET status='RETRYABLE',updated_at=? "
                                    "WHERE mvp_id=? AND status='COMMITTING'",
                                    (live_coordinator._now(), mvp))
            return "No commit occurred; source still matches G7. Commit may be retried after review."
    return _complete_commit(root, initiative, mvp)


def promote(root: Path, initiative: str) -> str:
    with agentflow.open_db(root, initiative) as conn:
        state = live_coordinator._state(conn)
        info, proof = _approved_source(conn, root, initiative, state)
        row = _row(conn, live_coordinator._active_mvp(state))
        if not row or row["status"] != "COMMITTED":
            raise ValueError("commit the human G7-approved MVP before promoting it")
        if info.head != row["source_commit"] or proof["proof_approval_id"] != row["proof_approval_id"]:
            raise ValueError("source or G7 proof differs from the recorded delivery")
        mvp = live_coordinator._active_mvp(state)
    result = agentflow.promote_mvp(root, initiative, mvp=mvp, source=info.root)
    context = agentflow.project_context(root, initiative)
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("UPDATE live_deliveries SET status='PROMOTED',integration_head=?,updated_at=? "
                     "WHERE mvp_id=? AND status='COMMITTED'",
                     (context["integration_head"], live_coordinator._now(), mvp))
        live_coordinator._event(conn, "LIVE_MVP_PROMOTED", {
            "mvp": mvp, "integration_head": context["integration_head"]})
    live_coordinator.refresh_tickets(root, initiative)
    return f"Promoted {mvp.upper()} into local integration at {context['integration_head'][:12]}. No push."


def snapshot(root: Path, initiative: str) -> str:
    with agentflow.open_db(root, initiative) as conn:
        state = live_coordinator._state(conn)
        _approved_source(conn, root, initiative, state)
        mvp = live_coordinator._active_mvp(state)
        receipt = _row(conn, mvp)
        if not receipt or receipt["status"] != "PROMOTED":
            raise ValueError("promote the committed MVP before snapshotting local plans and tickets")
    result = agentflow.snapshot_project_artifacts(root, initiative)
    context = agentflow.project_context(root, initiative)
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("UPDATE live_deliveries SET status='SNAPSHOTTED',docs_head=?,updated_at=? "
                     "WHERE mvp_id=? AND status='PROMOTED'",
                     (context["integration_head"], live_coordinator._now(), mvp))
        live_coordinator._event(conn, "LIVE_ARTIFACTS_SNAPSHOTTED", {
            "mvp": mvp, "head": context["integration_head"]})
    live_coordinator.refresh_tickets(root, initiative)
    return f"Snapshotted local plans/reports/tickets for {mvp.upper()} at {context['integration_head'][:12]}. No push."


def push(root: Path, initiative: str) -> str:
    with agentflow.open_db(root, initiative) as conn:
        state = live_coordinator._state(conn)
        _approved_source(conn, root, initiative, state)
        mvp = live_coordinator._active_mvp(state)
        receipt = _row(conn, mvp)
        if not receipt or receipt["status"] != "SNAPSHOTTED":
            raise ValueError("snapshot local plans and tickets before pushing integration")
    context = agentflow.project_context(root, initiative)
    if context["error"] or context["integration_head"] != receipt["docs_head"]:
        raise ValueError("integration moved after the reviewed snapshot; refuse stale remote push")
    result = agentflow.push_project(root, initiative)
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("UPDATE live_deliveries SET status='PUSHED',pushed_head=?,updated_at=? "
                     "WHERE mvp_id=? AND status='SNAPSHOTTED'",
                     (receipt["docs_head"], live_coordinator._now(), mvp))
        live_coordinator._event(conn, "LIVE_INTEGRATION_PUSHED", {"mvp": mvp, "head": receipt["docs_head"]})
    live_coordinator.refresh_tickets(root, initiative)
    return f"Pushed reviewed {mvp.upper()} integration revision {receipt['docs_head'][:12]} (non-force)."


def next_worktree(root: Path, initiative: str, mvp: str) -> git_adapter.RepositoryInfo | None:
    """Prepare a fresh worktree at the current integration tip for the next MVP.

    Used only after explicit human scheduling from the live terminal. Creating
    the branch never changes the old approved MVP worktree.
    """
    project = agentflow.project_context(root, initiative)
    if not project:
        return None  # No integration opt-in: preserve MVP 05 local-only behavior.
    if project["error"] or project["dirty"]:
        raise ValueError("integration worktree is invalid or dirty; cannot start a new MVP")
    previous = f"mvp-{int(mvp[4:]) - 1:02d}"
    receipt = delivery(root, initiative, previous)
    if not receipt or receipt["status"] not in PUBLISHED:
        raise ValueError("complete local integration promotion and ticket snapshot before the next MVP")
    integration = Path(project["integration_root"])
    branch = f"agentflow/{initiative}/{mvp}"
    current = git_adapter.inspect(integration)
    for tree in current.worktrees:
        if tree.branch == branch:
            existing = git_adapter.inspect(tree.path)
            if (existing.head != current.head or existing.dirty or
                    existing.common_dir != current.common_dir):
                raise ValueError("existing next MVP worktree is dirty or not at the integration tip")
            return existing
    return git_adapter.create_mvp_worktree(integration, initiative=initiative, mvp=mvp,
                                           base=current.head)


def status(root: Path, initiative: str) -> str:
    project = agentflow.project_context(root, initiative)
    with agentflow.open_db(root, initiative) as conn:
        state = live_coordinator._state(conn)
        mvp = live_coordinator._active_mvp(state)
        entries = (conn.execute("SELECT * FROM live_deliveries ORDER BY mvp_id").fetchall()
                   if conn.execute("SELECT 1 FROM sqlite_master WHERE name='live_deliveries'").fetchone()
                   else [])
    lines = [f"Active {mvp.upper()}: {state['status']}",
             "Integration: " + (project["integration_branch"] if project else "not set up"),
             "Local parent ticket: work/" + initiative + "/project-ticket.md",
             "GitHub Issues: optional; no issue changes are made by Git delivery."]
    if project:
        condition = project["error"] or ("DIRTY" if project["dirty"] else "CLEAN")
        lines.extend([f"Integration worktree: {project['integration_root']}",
                      f"Integration HEAD: {project['integration_head'][:12]}",
                      f"Integration condition: {condition}"])
    for item in entries:
        lines.append(f"{item['mvp_id'].upper()}: {item['status']} · "
                     f"{(item['source_commit'] or item['approved_head'])[:12]}")
    if not entries:
        lines.append("Delivery receipts: none yet (G7 approval required).")
    return "\n".join(lines)
