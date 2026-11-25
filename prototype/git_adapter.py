"""Safe local Git repository and worktree inspection for the Agentflow prototype.

No fetch, push, checkout, commit, deletion or remote operation occurs here.
Only create_mvp_worktree mutates Git, and it requires an explicit invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


class GitError(ValueError):
    """A safe, user-facing Git operation failure."""


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    head: str
    branch: str | None
    locked: bool = False
    prunable: bool = False


@dataclass(frozen=True)
class RepositoryInfo:
    root: Path
    common_dir: Path
    branch: str | None
    head: str
    dirty: bool
    change_count: int
    origin: str
    worktrees: tuple[WorktreeInfo, ...]


MVP_ID = re.compile(r"^mvp-[0-9]{2,3}$")
GIT_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _git(path: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *arguments],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"could not run Git: {error}") from error
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        explanation = detail[-1] if detail else f"exit status {result.returncode}"
        raise GitError(f"git {arguments[0]} failed: {explanation}")
    return result


def _resolve(path: Path) -> Path:
    if not path.is_dir():
        raise GitError(f"repository/worktree directory does not exist: {path}")
    return path.resolve(strict=True)


def _common_dir(root: Path) -> Path:
    result = _git(root, "rev-parse", "--git-common-dir").stdout.strip()
    location = Path(result)
    return (location if location.is_absolute() else root / location).resolve(strict=True)


def _safe_origin(root: Path) -> str:
    result = _git(root, "remote", "get-url", "origin", check=False)
    if result.returncode:
        return "no origin remote"
    raw = result.stdout.strip()
    if raw.startswith("git@github.com:"):
        name = raw.split(":", 1)[1].removesuffix(".git").strip("/")
        return f"github.com/{name}" if name else "GitHub origin"
    parsed = urlsplit(raw)
    if parsed.hostname and parsed.hostname.lower() == "github.com":
        name = parsed.path.removesuffix(".git").strip("/")
        return f"github.com/{name}" if name else "GitHub origin"
    # Never display userinfo or an arbitrary remote URL, which may contain credentials.
    return "origin configured (non-GitHub)"


def list_worktrees(repository: Path) -> tuple[WorktreeInfo, ...]:
    root = _resolve(repository)
    output = _git(root, "worktree", "list", "--porcelain", "-z").stdout
    entries: list[WorktreeInfo] = []
    fields: dict[str, str] = {}

    def finish() -> None:
        if not fields.get("worktree"):
            return
        branch = fields.get("branch")
        if branch and branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/"):]
        entries.append(WorktreeInfo(
            path=Path(fields["worktree"]).resolve(strict=False),
            head=fields.get("HEAD", ""),
            branch=branch,
            locked="locked" in fields,
            prunable="prunable" in fields,
        ))

    for part in output.split("\0"):
        if not part:
            finish()
            fields = {}
            continue
        name, _, value = part.partition(" ")
        fields[name] = value
    if fields:
        finish()
    if not entries:
        raise GitError("Git returned no registered worktrees")
    return tuple(entries)


def inspect(repository: Path) -> RepositoryInfo:
    root = _resolve(repository)
    actual = Path(_git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve(strict=True)
    if _git(actual, "rev-parse", "--is-bare-repository").stdout.strip() != "false":
        raise GitError("bare repositories cannot host an Agentflow worktree")
    head = _git(actual, "rev-parse", "--verify", "HEAD").stdout.strip()
    branch_response = _git(actual, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    branch = branch_response.stdout.strip() if branch_response.returncode == 0 else None
    changes = _git(actual, "status", "--porcelain=v1", "-z", "--untracked-files=normal").stdout
    return RepositoryInfo(
        root=actual,
        common_dir=_common_dir(actual),
        branch=branch,
        head=head,
        dirty=bool(changes),
        change_count=len([item for item in changes.split("\0") if item]),
        origin=_safe_origin(actual),
        worktrees=list_worktrees(actual),
    )


def same_repository(first: RepositoryInfo, second: RepositoryInfo) -> bool:
    return first.common_dir == second.common_dir


def create_mvp_worktree(
    repository: Path, *, initiative: str, mvp: str,
    base: str = "HEAD", target: Path | None = None,
) -> RepositoryInfo:
    """Create exactly one new branch and worktree, never touching existing files."""
    source = inspect(repository)
    if not MVP_ID.fullmatch(mvp):
        raise GitError("MVP ID must be in the form mvp-01 or mvp-001")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", initiative):
        raise GitError("invalid initiative ID")
    if not base or base.startswith("-") or any(c.isspace() or ord(c) < 32 for c in base):
        raise GitError("invalid base revision")
    branch = f"agentflow/{initiative}/{mvp}"
    if not GIT_BRANCH.fullmatch(branch):
        raise GitError("invalid generated branch name")
    if _git(source.root, "check-ref-format", "--branch", branch, check=False).returncode:
        raise GitError("Git rejected the generated branch name")

    base_commit = _git(source.root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").stdout.strip()
    primary = source.worktrees[0].path
    destination = Path(target) if target is not None else (
        primary.parent / f"{primary.name}-agentflow-{initiative}-{mvp}"
    )
    destination = destination.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise GitError(f"worktree destination already exists: {destination}")
    for existing in source.worktrees:
        if existing.path == destination or existing.path in destination.parents:
            raise GitError("cannot create an MVP worktree inside an existing checkout")

    _git(source.root, "worktree", "add", "-b", branch, str(destination), base_commit)
    created = inspect(destination)
    if created.branch != branch or created.head != base_commit or not same_repository(source, created):
        raise GitError("created worktree identity does not match the requested branch/revision")
    return created


def integration_branch(initiative: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", initiative):
        raise GitError("invalid initiative ID")
    return f"agentflow/{initiative}/integration"


def create_integration_worktree(
    repository: Path, *, initiative: str, base: str = "HEAD",
    target: Path | None = None,
) -> RepositoryInfo:
    """Create one local integration branch/worktree; safely reuse a registered one."""
    source = inspect(repository)
    if source.dirty:
        raise GitError("source worktree must be clean before creating an integration branch")
    branch = integration_branch(initiative)
    if not base or base.startswith("-") or any(c.isspace() or ord(c) < 32 for c in base):
        raise GitError("invalid integration base revision")
    base_commit = _git(source.root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").stdout.strip()
    primary = source.worktrees[0].path
    destination = Path(target) if target is not None else (
        primary.parent / f"{primary.name}-agentflow-{initiative}-integration"
    )
    destination = destination.expanduser().resolve(strict=False)

    for tree in source.worktrees:
        if tree.branch == branch:
            if tree.path != destination:
                raise GitError(f"integration branch is already checked out at {tree.path}; select that path")
            existing = inspect(tree.path)
            if not same_repository(source, existing):
                raise GitError("registered integration worktree belongs to another repository")
            return existing
    if destination.exists() or destination.is_symlink():
        raise GitError(f"integration worktree destination already exists: {destination}")
    for tree in source.worktrees:
        if tree.path in destination.parents:
            raise GitError("cannot create integration worktree inside an existing checkout")
    if _git(source.root, "check-ref-format", "--branch", branch, check=False).returncode:
        raise GitError("Git rejected integration branch name")
    _git(source.root, "worktree", "add", "-b", branch, str(destination), base_commit)
    created = inspect(destination)
    if created.branch != branch or created.head != base_commit or not same_repository(source, created):
        raise GitError("created integration worktree identity mismatch")
    return created


def promote_fast_forward(
    integration: Path, source: Path, *, initiative: str, mvp: str,
) -> RepositoryInfo:
    """Move integration forward to a committed MVP tip, never rewriting or merging divergent history."""
    target = inspect(integration)
    candidate = inspect(source)
    expected_integration = integration_branch(initiative)
    expected_mvp = f"agentflow/{initiative}/{mvp}"
    if not MVP_ID.fullmatch(mvp) or candidate.branch != expected_mvp:
        raise GitError(f"source must be the committed {expected_mvp} MVP worktree")
    if target.branch != expected_integration or not same_repository(target, candidate):
        raise GitError("integration or MVP worktree identity mismatch")
    if target.dirty or candidate.dirty:
        raise GitError("both integration and MVP worktrees must be clean before promoting")
    if target.head == candidate.head:
        return target
    ancestor = _git(target.root, "merge-base", "--is-ancestor", target.head, candidate.head, check=False)
    if ancestor.returncode:
        raise GitError("MVP branch diverges from integration; manual integration/review is required")
    _git(target.root, "merge", "--ff-only", candidate.head)
    result = inspect(target.root)
    if result.head != candidate.head or result.branch != expected_integration or result.dirty:
        raise GitError("integration moved to an unexpected revision; manual inspection required")
    return result


def push_integration(integration: Path, *, initiative: str, remote: str = "origin") -> RepositoryInfo:
    """Explicit non-force push of one named integration branch to one configured remote."""
    target = inspect(integration)
    branch = integration_branch(initiative)
    if target.branch != branch or target.dirty:
        raise GitError("integration worktree must be on its expected branch and clean before pushing")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote):
        raise GitError("invalid Git remote name")
    if _git(target.root, "remote", "get-url", remote, check=False).returncode:
        raise GitError(f"Git remote {remote} is not configured")
    # Never expose the remote URL or Git's stderr, which might contain credentials.
    result = _git(
        target.root, "push", "--porcelain", remote,
        f"refs/heads/{branch}:refs/heads/{branch}", check=False,
    )
    if result.returncode:
        raise GitError("integration push rejected or failed; inspect Git remote permissions/history (no force push attempted)")
    return inspect(target.root)
