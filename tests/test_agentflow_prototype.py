import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prototype"))
from agentflow import (  # noqa: E402
    advance,
    approve,
    database_path,
    initialize,
    open_db,
    request_changes,
    retry,
    status,
)


class AgentflowPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.initiative = "demo-orders"
        initialize(self.root, self.initiative)

    def workflow(self):
        with open_db(self.root, self.initiative) as conn:
            return dict(conn.execute("SELECT * FROM workflow WHERE singleton = 1").fetchone())

    def pending(self):
        row = self.workflow()
        self.assertEqual(row["status"], "WAITING_FOR_HUMAN")
        return row["pending_gate"], row["pending_sha"]

    def test_init_is_idempotently_rejected_and_preserves_brief(self):
        brief = self.root / "work" / self.initiative / "brief.md"
        brief.write_text("My own brief", encoding="utf-8")
        with self.assertRaises(ValueError):
            initialize(self.root, self.initiative)
        self.assertEqual(brief.read_text(encoding="utf-8"), "My own brief")

    def test_pauses_at_human_gate(self):
        output = advance(self.root, self.initiative)
        self.assertTrue(any("G1_DISCOVERY" in line for line in output))
        self.assertEqual(self.pending()[0], "G1_DISCOVERY")
        self.assertIn("SIMULATION_ONLY", status(self.root, self.initiative))

    def test_approval_automatically_starts_next_agent(self):
        advance(self.root, self.initiative)
        gate, digest = self.pending()
        output = approve(self.root, self.initiative, gate, digest)
        self.assertTrue(any("RUN  PROTOTYPE" in line for line in output))
        self.assertEqual(self.pending()[0], "G2_PROTOTYPE")

    def test_stale_artifact_approval_rejected(self):
        advance(self.root, self.initiative)
        gate, digest = self.pending()
        report = self.root / "work" / self.initiative / "discovery.md"
        report.write_text(report.read_text() + "\nchanged", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale"):
            approve(self.root, self.initiative, gate, digest)
        self.assertEqual(self.pending()[0], gate)

    def test_request_changes_returns_to_owner(self):
        advance(self.root, self.initiative)
        response = request_changes(self.root, self.initiative, "Need a clearer hypothesis")
        self.assertIn("DISCOVERY", response)
        output = advance(self.root, self.initiative)
        self.assertTrue(any("RUN  DISCOVERY" in line for line in output))
        self.assertEqual(self.pending()[0], "G1_DISCOVERY")

    def test_invalid_handoff_blocks_and_retry_repairs(self):
        result = advance(self.root, self.initiative, invalid_handoff=True)
        self.assertTrue(any("BLOCKED" in line for line in result))
        self.assertEqual(self.workflow()["status"], "BLOCKED")
        result = retry(self.root, self.initiative)
        self.assertTrue(any("Handoff validated" in line for line in result))
        self.assertEqual(self.pending()[0], "G1_DISCOVERY")

    def test_non_simulated_user_file_is_not_overwritten(self):
        report = self.root / "work" / self.initiative / "discovery.md"
        report.write_text("My real discovery", encoding="utf-8")
        output = advance(self.root, self.initiative)
        self.assertTrue(any("BLOCKED" in line for line in output))
        self.assertEqual(report.read_text(encoding="utf-8"), "My real discovery")

    def test_symlink_escape_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir) / "discovery.md"
            report = self.root / "work" / self.initiative / "discovery.md"
            report.symlink_to(outside)
            output = advance(self.root, self.initiative)
            self.assertTrue(any("BLOCKED" in line for line in output))
            self.assertFalse(outside.exists())

    def test_full_mock_lifecycle(self):
        advance(self.root, self.initiative)
        for expected in ("G1_DISCOVERY", "G2_PROTOTYPE", "G3_PRODUCT_SPEC", "G4_ROADMAP", "G5_MVP_01"):
            gate, digest = self.pending()
            self.assertEqual(gate, expected)
            approve(self.root, self.initiative, gate, digest)
        self.assertEqual(self.workflow()["status"], "SIMULATED_COMPLETE")
        self.assertEqual(self.workflow()["stage_index"], 13)
        self.assertTrue((self.root / "work/demo-orders/mvps/mvp-01/edd/prove.md").is_file())
        with sqlite3.connect(database_path(self.root, self.initiative)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 5)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events WHERE kind='HANDOFF_CHECKED'").fetchone()[0], 12)

    def test_rejects_path_traversal_in_initiative_id(self):
        with self.assertRaises(ValueError):
            initialize(self.root, "../invalid")


if __name__ == "__main__":
    unittest.main()
