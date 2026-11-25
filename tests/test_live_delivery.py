"""MVP 06: G7-bound local Git delivery through the one-command live UI."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from prototype import agentflow, live_console, live_coordinator, live_delivery
from test_codex_runtime import FAKE_CODEX
from test_git_adapter import git, init_repo
import test_live_multi_mvp as fixture


class LiveDeliveryTests(unittest.TestCase):
    # Reuse the real Codex/evidence/G7 exercise from MVP 05, not forged state.
    state = fixture.LiveMultiMVPTests.state
    run_agents = fixture.LiveMultiMVPTests.run_agents
    accept = fixture.LiveMultiMVPTests.accept
    collect_evidence = fixture.LiveMultiMVPTests.collect_evidence
    finish_current_mvp = fixture.LiveMultiMVPTests.finish_current_mvp
    finish_product_and_first_scope = fixture.LiveMultiMVPTests.finish_product_and_first_scope

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.id = "delivery-demo"
        agentflow.initialize(self.root, self.id)
        self.repo = init_repo(self.root, "source-repo")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "user.email", "test@example.test")
        agentflow.attach_repo(self.root, self.id, self.repo)
        self.work = self.root / "work" / self.id
        (self.work / "brief.md").write_text("# Ship a product in MVPs\n", encoding="utf-8")
        self.binary = self.root / "fake_codex"
        self.binary.write_text(FAKE_CODEX, encoding="utf-8")
        self.binary.chmod(0o700)
        live_coordinator.initialize(self.root, self.id)

    def prepare(self):
        result = live_delivery.prepare(self.root, self.id)
        self.assertIn("integration", result)
        context = agentflow.git_context(self.root, self.id)
        self.assertEqual(context["branch"], f"agentflow/{self.id}/mvp-01")
        self.assertNotEqual(context["selected_root"], str(self.repo))
        self.assertEqual(self.state()["expected_branch"], context["branch"])
        return Path(context["selected_root"])

    def finish_first(self):
        self.finish_product_and_first_scope()
        self.finish_current_mvp()

    def test_setup_creates_separate_integration_and_source_without_running_codex(self):
        selected = self.prepare()
        project = agentflow.project_context(self.root, self.id)
        self.assertNotEqual(project["integration_root"], str(selected))
        self.assertEqual(self.state()["stage_index"], 0)
        self.assertEqual(self.state()["status"], "READY")
        self.assertIn("already prepared", live_delivery.prepare(self.root, self.id))
        with self.assertRaisesRegex(ValueError, "G7|complete"):
            live_delivery.commit(self.root, self.id)
        self.assertIn("[I] Git delivery", live_console.snapshot(self.root, self.id))

    def test_approved_code_reaches_integration_snapshots_and_next_mvp(self):
        remote = self.root / "bare.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        git(self.repo, "remote", "add", "origin", str(remote))
        selected = self.prepare()
        self.finish_first()
        self.assertIn("Status        COMPLETED", live_console.snapshot(self.root, self.id))
        self.assertTrue(selected.joinpath("green-feature-example.txt").is_file())
        self.assertTrue(agentflow.git_context(self.root, self.id)["dirty"])

        committed = live_delivery.commit(self.root, self.id)
        self.assertIn("Committed", committed)
        commit_head = git(selected, "rev-parse", "HEAD")
        self.assertFalse(agentflow.git_context(self.root, self.id)["dirty"])
        self.assertEqual(self.state()["expected_head"], commit_head)
        self.assertEqual(live_delivery.delivery(self.root, self.id)["status"], "COMMITTED")

        self.assertIn("Promoted", live_delivery.promote(self.root, self.id))
        self.assertEqual(live_delivery.delivery(self.root, self.id)["status"], "PROMOTED")
        integration = Path(agentflow.project_context(self.root, self.id)["integration_root"])
        self.assertEqual(git(integration, "rev-parse", "HEAD"), commit_head)
        self.assertTrue((integration / "green-feature-example.txt").is_file())

        self.assertIn("Snapshotted", live_delivery.snapshot(self.root, self.id))
        self.assertTrue((integration / "docs/agentflow" / self.id / "project-ticket.md").is_file())
        self.assertIn("Pushed", live_delivery.push(self.root, self.id))
        pushed_head = git(integration, "rev-parse", "HEAD")
        self.assertEqual(git(remote, "rev-parse", f"refs/heads/agentflow/{self.id}/integration"), pushed_head)
        self.assertEqual(live_delivery.delivery(self.root, self.id)["status"], "PUSHED")
        parent = (self.work / "project-ticket.md").read_text(encoding="utf-8")
        self.assertIn("GIT DELIVERED", parent)
        self.assertIn("mvp-01", parent)

        self.assertIn("SCHEDULED MVP-02", live_coordinator.schedule_next(self.root, self.id))
        self.assertEqual(self.state()["active_mvp"], "mvp-02")
        self.assertEqual(self.state()["expected_head"], pushed_head)
        self.assertEqual(agentflow.git_context(self.root, self.id)["branch"],
                         f"agentflow/{self.id}/mvp-02")
        self.assertIn("[I] Git delivery", live_console.snapshot(self.root, self.id))

    def test_source_change_after_g7_blocks_commit_without_modifying_history(self):
        selected = self.prepare()
        self.finish_first()
        original_head = git(selected, "rev-parse", "HEAD")
        (selected / "unexpected-change.txt").write_text("not independently checked\n")
        with self.assertRaisesRegex(ValueError, "changed outside|fingerprint"):
            live_delivery.commit(self.root, self.id)
        self.assertEqual(git(selected, "rev-parse", "HEAD"), original_head)
        self.assertIsNone(live_delivery.delivery(self.root, self.id))

    def test_promotion_requires_local_commit_and_clean_integration(self):
        self.prepare()
        self.finish_first()
        with self.assertRaisesRegex(ValueError, "commit"):
            live_delivery.promote(self.root, self.id)
        live_delivery.commit(self.root, self.id)
        integration = Path(agentflow.project_context(self.root, self.id)["integration_root"])
        (integration / "other.txt").write_text("someone else is editing\n")
        with self.assertRaisesRegex(ValueError, "dirty"):
            live_delivery.promote(self.root, self.id)
        self.assertEqual(live_delivery.delivery(self.root, self.id)["status"], "COMMITTED")

    def test_terminal_menu_delivers_with_approvals_and_never_pushes_implicitly(self):
        self.prepare()
        self.finish_first()
        answers = iter(("i", "2", "yes", "3", "yes", "4", "yes", "b", "q"))
        screen = []
        self.assertEqual(live_console.launch(
            self.root, self.id, ask=lambda _question: next(answers),
            say=screen.append, codex_bin=str(self.binary), timeout=5,
        ), 0)
        content = "\n".join(screen)
        self.assertIn("[2] Commit G7-approved source", content)
        self.assertIn("Git Agent: inspecting", content)
        self.assertIn("Git Agent report:", content)
        self.assertIn("[3] Promote to integration", content)
        self.assertIn("[4] Snapshot local reports", content)
        self.assertIn("[5] Push integration (optional)", content)
        self.assertIn("No push", content)
        self.assertEqual(live_delivery.delivery(self.root, self.id)["status"], "SNAPSHOTTED")
        with agentflow.open_db(self.root, self.id) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='INTEGRATION_PUSHED'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM live_git_agent_reviews").fetchone()[0], 3)

    def test_failed_commit_hook_requires_explicit_reconciliation_before_retry(self):
        selected = self.prepare()
        self.finish_first()
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o700)
        head_before = git(selected, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "git commit failed"):
            live_delivery.commit(self.root, self.id)
        self.assertEqual(live_delivery.delivery(self.root, self.id)["status"], "COMMITTING")
        self.assertEqual(git(selected, "rev-parse", "HEAD"), head_before)
        with self.assertRaisesRegex(ValueError, "already recorded"):
            live_delivery.commit(self.root, self.id)
        hook.unlink()
        self.assertIn("may be retried", live_delivery.reconcile_commit(self.root, self.id))
        self.assertEqual(live_delivery.delivery(self.root, self.id)["status"], "RETRYABLE")
        self.assertIn("Committed", live_delivery.commit(self.root, self.id))
        self.assertEqual(live_delivery.delivery(self.root, self.id)["status"], "COMMITTED")

    def test_second_mvp_can_deliver_from_the_prior_integration_snapshot(self):
        self.prepare()
        self.finish_first()
        live_delivery.commit(self.root, self.id)
        live_delivery.promote(self.root, self.id)
        live_delivery.snapshot(self.root, self.id)
        first = live_delivery.delivery(self.root, self.id, "mvp-01")
        previous_integration = agentflow.project_context(self.root, self.id)["integration_head"]

        live_coordinator.schedule_next(self.root, self.id)
        self.assertEqual(self.state()["expected_head"], previous_integration)
        self.run_agents()
        self.assertEqual(self.state()["pending_gate"], "G5_MVP_02")
        self.accept()
        self.finish_current_mvp()
        self.assertIn("Committed", live_delivery.commit(self.root, self.id))
        self.assertIn("Promoted", live_delivery.promote(self.root, self.id))
        self.assertIn("Snapshotted", live_delivery.snapshot(self.root, self.id))

        self.assertEqual(live_delivery.delivery(self.root, self.id, "mvp-01"), first)
        self.assertEqual(live_delivery.delivery(self.root, self.id, "mvp-02")["status"], "SNAPSHOTTED")
        integration = Path(agentflow.project_context(self.root, self.id)["integration_root"])
        self.assertTrue((integration / "docs/agentflow" / self.id / "mvps/mvp-01/ticket.md").is_file())
        self.assertTrue((integration / "docs/agentflow" / self.id / "mvps/mvp-02/ticket.md").is_file())

    def test_tampered_approved_evidence_blocks_delivery_after_commit(self):
        self.prepare()
        self.finish_first()
        live_delivery.commit(self.root, self.id)
        receipt = self.work / "mvps/mvp-01/edd/verification-evidence.md"
        receipt.write_text("CHANGED AFTER HUMAN G7\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "approved|evidence"):
            live_delivery.promote(self.root, self.id)
        self.assertEqual(live_delivery.delivery(self.root, self.id)["status"], "COMMITTED")


if __name__ == "__main__":
    unittest.main()
