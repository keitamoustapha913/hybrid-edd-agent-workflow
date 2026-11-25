"""The Git specialist reviews; only the deterministic adapter may mutate Git."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prototype import agentflow, git_agent, live_console, live_coordinator
from test_codex_runtime import FAKE_CODEX
from test_git_adapter import git, init_repo


class GitAgentReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.id = "git-review"
        self.repo = init_repo(self.root, "app")
        agentflow.initialize(self.root, self.id)
        agentflow.attach_repo(self.root, self.id, self.repo)
        live_coordinator.initialize(self.root, self.id)
        self.binary = self.root / "fake_codex"
        self.binary.write_text(FAKE_CODEX, encoding="utf-8")
        self.binary.chmod(0o700)

    def review(self, mode="valid"):
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": mode}):
            return git_agent.review(self.root, self.id, action="PREPARE",
                                    codex_bin=str(self.binary), timeout=5)

    def test_real_separate_readonly_codex_session_produces_bound_review(self):
        head = git(self.repo, "rev-parse", "HEAD")
        result = self.review()
        self.assertEqual(result["decision"], "READY")
        self.assertEqual(result["source_head"], head)
        self.assertEqual(result["action"], "PREPARE")
        self.assertIn("Checked", result["summary"])
        self.assertTrue((Path(result["run_dir"]) / "events.jsonl").is_file())
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(len(git(self.repo, "worktree", "list", "--porcelain").split("worktree ")) - 1, 1)
        self.assertIsNone(agentflow.project_context(self.root, self.id))
        parent = (self.root / "work" / self.id / "project-ticket.md").read_text(encoding="utf-8")
        self.assertIn("Git Agent read-only reviews", parent)
        self.assertIn("PREPARE", parent)

    def test_mismatched_agent_handoff_cannot_authorize_a_git_action(self):
        with self.assertRaisesRegex(ValueError, "identity|digest|context"):
            self.review("fake_git_mismatch")
        self.assertIsNone(agentflow.project_context(self.root, self.id))

    def test_repository_mutation_during_readonly_review_is_detected(self):
        with self.assertRaisesRegex(ValueError, "changed|modified|worktree"):
            self.review("fake_git_repo_touch")
        self.assertIsNone(agentflow.project_context(self.root, self.id))

    def test_changed_worktree_after_review_invalidates_later_human_authorization(self):
        reviewed = self.review()
        (self.repo / "late-change.txt").write_text("after review\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after Git Agent review"):
            git_agent.assert_fresh(self.root, self.id, reviewed)
        self.assertIsNone(agentflow.project_context(self.root, self.id))

    def test_git_agent_can_block_setup_without_asking_for_confirmation(self):
        answers = iter(("i", "1", "b", "q"))
        messages = []
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": "fake_git_block"}):
            self.assertEqual(live_console.launch(
                self.root, self.id, ask=lambda _question: next(answers),
                say=messages.append, codex_bin=str(self.binary), timeout=5,
            ), 0)
        self.assertIn("Git Agent blocked", "\n".join(messages))
        self.assertIsNone(agentflow.project_context(self.root, self.id))


if __name__ == "__main__":
    unittest.main()
