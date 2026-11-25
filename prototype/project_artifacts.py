"""Explicit, reviewable publication of workflow Markdown into the integration branch.

The caller must authorize this operation: documents can contain private data.
This only creates local Git commits; pushing is a separate explicit operation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess


MARKER = "<!-- AGENTFLOW_MANAGED_PROJECT_ARTIFACT -->\n"
ALLOWED_ROOT_FILES = {
    "brief.md", "discovery.md", "prototype-report.md",
    "product-spec.md", "product-ux-spec.md", "mvp-roadmap.md", "project-ticket.md",
}
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    head: str
    artifact_count: int
    index_path: str
    committed: bool


def _git(path: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *arguments], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Git artifact command unavailable: {error}") from error
    if check and result.returncode:
        raise ValueError(f"git {arguments[0]} failed during artifact snapshot; inspect the integration worktree")
    return result


def collect(source: Path) -> list[tuple[Path, Path, bytes]]:
    """Return (relative path, original path, contents) from approved report locations."""
    source = source.resolve(strict=True)
    selected: list[tuple[Path, Path, bytes]] = []
    total = 0
    candidates = [source / name for name in sorted(ALLOWED_ROOT_FILES)]
    mvps = source / "mvps"
    if mvps.is_dir() and not mvps.is_symlink():
        candidates.extend(sorted(mvps.glob("mvp-*/spec.md")))
        candidates.extend(sorted(mvps.glob("mvp-*/ux/*.md")))
        candidates.extend(sorted(mvps.glob("mvp-*/ticket.md")))
        candidates.extend(sorted(mvps.glob("mvp-*/edd/*.md")))
    for candidate in candidates:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        if source not in candidate.resolve().parents:
            raise ValueError("artifact path escapes its initiative directory")
        content = candidate.read_bytes()
        total += len(content)
        if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("artifact snapshot exceeds safe size limits; publish manually after review")
        selected.append((candidate.relative_to(source), candidate, content))
    return selected


def snapshot(source: Path, integration: Path, *, initiative: str) -> Snapshot:
    """Copy and commit approved Markdown + index with no other files staged."""
    if not initiative or "/" in initiative or ".." in initiative:
        raise ValueError("invalid initiative")
    artifacts = collect(source)
    if not artifacts:
        raise ValueError("no workflow Markdown artifacts exist yet")
    integration = integration.resolve(strict=True)
    relative_root = Path("docs") / "agentflow" / initiative
    publication_root = integration / relative_root
    publication_root_resolved = publication_root.resolve(strict=False)
    if integration not in publication_root_resolved.parents:
        raise ValueError("publication directory escapes integration checkout")
    planned: list[tuple[Path, bytes]] = []
    index_lines = [
        "# Agentflow project artifacts",
        "",
        "> Published by explicit operator action. Review each source document for secrets or private data before pushing.",
        "> A simulation-generated report is not real agent/test evidence.",
        "",
        "| Artifact | Source SHA-256 |",
        "| --- | --- |",
    ]
    for relative, _original, content in artifacts:
        target = publication_root / relative
        if publication_root_resolved not in target.resolve(strict=False).parents:
            raise ValueError("artifact target escapes the publication directory")
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise ValueError(f"artifact destination is not a regular file: {target}")
        if target.is_file() and not target.read_bytes().startswith(MARKER.encode()):
            raise ValueError(f"refusing to overwrite non-Agentflow document: {target}")
        planned.append((target, MARKER.encode() + content))
        index_lines.append(f"| [{relative.as_posix()}]({relative.as_posix()}) | {hashlib.sha256(content).hexdigest()} |")
    index_path = publication_root / "INDEX.md"
    if index_path.exists() and (index_path.is_symlink() or not index_path.is_file()
                                or not index_path.read_bytes().startswith(MARKER.encode())):
        raise ValueError("refusing to overwrite a non-Agentflow project index")
    planned.append((index_path, (MARKER + "\n".join(index_lines) + "\n").encode()))
    for target, data in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
    _git(integration, "add", "--", str(relative_root))
    changed = _git(integration, "diff", "--cached", "--quiet", "--", str(relative_root), check=False)
    if changed.returncode not in (0, 1):
        raise ValueError("cannot inspect staged project artifacts")
    committed = changed.returncode == 1
    if committed:
        _git(integration, "commit", "-m", f"docs(agentflow): snapshot {initiative} workflow reports")
    head = _git(integration, "rev-parse", "HEAD").stdout.strip()
    return Snapshot(head, len(artifacts), str(relative_root / "INDEX.md"), committed)
