import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prototype"))

import git_adapter  # noqa: E402


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def init_repo(root: Path, name: str = "sample") -> Path:
    repo = root / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.name=Agentflow Tests", "-c", "user.email=agentflow@example.test",
        "commit", "--allow-empty", "-q", "-m", "initial commit")
    return repo


class GitAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = init_repo(self.root)

    def test_inspect_repository_and_dirty_state(self):
        info = git_adapter.inspect(self.repo)
        self.assertEqual(info.root, self.repo)
        self.assertEqual(info.branch, "main")
        self.assertEqual(len(info.head), 40)
        self.assertFalse(info.dirty)
        self.assertEqual(info.worktrees[0].path, self.repo)
        (self.repo / "untracked.txt").write_text("User file", encoding="utf-8")
        self.assertTrue(git_adapter.inspect(self.repo).dirty)
        self.assertTrue((self.repo / "untracked.txt").is_file())

    def test_origin_url_redacts_credentials(self):
        git(self.repo, "remote", "add", "origin", "https://token:secret@github.com/acme/shop.git")
        info = git_adapter.inspect(self.repo)
        self.assertEqual(info.origin, "github.com/acme/shop")
        self.assertNotIn("secret", repr(info))

    def test_create_mvp_worktree_isolated_branch(self):
        initial = git_adapter.inspect(self.repo)
        created = git_adapter.create_mvp_worktree(
            self.repo, initiative="demo-orders", mvp="mvp-01",
        )
        self.assertEqual(created.branch, "agentflow/demo-orders/mvp-01")
        self.assertEqual(created.head, initial.head)
        self.assertEqual(created.common_dir, initial.common_dir)
        self.assertFalse(created.dirty)
        self.assertNotEqual(created.root, initial.root)
        self.assertEqual(len(git_adapter.list_worktrees(self.repo)), 2)
        self.assertEqual(git_adapter.inspect(self.repo).branch, "main")
        self.assertTrue(git_adapter.same_repository(initial, created))

    def test_explicit_path_and_existing_path_refused(self):
        destination = self.root / "my-mvp"
        destination.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            git_adapter.create_mvp_worktree(
                self.repo, initiative="demo-orders", mvp="mvp-01", target=destination,
            )
        self.assertEqual(len(git_adapter.list_worktrees(self.repo)), 1)

    def test_cannot_create_inside_existing_checkout(self):
        with self.assertRaisesRegex(ValueError, "inside an existing checkout"):
            git_adapter.create_mvp_worktree(
                self.repo, initiative="demo-orders", mvp="mvp-01", target=self.repo / "nested",
            )
        self.assertFalse((self.repo / "nested").exists())

    def test_invalid_mvp_base_and_non_repo_refused(self):
        with self.assertRaisesRegex(ValueError, "MVP ID"):
            git_adapter.create_mvp_worktree(self.repo, initiative="demo-orders", mvp="../evil")
        with self.assertRaisesRegex(ValueError, "invalid base"):
            git_adapter.create_mvp_worktree(self.repo, initiative="demo-orders", mvp="mvp-01", base="--hack")
        with self.assertRaisesRegex(ValueError, "failed"):
            git_adapter.create_mvp_worktree(self.repo, initiative="demo-orders", mvp="mvp-01", base="not-a-commit")
        with self.assertRaises(ValueError):
            git_adapter.inspect(self.root)

    def test_creating_same_branch_twice_never_overwrites(self):
        first = git_adapter.create_mvp_worktree(self.repo, initiative="demo-orders", mvp="mvp-01")
        with self.assertRaises(ValueError):
            git_adapter.create_mvp_worktree(
                self.repo, initiative="demo-orders", mvp="mvp-01",
                target=self.root / "second-branch",
            )
        self.assertTrue(first.root.is_dir())
        self.assertFalse((self.root / "second-branch").exists())


if __name__ == "__main__":
    unittest.main()
