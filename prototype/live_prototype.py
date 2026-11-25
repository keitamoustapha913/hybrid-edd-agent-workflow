"""MVP 03: real, Codex-produced local HTML previews and hash-bound G2 reviews.

An HTML artifact is a prototype to be inspected by a human, not evidence that
any interaction was tested. The preview server exposes only one frozen HTML
file, on loopback, with a restrictive browser content-security policy.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
import uuid
import webbrowser

from . import agentflow, codex_runtime


MAX_HTML_BYTES = 600_000
MAX_REVIEW_NOTES = 3_000
ITERATION = re.compile(r"^iteration-([0-9]{3,})$")
BLOCKED_TAGS = frozenset({
    "iframe", "frame", "frameset", "object", "embed", "base", "link",
    "svg", "math", "video", "audio", "source", "track", "portal", "form",
})
BLOCKED_JS = re.compile(
    r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|"
    r"serviceWorker|localStorage|sessionStorage|indexedDB)\b|"
    r"(?:window\s*\.\s*(?:open|location|parent|top)|"
    r"document\s*\.\s*(?:cookie|domain)|"
    r"(?:import\s*\(|\brequire\s*\())",
    flags=re.IGNORECASE,
)
REMOTE_URL = re.compile(r"https?://|ftp://|['\"]//|url\s*\(|@import", flags=re.IGNORECASE)
CSP = (
    "sandbox allow-scripts; "
    "default-src 'none'; "
    "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; font-src data:; "
    "connect-src 'none'; form-action 'none'; base-uri 'none'; "
    "object-src 'none'; frame-src 'none'; worker-src 'none'"
)

class PreviewRejected(ValueError):
    """A candidate preview is unsafe or lacks a meaningful browser document."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file(root: Path, relative: str) -> Path:
    return codex_runtime._relative_file(root.resolve(), relative)


def _base(root: Path, initiative: str) -> Path:
    return _file(root, f"work/{initiative}/prototype-review-live")


def _directory(root: Path, initiative: str, iteration: int) -> Path:
    if iteration < 1 or iteration > 999_999:
        raise ValueError("invalid prototype iteration number")
    return _file(root, f"work/{initiative}/prototype-review-live/iteration-{iteration:03d}")


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 120_000:
        raise ValueError(f"missing, symlinked or oversized G2 record: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"G2 record must be a JSON object: {path}")
    return data


class _HTMLInspection(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.script_count = 0
        self.title_count = 0
        self.controls = 0
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        if tag in BLOCKED_TAGS:
            self.errors.append(f"unsupported embedded/network-capable element: {tag}")
        if tag == "script":
            self.script_count += 1
        if tag == "title":
            self.title_count += 1
        if tag in {"button", "input", "select", "textarea"}:
            self.controls += 1
        for key, raw in attrs:
            value = raw or ""
            if key in {"src", "srcset", "action", "formaction", "ping", "manifest"}:
                if tag != "img" or not re.fullmatch(
                    r"data:image/(?:png|jpeg|gif|webp);base64,[a-zA-Z0-9+/=]+", value
                ):
                    self.errors.append(f"external or unsupported resource attribute: {key}")
            if key == "href" and not value.startswith("#"):
                self.errors.append("only in-document fragment links are supported")
            if key == "http-equiv" and value.lower() in {"refresh", "content-security-policy"}:
                self.errors.append("prototype cannot override browser security or redirect")
            if key == "type" and tag == "script" and value not in {"", "text/javascript"}:
                self.errors.append("module or non-classic scripts are unsupported")
            if REMOTE_URL.search(value):
                self.errors.append(f"remote asset/navigation reference in {key}")


def check_html(data: bytes) -> dict:
    """Conservative static analysis only; it does NOT test human interactions."""
    if not data or len(data) > MAX_HTML_BYTES:
        raise PreviewRejected("prototype HTML is missing or exceeds 600 KB")
    try:
        body = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreviewRejected("prototype HTML must be UTF-8") from error
    if not re.match(r"\s*<!doctype\s+html\s*>", body, re.IGNORECASE):
        raise PreviewRejected("prototype HTML must begin with <!doctype html>")
    scanner = _HTMLInspection()
    try:
        scanner.feed(body)
        scanner.close()
    except ValueError as error:
        raise PreviewRejected("prototype HTML parser rejected markup") from error
    if not {"html", "head", "body", "main"} <= scanner.tags:
        scanner.errors.append("prototype must contain html, head, body and main")
    if not scanner.title_count:
        scanner.errors.append("prototype needs a descriptive title")
    if not scanner.controls or not scanner.script_count:
        scanner.errors.append("prototype requires an operable control and inline behavior")
    if REMOTE_URL.search(body):
        scanner.errors.append("prototype contains a remote/resource URL")
    if BLOCKED_JS.search(body):
        scanner.errors.append("prototype uses external APIs, persistence or navigation")
    if scanner.errors:
        raise PreviewRejected("; ".join(dict.fromkeys(scanner.errors))[:1_000])
    return {
        "html_parse": "PASS",
        "self_contained_static_preview": "PASS",
        "inline_controls_observed": scanner.controls,
        "inline_scripts_observed": scanner.script_count,
        "network_requests_exercised": "NOT_EXECUTED",
        "visual_usability": "NOT_TESTED",
        "keyboard_accessibility": "NOT_TESTED",
        "real_product_backend": "NOT_CONNECTED",
    }


def ensure(conn: sqlite3.Connection) -> None:
    # executescript() implicitly commits an open SQLite transaction; never
    # use it inside the Coordinator's BEGIN IMMEDIATE adoption transaction.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS live_preview_iterations(
            iteration INTEGER PRIMARY KEY, run_id TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL, report_sha TEXT NOT NULL,
            preview_sha TEXT NOT NULL, bundle_sha TEXT NOT NULL,
            feedback_path TEXT, observed_notes TEXT,
            created_at TEXT NOT NULL, decided_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS live_preview_decisions(
            approval_id INTEGER PRIMARY KEY, iteration INTEGER NOT NULL,
            report_sha TEXT NOT NULL, preview_sha TEXT NOT NULL,
            bundle_sha TEXT NOT NULL, observed_notes TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )


def latest(conn: sqlite3.Connection) -> sqlite3.Row | None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_preview_iterations'"
    ).fetchone()
    if not exists:
        return None
    return conn.execute(
        "SELECT * FROM live_preview_iterations ORDER BY iteration DESC LIMIT 1"
    ).fetchone()


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as destination:
        destination.write(data)


def inspect(root: Path, initiative: str, row: sqlite3.Row | dict,
            *, canonical_report: bool = True) -> dict:
    """Check every frozen byte and the full Merkle-style G2 bundle identity."""
    root = root.resolve(strict=True)
    iteration = int(row["iteration"])
    directory = _directory(root, initiative, iteration)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("frozen G2 iteration directory missing or symlinked")
    report = _file(directory, "prototype-report.md")
    preview = _file(directory, "preview/index.html")
    checks_path = _file(directory, "checks.json")
    manifest_path = _file(directory, "preview-manifest.json")
    bundle_path = _file(directory, "review-bundle.json")
    files = [report, preview, checks_path, manifest_path, bundle_path]
    if any(not path.is_file() or path.is_symlink() for path in files):
        raise ValueError("frozen G2 iteration is incomplete or symlinked")
    report_sha, preview_sha = _digest(report.read_bytes()), _digest(preview.read_bytes())
    checks_sha, manifest_sha = _digest(checks_path.read_bytes()), _digest(manifest_path.read_bytes())
    bundle_sha = _digest(bundle_path.read_bytes())
    bundle = _read_json(bundle_path)
    manifest = _read_json(manifest_path)
    checks = _read_json(checks_path)
    if (row["report_sha"] != report_sha or row["preview_sha"] != preview_sha
            or row["bundle_sha"] != bundle_sha):
        raise ValueError("frozen G2 report, preview or bundle digest changed")
    expected = {
        "iteration": f"iteration-{iteration:03d}",
        "run_id": row["run_id"],
        "report_sha": report_sha,
        "preview_sha": preview_sha,
        "checks_sha": checks_sha,
        "manifest_sha": manifest_sha,
    }
    if any(bundle.get(key) != value for key, value in expected.items()):
        raise ValueError("G2 review bundle does not match frozen artifacts")
    if bundle.get("prior_feedback_path"):
        prior = _file(root, bundle["prior_feedback_path"])
        if not prior.is_file() or prior.is_symlink() or _digest(prior.read_bytes()) != bundle["prior_feedback_sha"]:
            raise ValueError("prior human G2 feedback was changed after preview generation")
    elif bundle.get("prior_feedback_sha") is not None:
        raise ValueError("G2 bundle has a feedback digest without its file")
    if (manifest.get("preview_sha") != preview_sha
            or manifest.get("run_id") != row["run_id"]
            or manifest.get("kind") != "CODEX_GENERATED_OFFLINE_HTML"):
        raise ValueError("G2 preview manifest identity differs")
    if checks.get("visual_usability") != "NOT_TESTED":
        raise ValueError("G2 static checks must never claim to have performed UX inspection")
    check_html(preview.read_bytes())
    if canonical_report:
        current = _file(root, f"work/{initiative}/prototype-report.md")
        if not current.is_file() or _digest(current.read_bytes()) != report_sha:
            raise ValueError("G2 frozen report differs from canonical report")
        current_preview = _file(root, f"work/{initiative}/prototype-preview/index.html")
        if not current_preview.is_file() or _digest(current_preview.read_bytes()) != preview_sha:
            raise ValueError("G2 frozen preview differs from canonical prototype")
    return {
        "iteration": expected["iteration"],
        "run_id": row["run_id"],
        "status": row["status"],
        "report_sha": report_sha,
        "preview_sha": preview_sha,
        "bundle_sha": bundle_sha,
        "preview_path": f"work/{initiative}/prototype-review-live/iteration-{iteration:03d}/preview/index.html",
        "bundle_path": f"work/{initiative}/prototype-review-live/iteration-{iteration:03d}/review-bundle.json",
        "checks": checks,
    }


def freeze(conn: sqlite3.Connection, root: Path, initiative: str,
           run_id: str, candidate_root: Path, *, head: str, branch: str,
           prior_feedback: str | None) -> dict:
    """Immutable iteration generation; safe to repeat after interrupted adoption."""
    root = root.resolve(strict=True)
    ensure(conn)
    report_path = _file(candidate_root, f"work/{initiative}/prototype-report.md")
    preview_path = _file(candidate_root, f"work/{initiative}/prototype-preview/index.html")
    if (report_path.is_symlink() or preview_path.is_symlink()
            or not report_path.is_file() or not preview_path.is_file()):
        raise PreviewRejected("Codex prototype candidate lacks required report or HTML preview")
    report_bytes, html_bytes = report_path.read_bytes(), preview_path.read_bytes()
    check_result = check_html(html_bytes)
    feedback_sha = None
    if prior_feedback:
        feedback = _file(root, prior_feedback)
        if not feedback.is_file() or feedback.is_symlink():
            raise ValueError("recorded prior human feedback is missing or unsafe")
        feedback_sha = _digest(feedback.read_bytes())
    base = _base(root, initiative)
    base.mkdir(parents=True, exist_ok=True)
    if base.is_symlink():
        raise ValueError("live prototype review root is symlinked")
    known = conn.execute(
        "SELECT * FROM live_preview_iterations WHERE run_id=?", (run_id,)
    ).fetchone()
    if known:
        result = inspect(root, initiative, known, canonical_report=False)
        if result["report_sha"] != _digest(report_bytes) or result["preview_sha"] != _digest(html_bytes):
            raise ValueError("previous run's frozen G2 evidence differs from its candidate")
        return result
    # Handle a crash after atomic directory creation but before the DB commit.
    found = None
    numbers = []
    for folder in base.iterdir():
        matched = ITERATION.fullmatch(folder.name)
        if not matched:
            continue
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("unsafe G2 review iteration directory")
        number = int(matched.group(1))
        numbers.append(number)
        bundle = _read_json(_file(folder, "review-bundle.json"))
        if bundle.get("run_id") == run_id:
            found = number
    number = found if found is not None else max(numbers, default=0) + 1
    directory = _directory(root, initiative, number)
    if found is None:
        staged = _file(root, f"work/{initiative}/prototype-review-live/.staging-{uuid.uuid4().hex}")
        staged.mkdir(parents=True, exist_ok=False)
        try:
            manifest = {
                "schema_version": 1, "kind": "CODEX_GENERATED_OFFLINE_HTML",
                "run_id": run_id, "preview_sha": _digest(html_bytes),
                "preview_path": "preview/index.html", "sandbox": "LOOPBACK_CSP",
                "source_head": head, "source_branch": branch,
            }
            checks = {
                "schema_version": 1, "run_id": run_id, **check_result,
                "report_sha": _digest(report_bytes), "preview_sha": _digest(html_bytes),
            }
            checks_bytes, manifest_bytes = _json_bytes(checks), _json_bytes(manifest)
            bundle = {
                "schema_version": 1, "iteration": f"iteration-{number:03d}",
                "run_id": run_id, "report_sha": _digest(report_bytes),
                "preview_sha": _digest(html_bytes),
                "checks_sha": _digest(checks_bytes),
                "manifest_sha": _digest(manifest_bytes),
                "prior_feedback_sha": feedback_sha,
                "prior_feedback_path": prior_feedback,
                "source_head": head, "source_branch": branch,
                "created_at": _now(),
            }
            _write_new(staged / "prototype-report.md", report_bytes)
            _write_new(staged / "preview/index.html", html_bytes)
            _write_new(staged / "checks.json", checks_bytes)
            _write_new(staged / "preview-manifest.json", manifest_bytes)
            _write_new(staged / "review-bundle.json", _json_bytes(bundle))
            staged.rename(directory)
        except Exception:
            # No deletion: an incomplete staging directory is diagnostic evidence.
            raise
    snapshot_bundle = _read_json(directory / "review-bundle.json")
    if (snapshot_bundle.get("source_head") != head
            or snapshot_bundle.get("source_branch") != branch
            or snapshot_bundle.get("prior_feedback_sha") != feedback_sha
            or snapshot_bundle.get("prior_feedback_path") != prior_feedback):
        raise ValueError("frozen G2 bundle differs from the reserved Git revision or feedback")
    conn.execute(
        "INSERT INTO live_preview_iterations("
        "iteration,run_id,status,report_sha,preview_sha,bundle_sha,created_at"
        ") VALUES(?,?,'READY',?,?,?,?)",
        (number, run_id, snapshot_bundle["report_sha"], snapshot_bundle["preview_sha"],
         _digest((directory / "review-bundle.json").read_bytes()), _now()),
    )
    row = conn.execute("SELECT * FROM live_preview_iterations WHERE iteration=?", (number,)).fetchone()
    return inspect(root, initiative, row, canonical_report=False)


def review_ready(conn: sqlite3.Connection, root: Path, initiative: str,
                 report_sha: str) -> dict:
    row = latest(conn)
    if row is None or row["status"] != "READY":
        raise ValueError("no fresh Codex-produced G2 iteration ready for review")
    info = inspect(root, initiative, row)
    if info["report_sha"] != report_sha:
        raise ValueError("pending G2 report SHA does not match frozen iteration")
    return info


def approve_preflight(conn: sqlite3.Connection, root: Path, initiative: str,
                      report_sha: str, bundle_sha: str, notes: str, head: str) -> tuple[int, str]:
    info = review_ready(conn, root, initiative, report_sha)
    if bundle_sha != info["bundle_sha"]:
        raise ValueError("stale or incorrect G2 review bundle SHA-256")
    if not notes.strip() or len(notes) > MAX_REVIEW_NOTES:
        raise ValueError("record actual human preview observations (1–3000 characters)")
    if not head.strip():
        raise ValueError("missing bound source revision")
    row = latest(conn)
    acceptance = (
        f"# Prototype findings acceptance — {info['iteration']}\n\n"
        "> Human-reviewed, Codex-generated offline HTML prototype. "
        "Static safety checks are not UX or software verification.\n\n"
        f"- Decision: ACCEPTED_G2\n"
        f"- Codex run: {info['run_id']}\n"
        f"- Report: work/{initiative}/prototype-report.md\n"
        f"- Report SHA-256: {report_sha}\n"
        f"- Preview: {info['preview_path']}\n"
        f"- Preview SHA-256: {info['preview_sha']}\n"
        f"- Review bundle: {info['bundle_path']}\n"
        f"- Review bundle SHA-256: {bundle_sha}\n"
        f"- Source HEAD: {head}\n"
        f"- Recorded at: {_now()}\n\n"
        f"## Human observations\n\n{notes.strip()}\n\n"
        "## Carry-forward contract\n\n"
        "The Specification Agent may use the accepted *findings* and constraints. "
        "The interactive demo is not production code; it must not skip RED/GREEN, "
        "VERIFY or independent UI/UX inspection.\n"
    )
    return row["iteration"], acceptance


def record_approval(conn: sqlite3.Connection, iteration: int,
                    approval_id: int, bundle_sha: str, notes: str) -> None:
    row = conn.execute(
        "SELECT * FROM live_preview_iterations WHERE iteration=?", (iteration,)
    ).fetchone()
    if row is None or row["status"] != "READY" or row["bundle_sha"] != bundle_sha:
        raise ValueError("G2 iteration was already decided or changed")
    conn.execute(
        "INSERT INTO live_preview_decisions("
        "approval_id,iteration,report_sha,preview_sha,bundle_sha,observed_notes,created_at"
        ") VALUES(?,?,?,?,?,?,?)",
        (approval_id, iteration, row["report_sha"], row["preview_sha"],
         bundle_sha, notes.strip(), _now()),
    )
    conn.execute(
        "UPDATE live_preview_iterations SET status='ACCEPTED',observed_notes=?,decided_at=? WHERE iteration=?",
        (notes.strip(), _now(), iteration),
    )


def record_feedback(conn: sqlite3.Connection, root: Path, initiative: str,
                    report_sha: str, feedback_path: str) -> dict:
    info = review_ready(conn, root, initiative, report_sha)
    feedback = _file(root, feedback_path)
    if not feedback.is_file():
        raise ValueError("G2 feedback was not persisted")
    conn.execute(
        "UPDATE live_preview_iterations SET status='CHANGES_REQUESTED',"
        "feedback_path=?,decided_at=? WHERE iteration=?",
        (feedback_path, _now(), latest(conn)["iteration"]),
    )
    return info


def validate_approved(conn: sqlite3.Connection, root: Path, initiative: str,
                      approval_id: int) -> None:
    row = conn.execute(
        "SELECT * FROM live_preview_decisions WHERE approval_id=?", (approval_id,)
    ).fetchone()
    if row is None:
        raise ValueError("approved G2 review decision is missing")
    frozen = conn.execute(
        "SELECT * FROM live_preview_iterations WHERE iteration=?", (row["iteration"],)
    ).fetchone()
    if frozen is None or frozen["status"] != "ACCEPTED":
        raise ValueError("previously accepted G2 iteration is no longer accepted")
    info = inspect(root, initiative, frozen)
    if (info["bundle_sha"] != row["bundle_sha"]
            or info["report_sha"] != row["report_sha"]
            or info["preview_sha"] != row["preview_sha"]):
        raise ValueError("accepted G2 preview or review bundle changed")


def describe(root: Path, initiative: str) -> dict:
    root = root.resolve(strict=True)
    with agentflow.open_db(root, initiative) as conn:
        row = latest(conn)
        if row is None:
            raise ValueError("no live G2 prototype has been generated")
        return inspect(root, initiative, row, canonical_report=row["status"] in {"READY", "ACCEPTED"})


def make_server(preview_bytes: bytes, expected_sha: str) -> ThreadingHTTPServer:
    """Serve only frozen HTML on a random loopback port; never a repository tree."""
    if _digest(preview_bytes) != expected_sha:
        raise ValueError("preview bytes were modified before server setup")
    check_html(preview_bytes)

    class PreviewHandler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self._reply(head_only=True)

        def do_GET(self):
            self._reply(head_only=False)

        def _reply(self, *, head_only: bool) -> None:
            if urlsplit(self.path).path not in {"/", "/index.html"}:
                self.send_error(404, "Only the reviewed prototype is served")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(preview_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.end_headers()
            if not head_only:
                self.wfile.write(preview_bytes)

        def log_message(self, *_args) -> None:
            pass

    class LocalOnlyServer(ThreadingHTTPServer):
        allow_reuse_address = False
        daemon_threads = True

    return LocalOnlyServer(("127.0.0.1", 0), PreviewHandler)


def serve(root: Path, initiative: str, *, open_browser: bool = False) -> str:
    info = describe(root, initiative)
    if info["status"] != "READY":
        raise ValueError("live G2 preview is not awaiting review; inspect live-status")
    path = _file(root.resolve(), info["preview_path"])
    with make_server(path.read_bytes(), info["preview_sha"]) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"REVIEW {info['iteration']} · Codex-produced offline HTML")
        print(f"Preview: {url}")
        print(f"Report SHA-256: {info['report_sha']}")
        print(f"Preview SHA-256: {info['preview_sha']}")
        print(f"Bundle SHA-256: {info['bundle_sha']}")
        print("Interact with it in a browser running on this host; Ctrl+C stops the preview.")
        print("Opening the HTML directly bypasses the server's CSP; use this URL.", flush=True)
        if open_browser and not webbrowser.open(url):
            print("No local browser handler opened; use the displayed URL.", flush=True)
        try:
            server.serve_forever(poll_interval=0.15)
        except KeyboardInterrupt:
            pass
    return f"Stopped preview {info['iteration']}"
