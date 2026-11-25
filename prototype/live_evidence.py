"""MVP 04: host-observed checks and visual artifacts before UIUX inspection.

Evidence commands are explicitly supplied by the human, never copied from an
agent's report. They execute without shell expansion in a temporary source
snapshot. This is isolation from the selected checkout, NOT an OS security
sandbox: only run commands you trust in an appropriately restricted machine.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import uuid

from . import agentflow, codex_runtime


MAX_CHECKS = 8
MAX_SECONDS = 300
MAX_FILES = 30_000
MAX_SOURCE_BYTES = 500_000_000
MAX_LOG_BYTES = 2_000_000
MAX_IMAGE_BYTES = 15_000_000
MAX_IMAGE_COUNT = 16
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(data)


def _json_bytes(record: dict) -> bytes:
    return (json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _relative(base: Path, relative: str) -> Path:
    if not relative or "\\" in relative or "\x00" in relative:
        raise ValueError("evidence path must be a nonempty, local POSIX-relative path")
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", "..", ".git"} for part in path.parts):
        raise ValueError("evidence path must not escape the isolated source snapshot")
    return codex_runtime._relative_file(base, relative)


def _image_kind(file: Path) -> str:
    if file.is_symlink() or not file.is_file():
        raise ValueError(f"missing or symlinked UX screenshot: {file.name}")
    if not 20 <= file.stat().st_size <= MAX_IMAGE_BYTES:
        raise ValueError(f"UX screenshot has invalid size: {file.name}")
    with file.open("rb") as source:
        header = source.read(32)
    ext = file.suffix.lower()
    if ext == ".png" and header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR":
        if int.from_bytes(header[16:20], "big") > 0 and int.from_bytes(header[20:24], "big") > 0:
            return "PNG"
    if ext in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if ext == ".webp" and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "WEBP"
    raise ValueError(f"UX screenshot has an unsupported or mismatched image signature: {file.name}")


def ensure(conn: sqlite3.Connection) -> None:
    # Avoid executescript(): it commits an existing BEGIN IMMEDIATE transaction.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS live_evidence_runs (
            run_id TEXT PRIMARY KEY, source_head TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL, bundle_sha TEXT NOT NULL,
            receipt_sha TEXT NOT NULL, at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PASS','STALE')),
            mvp_id TEXT NOT NULL DEFAULT 'mvp-01'
        )"""
    )
    if "mvp_id" not in {item[1] for item in conn.execute("PRAGMA table_info(live_evidence_runs)")}:
        conn.execute("ALTER TABLE live_evidence_runs ADD COLUMN mvp_id TEXT NOT NULL DEFAULT 'mvp-01'")


def _current_mvp(conn: sqlite3.Connection) -> str:
    state = conn.execute("SELECT * FROM live_workflow WHERE singleton=1").fetchone()
    return state["active_mvp"] if state and "active_mvp" in state.keys() else "mvp-01"


def latest(conn: sqlite3.Connection, mvp_id: str | None = None) -> sqlite3.Row | None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_evidence_runs'"
    ).fetchone():
        return None
    mvp = mvp_id or _current_mvp(conn)
    if "mvp_id" not in {item[1] for item in conn.execute("PRAGMA table_info(live_evidence_runs)")}:
        return (conn.execute("SELECT * FROM live_evidence_runs ORDER BY at DESC, rowid DESC LIMIT 1").fetchone()
                if mvp == "mvp-01" else None)
    return conn.execute(
        "SELECT * FROM live_evidence_runs WHERE mvp_id=? "
        "ORDER BY at DESC, rowid DESC LIMIT 1", (mvp,),
    ).fetchone()


def _git_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True, check=False, timeout=45,
    )
    if result.returncode:
        raise ValueError("cannot list selected Git checkout files for independent evidence")
    files = sorted({os.fsdecode(item) for item in result.stdout.split(b"\0") if item})
    if not files or len(files) > MAX_FILES:
        raise ValueError("empty or oversized source snapshot; narrow the selected worktree")
    return files


def _copy_snapshot(repo: Path, target: Path) -> tuple[int, int]:
    size = 0
    files = _git_files(repo)
    for relative in files:
        original = _relative(repo, relative)
        duplicate = _relative(target, relative)
        if not original.is_file() or original.is_symlink():
            raise ValueError(f"snapshot does not support symlinks, deletions or special files: {relative}")
        size += original.stat().st_size
        if size > MAX_SOURCE_BYTES:
            raise ValueError("source snapshot exceeds 500 MB; use a smaller selected worktree")
        duplicate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, duplicate)
    return len(files), size


def _argv(command: str) -> list[str]:
    if not command or len(command) > 1_000 or "\n" in command or "\x00" in command:
        raise ValueError("verification command must be a single bounded argument vector")
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise ValueError("verification command has invalid quoting") from error
    if not argv or argv[0].startswith("-") or len(argv) > 40:
        raise ValueError("verification command is empty or has too many arguments")
    return argv


def _invoke(snapshot: Path, evidence_dir: Path, command: str,
            category: str, number: int, timeout: int) -> dict:
    argv = _argv(command)
    prefix = f"commands/{number:02d}-{category}"
    out_path = evidence_dir / f"{prefix}.stdout.txt"
    err_path = evidence_dir / f"{prefix}.stderr.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timed_out = False
    with out_path.open("xb") as stdout, err_path.open("xb") as stderr:
        try:
            process = subprocess.Popen(
                argv, cwd=snapshot, stdout=stdout, stderr=stderr,
                start_new_session=True,
            )
        except OSError as error:
            raise ValueError(f"could not launch approved {category} command: {argv[0]}") from error
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The process exited between timeout and termination.
            exit_code = process.wait()
    elapsed = round(time.monotonic() - started, 3)
    if out_path.stat().st_size > MAX_LOG_BYTES or err_path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError(f"{category} output exceeded 2 MB; inspect run logs")
    return {
        "kind": category, "argv": argv, "returncode": exit_code,
        "timed_out": timed_out, "duration_seconds": elapsed,
        "stdout": prefix + ".stdout.txt", "stdout_sha": _sha(out_path),
        "stderr": prefix + ".stderr.txt", "stderr_sha": _sha(err_path),
        "status": "PASS" if exit_code == 0 and not timed_out else "FAIL",
    }


def _receipt(initiative: str, mvp_id: str, run_id: str, head: str, branch: str, fingerprint: str,
             commands: list[dict], images: list[dict]) -> bytes:
    root = f"work/{initiative}/live-evidence/{run_id}"
    lines = [
        f"# Host-observed {mvp_id.upper()} independent evidence — MVP 04", "",
        "> Commands were selected and executed by the host after VERIFY. Their PASS statuses",
        "> describe process exit codes only; UX quality, accessibility and product correctness",
        "> still require the dedicated UIUX Inspector and a human G6 decision.", "",
        f"- Evidence run: {run_id}", f"- Git HEAD: {head}",
        f"- Git branch: {branch}", f"- Source fingerprint: {fingerprint}",
        f"- Recorded: {_now()}", "", "## Executed checks", "",
        "| Kind | Exact argv | Exit | Time (s) | Logs |",
        "| --- | --- | --- | --- | --- |",
    ]
    for command in commands:
        argv = " ".join(shlex.quote(arg) for arg in command["argv"]).replace("|", "\\|")
        lines.append(
            f"| {command['kind']} | `{argv}` | {command['returncode']} | "
            f"{command['duration_seconds']} | `{root}/{command['stdout']}`, "
            f"`{root}/{command['stderr']}` |"
        )
    lines.extend(["", "## Captured UI/UX observations", ""])
    for image in images:
        lines.append(f"- `{root}/{image['frozen_path']}` · {image['kind']} · SHA-256 {image['sha']}")
    lines += ["", "## Evidence limitations", "",
              "- PASS means these explicit test/UX command processes exited successfully.",
              "- Image signatures and provenance are checked; pixels and accessibility are not automatically graded.",
              "- UIUX INSPECT must inspect these artifacts and list any missing states/devices or failures.",
              "- This source snapshot had no Git metadata; commands requiring `.git` need a different adapter.", ""]
    return "\n".join(lines).encode("utf-8")


def collect(root: Path, initiative: str, *, tests: list[str],
            ux_checks: list[str], ux_artifacts: list[str], timeout: int = 120) -> str:
    """Run human-approved commands against a temporary copy, then adopt PASS."""
    from . import live_coordinator

    if not tests or not ux_checks or not ux_artifacts:
        raise ValueError("MVP 04 requires --test, --ux-check and --ux-artifact evidence")
    if len(tests) + len(ux_checks) > MAX_CHECKS or len(ux_artifacts) > MAX_IMAGE_COUNT:
        raise ValueError("too many evidence commands or screenshots for one run")
    if not 1 <= timeout <= MAX_SECONDS:
        raise ValueError("evidence timeout must be 1–300 seconds per command")
    for command in tests + ux_checks:
        _argv(command)
    if len(set(ux_artifacts)) != len(ux_artifacts):
        raise ValueError("duplicate UX screenshot paths")
    for relative in ux_artifacts:
        _relative(Path("/tmp/unused-evidence-root"), relative)
        if Path(relative).suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("UX artifacts must be .png, .jpg, .jpeg or .webp screenshots")
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        row = live_coordinator._state(conn)
        if row["status"] != live_coordinator.EVIDENCE_BARRIER or row["stage_index"] != 11:
            raise ValueError("host evidence may run only after live VERIFY and before UIUX INSPECT")
        repo = live_coordinator._source(conn, row, root, initiative)
        mvp_id = live_coordinator._active_mvp(row)
        head, branch, baseline = row["expected_head"], row["expected_branch"], row["expected_fingerprint"]
    run_id = "evidence-" + uuid.uuid4().hex
    evidence_dir = root / "work" / initiative / "live-evidence" / run_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=evidence_dir) as scratch:
        snapshot = Path(scratch)
        count, size = _copy_snapshot(repo, snapshot)
        if codex_runtime._git_fingerprint(repo) != baseline:
            raise ValueError("selected Git source changed during independent snapshot")
        commands = []
        ux_started = False
        for kind, command in ([('TEST', item) for item in tests]
                              + [('UX_CHECK', item) for item in ux_checks]):
            if kind == "UX_CHECK" and not ux_started:
                # Functional tests may themselves emit images. The requested
                # UX-specific capture step must generate these outputs freshly.
                for relative in ux_artifacts:
                    output = _relative(snapshot, relative)
                    if output.is_file():
                        output.unlink()  # Ephemeral snapshot only.
                ux_started = True
            result = _invoke(snapshot, evidence_dir, command, kind, len(commands) + 1, timeout)
            commands.append(result)
            if result["status"] != "PASS":
                _write_new(evidence_dir / "failure.json", _json_bytes({
                    "run_id": run_id, "commands": commands, "source_head": head,
                    "reason": f"{kind} exited {result['returncode']} or timed out",
                }))
                raise ValueError(
                    f"{kind} did not pass (exit {result['returncode']}); "
                    f"inspect work/{initiative}/live-evidence/{run_id}/commands"
                )
        images = []
        for number, relative in enumerate(ux_artifacts, start=1):
            generated = _relative(snapshot, relative)
            kind = _image_kind(generated)
            target = evidence_dir / "images" / f"{number:02d}{generated.suffix.lower()}"
            _write_new(target, generated.read_bytes())
            images.append({
                "produced_path": relative, "frozen_path": target.relative_to(evidence_dir).as_posix(),
                "sha": _sha(target), "kind": kind,
            })
    if codex_runtime._git_fingerprint(repo) != baseline:
        raise ValueError("selected Git source drifted while independent checks were running")
    receipt = _receipt(initiative, mvp_id, run_id, head, branch, baseline, commands, images)
    canonical = f"work/{initiative}/mvps/{mvp_id}/edd/verification-evidence.md"
    bundle = {
        "schema_version": 1, "state": "PASS", "run_id": run_id,
        "mvp_id": mvp_id,
        "source_head": head, "source_branch": branch, "source_fingerprint": baseline,
        "snapshot_files": count, "snapshot_bytes": size,
        "checks": commands, "ux_artifacts": images,
        "receipt_path": canonical, "receipt_sha": _digest(receipt),
    }
    bundle_bytes = _json_bytes(bundle)
    _write_new(evidence_dir / "bundle.json", bundle_bytes)
    with agentflow.open_db(root, initiative) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = live_coordinator._state(conn)
        if row["status"] != live_coordinator.EVIDENCE_BARRIER or row["stage_index"] != 11:
            raise ValueError("the evidence gate changed while checks were running; no result adopted")
        if live_coordinator._active_mvp(row) != mvp_id:
            raise ValueError("active MVP changed during host evidence; no result adopted")
        live_coordinator._source(conn, row, root, initiative)
        ensure(conn)
        live_coordinator._owned_write(conn, root, canonical, receipt.decode("utf-8"), run_id)
        conn.execute(
            "INSERT INTO live_evidence_runs(run_id,source_head,source_fingerprint,"
            "bundle_sha,receipt_sha,at,status,mvp_id) VALUES(?,?,?,?,?,?,'PASS',?)",
            (run_id, head, baseline, _digest(bundle_bytes), _digest(receipt), _now(), mvp_id),
        )
        conn.execute("UPDATE live_workflow SET status='READY',last_error=NULL WHERE singleton=1")
        live_coordinator._event(conn, "INDEPENDENT_EVIDENCE_COLLECTED", {
            "run_id": run_id, "mvp_id": mvp_id, "bundle_sha": _digest(bundle_bytes),
            "tests": len(tests), "ux_checks": len(ux_checks), "images": len(images),
        })
    live_coordinator.refresh_tickets(root, initiative)
    return (f"PASS independent evidence · {run_id}\n"
            f"Bundle SHA-256: {_digest(bundle_bytes)}\n"
            f"Receipt: {canonical}\n"
            "UIUX INSPECT ready. The live interface auto-resumes when enabled; "
            "otherwise review the receipt and press [N].")


def validate(conn: sqlite3.Connection, root: Path, initiative: str, *,
             mvp_id: str | None = None, require_current: bool = True) -> dict:
    """Rehash the exact host-observed receipts before every downstream step."""
    mvp = mvp_id or _current_mvp(conn)
    row = latest(conn, mvp)
    if row is None or row["status"] != "PASS":
        raise ValueError("no current host-observed verification + UX evidence; run live-evidence")
    workflow = conn.execute("SELECT * FROM live_workflow WHERE singleton=1").fetchone()
    if require_current and (row["source_head"] != workflow["expected_head"]
            or row["source_fingerprint"] != workflow["expected_fingerprint"]):
        raise ValueError("independent evidence belongs to a different source revision")
    evidence_dir = root / "work" / initiative / "live-evidence" / row["run_id"]
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise ValueError("frozen verification evidence directory missing or symlinked")
    bundle_file = _relative(evidence_dir, "bundle.json")
    if not bundle_file.is_file() or bundle_file.is_symlink() or _sha(bundle_file) != row["bundle_sha"]:
        raise ValueError("independent evidence bundle digest changed")
    bundle = json.loads(bundle_file.read_text(encoding="utf-8"))
    if (bundle.get("run_id") != row["run_id"] or bundle.get("state") != "PASS"
            or bundle.get("source_head") != row["source_head"]
            or (require_current and bundle.get("source_branch") != workflow["expected_branch"])
            or bundle.get("source_fingerprint") != row["source_fingerprint"]
            or bundle.get("receipt_sha") != row["receipt_sha"]):
        raise ValueError("independent evidence bundle identity changed")
    if bundle.get("mvp_id", "mvp-01") != mvp:
        raise ValueError("independent evidence belongs to a different MVP")
    receipt = _relative(root, bundle["receipt_path"])
    if not receipt.is_file() or receipt.is_symlink() or _sha(receipt) != row["receipt_sha"]:
        raise ValueError("independent verification receipt changed or is missing")
    if (not bundle.get("checks") or not bundle.get("ux_artifacts")
            or not any(c.get("kind") == "TEST" for c in bundle["checks"])
            or not any(c.get("kind") == "UX_CHECK" for c in bundle["checks"])):
        raise ValueError("incomplete independent verification or UX evidence")
    for command in bundle["checks"]:
        if command.get("returncode") != 0 or command.get("timed_out") or command.get("status") != "PASS":
            raise ValueError("independent evidence includes an unsuccessful process")
        for suffix in ("stdout", "stderr"):
            path = _relative(evidence_dir, command[suffix])
            if not path.is_file() or path.is_symlink() or _sha(path) != command[suffix + "_sha"]:
                raise ValueError("host-observed command output was modified")
    for image in bundle["ux_artifacts"]:
        path = _relative(evidence_dir, image["frozen_path"])
        if _image_kind(path) != image["kind"] or _sha(path) != image["sha"]:
            raise ValueError("host-captured UX screenshot was modified")
    return {
        "run_id": row["run_id"], "bundle_sha": row["bundle_sha"],
        "receipt_sha": row["receipt_sha"], "bundle_path": bundle_file,
        "receipt_path": receipt, "screenshots": len(bundle["ux_artifacts"]),
        "checks": len(bundle["checks"]),
    }


def invalidate(conn: sqlite3.Connection, mvp_id: str | None = None) -> None:
    ensure(conn)
    conn.execute(
        "UPDATE live_evidence_runs SET status='STALE' WHERE status='PASS' AND mvp_id=?",
        (mvp_id or _current_mvp(conn),),
    )
