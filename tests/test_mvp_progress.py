"""Per-MVP scope, six-stage EDD progress, and honest simulated evidence."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prototype import agentflow, dashboard, mvp_progress


KIT_ROOT = Path(__file__).resolve().parents[1]


class MVPProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.initiative = "demo-mvp"
        agentflow.initialize(self.root, self.initiative)

    def approve_pending(self):
        with agentflow.open_db(self.root, self.initiative) as conn:
            row = agentflow.current(conn)
            gate, digest = row["pending_gate"], row["pending_sha"]
        self.assertIsNotNone(gate)
        return agentflow.approve(self.root, self.initiative, gate, digest)

    def to_roadmap(self):
        agentflow.advance(self.root, self.initiative)
        for _ in range(3):
            self.approve_pending()

    def collect(self):
        with agentflow.open_db(self.root, self.initiative) as conn:
            return mvp_progress.collect(self.root, self.initiative, conn)

    def test_before_mvp_design_shows_no_mvp_not_fictitious_steps(self):
        self.assertEqual(self.collect(), ())
        text = dashboard.snapshot_text(
            self.root, self.initiative, agentflow, width=100, height=30, show_mvp=True,
        )
        self.assertIn("MVP / EDD TRACKER", text)
        self.assertIn("NO MVP SPECS YET", text)

    def test_scope_review_and_six_unstarted_edd_stages(self):
        self.to_roadmap()
        before = self.collect()[0]
        self.assertEqual(before.scope, "PENDING")
        self.assertEqual(before.completed, 0)
        self.assertTrue(all(step.status == "WAITING_SCOPE" for step in before.stages))
        self.approve_pending()  # G4 roadmap approval requests G5 per-MVP scope.
        after = self.collect()[0]
        self.assertEqual(after.scope, "REVIEW")
        self.assertEqual(after.active_stage, "scope approval")
        self.assertIsNotNone(after.scope_time)
        self.assertEqual(dashboard.load_view(self.root, self.initiative, agentflow).active_mvp, "mvp-01")
        child = self.root / f"work/{self.initiative}/mvps/mvp-01/ticket.md"
        self.assertIn("Scope approval: **REVIEW**", child.read_text(encoding="utf-8"))

    def test_completed_cycle_has_six_validated_mock_stages_with_times(self):
        self.to_roadmap()
        self.approve_pending()  # G4
        self.approve_pending()  # G5 and six simulated EDD agents
        mvp = self.collect()[0]
        self.assertEqual(mvp.scope, "APPROVED")
        self.assertEqual(mvp.status, "DONE (MOCK)")
        self.assertEqual(mvp.completed, 6)
        self.assertEqual([s.stage for s in mvp.stages], list(mvp_progress.EDD_STAGES))
        self.assertTrue(all(s.status == "DONE" and s.timestamp for s in mvp.stages))
        self.assertIn("EDD 6/6", agentflow.status(self.root, self.initiative))
        child = self.root / f"work/{self.initiative}/mvps/mvp-01/ticket.md"
        text = child.read_text(encoding="utf-8")
        self.assertIn("| PROVE | DONE |", text)
        self.assertIn("Last observed (UTC)", text)

    def test_blocked_red_does_not_erase_requirements_or_claim_pass(self):
        self.to_roadmap()
        self.approve_pending()  # G4
        original = agentflow.mock_specialist

        def with_invalid_red(root, initiative, stage, invalid_handoff):
            if stage.name == "red":
                return "broken-red", {"valid": False, "errors": ["missing RED proof"]}
            return original(root, initiative, stage, invalid_handoff)

        with patch.object(agentflow, "mock_specialist", side_effect=with_invalid_red):
            self.approve_pending()  # G5 -> requirements valid, RED invalid
        mvp = self.collect()[0]
        self.assertEqual(mvp.status, "BLOCKED")
        self.assertEqual(mvp.active_stage, "red")
        self.assertEqual(mvp.completed, 1)
        self.assertEqual(mvp.stages[0].status, "DONE")
        self.assertEqual(mvp.stages[1].status, "BLOCKED")
        self.assertEqual(mvp.stages[2].status, "QUEUED")
        self.assertIsNotNone(mvp.stages[1].timestamp)
        view = dashboard.load_view(self.root, self.initiative, agentflow)
        matrix = "\n".join(line for line, _ in dashboard._mvp_rows(view, 75))
        self.assertIn("! RED", matrix)

    def test_second_mvp_artifact_is_discovered_but_not_assumed_executed(self):
        self.to_roadmap()
        child = self.root / f"work/{self.initiative}/mvps/mvp-02"
        (child / "edd").mkdir(parents=True)
        (child / "spec.md").write_text("# MVP-02\n", encoding="utf-8")
        (child / "edd" / "prove.md").write_text("# I claim PASS\n", encoding="utf-8")
        agentflow.refresh_tickets(self.root, self.initiative)
        mvps = self.collect()
        self.assertEqual([m.mvp_id for m in mvps], ["mvp-01", "mvp-02"])
        second = mvps[1]
        self.assertEqual(second.status, "UNSCHEDULED")
        self.assertEqual(second.completed, 0)
        self.assertTrue(all(step.status == "UNSCHEDULED" for step in second.stages))
        self.assertTrue(second.stages[-1].report_exists)
        child_ticket = (child / "ticket.md").read_text(encoding="utf-8")
        self.assertIn("| PROVE | UNSCHEDULED |", child_ticket)
        self.assertIn("file does not change that status", child_ticket)
        view = dashboard.load_view(self.root, self.initiative, agentflow)
        snapshot = "\n".join(line for line, _ in dashboard._mvp_rows(view, 90))
        self.assertIn("MVP-02", snapshot)
        self.assertIn("Report exists, but no validated handoff", snapshot)

    def test_cli_snapshot_exposes_read_only_mvp_panel(self):
        self.to_roadmap()
        result = subprocess.run(
            [sys.executable, str(KIT_ROOT / "workflow.py"), "--root", str(self.root),
             "tui", self.initiative, "--snapshot", "--mvp-panel",
             "--width", "100", "--height", "30"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MVP / EDD TRACKER", result.stdout)
        self.assertIn("MVP-01", result.stdout)
        self.assertIn("REQUIREMENTS", result.stdout)
        self.assertIn("[V] Back", result.stdout)


if __name__ == "__main__":
    unittest.main()
