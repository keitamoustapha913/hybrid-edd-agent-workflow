import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prototype"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agentflow  # noqa: E402
import dashboard  # noqa: E402
from test_git_adapter import git, init_repo  # noqa: E402


class GitAwareCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.state_root = self.workspace / "agentflow-kit"
        self.state_root.mkdir()
        self.repo = init_repo(self.workspace, "app")
        self.other = init_repo(self.workspace, "other-app")
        self.initiative = "demo-git"
        agentflow.initialize(self.state_root, self.initiative)

    def test_attach_is_read_only_to_repository(self):
        before = git(self.repo, "rev-parse", "HEAD")
        message = agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        self.assertIn("Attached Git repository", message)
        context = agentflow.git_context(self.state_root, self.initiative)
        self.assertEqual(context["branch"], "main")
        self.assertEqual(context["head"], before)
        self.assertFalse(context["dirty"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before)
        self.assertIn("Git HEAD", agentflow.status(self.state_root, self.initiative))

    def test_create_worktree_then_bind_to_it(self):
        agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        output = agentflow.create_worktree(self.state_root, self.initiative, "mvp-01")
        self.assertIn("agentflow/demo-git/mvp-01", output)
        context = agentflow.git_context(self.state_root, self.initiative)
        self.assertTrue(context["selected_root"].endswith("app-agentflow-demo-git-mvp-01"))
        self.assertEqual(context["branch"], "agentflow/demo-git/mvp-01")
        self.assertEqual(len(context["worktrees"]), 2)
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")
        self.assertIn("Registered worktrees", agentflow.git_status(self.state_root, self.initiative))
        git_snapshot = dashboard.snapshot_text(
            self.state_root, self.initiative, agentflow, show_git=True,
        )
        self.assertIn("GIT / WORKTREES", git_snapshot)
        self.assertIn("agentflow/demo-git/mvp-01", git_snapshot)
        self.assertIn("[B] Bind", git_snapshot)
        self.assertIn("[Q] Quit", git_snapshot)

    def test_select_existing_worktree_and_reject_foreign_one(self):
        agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        agentflow.create_worktree(self.state_root, self.initiative, "mvp-01")
        agentflow.select_worktree(self.state_root, self.initiative, self.repo)
        self.assertEqual(agentflow.git_context(self.state_root, self.initiative)["branch"], "main")
        with self.assertRaisesRegex(ValueError, "different Git repository"):
            agentflow.select_worktree(self.state_root, self.initiative, self.other)
        self.assertEqual(agentflow.git_context(self.state_root, self.initiative)["branch"], "main")

    def test_dirty_repository_blocks_simulated_start_without_touching_user_file(self):
        agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        user_file = self.repo / "user-notes.md"
        user_file.write_text("Keep this", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "uncommitted changes"):
            agentflow.advance(self.state_root, self.initiative)
        self.assertEqual(user_file.read_text(encoding="utf-8"), "Keep this")
        with agentflow.open_db(self.state_root, self.initiative) as conn:
            self.assertEqual(agentflow.current(conn)["status"], "NOT_STARTED")

    def test_git_revision_change_rejects_pending_human_approval(self):
        agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        agentflow.advance(self.state_root, self.initiative)
        with agentflow.open_db(self.state_root, self.initiative) as conn:
            row = agentflow.current(conn)
            gate, digest = row["pending_gate"], row["pending_sha"]
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.test",
            "commit", "--allow-empty", "-q", "-m", "external change")
        with self.assertRaisesRegex(ValueError, "revision changed"):
            agentflow.approve(self.state_root, self.initiative, gate, digest)
        with agentflow.open_db(self.state_root, self.initiative) as conn:
            self.assertEqual(agentflow.current(conn)["pending_gate"], gate)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_dirty_repository_rejects_pending_human_approval(self):
        agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        agentflow.advance(self.state_root, self.initiative)
        with agentflow.open_db(self.state_root, self.initiative) as conn:
            row = agentflow.current(conn)
            gate, digest = row["pending_gate"], row["pending_sha"]
        (self.repo / "uncommitted.txt").write_text("uncommitted", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "uncommitted changes"):
            agentflow.approve(self.state_root, self.initiative, gate, digest)

    def test_branch_switch_is_reported_as_invalid_binding(self):
        agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        git(self.repo, "switch", "-q", "-c", "another-branch")
        context = agentflow.git_context(self.state_root, self.initiative)
        self.assertIn("branch changed", context["error"])
        with self.assertRaisesRegex(ValueError, "Git binding changed"):
            agentflow.advance(self.state_root, self.initiative)

    def test_cannot_change_worktree_after_running(self):
        agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        agentflow.advance(self.state_root, self.initiative)
        with self.assertRaisesRegex(ValueError, "unstarted initiative"):
            agentflow.create_worktree(self.state_root, self.initiative, "mvp-01")
        with self.assertRaisesRegex(ValueError, "unstarted initiative"):
            agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        self.assertEqual(len(git(self.repo, "worktree", "list", "--porcelain").split("worktree")) - 1, 1)

    def test_previous_database_without_git_table_can_be_attached(self):
        with agentflow.open_db(self.state_root, self.initiative) as conn:
            conn.execute("DROP TABLE git_binding")
        self.assertIsNone(agentflow.git_context(self.state_root, self.initiative))
        agentflow.attach_repo(self.state_root, self.initiative, self.repo)
        self.assertEqual(agentflow.git_context(self.state_root, self.initiative)["branch"], "main")

    def test_no_binding_keeps_original_mock_workflow(self):
        self.assertIsNone(agentflow.git_context(self.state_root, self.initiative))
        output = agentflow.advance(self.state_root, self.initiative)
        self.assertTrue(any("G1_DISCOVERY" in line for line in output))

    def test_cli_repository_worktree_and_snapshot_commands(self):
        executable = str(Path(agentflow.__file__))

        def run(*args):
            return subprocess.run(
                [sys.executable, "-B", executable, "--root", str(self.state_root),
                 *args, self.initiative],
                capture_output=True, text=True, check=False,
            )

        attached = subprocess.run(
            [sys.executable, "-B", executable, "--root", str(self.state_root),
             "repo-attach", self.initiative, "--path", str(self.repo)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(attached.returncode, 0, attached.stderr)
        created = subprocess.run(
            [sys.executable, "-B", executable, "--root", str(self.state_root),
             "worktree-create", self.initiative, "--mvp", "mvp-01"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        state = run("repo-status")
        self.assertEqual(state.returncode, 0, state.stderr)
        self.assertIn("agentflow/demo-git/mvp-01", state.stdout)
        snapshot = subprocess.run(
            [sys.executable, "-B", executable, "--root", str(self.state_root),
             "tui", self.initiative, "--snapshot", "--git-panel"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
        self.assertIn("GIT / WORKTREES", snapshot.stdout)

    def test_attaching_repo_that_contains_agentflow_state_is_refused(self):
        nested_state = self.repo / "harness"
        nested_state.mkdir()
        agentflow.initialize(nested_state, "new-demo")
        with self.assertRaisesRegex(ValueError, "state would be inside"):
            agentflow.attach_repo(nested_state, "new-demo", self.repo)


if __name__ == "__main__":
    unittest.main()
