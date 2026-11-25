"""Versioned, local-first G2 prototype review for the simulated Coordinator.

The included preview is an *illustrative static web experiment*, not code
produced by Codex. A real runner can replace the preview adapter while keeping
the iteration, feedback, validation and human gate contracts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid


ITERATION = re.compile(r"^iteration-([0-9]{3,})$")
MARKER = "AGENTFLOW_SIMULATED_PROTOTYPE"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _write_json(path: Path, value: dict) -> None:
    with path.open("xb") as output:  # immutable artifacts never silently replace a previous iteration
        output.write(_json_bytes(value))


def _atomic_json(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to overwrite symlinked review state: {path}")
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _safe_file(root: Path, path: Path) -> Path:
    if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents:
        raise ValueError(f"prototype review file missing, symlinked, or outside root: {path}")
    return path


def _read_json(root: Path, path: Path) -> dict:
    data = _safe_file(root, path).read_bytes()
    if len(data) > 128 * 1024:
        raise ValueError("prototype review JSON exceeds size limit")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"invalid prototype review JSON: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"prototype review JSON must be an object: {path}")
    return value


def _folder(root: Path, initiative: str) -> Path:
    destination = root / "work" / initiative / "prototype-review"
    if root.resolve() not in destination.resolve(strict=False).parents:
        raise ValueError("prototype review directory escapes workspace")
    if destination.is_symlink():
        raise ValueError("prototype review directory cannot be symlinked")
    return destination


def _iteration_directory(root: Path, initiative: str, iteration: str) -> Path:
    if not ITERATION.fullmatch(iteration):
        raise ValueError("malformed prototype iteration ID")
    base = _folder(root, initiative)
    directory = base / iteration
    if (directory.is_symlink() or
            root.resolve() not in directory.resolve(strict=False).parents):
        raise ValueError("prototype iteration directory is symlinked or outside workspace")
    return directory


def next_iteration(root: Path, initiative: str) -> str:
    """Choose a new name without overwriting even an orphaned prior attempt."""
    base = _folder(root, initiative)
    numbers = []
    if base.is_dir():
        for item in base.iterdir():
            match = ITERATION.fullmatch(item.name)
            if match:
                numbers.append(int(match.group(1)))
    return f"iteration-{max(numbers, default=0) + 1:03d}"


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prototype_iterations (
            iteration_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            report_sha TEXT NOT NULL,
            preview_sha TEXT NOT NULL,
            bundle_sha TEXT NOT NULL,
            feedback_id TEXT,
            feedback_text TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT
        )
        """
    )


def _latest(conn: sqlite3.Connection) -> sqlite3.Row | None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prototype_iterations'"
    ).fetchone()
    if not exists:
        return None
    return conn.execute(
        "SELECT * FROM prototype_iterations ORDER BY rowid DESC LIMIT 1"
    ).fetchone()


def _preview_html(iteration: str, prior_feedback: str) -> str:
    prior = escape(prior_feedback[:1500])
    hint = (
        f'<p class="feedback">Previous reviewer request: {prior}</p>'
        if prior else '<p class="feedback">First prototype iteration: no prior feedback.</p>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
  <title>Agentflow prototype review — {iteration}</title>
  <style>
    :root {{ color-scheme: dark; font: 16px system-ui, sans-serif; }}
    body {{ background: #0b1220; color: #eef2ff; margin: 0; padding: 28px; }}
    main {{ max-width: 760px; margin: auto; }}
    h1 {{ font-size: 1.9rem; margin-bottom: 8px; }}
    .tag {{ color: #fbbf24; font-size: 0.84rem; font-weight: 700; }}
    .feedback {{ color: #cbd5e1; background: #17243c; padding: 14px; border-radius: 10px; }}
    .tools {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 22px 0; }}
    input {{ flex: 1; min-width: 180px; border-radius: 8px; padding: 12px; border: 1px solid #64748b; background: #111d34; color: white; }}
    button {{ cursor: pointer; border: 0; border-radius: 8px; background: #34d399; color: #062117; font-weight: 700; padding: 12px 16px; }}
    table {{ width: 100%; border-collapse: collapse; background: #111d34; border-radius: 12px; overflow: hidden; }}
    th,td {{ text-align: left; padding: 14px; border-bottom: 1px solid #243452; }}
    small {{ color: #94a3b8; }}
  </style>
</head>
<body>
  <!-- {MARKER}: local sample data only; no real Codex agent was invoked -->
  <main>
    <div class="tag">SIMULATED EXPERIMENT · {iteration.upper()}</div>
    <h1>Find an order and export it</h1>
    <p>Try searching the sample orders, then export the visible results.</p>
    {hint}
    <div class="tools">
      <input id="search" aria-label="Search order ID" placeholder="Search order ID (e.g. 1002)">
      <button id="export" type="button">Export visible CSV</button>
    </div>
    <p id="count" role="status"></p>
    <table><thead><tr><th>Order</th><th>Customer</th><th>Status</th></tr></thead><tbody id="rows"></tbody></table>
    <p><small>All orders are synthetic. This preview illustrates the review mechanism, not validated product behavior.</small></p>
  </main>
  <script>
    const orders = [
      {{ id: '1001', customer: 'Sample A', status: 'Ready' }},
      {{ id: '1002', customer: 'Sample B', status: 'Pending' }},
      {{ id: '1003', customer: 'Sample C', status: 'Ready' }}
    ];
    const input = document.getElementById('search');
    const rows = document.getElementById('rows');
    const count = document.getElementById('count');
    const visible = () => orders.filter(order => order.id.includes(input.value.trim()));
    function render() {{
      const filtered = visible();
      rows.replaceChildren(...filtered.map(order => {{
        const tr = document.createElement('tr');
        [order.id, order.customer, order.status].forEach(value => {{
          const td = document.createElement('td'); td.textContent = value; tr.appendChild(td);
        }});
        return tr;
      }}));
      count.textContent = filtered.length + ' sample order(s)';
    }}
    input.addEventListener('input', render);
    document.getElementById('export').addEventListener('click', () => {{
      const lines = ['id,customer,status', ...visible().map(o => [o.id,o.customer,o.status].join(','))];
      const blob = new Blob([lines.join('\\n')], {{type:'text/csv'}});
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob); link.download = 'sample-orders.csv';
      link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }});
    render();
  </script>
</body>
</html>
"""


def create_iteration(root: Path, initiative: str, conn: sqlite3.Connection) -> dict:
    """Capture the latest mock report and produce an immutable G2 review bundle."""
    _ensure_table(conn)
    prior = _latest(conn)
    feedback_text = prior["feedback_text"] if prior and prior["status"] == "CHANGES_REQUESTED" else ""
    feedback_id = prior["feedback_id"] if prior and prior["status"] == "CHANGES_REQUESTED" else None
    iteration = next_iteration(root, initiative)
    base = _folder(root, initiative)
    directory = _iteration_directory(root, initiative, iteration)
    report = _safe_file(root, root / "work" / initiative / "prototype-report.md")
    if directory.exists():
        raise ValueError(f"prototype iteration already exists: {iteration}")
    directory.mkdir(parents=True)
    preview = directory / "preview" / "index.html"
    preview.parent.mkdir()
    preview.write_text(_preview_html(iteration, feedback_text or ""), encoding="utf-8")
    archived = directory / "prototype-report.md"
    archived.write_bytes(report.read_bytes())
    checks = {
        "schema_version": 1,
        "mode": "SIMULATION_ONLY",
        "checks": [
            {"name": "preview_html_present", "result": "PASS" if preview.stat().st_size > 500 else "FAIL"},
            {"name": "report_present", "result": "PASS" if report.stat().st_size > 0 else "FAIL"},
            {"name": "interactive_user_behavior", "result": "NOT_EXECUTED"},
        ],
    }
    _write_json(directory / "checks.json", checks)
    if any(c["result"] == "FAIL" for c in checks["checks"]):
        raise ValueError("prototype preview or report preflight failed")
    relative_preview = f"work/{initiative}/prototype-review/{iteration}/preview/index.html"
    manifest = {
        "schema_version": 1,
        "iteration_id": iteration,
        "preview_type": "static_web_mock",
        "preview_path": relative_preview,
        "preview_sha256": _sha(preview),
        "preview_status": "READY_FOR_MANUAL_REVIEW",
        "code_revision": None,
        "feedback_from_previous": feedback_id,
        "simulation_only": True,
    }
    _write_json(directory / "preview-manifest.json", manifest)
    bundle = {
        "schema_version": 1,
        "iteration_id": iteration,
        "report_path": f"work/{initiative}/prototype-report.md",
        "report_sha256": _sha(report),
        "archived_report_sha256": _sha(archived),
        "preview_path": relative_preview,
        "preview_sha256": _sha(preview),
        "preview_manifest_sha256": _sha(directory / "preview-manifest.json"),
        "checks_sha256": _sha(directory / "checks.json"),
        "prior_feedback_id": feedback_id,
        "prior_feedback_path": f"work/{initiative}/prototype-review/{prior['iteration_id']}/feedback.json" if feedback_id else None,
        "prior_feedback_sha256": _sha(base / prior["iteration_id"] / "feedback.json") if feedback_id else None,
        "simulation_only": True,
    }
    _write_json(directory / "review-bundle.json", bundle)
    digest = _sha(directory / "review-bundle.json")
    created = _now()
    _write_json(directory / "review.json", {
        "iteration_id": iteration,
        "status": "WAITING_FOR_HUMAN",
        "review_bundle_sha256": digest,
        "created_at": created,
    })
    (directory / "launch.log").write_text(
        "Local static HTML mock generated. Open preview/index.html manually.\n"
        "No Codex agent, application build, or user acceptance test ran.\n",
        encoding="utf-8",
    )
    conn.execute(
        """INSERT INTO prototype_iterations
           (iteration_id,status,report_sha,preview_sha,bundle_sha,created_at)
           VALUES(?,?,?,?,?,?)""",
        (iteration, "WAITING_FOR_HUMAN", bundle["report_sha256"], bundle["preview_sha256"], digest, created),
    )
    _atomic_json(base / "current.json", {
        "iteration_id": iteration, "review_bundle_sha256": digest,
    })
    return {"iteration_id": iteration, "preview_path": relative_preview,
            "bundle_sha": digest, "report_sha": bundle["report_sha256"], "status": "WAITING_FOR_HUMAN"}


def inspect(root: Path, initiative: str, conn: sqlite3.Connection) -> dict | None:
    """Read current iteration without changing state or trusting claim text."""
    row = _latest(conn)
    if row is None:
        return None
    iteration = row["iteration_id"]
    result = {
        "iteration_id": iteration,
        "status": row["status"],
        "bundle_sha": row["bundle_sha"],
        "preview_path": f"work/{initiative}/prototype-review/{iteration}/preview/index.html",
        "feedback_id": row["feedback_id"],
        "feedback_text": row["feedback_text"],
        "integrity": "INVALID",
        "error": None,
    }
    try:
        base = _folder(root, initiative)
        directory = _iteration_directory(root, initiative, iteration)
        current = _read_json(root, base / "current.json")
        bundle_file = _safe_file(root, directory / "review-bundle.json")
        bundle = _read_json(root, bundle_file)
        review = _read_json(root, directory / "review.json")
        manifest_file = _safe_file(root, directory / "preview-manifest.json")
        manifest = _read_json(root, manifest_file)
        checks_file = _safe_file(root, directory / "checks.json")
        checks = _read_json(root, checks_file)
        preview = _safe_file(root, root / result["preview_path"])
        archived = _safe_file(root, directory / "prototype-report.md")
        canonical = _safe_file(root, root / "work" / initiative / "prototype-report.md")
        if (current.get("iteration_id") != iteration or current.get("review_bundle_sha256") != row["bundle_sha"]):
            raise ValueError("current review pointer differs from Coordinator record")
        if review.get("status") != row["status"] or review.get("review_bundle_sha256") != row["bundle_sha"]:
            raise ValueError("review decision differs from Coordinator record")
        if _sha(bundle_file) != row["bundle_sha"]:
            raise ValueError("review bundle changed since it was issued")
        expected = {
            "report_sha256": _sha(canonical),
            "archived_report_sha256": _sha(archived),
            "preview_sha256": _sha(preview),
            "preview_manifest_sha256": _sha(manifest_file),
            "checks_sha256": _sha(checks_file),
        }
        if any(bundle.get(key) != value for key, value in expected.items()):
            raise ValueError("prototype report, preview, or evidence changed since review was issued")
        if manifest.get("preview_sha256") != row["preview_sha"] or manifest.get("iteration_id") != iteration:
            raise ValueError("preview manifest identity mismatch")
        if bundle.get("report_sha256") != row["report_sha"] or bundle.get("preview_sha256") != row["preview_sha"]:
            raise ValueError("Coordinator report/preview identity mismatch")
        if checks.get("mode") != "SIMULATION_ONLY" or any(
            item.get("result") == "FAIL" for item in checks.get("checks", [])
        ):
            raise ValueError("prototype preflight checks did not pass")
        if bundle.get("prior_feedback_id"):
            expected_previous = conn.execute(
                "SELECT iteration_id, feedback_id, feedback_text FROM prototype_iterations WHERE feedback_id=? AND status='CHANGES_REQUESTED'",
                (bundle["prior_feedback_id"],),
            ).fetchone()
            if not expected_previous:
                raise ValueError("source feedback has no recorded change-request decision")
            feedback_path = f"work/{initiative}/prototype-review/{expected_previous['iteration_id']}/feedback.json"
            if bundle.get("prior_feedback_path") != feedback_path:
                raise ValueError("previous feedback path differs from Coordinator state")
            feedback = _safe_file(root, root / feedback_path)
            if _sha(feedback) != bundle.get("prior_feedback_sha256"):
                raise ValueError("previous human feedback changed since the review was generated")
            result["feedback_id"] = expected_previous["feedback_id"]
            result["feedback_text"] = expected_previous["feedback_text"]
        result["integrity"] = "UNCHANGED"
    except (ValueError, OSError, KeyError, TypeError) as error:
        result["error"] = str(error)
    return result


def validate_for_approval(root: Path, initiative: str, conn: sqlite3.Connection, report_sha: str) -> dict:
    view = inspect(root, initiative, conn)
    if view is None:
        raise ValueError("G2 review bundle missing; request changes to generate a fresh prototype preview")
    if view["status"] != "WAITING_FOR_HUMAN":
        raise ValueError("G2 iteration is not awaiting human review")
    if view["integrity"] != "UNCHANGED":
        raise ValueError("stale prototype preview or review bundle: " + str(view["error"]))
    row = _latest(conn)
    if row["report_sha"] != report_sha:
        raise ValueError("stale prototype report approval hash")
    return view


def record_feedback(root: Path, initiative: str, conn: sqlite3.Connection, reason: str) -> dict | None:
    """Close this exact G2 review and persist human feedback for the next run."""
    row = _latest(conn)
    if row is None:
        return None  # Legacy G2 from an older kit: a rerun creates the first review.
    if row["status"] != "WAITING_FOR_HUMAN":
        raise ValueError("prototype feedback already recorded for this iteration")
    if not reason.strip() or len(reason) > 3000:
        raise ValueError("prototype feedback must contain 1–3000 characters")
    iteration = row["iteration_id"]
    directory = _iteration_directory(root, initiative, iteration)
    feedback_id = f"FB-{iteration.upper()}"
    created = _now()
    feedback = {
        "schema_version": 1,
        "feedback_id": feedback_id,
        "iteration_id": iteration,
        "type": "human_change_request",
        "severity": "review_blocking",
        "reason": reason.strip(),
        "attachments": [],
        "created_at": created,
    }
    if (directory / "feedback.json").exists() or (directory / "feedback.json").is_symlink():
        raise ValueError("immutable prototype feedback already exists")
    if (directory / "feedback.md").exists() or (directory / "feedback.md").is_symlink():
        raise ValueError("immutable prototype feedback markdown already exists")
    _write_json(directory / "feedback.json", feedback)
    (directory / "feedback.md").write_text(
        f"# Prototype feedback — {iteration}\n\n- ID: {feedback_id}\n"
        "- Decision: CHANGES_REQUESTED\n\n## Requested change\n\n"
        + "\n".join("> " + line for line in reason.strip().splitlines()) + "\n",
        encoding="utf-8",
    )
    conn.execute(
        "UPDATE prototype_iterations SET status='CHANGES_REQUESTED',feedback_id=?,feedback_text=?,decided_at=? WHERE iteration_id=?",
        (feedback_id, reason.strip(), created, iteration),
    )
    _atomic_json(directory / "review.json", {
        "iteration_id": iteration, "status": "CHANGES_REQUESTED",
        "review_bundle_sha256": row["bundle_sha"], "decided_at": created,
    })
    return feedback


def record_approval(root: Path, initiative: str, conn: sqlite3.Connection, report_sha: str) -> dict:
    review = validate_for_approval(root, initiative, conn, report_sha)
    row = _latest(conn)
    iteration = row["iteration_id"]
    decided = _now()
    directory = _iteration_directory(root, initiative, iteration)
    approval = {
        "gate": "G2_PROTOTYPE",
        "iteration_id": iteration,
        "report_sha256": row["report_sha"],
        "preview_path": review["preview_path"],
        "preview_sha256": row["preview_sha"],
        "review_bundle_sha256": row["bundle_sha"],
        "decision": "APPROVED",
        "source": "SIMULATED_LOCAL_CLI",
        "created_at": decided,
        "code_disposition": "REVIEW_BEFORE_REUSE",
    }
    if (directory / "approval.json").exists() or (directory / "approval.json").is_symlink():
        raise ValueError("this prototype review was already approved")
    acceptance_path = root / "work" / initiative / "prototype-acceptance.md"
    if acceptance_path.exists() or acceptance_path.is_symlink():
        raise ValueError("refusing to overwrite existing prototype acceptance artifact")
    _write_json(directory / "approval.json", approval)
    conn.execute(
        "UPDATE prototype_iterations SET status='APPROVED',decided_at=? WHERE iteration_id=?",
        (decided, iteration),
    )
    _atomic_json(directory / "review.json", {
        "iteration_id": iteration, "status": "APPROVED",
        "review_bundle_sha256": row["bundle_sha"], "decided_at": decided,
    })
    acceptance_path.write_text(
        f"# Accepted prototype findings — {initiative}\n\n"
        f"- Iteration: **{iteration}**\n- Gate: **G2_PROTOTYPE**\n"
        f"- Report SHA-256: `{row['report_sha']}`\n"
        f"- Preview: `{review['preview_path']}`\n"
        f"- Preview SHA-256: `{row['preview_sha']}`\n"
        f"- Review bundle SHA-256: `{row['bundle_sha']}`\n"
        "- Decision: **APPROVED (local CLI demo)**\n"
        "- Prototype code disposition: **REVIEW BEFORE REUSE**\n"
        "- This accepts reviewed findings, **not production code or real test evidence**.\n",
        encoding="utf-8",
    )
    return review
