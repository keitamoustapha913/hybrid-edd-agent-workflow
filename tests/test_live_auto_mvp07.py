"""MVP 07: a human decision authorizes only the next eligible live stretch."""

import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from prototype import agentflow, live_console, live_coordinator, live_prototype
from test_codex_runtime import FAKE_CODEX
from test_git_adapter import init_repo

PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVQIHWP4/5+hHgAHggJ/P0kxkAAAAABJRU5ErkJggg=="
)


class LiveAutoContinuationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.initiative = "auto-workflow"
        agentflow.initialize(self.root, self.initiative)
        repo = init_repo(self.root, "source-repo")
        (repo / "baseline.txt").write_text("Original tracked source fixture\n")
        subprocess.run(["git", "-C", str(repo), "add", "baseline.txt"], check=True)
        subprocess.run([
            "git", "-C", str(repo), "-c", "user.name=Agentflow Tests",
            "-c", "user.email=agentflow@example.test", "commit", "-q", "-m", "source fixture",
        ], check=True)
        agentflow.attach_repo(self.root, self.initiative, repo)
        (self.root / "work" / self.initiative / "brief.md").write_text("# Tiny web demo\n")
        self.binary = self.root / "fake_codex"
        self.binary.write_text(FAKE_CODEX)
        self.binary.chmod(0o700)

    def drive(self, *inputs):
        answers = iter(inputs)
        output = []
        prompts = []

        def ask(prompt):
            prompts.append(prompt)
            return next(answers)

        result = live_console.launch(
            self.root, self.initiative, ask=ask, say=output.append,
            codex_bin=str(self.binary), timeout=5,
        )
        self.assertEqual(result, 0)
        return "\n".join(output), "\n".join(prompts)

    def current(self):
        with agentflow.open_db(self.root, self.initiative) as db:
            return dict(live_coordinator._state(db))

    def events(self, kind):
        with agentflow.open_db(self.root, self.initiative) as db:
            return db.execute("SELECT detail FROM live_events WHERE kind=?", (kind,)).fetchall()

    def test_approval_starts_next_agent_and_stops_at_fresh_human_g2(self):
        screen, prompts = self.drive("n", "yes", "a", "yes", "q")
        self.assertIn("Auto-continue", screen)
        self.assertIn("G2_PROTOTYPE", screen)
        self.assertEqual(self.current()["pending_gate"], "G2_PROTOTYPE")
        self.assertIn("G1_DISCOVERY", prompts)
        self.assertEqual(len(self.events("AUTO_CONTINUATION_AUTHORIZED")), 1)

    def test_prototype_feedback_runs_new_iteration_and_renews_g2(self):
        self.drive("n", "yes", "a", "yes", "c", "Improve the small-screen button", "q")
        self.assertEqual(self.current()["pending_gate"], "G2_PROTOTYPE")
        self.assertEqual(live_prototype.describe(self.root, self.initiative)["iteration"], "iteration-002")
        with agentflow.open_db(self.root, self.initiative) as db:
            iterations = db.execute(
                "SELECT iteration,status FROM live_preview_iterations ORDER BY iteration"
            ).fetchall()
        self.assertEqual([(r[0], r[1]) for r in iterations],
                         [(1, "CHANGES_REQUESTED"), (2, "READY")])
        self.assertEqual(len(self.events("AUTO_CONTINUATION_AUTHORIZED")), 2)

    def test_declined_approval_cannot_trigger_auto_run(self):
        self.drive("n", "yes", "a", "no", "q")
        self.assertEqual(self.current()["pending_gate"], "G1_DISCOVERY")
        self.assertFalse(self.events("AUTO_CONTINUATION_AUTHORIZED"))

    def test_manual_toggle_persists_and_restored_session_does_not_run(self):
        screen, _ = self.drive("t", "n", "yes", "a", "yes", "q")
        self.assertIn("Auto-continuation OFF", screen)
        self.assertEqual(self.current()["status"], "READY")
        self.assertEqual(self.current()["stage_index"], 1)
        self.assertFalse(self.events("AUTO_CONTINUATION_AUTHORIZED"))
        restored_screen, _ = self.drive("q")
        self.assertIn("Auto-continue OFF", restored_screen)
        self.assertEqual(self.current()["stage_index"], 1)
        self.assertEqual(self.current()["status"], "READY")

    def test_session_start_does_not_dispatch_without_explicit_start(self):
        self.drive("q")
        self.assertEqual(self.current()["stage_index"], 0)
        self.assertEqual(self.current()["status"], "READY")
        self.assertFalse(self.events("AUTO_CONTINUATION_AUTHORIZED"))

    def test_evidence_and_final_g7_human_gate_never_auto_deliver_git(self):
        # Each acceptance is an explicit human confirmation; the individual
        # Codex/Handoff sessions in between are automatically scheduled.
        self.drive(
            "n", "yes", "a", "yes",                  # G1
            "a", "I used the interactive demo", "yes", # G2
            "a", "yes", "a", "yes", "a", "yes",    # G3, G4, G5
            "q",
        )
        self.assertEqual(self.current()["status"], "AWAITING_EVIDENCE")

        test_code = "print('test succeeded')"
        image_code = (
            "from pathlib import Path; import base64; "
            "p=Path('captures/mobile.png'); p.parent.mkdir(parents=True, exist_ok=True); "
            f"p.write_bytes(base64.b64decode('{PIXEL}'))"
        )
        plan = {
            "schema_version": 1,
            "tests": [f"{shlex.quote(sys.executable)} -c {shlex.quote(test_code)}"],
            "ux_checks": [f"{shlex.quote(sys.executable)} -c {shlex.quote(image_code)}"],
            "ux_artifacts": ["captures/mobile.png"],
            "timeout": 5,
        }
        live_console._plan_path(self.root, self.initiative).write_text(json.dumps(plan))
        screen, _ = self.drive("e", "yes", "q")
        self.assertIn("PASS independent evidence", screen)
        self.assertEqual(self.current()["pending_gate"], "G6_UX_INSPECTION")

        self.drive(
            "a", "Reviewed the screenshot and recorded test exits", "yes",
            "a", "Reviewed the PROVE report and limitations", "yes", "q",
        )
        self.assertEqual(self.current()["status"], "COMPLETED")
        self.assertEqual(self.current()["active_mvp"], "mvp-01")
        self.assertFalse(self.events("GIT_PUSH"))
        with agentflow.open_db(self.root, self.initiative) as db:
            self.assertFalse(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='live_deliveries'"
            ).fetchone())
        self.assertEqual(len(self.events("AUTO_CONTINUATION_AUTHORIZED")), 7)


if __name__ == "__main__":
    unittest.main()
