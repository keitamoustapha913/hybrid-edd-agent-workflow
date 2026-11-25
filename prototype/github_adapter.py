"""GitHub Issues parent/sub-issue and progress publishing adapter.

All calls are explicit and use the GitHub CLI's REST API transport. The
Coordinator simulation never makes network writes merely by advancing a stage.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess


REPO_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,99}/[A-Za-z0-9_.-]{1,100}$")


class GitHubError(ValueError):
    """GitHub operation failed; no secrets or raw API responses are exposed."""


@dataclass(frozen=True)
class IssueInfo:
    number: int
    database_id: int
    url: str
    title: str


def validate_repo(value: str) -> str:
    if not REPO_SLUG.fullmatch(value) or ".." in value.split("/", 1)[1]:
        raise GitHubError("GitHub repository must be OWNER/REPO")
    return value


def _api(repo: str, method: str, suffix: str, payload: dict | None = None) -> dict:
    validate_repo(repo)
    if method not in {"GET", "POST"}:
        raise GitHubError("unsupported GitHub method")
    arguments = [
        "gh", "api", "--method", method, f"repos/{repo}/{suffix}",
        "-H", "Accept: application/vnd.github+json",
        "-H", "X-GitHub-Api-Version: 2026-03-10",
    ]
    if payload is not None:
        arguments.extend(["--input", "-"])
    environment = dict(os.environ, GH_PROMPT_DISABLED="1")
    try:
        process = subprocess.run(
            arguments, input=json.dumps(payload) if payload is not None else None,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=45, check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitHubError("GitHub CLI unavailable or timed out; verify installation and authentication") from error
    # Do not relay stdout/stderr verbatim: remote errors may include sensitive data.
    if process.returncode:
        raise GitHubError(
            f"GitHub {method} failed for {repo} (exit {process.returncode}); "
            "check gh auth status, repository permissions, and sub-issue support. "
            "The outcome may require reconciliation before retrying."
        )
    try:
        data = json.loads(process.stdout)
    except (ValueError, TypeError) as error:
        raise GitHubError("GitHub returned an invalid API response; reconcile before retrying") from error
    if not isinstance(data, dict):
        raise GitHubError("GitHub returned a non-object API response")
    return data


def _issue(data: dict, repo: str) -> IssueInfo:
    number, database_id, url, title = (
        data.get("number"), data.get("id"), data.get("html_url"), data.get("title"),
    )
    if type(number) is not int or number < 1 or type(database_id) is not int or database_id < 1:
        raise GitHubError("GitHub returned invalid issue identifiers; reconcile before retrying")
    if not isinstance(url, str) or url.lower() != f"https://github.com/{repo}/issues/{number}".lower():
        raise GitHubError("GitHub returned unexpected issue URL; reconcile before retrying")
    if not isinstance(title, str):
        raise GitHubError("GitHub returned an invalid issue title")
    return IssueInfo(number, database_id, url, title)


def create_parent(repo: str, *, initiative: str, title: str, body: str) -> IssueInfo:
    return _issue(_api(repo, "POST", "issues", {
        "title": title,
        "body": body + f"\n\n<!-- agentflow-parent:{initiative} -->\n",
    }), repo)


def create_mvp_subissue(
    repo: str, *, initiative: str, parent_database_id: int,
    mvp: str, title: str, body: str,
) -> IssueInfo:
    if type(parent_database_id) is not int or parent_database_id < 1:
        raise GitHubError("parent issue database ID must be valid")
    return _issue(_api(repo, "POST", "issues", {
        "title": title,
        "body": body + f"\n\n<!-- agentflow-subissue:{initiative}:{mvp} -->\n",
        "parent_issue_id": parent_database_id,
    }), repo)


def post_comment(repo: str, issue_number: int, body: str) -> int:
    if type(issue_number) is not int or issue_number < 1:
        raise GitHubError("invalid issue number")
    result = _api(repo, "POST", f"issues/{issue_number}/comments", {"body": body})
    comment_id = result.get("id")
    if type(comment_id) is not int or comment_id < 1:
        raise GitHubError("GitHub returned invalid comment identity; reconcile before retrying")
    return comment_id
