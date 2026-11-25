import sys
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prototype"))

import agentflow  # noqa: E402
import dashboard  # noqa: E402


class TerminalDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.initiative = "demo-dashboard"
        agentflow.initialize(self.root, self.initiative)

    def view(self):
        return dashboard.load_view(self.root, self.initiative, agentflow)

    def test_initial_dashboard_shows_start_action(self):
        view = self.view()
        self.assertEqual(view.active_agent, "DISCOVERY")
        self.assertEqual(view.stage_index, 0)
        text = dashboard.snapshot_text(self.root, self.initiative, agentflow)
        self.assertIn("SIMULATION MODE", text)
        self.assertIn("[N] Start / Continue", text)
        self.assertIn("No report yet", text)

    def test_run_shows_pending_gate_report_and_approval_actions(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        view = self.view()
        self.assertEqual(view.active_agent, "HUMAN REVIEW")
        self.assertEqual(view.last_agent, "DISCOVERY")
        self.assertEqual(view.pending_gate, "G1_DISCOVERY")
        self.assertEqual(view.artifact_integrity, "UNCHANGED — matches review request")
        text = dashboard.snapshot_text(self.root, self.initiative, agentflow, width=55, height=32)
        self.assertIn("APPROVAL REQUIRED", text)
        self.assertIn("[A] Approve  [C] Changes", text)
        self.assertIn("DISCOVERY — SIMULATED OUTPUT", text)
        self.assertIn("Artifact verified", text)
        self.assertTrue(all(len(line) <= 55 for line in text.splitlines()))

    def test_full_size_dashboard_has_distinct_fixed_panels(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        lines = dashboard.snapshot_text(self.root, self.initiative, agentflow).splitlines()
        self.assertEqual(len(lines), 24)
        self.assertIn("WORKFLOW STAGES", lines[3])
        self.assertIn("APPROVAL REQUIRED", lines[3])
        self.assertIn("CURRENT REPORT", lines[8])
        self.assertIn("RECENT ACTIVITY", lines[19])
        self.assertIn("[A] Approve", lines[-2])
        self.assertTrue(all(len(line) <= 80 for line in lines))

    def test_compact_dashboard_keeps_current_report_and_actions(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        lines = dashboard.snapshot_text(
            self.root, self.initiative, agentflow, width=55, height=20,
        ).splitlines()
        self.assertEqual(len(lines), 20)
        text = "\n".join(lines)
        for heading in ("WORKFLOW", "APPROVAL REQUIRED", "CURRENT REPORT", "RECENT ACTIVITY"):
            self.assertIn(heading, text)
        self.assertIn("[Q] Quit", lines[-2])
        self.assertIn("◆ Discovery", text)
        self.assertTrue(all(len(line) <= 55 for line in lines))

    def test_report_scroll_does_not_move_action_bar(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        view = replace(self.view(), report_text="\n".join(f"Line {i:02d}" for i in range(40)))
        top = dashboard.compose_screen(view, width=80, height=24, scroll=0)
        bottom = dashboard.compose_screen(view, width=80, height=24, scroll=1000)
        self.assertGreater(top.max_scroll, 0)
        self.assertEqual(top.render()[-2:], bottom.render()[-2:])
        self.assertIn("Line 00", "\n".join(top.render()))
        self.assertIn("Line 39", "\n".join(bottom.render()))

    def test_history_view_uses_same_panel_and_footer(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        canvas = dashboard.compose_screen(self.view(), show_history=True)
        text = "\n".join(canvas.render())
        self.assertIn("EVENT HISTORY", text)
        self.assertIn("[H] Report", text)
        self.assertIn("GATE REQUESTED", text)

    def test_approve_advances_to_next_agent_automatically(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        dashboard.perform_action(self.root, self.initiative, agentflow, "approve")
        self.assertEqual(self.view().pending_gate, "G2_PROTOTYPE")
        self.assertEqual(self.view().last_agent, "PROTOTYPE")

    def test_change_request_returns_to_producer_and_autoruns(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        response = dashboard.perform_action(self.root, self.initiative, agentflow, "changes", "Refine user interviews")
        self.assertIn("Changes requested", response)
        self.assertEqual(self.view().pending_gate, "G1_DISCOVERY")
        with agentflow.open_db(self.root, self.initiative) as conn:
            changes = conn.execute("SELECT COUNT(*) FROM events WHERE kind='CHANGES_REQUESTED'").fetchone()[0]
        self.assertEqual(changes, 1)

    def test_tampered_report_is_flagged_and_approval_rejected(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        path = self.root / "work" / self.initiative / "discovery.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nChanged", encoding="utf-8")
        self.assertIn("CHANGED", self.view().artifact_integrity)
        self.assertIn("approval blocked", "\n".join(dashboard.compose_screen(self.view()).render()))
        with self.assertRaisesRegex(ValueError, "stale"):
            dashboard.perform_action(self.root, self.initiative, agentflow, "approve")

    def test_blocked_handoff_has_retry_action(self):
        agentflow.advance(self.root, self.initiative, invalid_handoff=True)
        view = self.view()
        self.assertEqual(view.state, "BLOCKED")
        self.assertIn("[R] Retry handoff", dashboard.available_actions(view))
        self.assertIn("ATTENTION REQUIRED", "\n".join(dashboard.compose_screen(view).render()))
        dashboard.perform_action(self.root, self.initiative, agentflow, "retry")
        self.assertEqual(self.view().pending_gate, "G1_DISCOVERY")

    def test_finished_dashboard_has_no_approval_action(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        for _ in range(5):
            dashboard.perform_action(self.root, self.initiative, agentflow, "approve")
        view = self.view()
        self.assertEqual(view.state, "SIMULATED_COMPLETE")
        self.assertEqual(view.stage_index, 13)
        self.assertIn("DEMO COMPLETE", "\n".join(dashboard.render_lines(view)))
        self.assertIn("WORKFLOW FINISHED", "\n".join(dashboard.compose_screen(view).render()))
        self.assertNotIn("Approve", dashboard.available_actions(view))

    def test_untrusted_report_path_cannot_escape_initiative(self):
        dashboard.perform_action(self.root, self.initiative, agentflow, "run")
        with agentflow.open_db(self.root, self.initiative) as conn:
            conn.execute("UPDATE workflow SET pending_path='../../outside.md' WHERE singleton=1")
        view = self.view()
        self.assertEqual(view.artifact_integrity, "MISSING / INVALID — approval blocked")
        self.assertIn("outside the permitted", view.report_text)

    def test_snapshot_subcommand_works_without_tty(self):
        result = subprocess.run(
            [sys.executable, "-B", str(Path(agentflow.__file__)), "--root", str(self.root),
             "tui", self.initiative, "--snapshot", "--width", "55", "--height", "20"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NEXT ACTION", result.stdout)
        self.assertIn("DISCOVERY", result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 20)

    def test_noninteractive_tui_rejects_without_creating_initiative(self):
        result = subprocess.run(
            [sys.executable, "-B", str(Path(agentflow.__file__)), "--root", str(self.root),
             "tui", "new-demo"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("needs a terminal", result.stderr)
        self.assertFalse((self.root / "work" / "new-demo").exists())


if __name__ == "__main__":
    unittest.main()
