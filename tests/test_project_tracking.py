import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prototype"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agentflow  # noqa: E402
import dashboard  # noqa: E402
import git_adapter  # noqa: E402
import github_adapter  # noqa: E402
from test_git_adapter import git, init_repo  # noqa: E402


class ProjectTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_root = Path(self.temp.name)
        self.state_root = self.temp_root / "workflow-kit"
        self.state_root.mkdir()
        self.repo = init_repo(self.temp_root, "app")
        self.id = "demo-project"
        agentflow.initialize(self.state_root, self.id)
        self.parent = self.state_root / "work" / self.id / "project-ticket.md"

    def _to_roadmap(self):
        agentflow.advance(self.state_root, self.id)
        for gate in ("G1_DISCOVERY", "G2_PROTOTYPE", "G3_PRODUCT_SPEC"):
            with agentflow.open_db(self.state_root, self.id) as conn:
                row = agentflow.current(conn)
                self.assertEqual(row["pending_gate"], gate)
                digest = row["pending_sha"]
            agentflow.approve(self.state_root, self.id, gate, digest)

    def test_local_parent_ticket_exists_from_initialization_without_github(self):
        self.assertTrue(self.parent.is_file())
        content = self.parent.read_text(encoding="utf-8")
        self.assertIn("Local source of truth", content)
        self.assertIn("GitHub mirror: disabled", agentflow.project_status(self.state_root, self.id))
        self.assertIn("Discovery", content)
        self.assertFalse((self.state_root / "work" / self.id / "mvps").exists())

    def test_parent_and_mvp_child_update_after_each_stage(self):
        self._to_roadmap()
        child = self.state_root / "work" / self.id / "mvps" / "mvp-01" / "ticket.md"
        self.assertTrue(child.is_file())
        self.assertIn("mvp-01", self.parent.read_text(encoding="utf-8"))
        self.assertIn("Prototype findings", self.parent.read_text(encoding="utf-8"))
        self.assertIn("PENDING / NOT RECORDED", child.read_text(encoding="utf-8"))
        for gate in ("G4_ROADMAP", "G5_MVP_01"):
            with agentflow.open_db(self.state_root, self.id) as conn:
                row = agentflow.current(conn)
                self.assertEqual(row["pending_gate"], gate)
                digest = row["pending_sha"]
            agentflow.approve(self.state_root, self.id, gate, digest)
        child_text = child.read_text(encoding="utf-8")
        self.assertIn("APPROVED", child_text)
        for label in ("REQUIREMENTS", "RED", "GREEN", "REFACTOR", "VERIFY", "PROVE"):
            self.assertIn(label, child_text)
        self.assertIn("simulated", child_text)
        self.assertIn("SIMULATED_COMPLETE", self.parent.read_text(encoding="utf-8"))

    def test_human_notes_survive_ticket_regeneration(self):
        self.parent.write_text(
            self.parent.read_text(encoding="utf-8") + "\nOwner note: keep this section.\n",
            encoding="utf-8",
        )
        agentflow.advance(self.state_root, self.id)
        text = self.parent.read_text(encoding="utf-8")
        self.assertIn("Owner note: keep this section.", text)
        self.assertIn("G1_DISCOVERY", text)

    def test_every_generated_mvp_receives_its_own_local_subticket(self):
        self._to_roadmap()
        second_spec = self.state_root / "work" / self.id / "mvps" / "mvp-02" / "spec.md"
        second_spec.parent.mkdir(parents=True)
        second_spec.write_text("# MVP-02\n\nDependent vertical slice.\n", encoding="utf-8")
        agentflow.refresh_tickets(self.state_root, self.id)
        second_ticket = second_spec.parent / "ticket.md"
        self.assertTrue(second_ticket.is_file())
        self.assertIn("mvp-02", second_ticket.read_text(encoding="utf-8"))
        self.assertIn("[ticket.md](mvps/mvp-02/ticket.md)", self.parent.read_text(encoding="utf-8"))
        self.assertIn("mvp-01", self.parent.read_text(encoding="utf-8"))

    def test_existing_manual_ticket_is_not_overwritten(self):
        self.parent.write_text("My manual ticket", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manually managed"):
            agentflow.refresh_tickets(self.state_root, self.id)
        self.assertEqual(self.parent.read_text(encoding="utf-8"), "My manual ticket")

    def test_integration_branch_is_per_project_and_persists(self):
        agentflow.attach_repo(self.state_root, self.id, self.repo)
        result = agentflow.create_integration(self.state_root, self.id)
        self.assertIn("agentflow/demo-project/integration", result)
        context = agentflow.project_context(self.state_root, self.id)
        self.assertTrue(Path(context["integration_root"]).is_dir())
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(git_adapter.inspect(Path(context["integration_root"])).branch, context["integration_branch"])
        self.assertIn("Integration already configured", agentflow.create_integration(self.state_root, self.id))
        self.assertEqual(len(git_adapter.list_worktrees(self.repo)), 2)
        self.assertIn("integration", self.parent.read_text(encoding="utf-8"))
        self.assertIn("LOCAL PROJECT TICKET", dashboard.snapshot_text(
            self.state_root, self.id, agentflow, width=110, height=35, show_project=True,
        ))

    def test_new_mvp_worktree_is_based_on_integration_tip(self):
        agentflow.attach_repo(self.state_root, self.id, self.repo)
        agentflow.create_integration(self.state_root, self.id)
        result = agentflow.create_worktree(self.state_root, self.id, "mvp-01")
        self.assertIn("mvp-01", result)
        project = agentflow.project_context(self.state_root, self.id)
        selected = agentflow.git_context(self.state_root, self.id)
        self.assertEqual(selected["head"], project["head"])
        self.assertEqual(selected["branch"], "agentflow/demo-project/mvp-01")

    def test_promote_then_snapshot_and_push_to_local_bare_remote(self):
        remote = self.temp_root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        git(self.repo, "remote", "add", "origin", str(remote))
        git(self.repo, "config", "user.name", "Agentflow Tests")
        git(self.repo, "config", "user.email", "agentflow@example.test")
        agentflow.attach_repo(self.state_root, self.id, self.repo)
        agentflow.create_integration(self.state_root, self.id)
        agentflow.create_worktree(self.state_root, self.id, "mvp-01")
        selected = Path(agentflow.git_context(self.state_root, self.id)["selected_root"])
        (selected / "src.txt").write_text("real committed code", encoding="utf-8")
        git(selected, "add", "src.txt")
        git(selected, "commit", "-q", "-m", "feat: MVP-01")
        promoted = agentflow.promote_mvp(
            self.state_root, self.id, mvp="mvp-01", source=selected,
        )
        self.assertIn("contains mvp-01", promoted)
        context = agentflow.project_context(self.state_root, self.id)
        integration = Path(context["integration_root"])
        self.assertEqual((integration / "src.txt").read_text(encoding="utf-8"), "real committed code")
        agentflow.advance(self.state_root, self.id)
        snap = agentflow.snapshot_project_artifacts(self.state_root, self.id)
        self.assertIn("project artifacts", snap.lower())
        published = integration / "docs" / "agentflow" / self.id / "project-ticket.md"
        self.assertTrue(published.is_file())
        self.assertIn("G1_DISCOVERY", published.read_text(encoding="utf-8"))
        self.assertTrue((integration / "docs" / "agentflow" / self.id / "discovery.md").is_file())
        first_head = git(integration, "rev-parse", "HEAD")
        again = agentflow.snapshot_project_artifacts(self.state_root, self.id)
        self.assertIn("already current", again)
        self.assertEqual(git(integration, "rev-parse", "HEAD"), first_head)
        pushed = agentflow.push_project(self.state_root, self.id)
        self.assertIn("Pushed", pushed)
        self.assertEqual(
            git(remote, "rev-parse", f"refs/heads/agentflow/{self.id}/integration"),
            first_head,
        )
        self.assertNotEqual(git(self.repo, "branch", "--show-current"), "agentflow/demo-project/integration")

    def test_divergent_history_blocks_automatic_promotion(self):
        agentflow.attach_repo(self.state_root, self.id, self.repo)
        agentflow.create_integration(self.state_root, self.id)
        agentflow.create_worktree(self.state_root, self.id, "mvp-01")
        context = agentflow.project_context(self.state_root, self.id)
        selected = Path(agentflow.git_context(self.state_root, self.id)["selected_root"])
        git(self.repo, "config", "user.name", "Agentflow Tests")
        git(self.repo, "config", "user.email", "agentflow@example.test")
        git(Path(context["integration_root"]), "commit", "--allow-empty", "-q", "-m", "external integration change")
        (selected / "src.txt").write_text("code", encoding="utf-8")
        git(selected, "add", "src.txt")
        git(selected, "commit", "-q", "-m", "feat: diverging MVP")
        with self.assertRaisesRegex(ValueError, "binding is invalid|diverges"):
            agentflow.promote_mvp(self.state_root, self.id, mvp="mvp-01", source=selected)

    def test_remote_and_github_writes_require_explicit_confirmation(self):
        executable = str(Path(agentflow.__file__))
        for command in ("integration-push", "integration-snapshot", "tracker-sync"):
            result = subprocess.run(
                [sys.executable, "-B", executable, "--root", str(self.state_root),
                 command, self.id],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("requires --confirm", result.stderr)

    def test_old_state_without_project_tables_is_migrated(self):
        with agentflow.open_db(self.state_root, self.id) as conn:
            for table in ("project_config", "mvp_issues", "external_ops"):
                conn.execute(f"DROP TABLE {table}")
        agentflow.attach_repo(self.state_root, self.id, self.repo)
        agentflow.create_integration(self.state_root, self.id)
        self.assertIn("integration", agentflow.project_status(self.state_root, self.id))


class OptionalGitHubMirrorTests(unittest.TestCase):
    def setUp(self):
        ProjectTrackingTests.setUp(self)
        git(self.repo, "remote", "add", "origin", "https://github.com/acme/example.git")
        agentflow.attach_repo(self.state_root, self.id, self.repo)
        agentflow.create_integration(self.state_root, self.id)
        self.remote_calls = []

        def fake_api(repo, method, suffix, payload=None):
            self.remote_calls.append((repo, method, suffix, payload))
            if suffix == "issues":
                number = 11 if payload.get("parent_issue_id") is None else 12
                return {
                    "number": number, "id": 1000 + number,
                    "title": payload["title"],
                    "html_url": f"https://github.com/{repo}/issues/{number}",
                }
            if suffix.endswith("/comments"):
                return {"id": 2000 + len(self.remote_calls)}
            raise AssertionError(f"unexpected endpoint: {suffix}")

        patcher = patch.object(github_adapter, "_api", side_effect=fake_api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parent_and_native_mvp_subissue_are_optional(self):
        self.assertNotIn("https://github", self.parent.read_text(encoding="utf-8"))
        parent = agentflow.create_parent_ticket(
            self.state_root, self.id, repo="acme/example",
        )
        self.assertIn("/issues/11", parent)
        ProjectTrackingTests._to_roadmap(self)
        child = agentflow.create_mvp_ticket(self.state_root, self.id, mvp="mvp-01")
        self.assertIn("/issues/12", child)
        self.assertEqual(self.remote_calls[1][3]["parent_issue_id"], 1011)
        self.assertIn("https://github.com/acme/example/issues/11", self.parent.read_text(encoding="utf-8"))
        sub_ticket = self.state_root / "work" / self.id / "mvps" / "mvp-01" / "ticket.md"
        self.assertIn("/issues/12", sub_ticket.read_text(encoding="utf-8"))

    def test_progress_sync_appends_comments_once_per_state(self):
        agentflow.create_parent_ticket(self.state_root, self.id, repo="acme/example")
        ProjectTrackingTests._to_roadmap(self)
        agentflow.create_mvp_ticket(self.state_root, self.id, mvp="mvp-01")
        initial = agentflow.sync_project_tickets(self.state_root, self.id)
        self.assertIn("2 GitHub issue", initial)
        before = len(self.remote_calls)
        repeated = agentflow.sync_project_tickets(self.state_root, self.id)
        self.assertEqual(len(self.remote_calls), before)
        self.assertIn("0 GitHub issue", repeated)
        self.assertIn("SIMULATION ONLY", self.remote_calls[-1][3]["body"])

    def test_no_wrong_repository_ticket_creation(self):
        with self.assertRaisesRegex(ValueError, "does not match Git origin"):
            agentflow.create_parent_ticket(self.state_root, self.id, repo="another/repo")
        self.assertFalse(self.remote_calls)


if __name__ == "__main__":
    unittest.main()
