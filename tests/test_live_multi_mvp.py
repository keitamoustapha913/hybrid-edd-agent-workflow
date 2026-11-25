"""MVP 05: sequential, scope-gated live MVPs keep independent proof."""

import base64
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from prototype import agentflow, live_console, live_coordinator, live_evidence, live_prototype
from test_codex_runtime import FAKE_CODEX
from test_git_adapter import init_repo


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4/5+hHgAHggJ/P0kxkAAAAABJRU5ErkJggg=="


class LiveMultiMVPTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.id = "multi-mvp"
        agentflow.initialize(self.root, self.id)
        self.repo = init_repo(self.root, "source-repo")
        agentflow.attach_repo(self.root, self.id, self.repo)
        self.work = self.root / "work" / self.id
        (self.work / "brief.md").write_text("# Build a small product in staged MVPs.\n")
        self.binary = self.root / "fake_codex"
        self.binary.write_text(FAKE_CODEX)
        self.binary.chmod(0o700)
        live_coordinator.initialize(self.root, self.id)

    def state(self):
        with agentflow.open_db(self.root, self.id) as db:
            return dict(live_coordinator._state(db))

    def run_agents(self, *, mode="valid"):
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": mode}):
            return live_coordinator.run(self.root, self.id, codex_bin=str(self.binary), timeout=5)

    def accept(self):
        state = self.state()
        gate = state["pending_gate"]
        bundle = None
        notes = ""
        if gate == "G2_PROTOTYPE":
            bundle = live_prototype.describe(self.root, self.id)["bundle_sha"]
            notes = "Personally tried the example browser interaction."
        if gate in {"G6_UX_INSPECTION", "G7_PROVE"}:
            with agentflow.open_db(self.root, self.id) as db:
                bundle = live_evidence.validate(db, self.root, self.id)["bundle_sha"]
            notes = "Reviewed the screenshot, command exits and scoped limitations."
        return live_coordinator.approve(
            self.root, self.id, gate, state["pending_sha"],
            bundle_sha=bundle, review_notes=notes,
        )

    def collect_evidence(self):
        assert self.state()["status"] == "AWAITING_EVIDENCE"
        test_script = "from pathlib import Path; assert Path('green-feature-example.txt').is_file()"
        ux_script = (
            "from pathlib import Path; import base64; "
            "p=Path('captures/mobile.png'); p.parent.mkdir(parents=True, exist_ok=True); "
            f"p.write_bytes(base64.b64decode('{PNG}'))"
        )
        return live_evidence.collect(
            self.root, self.id,
            tests=[f"{shlex.quote(sys.executable)} -c {shlex.quote(test_script)}"],
            ux_checks=[f"{shlex.quote(sys.executable)} -c {shlex.quote(ux_script)}"],
            ux_artifacts=["captures/mobile.png"], timeout=5,
        )

    def finish_current_mvp(self):
        self.run_agents(mode="writer_change")
        self.assertEqual(self.state()["status"], "AWAITING_EVIDENCE")
        self.assertIn("PASS independent evidence", self.collect_evidence())
        self.run_agents()
        self.assertEqual(self.state()["pending_gate"], "G6_UX_INSPECTION")
        self.accept()
        self.run_agents()
        self.assertEqual(self.state()["pending_gate"], "G7_PROVE")
        self.accept()
        self.assertEqual(self.state()["status"], "COMPLETED")

    def finish_product_and_first_scope(self):
        for gate in ("G1_DISCOVERY", "G2_PROTOTYPE", "G3_PRODUCT_SPEC", "G4_ROADMAP", "G5_MVP_01"):
            self.run_agents()
            self.assertEqual(self.state()["pending_gate"], gate)
            self.accept()

    def test_cannot_schedule_before_g7_or_skip_roadmap_mvp(self):
        with self.assertRaisesRegex(ValueError, "finish the current MVP"):
            live_coordinator.schedule_next(self.root, self.id)
        self.finish_product_and_first_scope()
        self.finish_current_mvp()
        with self.assertRaisesRegex(ValueError, "only the next sequential MVP"):
            live_coordinator.schedule_next(self.root, self.id, mvp_id="mvp-03")
        self.assertEqual(self.state()["active_mvp"], "mvp-01")

    def test_next_mvp_must_be_named_in_approved_roadmap(self):
        for gate in ("G1_DISCOVERY", "G2_PROTOTYPE", "G3_PRODUCT_SPEC", "G4_ROADMAP", "G5_MVP_01"):
            self.run_agents(mode="single_roadmap")
            self.assertEqual(self.state()["pending_gate"], gate)
            self.accept()
        self.finish_current_mvp()
        self.assertIsNone(live_coordinator.available_next(self.root, self.id))
        with self.assertRaisesRegex(ValueError, "not named in the approved G4 roadmap"):
            live_coordinator.schedule_next(self.root, self.id)

    def test_existing_scoped_mvp_docs_still_require_new_g5(self):
        self.finish_product_and_first_scope()
        self.finish_current_mvp()
        existing = self.work / "mvps/mvp-02"
        (existing / "ux").mkdir(parents=True)
        (existing / "spec.md").write_text("# Feature scope from approved roadmap\n")
        (existing / "ux/spec.md").write_text("# UI/UX acceptance contract\n")
        decisions = iter(("m", "yes", "q"))
        screens = []
        self.assertEqual(live_console.launch(
            self.root, self.id, ask=lambda _prompt: next(decisions),
            say=screens.append, codex_bin=str(self.binary), timeout=5,
        ), 0)
        self.assertIn("[M] Next MVP", "\n".join(screens))
        self.assertEqual(self.state()["active_mvp"], "mvp-02")
        # No new specialist is needed to invent a duplicate scope, but the
        # current feature and UX contracts must both be reviewed at G5.
        self.assertEqual(self.state()["status"], "WAITING_FOR_HUMAN")
        self.assertEqual(self.state()["pending_gate"], "G5_MVP_02")
        self.assertEqual(self.state()["pending_path"], f"work/{self.id}/mvps/mvp-02/spec.md")
        (existing / "ux/spec.md").write_text("# Unreviewed edit\n")
        with self.assertRaisesRegex(ValueError, "companion UI/UX scope hash changed"):
            self.accept()
        (existing / "ux/spec.md").write_text("# UI/UX acceptance contract\n")
        self.accept()
        self.assertEqual(self.state()["stage_index"], 5)

    def test_legacy_live_db_migrates_only_when_writing(self):
        # Rebuild the new, still-unstarted live state using the pre-MVP05
        # schema shape and ensure the read-only snapshot does not ALTER it.
        old_schema = live_coordinator.SCHEMA.replace(
            ",\n    active_mvp TEXT NOT NULL DEFAULT 'mvp-01'", ""
        ).replace(
            ",\n    mvp_id TEXT NOT NULL DEFAULT 'mvp-01'", ""
        )
        with agentflow.open_db(self.root, self.id) as db:
            db.execute("DROP TABLE live_workflow")
            db.execute("DROP TABLE live_approvals")
            db.executescript(old_schema)
            from prototype import codex_runtime, git_adapter
            info = git_adapter.inspect(self.repo)
            db.execute(
                "INSERT INTO live_workflow(singleton,stage_index,status,expected_head,"
                "expected_branch,expected_common_dir,expected_fingerprint) "
                "VALUES(1,0,'READY',?,?,?,?)",
                (info.head, info.branch, str(info.common_dir), codex_runtime._git_fingerprint(self.repo)),
            )
        self.assertIn("LIVE COORDINATOR", live_console.snapshot(self.root, self.id))
        with agentflow.open_db(self.root, self.id) as db:
            self.assertNotIn("active_mvp", [row[1] for row in db.execute("PRAGMA table_info(live_workflow)")])
            live_coordinator._ensure_mvp_schema(db)
            self.assertEqual(live_coordinator._active_mvp(live_coordinator._state(db)), "mvp-01")
            self.assertIn("mvp_id", [row[1] for row in db.execute("PRAGMA table_info(live_approvals)")])

    def test_two_mvp_cycles_have_distinct_scope_proof_and_evidence(self):
        self.finish_product_and_first_scope()
        self.finish_current_mvp()
        first = self.work / "mvps/mvp-01/edd/verification-evidence.md"
        frozen_first = first.read_bytes()
        self.assertEqual(live_coordinator.available_next(self.root, self.id), "mvp-02")
        self.assertIn("SCHEDULED MVP-02", live_coordinator.schedule_next(self.root, self.id))
        self.assertEqual(self.state()["active_mvp"], "mvp-02")
        self.assertEqual(self.state()["stage_index"], 4)
        self.assertIsNone(live_console.load_plan(self.root, self.id))

        self.run_agents()
        self.assertEqual(self.state()["pending_gate"], "G5_MVP_02")
        self.assertTrue((self.work / "mvps/mvp-02/spec.md").is_file())
        self.assertTrue((self.work / "mvps/mvp-02/ux/spec.md").is_file())
        self.assertIn("mvp-02", self.state()["pending_path"])
        self.accept()
        self.assertEqual(self.state()["stage_index"], 5)
        self.finish_current_mvp()
        second = self.work / "mvps/mvp-02/edd/verification-evidence.md"
        self.assertTrue(second.is_file())
        self.assertEqual(first.read_bytes(), frozen_first)
        self.assertIn("MVP-02", second.read_text())
        self.assertTrue((self.work / "mvps/mvp-02/ux/inspection-acceptance.md").is_file())
        self.assertTrue((self.work / "mvps/mvp-02/edd/proof-acceptance.md").is_file())
        with agentflow.open_db(self.root, self.id) as db:
            records = db.execute("SELECT mvp_id FROM live_mvp_completions ORDER BY mvp_id").fetchall()
            self.assertEqual([r[0] for r in records], ["mvp-01", "mvp-02"])
            reviewed = db.execute(
                "SELECT mvp_id FROM live_approvals WHERE gate='G7_PROVE' "
                "AND revoked_at IS NULL ORDER BY mvp_id"
            ).fetchall()
            self.assertEqual([r[0] for r in reviewed], ["mvp-01", "mvp-02"])
            self.assertEqual(live_evidence.validate(db, self.root, self.id, mvp_id="mvp-01",
                                                    require_current=False)["checks"], 2)
            self.assertEqual(live_evidence.validate(db, self.root, self.id)["checks"], 2)
        self.assertIn("MVP-02", live_console.snapshot(self.root, self.id))
        # A later MVP cannot silently replace or ignore the prior MVP's proof.
        first.write_text("tampered old evidence\n")
        with self.assertRaisesRegex(ValueError, "(approved UI/UX contract changed|verification receipt changed)"):
            live_coordinator.schedule_next(self.root, self.id, mvp_id="mvp-03")


if __name__ == "__main__":
    unittest.main()
