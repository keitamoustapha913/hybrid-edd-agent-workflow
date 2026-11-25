"""MVP 04: real host command execution, frozen UI artifacts and new human gates."""

import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from prototype import agentflow, live_console, live_coordinator, live_evidence
from test_codex_runtime import FAKE_CODEX
from test_git_adapter import init_repo


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4/5+hHgAHggJ/P0kxkAAAAABJRU5ErkJggg=="


class LiveEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.id = "host-evidence"
        agentflow.initialize(self.root, self.id)
        self.repo = init_repo(self.root, "source-repo")
        agentflow.attach_repo(self.root, self.id, self.repo)
        self.binary = self.root / "fake_codex"
        self.binary.write_text(FAKE_CODEX)
        self.binary.chmod(0o700)
        self.work = self.root / "work" / self.id
        (self.work / "brief.md").write_text("# Brief\nExample with UI.\n")

    def state(self):
        with agentflow.open_db(self.root, self.id) as db:
            return dict(live_coordinator._state(db))

    def progress(self, *, mode="valid"):
        # No actual model calls are made; the real dispatch harness runs a fake CLI.
        from unittest.mock import patch
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": mode}):
            return live_coordinator.run(self.root, self.id, codex_bin=str(self.binary), timeout=5)

    def accept(self, *, notes="Reviewed actual host evidence and observed the example interaction."):
        from prototype import live_prototype
        state = self.state()
        bundle = None
        review = ""
        if state["pending_gate"] == "G2_PROTOTYPE":
            bundle = live_prototype.describe(self.root, self.id)["bundle_sha"]
            review = "Opened the synthetic offline prototype and tried the example button."
        elif state["pending_gate"] in {"G6_UX_INSPECTION", "G7_PROVE"}:
            with agentflow.open_db(self.root, self.id) as db:
                bundle = live_evidence.validate(db, self.root, self.id)["bundle_sha"]
            review = notes
        return live_coordinator.approve(
            self.root, self.id, state["pending_gate"], state["pending_sha"],
            bundle_sha=bundle, review_notes=review,
        )

    def through_verify(self):
        live_coordinator.initialize(self.root, self.id)
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G1_DISCOVERY")
        self.accept()
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G2_PROTOTYPE")
        self.accept()
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G3_PRODUCT_SPEC")
        self.accept()
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G4_ROADMAP")
        self.accept()
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G5_MVP_01")
        self.accept()
        self.progress(mode="writer_change")
        self.assertEqual(self.state()["status"], "AWAITING_EVIDENCE")
        self.assertEqual(self.state()["stage_index"], 11)
        self.assertIn("HOST EVIDENCE REVIEW", live_coordinator.status(self.root, self.id))

    def args(self):
        test = (
            f"{shlex.quote(sys.executable)} -c "
            + shlex.quote("from pathlib import Path; assert Path('green-feature-example.txt').read_text() == 'GREEN fixture\\n'")
        )
        script = (
            "from pathlib import Path; import base64; "
            "p=Path('captures/mobile.png'); p.parent.mkdir(parents=True, exist_ok=True); "
            f"p.write_bytes(base64.b64decode('{PNG}'))"
        )
        ux = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
        return {"tests": [test], "ux_checks": [ux], "ux_artifacts": ["captures/mobile.png"], "timeout": 5}

    def collect(self):
        return live_evidence.collect(self.root, self.id, **self.args())

    def test_guided_evidence_uses_saved_plan_with_one_key_not_retyped_flags(self):
        self.through_verify()
        plan = {"schema_version": 1, **self.args()}
        (self.work / "evidence-plan.json").write_text(json.dumps(plan))
        commands = iter(("e", "yes", "q"))
        prompts = []
        transcript = []

        def ask(prompt):
            prompts.append(prompt)
            return next(commands)

        self.assertEqual(live_console.launch(
            self.root, self.id, ask=ask, say=transcript.append,
            codex_bin=str(self.binary), timeout=5,
        ), 0)
        self.assertIn("Run these exact checks now?", "\n".join(prompts))
        self.assertNotIn("--ux-artifact", "\n".join(prompts))
        # MVP 07 auto-resumes the next eligible specialist after the human
        # explicitly confirms host checks; G6 still requires human review.
        self.assertEqual(self.state()["status"], "WAITING_FOR_HUMAN")
        self.assertEqual(self.state()["pending_gate"], "G6_UX_INSPECTION")
        with agentflow.open_db(self.root, self.id) as db:
            self.assertEqual(live_evidence.validate(db, self.root, self.id)["checks"], 2)

    def test_full_g6_inspection_and_g7_proof_complete_only_after_human(self):
        self.through_verify()
        self.assertIn("PASS independent evidence", self.collect())
        self.assertEqual(self.state()["status"], "READY")
        self.assertTrue((self.work / "mvps/mvp-01/edd/verification-evidence.md").is_file())
        with agentflow.open_db(self.root, self.id) as db:
            bundle = live_evidence.validate(db, self.root, self.id)
        self.assertEqual(bundle["screenshots"], 1)
        self.assertEqual(bundle["checks"], 2)
        self.assertIn("work/host-evidence/live-evidence", bundle["receipt_path"].read_text())
        self.assertEqual(self.state()["stage_index"], 11)
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G6_UX_INSPECTION")
        runs = list((self.work / "codex-runs").glob("codex-*"))
        inspector = next(run for run in runs if json.loads((run / "run.json").read_text())["stage"] == "ux_inspect")
        inspector_prompt = (inspector / "specialist/prompt.txt").read_text()
        self.assertIn("verification-evidence.md", inspector_prompt)
        self.assertIn("Source fingerprint", inspector_prompt)
        with self.assertRaisesRegex(ValueError, "exact --bundle-sha"):
            state = self.state()
            live_coordinator.approve(self.root, self.id, "G6_UX_INSPECTION", state["pending_sha"])
        self.accept()
        self.assertIn("Host evidence bundle", (
            self.work / "mvps/mvp-01/ux/inspection-acceptance.md"
        ).read_text())
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G7_PROVE")
        self.assertEqual(self.state()["status"], "WAITING_FOR_HUMAN")
        runs = list((self.work / "codex-runs").glob("codex-*"))
        prover = next(run for run in runs if json.loads((run / "run.json").read_text())["stage"] == "prove")
        prove_prompt = (prover / "specialist/prompt.txt").read_text()
        self.assertIn("inspection-acceptance.md", prove_prompt)
        self.assertIn("Host evidence bundle SHA-256", prove_prompt)
        self.accept(notes="Reviewed functional exit receipts and the limitations of the captured UI state.")
        self.assertEqual(self.state()["status"], "COMPLETED")
        self.assertEqual(self.state()["stage_index"], 13)
        self.assertTrue((self.work / "mvps/mvp-01/edd/proof-acceptance.md").is_file())
        self.assertFalse((self.repo / "captures/mobile.png").exists())
        self.assertIn("G7_PROVE", live_coordinator.status(self.root, self.id))

    def test_failed_check_keeps_evidence_barrier_and_auditable_logs(self):
        self.through_verify()
        settings = self.args()
        settings["tests"] = [f"{shlex.quote(sys.executable)} -c 'raise SystemExit(4)'"]
        with self.assertRaisesRegex(ValueError, "TEST did not pass"):
            live_evidence.collect(self.root, self.id, **settings)
        self.assertEqual(self.state()["status"], "AWAITING_EVIDENCE")
        self.assertFalse((self.work / "mvps/mvp-01/edd/verification-evidence.md").exists())
        runs = list((self.work / "live-evidence").glob("evidence-*"))
        self.assertEqual(len(runs), 1)
        self.assertEqual(json.loads((runs[0] / "failure.json").read_text())["commands"][0]["returncode"], 4)
        self.assertIn("PASS independent evidence", self.collect())

    def test_missing_fresh_screenshot_fails_even_when_ux_process_exits_zero(self):
        self.through_verify()
        settings = self.args()
        settings["ux_checks"] = [f"{shlex.quote(sys.executable)} -c 'print(\"nothing captured\")'"]
        with self.assertRaisesRegex(ValueError, "missing or symlinked UX screenshot"):
            live_evidence.collect(self.root, self.id, **settings)
        self.assertEqual(self.state()["status"], "AWAITING_EVIDENCE")

    def test_ux_check_cannot_reuse_image_emitted_by_an_earlier_test(self):
        self.through_verify()
        settings = self.args()
        settings["tests"] = settings["ux_checks"]  # Only the functional command captures a PNG.
        settings["ux_checks"] = [f"{shlex.quote(sys.executable)} -c 'print(\"no new image\")'"]
        with self.assertRaisesRegex(ValueError, "missing or symlinked UX screenshot"):
            live_evidence.collect(self.root, self.id, **settings)
        self.assertEqual(self.state()["status"], "AWAITING_EVIDENCE")

    def test_tampered_screenshot_blocks_uiux_and_future_approval(self):
        self.through_verify()
        self.collect()
        with agentflow.open_db(self.root, self.id) as db:
            proof = live_evidence.validate(db, self.root, self.id)
        run_dir = proof["bundle_path"].parent
        image = next((run_dir / "images").iterdir())
        image.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "signature|image|invalid size"):
            self.progress()
        shown = live_coordinator.status(self.root, self.id)
        self.assertIn("EVIDENCE INVALID", shown)
        self.assertEqual(self.state()["stage_index"], 11)

    def test_g6_change_request_to_green_invalidates_old_source_evidence(self):
        self.through_verify()
        self.collect()
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G6_UX_INSPECTION")
        result = live_coordinator.changes(
            self.root, self.id, "Make the mobile action discoverable.", target_stage="green"
        )
        self.assertIn("green", result)
        self.assertEqual(self.state()["stage_index"], 8)
        with agentflow.open_db(self.root, self.id) as db:
            self.assertEqual(live_evidence.latest(db)["status"], "STALE")
        self.progress(mode="writer_change")
        self.assertEqual(self.state()["status"], "AWAITING_EVIDENCE")
        self.assertFalse((self.work / "mvps/mvp-01/ux/inspection-acceptance.md").exists())
        self.collect()
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G6_UX_INSPECTION")

    def test_tampered_human_ux_acceptance_blocks_proof_agent(self):
        self.through_verify()
        self.collect()
        self.progress()
        self.accept()
        acceptance = self.work / "mvps/mvp-01/ux/inspection-acceptance.md"
        acceptance.write_text("unauthorized edit after G6 approval")
        with self.assertRaisesRegex(ValueError, "approved human acceptance changed"):
            self.progress()
        self.assertFalse((self.work / "mvps/mvp-01/edd/prove.md").exists())

    def test_requesting_more_visual_evidence_reopens_barrier_without_code_rerun(self):
        self.through_verify()
        self.collect()
        with agentflow.open_db(self.root, self.id) as db:
            original = live_evidence.validate(db, self.root, self.id)["bundle_sha"]
        self.progress()
        live_coordinator.changes(
            self.root, self.id, "Capture a new responsive state.", target_stage="evidence"
        )
        self.assertEqual(self.state()["status"], "AWAITING_EVIDENCE")
        self.assertEqual(self.state()["stage_index"], 11)
        with agentflow.open_db(self.root, self.id) as db:
            self.assertEqual(live_evidence.latest(db)["status"], "STALE")
        self.collect()
        with agentflow.open_db(self.root, self.id) as db:
            self.assertNotEqual(live_evidence.validate(db, self.root, self.id)["bundle_sha"], original)
        self.progress()
        self.assertEqual(self.state()["pending_gate"], "G6_UX_INSPECTION")

    def test_failed_uiux_inspector_routes_to_green_without_fake_proof(self):
        self.through_verify()
        self.collect()
        result = self.progress(mode="ux_failed")
        self.assertIn("BLOCKED ux_inspect", "\n".join(result))
        self.assertEqual(self.state()["status"], "BLOCKED")
        self.assertFalse((self.work / "mvps/mvp-01/ux/inspection.md").exists())
        self.assertFalse((self.work / "mvps/mvp-01/edd/prove.md").exists())
        self.assertIn("green", live_coordinator.changes(
            self.root, self.id, "Repair visual regression.", target_stage="green"
        ))
        self.assertEqual(self.state()["stage_index"], 8)
        with agentflow.open_db(self.root, self.id) as db:
            self.assertEqual(live_evidence.latest(db)["status"], "STALE")

    def test_bad_ux_paths_and_missing_confirmation_rejected_without_execution(self):
        self.through_verify()
        with self.assertRaisesRegex(ValueError, "escape"):
            settings = self.args()
            settings["ux_artifacts"] = ["../escape.png"]
            live_evidence.collect(self.root, self.id, **settings)
        kit = Path(__file__).resolve().parents[1] / "workflow.py"
        result = subprocess.run([
            sys.executable, str(kit), "--root", str(self.root),
            "live-evidence", self.id, "--test", "true", "--ux-check", "true",
            "--ux-artifact", "captures/mobile.png",
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --confirm", result.stderr)
        self.assertEqual(self.state()["status"], "AWAITING_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
