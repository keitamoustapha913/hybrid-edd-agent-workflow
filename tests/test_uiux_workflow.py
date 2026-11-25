"""UX contracts must be upstream, hash-bound and inspected in dedicated stages."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prototype import agentflow, dashboard, mvp_progress, project_artifacts


class UIUXWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.id = "ux-orders"
        agentflow.initialize(self.root, self.id)
        self.work = self.root / "work" / self.id

    def pending(self):
        with agentflow.open_db(self.root, self.id) as conn:
            row = agentflow.current(conn)
            return row["pending_gate"], row["pending_sha"]

    def approve(self, gate):
        actual, sha = self.pending()
        self.assertEqual(actual, gate)
        return agentflow.approve(self.root, self.id, gate, sha)

    def to_product_spec(self):
        agentflow.advance(self.root, self.id)
        self.approve("G1_DISCOVERY")
        self.approve("G2_PROTOTYPE")
        self.assertEqual(self.pending()[0], "G3_PRODUCT_SPEC")

    def to_scope(self):
        self.to_product_spec()
        self.approve("G3_PRODUCT_SPEC")
        self.approve("G4_ROADMAP")
        self.assertEqual(self.pending()[0], "G5_MVP_01")

    def test_upstream_spec_generates_ux_contract_and_g3_binds_both(self):
        self.to_product_spec()
        product_ux = self.work / "product-ux-spec.md"
        self.assertIn("UX-001", product_ux.read_text())
        self.assertIn("accessibility", product_ux.read_text().lower())
        view = dashboard.load_view(self.root, self.id, agentflow)
        self.assertEqual(view.ux_scope["sha"], agentflow.sha256(product_ux))
        self.assertEqual(view.ux_scope["path"], f"work/{self.id}/product-ux-spec.md")
        self.assertTrue(view.artifact_integrity.startswith("UNCHANGED"))
        gate_screen = dashboard.snapshot_text(self.root, self.id, agentflow, width=108, height=42)
        self.assertIn("FEATURE + UI/UX SPECS", gate_screen)
        self.assertIn("COMPANION UI/UX SPEC", gate_screen)
        parent = (self.work / "project-ticket.md").read_text()
        self.assertIn("Product UI/UX contract", parent)

    def test_changed_product_ux_spec_blocks_g3_even_when_product_spec_unchanged(self):
        self.to_product_spec()
        gate, digest = self.pending()
        ux_path = self.work / "product-ux-spec.md"
        ux_path.write_text(ux_path.read_text() + "\nChanged without G3 review\n")
        self.assertIn("CHANGED UI/UX scope", dashboard.load_view(self.root, self.id, agentflow).artifact_integrity)
        with self.assertRaisesRegex(ValueError, "stale UI/UX specification"):
            agentflow.approve(self.root, self.id, gate, digest)
        self.assertEqual(self.pending()[0], gate)

    def test_per_mvp_ux_spec_is_generated_and_hash_bound_to_g5(self):
        self.to_scope()
        ux_path = self.work / "mvps/mvp-01/ux/spec.md"
        self.assertIn("MVP-01-UX-001", ux_path.read_text())
        self.assertIn("UX-001", ux_path.read_text())
        self.assertIn("focused", ux_path.read_text().lower())
        view = dashboard.load_view(self.root, self.id, agentflow)
        self.assertEqual(view.ux_scope["sha"], agentflow.sha256(ux_path))
        gate_screen = dashboard.snapshot_text(self.root, self.id, agentflow, width=108, height=42)
        self.assertIn("FEATURE + UI/UX SPECS", gate_screen)
        child = (self.work / "mvps/mvp-01/ticket.md").read_text()
        self.assertIn("UI/UX scope", child)
        self.assertIn("0/2", child)
        gate, digest = self.pending()
        ux_path.write_text(ux_path.read_text() + "\nUnauthorised UX change\n")
        with self.assertRaisesRegex(ValueError, "stale UI/UX specification"):
            agentflow.approve(self.root, self.id, gate, digest)

    def test_prior_approved_product_ux_contract_is_rechecked_before_g5(self):
        self.to_scope()
        path = self.work / "product-ux-spec.md"
        path.write_text(path.read_text() + "\nChanged after G3.\n")
        with self.assertRaisesRegex(ValueError, "stale UI/UX specification"):
            self.approve("G5_MVP_01")
        self.assertEqual(self.pending()[0], "G5_MVP_01")

    def test_each_mvp_runs_one_ux_role_in_two_distinct_ordered_sessions(self):
        self.to_scope()
        self.approve("G5_MVP_01")
        with agentflow.open_db(self.root, self.id) as conn:
            handoffs = [json.loads(row["detail"]) for row in conn.execute(
                "SELECT detail FROM events WHERE kind='HANDOFF_CHECKED' ORDER BY event_id"
            )]
        stages = [item["stage"] for item in handoffs]
        self.assertEqual(stages[-8:], [
            "ux_design", "requirements", "red", "green", "refactor", "verify", "ux_inspect", "prove",
        ])
        ux = [item for item in handoffs if item["role"] == "UIUX"]
        self.assertEqual(len(ux), 2)
        self.assertNotEqual(ux[0]["run_id"], ux[1]["run_id"])
        self.assertTrue(all(item["valid"] for item in ux))
        self.assertEqual(ux[0]["mvp_id"], "mvp-01")
        mvp = self._collect()[0]
        self.assertEqual(mvp.ux_completed, 2)
        self.assertEqual(mvp.completed, 6)
        self.assertEqual([step.stage for step in mvp.ux_stages], list(mvp_progress.UX_STAGES))
        self.assertTrue(all(step.status == "DONE" for step in mvp.ux_stages))
        self.assertIn("UX 2/2", agentflow.status(self.root, self.id))
        child = (self.work / "mvps/mvp-01/ticket.md").read_text()
        self.assertIn("| UX DESIGN | DONE |", child)
        self.assertIn("| UX INSPECT | DONE |", child)
        self.assertLess(child.index("| VERIFY |"), child.index("| UX INSPECT |"))
        self.assertLess(child.index("| UX INSPECT |"), child.index("| PROVE |"))
        published = {relative.as_posix() for relative, _file, _data in project_artifacts.collect(self.work)}
        self.assertIn("product-ux-spec.md", published)
        self.assertIn("mvps/mvp-01/ux/spec.md", published)
        self.assertIn("mvps/mvp-01/ux/design-review.md", published)
        self.assertIn("mvps/mvp-01/ux/inspection.md", published)

    def _collect(self):
        with agentflow.open_db(self.root, self.id) as conn:
            return mvp_progress.collect(self.root, self.id, conn)

    def test_mock_inspection_does_not_claim_real_visual_verification(self):
        self.to_scope()
        self.approve("G5_MVP_01")
        path = self.work / "mvps/mvp-01/ux/inspection.md"
        text = path.read_text()
        self.assertIn("INSPECTION_RECORDED", text)
        self.assertIn("NOT EXECUTED", text)
        self.assertNotIn("UX_VERIFIED", text)
        self.assertFalse((path.parent / "evidence").exists())
        self.assertIn("SIMULATED_COMPLETE", agentflow.status(self.root, self.id))
        snapshot = dashboard.snapshot_text(self.root, self.id, agentflow,
                                           width=110, height=40, show_mvp=True)
        self.assertIn("UX DESIGN", snapshot)
        self.assertIn("UX INSPECT", snapshot)

    def test_invalid_ux_design_handoff_blocks_before_requirements(self):
        self.to_scope()
        actual = agentflow.mock_specialist

        def invalid_ux(root, initiative, stage, invalid_handoff):
            if stage.name == "ux_design":
                return "invalid-ux", {"valid": False, "errors": ["UX design evidence invalid"]}
            return actual(root, initiative, stage, invalid_handoff)

        with patch.object(agentflow, "mock_specialist", side_effect=invalid_ux):
            self.approve("G5_MVP_01")
        mvp = self._collect()[0]
        self.assertEqual(mvp.status, "BLOCKED")
        self.assertEqual(mvp.active_stage, "ux_design")
        self.assertEqual(mvp.ux_stages[0].status, "BLOCKED")
        self.assertEqual(mvp.stages[0].status, "QUEUED")
        self.assertEqual(dashboard.load_view(self.root, self.id, agentflow).active_agent, "UIUX DESIGN")
        self.assertFalse((self.work / "mvps/mvp-01/edd/requirements.md").exists())

    def test_invalid_ux_inspection_blocks_prove_and_identifies_active_inspector(self):
        self.to_scope()
        actual = agentflow.mock_specialist

        def invalid_inspection(root, initiative, stage, invalid_handoff):
            if stage.name == "ux_inspect":
                return "invalid-inspection", {"valid": False, "errors": ["No actual UX evidence"]}
            return actual(root, initiative, stage, invalid_handoff)

        with patch.object(agentflow, "mock_specialist", side_effect=invalid_inspection):
            self.approve("G5_MVP_01")
        mvp = self._collect()[0]
        self.assertEqual(mvp.active_stage, "ux_inspect")
        self.assertEqual(mvp.stages[-2].status, "DONE")  # VERIFY
        self.assertEqual(mvp.stages[-1].status, "QUEUED")  # PROVE
        self.assertEqual(mvp.ux_stages[-1].status, "BLOCKED")
        self.assertEqual(dashboard.load_view(self.root, self.id, agentflow).active_agent, "UIUX INSPECT")
        self.assertFalse((self.work / "mvps/mvp-01/edd/prove.md").exists())

    def test_additional_mvp_ux_stages_are_unscheduled_not_fictitiously_done(self):
        self.to_scope()
        second = self.work / "mvps/mvp-02"
        second.mkdir()
        (second / "spec.md").write_text("# additional MVP\n")
        ux = second / "ux"
        ux.mkdir()
        (ux / "spec.md").write_text("# future UX\n")
        (ux / "inspection.md").write_text("# fake UX_VERIFIED\n")
        agentflow.refresh_tickets(self.root, self.id)
        second_progress = self._collect()[1]
        self.assertEqual(second_progress.ux_completed, 0)
        self.assertTrue(all(s.status == "UNSCHEDULED" for s in second_progress.ux_stages))
        self.assertTrue(second_progress.ux_stages[1].report_exists)
        self.assertIn("UX INSPECT | UNSCHEDULED", (second / "ticket.md").read_text())


if __name__ == "__main__":
    unittest.main()
