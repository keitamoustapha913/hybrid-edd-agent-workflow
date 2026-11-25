"""One-command live UI: no report hashes or repeated test flags typed by users."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from prototype import agentflow, live_console, live_coordinator
from test_codex_runtime import FAKE_CODEX
from test_git_adapter import init_repo


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.id = "simple-live"
        agentflow.initialize(self.root, self.id)
        self.repo = init_repo(self.root, "source-repo")
        agentflow.attach_repo(self.root, self.id, self.repo)
        (self.root / "work" / self.id / "brief.md").write_text("# Brief\nBuild a demo.\n")
        self.binary = self.root / "fake_codex"
        self.binary.write_text(FAKE_CODEX)
        self.binary.chmod(0o700)

    def script(self, *answers):
        values = iter(answers)
        prompts = []

        def ask(prompt):
            prompts.append(prompt)
            return next(values)

        return ask, prompts

    def test_guided_live_automatically_continues_from_g1_to_new_g2_review(self):
        ask, prompts = self.script("n", "yes", "a", "yes", "q")
        output = []
        exit_code = live_console.launch(
            self.root, self.id, ask=ask, say=output.append,
            codex_bin=str(self.binary), timeout=5,
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("G1_DISCOVERY", "\n".join(output))
        self.assertIn("Approve G1_DISCOVERY", "\n".join(prompts))
        self.assertNotIn("SHA-256", "\n".join(prompts))
        with agentflow.open_db(self.root, self.id) as db:
            state = live_coordinator._state(db)
            self.assertEqual(state["status"], "WAITING_FOR_HUMAN")
            self.assertEqual(state["stage_index"], 2)
            self.assertEqual(state["pending_gate"], "G2_PROTOTYPE")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM live_approvals").fetchone()[0], 1)

    def test_evidence_plan_is_configured_once_and_reusable(self):
        ask, prompts = self.script(
            "uv run --offline pytest", "uv run python capture.py --output screenshots/main.png",
            "screenshots/main.png", "yes",
        )
        notes = []
        plan = live_console.configure_plan(self.root, self.id, ask, notes.append)
        self.assertEqual(plan["tests"], ["uv run --offline pytest"])
        self.assertEqual(plan["ux_artifacts"], ["screenshots/main.png"])
        self.assertEqual(plan, live_console.load_plan(self.root, self.id))
        path = self.root / "work" / self.id / "evidence-plan.json"
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text())["schema_version"], 1)
        ask_no, _ = self.script("no")
        reused = live_console.configure_plan(self.root, self.id, ask_no, notes.append)
        self.assertEqual(reused, plan)
        self.assertEqual(live_console.load_plan(self.root, self.id), plan)

    def test_new_live_plan_rejects_missing_capture_not_downgrade_to_pass(self):
        ask, _ = self.script("pytest", "echo okay", "../outside.png", "yes")
        with self.assertRaisesRegex(ValueError, "escape"):
            live_console.configure_plan(self.root, self.id, ask, lambda _line: None)
        self.assertFalse((self.root / "work" / self.id / "evidence-plan.json").exists())

    def test_live_snapshot_is_read_only_and_not_simulation(self):
        live_coordinator.initialize(self.root, self.id)
        output = live_console.snapshot(self.root, self.id)
        self.assertIn("LIVE COORDINATOR", output)
        self.assertIn("DISCOVERY", output)
        self.assertIn("simple-live", output)
        kit = Path(__file__).resolve().parents[1] / "workflow.py"
        result = subprocess.run([
            sys.executable, str(kit), "--root", str(self.root),
            "live", self.id, "--snapshot",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LIVE COORDINATOR", result.stdout)
        with agentflow.open_db(self.root, self.id) as db:
            self.assertEqual(agentflow.current(db)["status"], "NOT_STARTED")


if __name__ == "__main__":
    unittest.main()
