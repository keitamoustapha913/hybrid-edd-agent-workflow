"""MVP02 live Coordinator contracts, exercised with fake Codex subprocesses."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from prototype import agentflow, codex_runtime, live_coordinator, live_prototype
from test_codex_runtime import FAKE_CODEX
from test_git_adapter import init_repo


class LiveCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.id = "live-coordinator"
        agentflow.initialize(self.root, self.id)
        self.repo = init_repo(self.root, "selected-repository")
        agentflow.attach_repo(self.root, self.id, self.repo)
        self.binary = self.root / "fake_codex"
        self.binary.write_text(FAKE_CODEX)
        self.binary.chmod(0o700)
        self.work = self.root / "work" / self.id
        (self.work / "brief.md").write_text("# Project brief\n\nBuild an example.\n")

    def start(self):
        return live_coordinator.initialize(self.root, self.id)

    def progress(self, mode="valid"):
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": mode}):
            return live_coordinator.run(
                self.root, self.id, codex_bin=str(self.binary), timeout=5
            )

    def state(self):
        with agentflow.open_db(self.root, self.id) as conn:
            return dict(live_coordinator._state(conn))

    def accept(self, *, waive=False):
        state = self.state()
        bundle_sha = None
        observed = ""
        if state["pending_gate"] == "G2_PROTOTYPE" and not waive:
            review = live_prototype.describe(self.root, self.id)
            bundle_sha = review["bundle_sha"]
            observed = "I clicked the example button and observed the synthetic success state."
        return live_coordinator.approve(
            self.root, self.id, state["pending_gate"], state["pending_sha"],
            waive_preview=waive, reason="Accept experiment findings without a live preview." if waive else "",
            bundle_sha=bundle_sha, review_notes=observed,
        )

    def next_gate(self, expected, mode="valid"):
        self.progress(mode)
        self.assertEqual(self.state()["pending_gate"], expected)
        self.assertEqual(self.state()["status"], "WAITING_FOR_HUMAN")

    def through_g4(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        self.next_gate("G2_PROTOTYPE")
        self.accept()
        self.next_gate("G3_PRODUCT_SPEC")
        self.accept()
        self.next_gate("G4_ROADMAP")
        self.accept()

    def test_live_init_separate_from_simulation_and_tickets(self):
        result = self.start()
        self.assertIn("initialized", result)
        self.assertEqual(self.state()["stage_index"], 0)
        self.assertEqual(self.state()["status"], "READY")
        with agentflow.open_db(self.root, self.id) as conn:
            self.assertEqual(agentflow.current(conn)["status"], "NOT_STARTED")
        ticket = (self.work / "project-ticket.md").read_text()
        self.assertIn(live_coordinator.BEGIN, ticket)
        self.assertIn("Human notes", ticket)
        self.assertIn("SIMULATION", ticket.upper())

    def test_init_requires_attached_clean_repository_and_unstarted_simulation(self):
        (self.repo / "external.txt").write_text("dirty")
        with self.assertRaisesRegex(ValueError, "clean"):
            self.start()
        self.assertNotIn(live_coordinator.BEGIN, (self.work / "project-ticket.md").read_text())

    def test_discovery_adopted_from_handoff_and_g1_never_autoapproves(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.assertEqual((self.work / "discovery.md").read_text().splitlines()[0], "# Stage report")
        self.assertEqual(self.state()["stage_index"], 1)
        self.assertEqual(self.state()["active_run_id"], None)
        self.assertIn("G1_DISCOVERY", live_coordinator.status(self.root, self.id))
        with agentflow.open_db(self.root, self.id) as conn:
            self.assertEqual(agentflow.current(conn)["status"], "NOT_STARTED")
            events = conn.execute("SELECT kind FROM live_events ORDER BY id").fetchall()
            self.assertIn("STAGE_ADOPTED", [event[0] for event in events])
            run_id = conn.execute("SELECT run_id FROM live_artifacts").fetchone()[0]
        record = json.loads((self.work / "codex-runs" / run_id / "run.json").read_text())
        self.assertTrue(record["adopted_by_coordinator"])
        self.assertFalse(record["verified"])
        with self.assertRaisesRegex(ValueError, "stale"):
            live_coordinator.approve(self.root, self.id, "G1_DISCOVERY", "0" * 64)

    def test_approval_respects_mutated_artifact_and_companion_sha(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        (self.work / "discovery.md").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.accept()
        # G3 gate binds both artifacts separately.
        self.work.joinpath("discovery.md").write_text("# Stage report\nThe fake runner did not check real software.\n")
        self.accept()
        self.next_gate("G2_PROTOTYPE")
        self.accept()
        self.next_gate("G3_PRODUCT_SPEC")
        (self.work / "product-ux-spec.md").write_text("# Changed UIUX")
        with self.assertRaisesRegex(ValueError, "worktree|contract|companion"):
            self.accept()

    def test_g2_requires_exact_preview_bundle_and_manual_observation(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        self.next_gate("G2_PROTOTYPE")
        with self.assertRaisesRegex(ValueError, "requires --bundle-sha"):
            live_coordinator.approve(
                self.root, self.id, "G2_PROTOTYPE", self.state()["pending_sha"]
            )
        with self.assertRaisesRegex(ValueError, "real G2 preview exists"):
            self.accept(waive=True)
        self.assertFalse((self.work / "prototype-acceptance.md").exists())
        review = live_prototype.describe(self.root, self.id)
        with self.assertRaisesRegex(ValueError, "bundle SHA"):
            live_coordinator.approve(
                self.root, self.id, "G2_PROTOTYPE", self.state()["pending_sha"],
                bundle_sha="f" * 64, review_notes="I clicked the sample button.",
            )
        with self.assertRaisesRegex(ValueError, "human preview observations"):
            live_coordinator.approve(
                self.root, self.id, "G2_PROTOTYPE", self.state()["pending_sha"],
                bundle_sha=review["bundle_sha"],
            )
        self.accept()
        acceptance = (self.work / "prototype-acceptance.md").read_text()
        self.assertIn("iteration-001", acceptance)
        self.assertIn(review["bundle_sha"], acceptance)
        self.assertIn("Human observations", acceptance)
        self.next_gate("G3_PRODUCT_SPEC")
        self.assertTrue((self.work / "product-ux-spec.md").is_file())
        spec_runs = [
            run for run in (self.work / "codex-runs").iterdir()
            if json.loads((run / "run.json").read_text())["stage"] == "specification"
        ]
        self.assertEqual(len(spec_runs), 1)
        spec_prompt = (spec_runs[0] / "specialist/prompt.txt").read_text()
        self.assertIn("Human observations", spec_prompt)
        self.assertIn("clicked the example button", spec_prompt)
        with agentflow.open_db(self.root, self.id) as conn:
            self.assertEqual(conn.execute(
                "SELECT preview_status FROM live_approvals WHERE gate='G2_PROTOTYPE'"
            ).fetchone()[0], "REVIEWED")

    def test_live_preview_check_command_shows_actual_frozen_html_and_bundle(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        self.next_gate("G2_PROTOTYPE")
        info = live_prototype.describe(self.root, self.id)
        frozen = self.root / info["preview_path"]
        self.assertIn("Offline Codex prototype", frozen.read_text())
        kit = Path(__file__).resolve().parents[1] / "workflow.py"
        output = subprocess.run([
            sys.executable, str(kit), "--root", str(self.root),
            "live-preview", self.id, "--check-only",
        ], text=True, capture_output=True)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertIn("iteration-001", output.stdout)
        self.assertIn(info["bundle_sha"], output.stdout)
        self.assertIn("NOT_TESTED", output.stdout)
        self.assertIn("live-preview", live_coordinator.status(self.root, self.id))

    def test_tampered_live_preview_blocks_g2_even_with_correct_report_hash(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        self.next_gate("G2_PROTOTYPE")
        review = live_prototype.describe(self.root, self.id)
        (self.root / review["preview_path"]).write_text("tampered after Handoff")
        shown = live_coordinator.status(self.root, self.id)
        self.assertIn("Frozen G2 preview is invalid", shown)
        self.assertNotIn("Approve after review", shown)
        self.assertNotIn("--waive-preview", shown)
        with self.assertRaisesRegex(ValueError, "frozen G2"):
            self.accept()
        self.assertEqual(self.state()["status"], "WAITING_FOR_HUMAN")

    def test_tampering_approved_bundle_blocks_specification_dispatch(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        self.next_gate("G2_PROTOTYPE")
        review = live_prototype.describe(self.root, self.id)
        self.accept()
        (self.root / review["bundle_path"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "G2|bundle|changed"):
            self.progress()
        self.assertEqual(self.state()["stage_index"], 2)
        self.assertFalse((self.work / "product-spec.md").exists())

    def test_unsafe_codex_preview_blocks_before_adopting_any_prototype_files(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        result = self.progress("preview_external")
        self.assertIn("BLOCKED prototype: unsafe preview", "\n".join(result))
        self.assertEqual(self.state()["status"], "BLOCKED")
        self.assertFalse((self.work / "prototype-report.md").exists())
        self.assertFalse((self.work / "prototype-preview/index.html").exists())
        self.assertIn("Retry authorized", live_coordinator.retry(self.root, self.id))
        self.next_gate("G2_PROTOTYPE")

    def test_prototype_feedback_rerun_reopens_g2_and_preserves_ticket_notes(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        self.next_gate("G2_PROTOTYPE")
        original = live_prototype.describe(self.root, self.id)
        ticket = self.work / "project-ticket.md"
        ticket.write_text(ticket.read_text() + "\nReviewer notes: keep contrast high.\n")
        self.assertIn("rerun prototype", live_coordinator.changes(
            self.root, self.id, "Make the loading state clearly visible."
        ))
        self.assertEqual(self.state()["stage_index"], 1)
        feedback = self.work / self.state()["feedback_path"].removeprefix(f"work/{self.id}/")
        self.assertIn("loading state", feedback.read_text())
        self.next_gate("G2_PROTOTYPE")
        renewed = live_prototype.describe(self.root, self.id)
        self.assertEqual(original["iteration"], "iteration-001")
        self.assertEqual(renewed["iteration"], "iteration-002")
        self.assertNotEqual(original["preview_sha"], renewed["preview_sha"])
        self.assertNotEqual(original["bundle_sha"], renewed["bundle_sha"])
        self.assertIn("loading state", ticket.read_text())
        self.assertIn("Reviewer notes: keep contrast high.", ticket.read_text())
        self.assertEqual(len(list((self.work / "codex-runs").iterdir())), 3)
        self.assertEqual(self.state()["feedback_stage"], "prototype")

    def test_new_g2_bundle_is_bound_to_unchanged_prior_human_feedback(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        self.next_gate("G2_PROTOTYPE")
        live_coordinator.changes(
            self.root, self.id, "Use a more readable empty-state explanation."
        )
        feedback = self.root / self.state()["feedback_path"]
        self.next_gate("G2_PROTOTYPE")
        current = live_prototype.describe(self.root, self.id)
        bundle = json.loads((self.root / current["bundle_path"]).read_text())
        self.assertEqual(bundle["prior_feedback_sha"], live_prototype._digest(feedback.read_bytes()))
        feedback.write_text("tampered reviewer feedback")
        with self.assertRaisesRegex(ValueError, "prior human G2 feedback"):
            self.accept()
        self.assertEqual(self.state()["pending_gate"], "G2_PROTOTYPE")

    def test_scope_g5_then_ux_and_edd_until_independent_evidence_barrier(self):
        self.through_g4()
        # G5 is a gate-only stage: no Codex process is started here.
        count_before = len(list((self.work / "codex-runs").iterdir()))
        self.next_gate("G5_MVP_01")
        self.assertEqual(count_before, len(list((self.work / "codex-runs").iterdir())))
        self.assertIn("G5_MVP_01", (self.work / "project-ticket.md").read_text())
        self.accept()
        stages = self.progress("writer_change")
        self.assertIn("AWAITING_EVIDENCE", "\n".join(stages))
        self.assertEqual(self.state()["stage_index"], 11)
        self.assertEqual(self.state()["status"], "AWAITING_EVIDENCE")
        self.assertFalse((self.work / "mvps/mvp-01/ux/inspection.md").exists())
        self.assertFalse((self.work / "mvps/mvp-01/edd/prove.md").exists())
        self.assertTrue((self.repo / "red-test-example.txt").exists())
        self.assertTrue((self.repo / "green-feature-example.txt").exists())
        self.assertIn("UX INSPECT", (self.work / "mvps/mvp-01/ticket.md").read_text())
        with agentflow.open_db(self.root, self.id) as conn:
            self.assertEqual(agentflow.current(conn)["status"], "NOT_STARTED")

    def test_git_drift_stops_next_dispatch_without_rebaselining(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        (self.repo / "user-untracked.txt").write_text("changed after approval request")
        with self.assertRaisesRegex(ValueError, "outside the Coordinator"):
            self.accept()
        self.assertIn("WAITING_FOR_HUMAN", "\n".join(self.progress()))
        self.assertEqual(self.state()["pending_gate"], "G1_DISCOVERY")

    def test_codex_failure_blocks_and_retry_does_not_auto_run(self):
        self.start()
        messages = self.progress("nonzero")
        self.assertIn("BLOCKED", "\n".join(messages))
        self.assertEqual(self.state()["status"], "BLOCKED")
        self.assertFalse((self.work / "discovery.md").exists())
        self.assertIn("Retry authorized", live_coordinator.retry(self.root, self.id))
        self.assertEqual(self.state()["status"], "READY")
        self.next_gate("G1_DISCOVERY")

    def test_crash_reconcile_valid_terminal_run_reopens_g1(self):
        self.start()
        stage, run_id, plan, baseline = live_coordinator._reserve(
            self.root, self.id, codex_bin=str(self.binary), model=None
        )
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": "valid"}):
            record = codex_runtime.run_stage(
                plan, run_id=run_id, expected_fingerprint=baseline,
                allow_dirty=True, timeout=5,
            )
        self.assertEqual(record["state"], "CANDIDATE_VALIDATED")
        self.assertEqual(self.state()["status"], "RUNNING")
        result = live_coordinator.reconcile(self.root, self.id)
        self.assertIn("ADOPTED discovery", result)
        self.assertEqual(self.state()["pending_gate"], "G1_DISCOVERY")
        self.assertIn("No unresolved", live_coordinator.reconcile(self.root, self.id))

    def test_reconcile_mid_adoption_resumes_from_content_addressed_manifest(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        self.next_gate("G2_PROTOTYPE")
        self.accept()
        stage, run_id, plan, baseline = live_coordinator._reserve(
            self.root, self.id, codex_bin=str(self.binary), model=None
        )
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": "valid"}):
            result = codex_runtime.run_stage(
                plan, run_id=run_id, expected_fingerprint=baseline,
                allow_dirty=True, timeout=5,
            )
        self.assertEqual(result["state"], "CANDIDATE_VALIDATED")
        original = live_coordinator._atomic_write
        invoked = []

        def interrupted(path, data):
            original(path, data)
            invoked.append(str(path))
            raise OSError("simulated crash after first adopted artifact")

        with patch.object(live_coordinator, "_atomic_write", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                live_coordinator._finalize(self.root, self.id, run_id)
        self.assertEqual(len(invoked), 1)
        self.assertTrue((self.work / "codex-runs" / run_id / "adoption-manifest.json").is_file())
        self.assertEqual(self.state()["stage_index"], 2)
        # An actual process death leaves RUNNING; reconcile atomically finishes
        # the remaining files without overwriting unrelated user files.
        self.assertIn("ADOPTED specification", live_coordinator.reconcile(self.root, self.id))
        self.assertEqual(self.state()["pending_gate"], "G3_PRODUCT_SPEC")
        self.assertTrue((self.work / "product-spec.md").is_file())
        self.assertTrue((self.work / "product-ux-spec.md").is_file())

    def test_reconcile_after_g2_freeze_but_before_report_adoption_is_idempotent(self):
        self.start()
        self.next_gate("G1_DISCOVERY")
        self.accept()
        stage, run_id, plan, baseline = live_coordinator._reserve(
            self.root, self.id, codex_bin=str(self.binary), model=None
        )
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": "valid"}):
            result = codex_runtime.run_stage(
                plan, run_id=run_id, expected_fingerprint=baseline,
                allow_dirty=True, timeout=5,
            )
        self.assertEqual(result["state"], "CANDIDATE_VALIDATED")
        original = live_coordinator._atomic_write

        def interrupted(path, data):
            original(path, data)
            raise OSError("crash just after preview freeze and first report write")

        with patch.object(live_coordinator, "_atomic_write", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "crash just after preview freeze"):
                live_coordinator._finalize(self.root, self.id, run_id)
        frozen = self.work / "prototype-review-live/iteration-001/preview/index.html"
        self.assertTrue(frozen.is_file())
        with agentflow.open_db(self.root, self.id) as conn:
            self.assertIsNone(live_prototype.latest(conn))  # SQLite transaction rolled back
        self.assertIn("ADOPTED prototype", live_coordinator.reconcile(self.root, self.id))
        self.assertEqual(self.state()["pending_gate"], "G2_PROTOTYPE")
        self.assertEqual(live_prototype.describe(self.root, self.id)["iteration"], "iteration-001")

    def test_read_only_codex_mutation_blocks_and_never_adopts(self):
        self.start()
        result = self.progress("repo_touch")
        self.assertIn("BLOCKED", "\n".join(result))
        self.assertFalse((self.work / "discovery.md").exists())
        self.assertEqual(self.state()["status"], "BLOCKED")
        with self.assertRaisesRegex(ValueError, "outside the Coordinator"):
            live_coordinator.retry(self.root, self.id)

    def test_reject_stale_mvp_ux_approval_and_keep_g5_pending(self):
        self.through_g4()
        self.next_gate("G5_MVP_01")
        (self.work / "mvps/mvp-01/ux/spec.md").write_text("manually changed")
        with self.assertRaisesRegex(ValueError, "companion UI/UX scope hash changed"):
            self.accept()
        self.assertEqual(self.state()["pending_gate"], "G5_MVP_01")

    def test_g5_change_request_rolls_back_to_roadmap_and_revokes_old_g4(self):
        self.through_g4()
        self.next_gate("G5_MVP_01")
        self.assertIn("rerun mvp_design", live_coordinator.changes(
            self.root, self.id, "Add mobile keyboard interaction criteria to MVP scope."
        ))
        self.assertEqual(self.state()["stage_index"], 3)
        with agentflow.open_db(self.root, self.id) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM live_approvals "
                "WHERE gate='G4_ROADMAP' AND revoked_at IS NULL"
            ).fetchone()[0], 0)
        self.next_gate("G4_ROADMAP")
        self.assertIn("mobile keyboard", (self.work / "project-ticket.md").read_text())

    def test_uncertain_unfinished_run_never_replayed(self):
        self.start()
        stage, run_id, plan, baseline = live_coordinator._reserve(
            self.root, self.id, codex_bin=str(self.binary), model=None
        )
        self.assertIn("UNCERTAIN", live_coordinator.reconcile(self.root, self.id))
        self.assertEqual(self.state()["active_run_id"], run_id)
        self.assertIn("UNCERTAIN", "\n".join(self.progress()))
        with self.assertRaisesRegex(ValueError, "UNCERTAIN"):
            live_coordinator.retry(self.root, self.id)

    def test_reconcile_rejects_mutated_candidate_before_canonical_write(self):
        self.start()
        _, run_id, plan, baseline = live_coordinator._reserve(
            self.root, self.id, codex_bin=str(self.binary), model=None
        )
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": "valid"}):
            record = codex_runtime.run_stage(
                plan, run_id=run_id, expected_fingerprint=baseline,
                allow_dirty=True, timeout=5,
            )
        self.assertEqual(record["state"], "CANDIDATE_VALIDATED")
        candidate = self.work / "codex-runs" / run_id / "candidate-root/work" / self.id / "discovery.md"
        candidate.write_text("candidate tampered after Handoff")
        self.assertIn("UNCERTAIN", live_coordinator.reconcile(self.root, self.id))
        self.assertFalse((self.work / "discovery.md").exists())
        self.assertEqual(self.state()["status"], "UNCERTAIN")

    def test_cannot_overwrite_preexisting_user_or_sim_artifact(self):
        self.start()
        (self.work / "discovery.md").write_text("User owns this content")
        stages = self.progress()
        self.assertIn("UNCERTAIN", "\n".join(stages))
        self.assertEqual((self.work / "discovery.md").read_text(), "User owns this content")
        self.assertEqual(self.state()["status"], "UNCERTAIN")

    def test_two_processes_cannot_reserve_same_dispatch(self):
        self.start()
        live_coordinator._reserve(self.root, self.id, codex_bin=str(self.binary), model=None)
        with self.assertRaisesRegex(ValueError, "RUNNING"):
            live_coordinator._reserve(self.root, self.id, codex_bin=str(self.binary), model=None)

    def test_cli_live_status_and_explicit_authorization(self):
        self.start()
        kit = Path(__file__).resolve().parents[1] / "workflow.py"
        command = [
            sys.executable, str(kit), "--root", str(self.root),
            "live-run", self.id, "--codex-bin", str(self.binary),
        ]
        denied = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(denied.returncode, 2)
        self.assertIn("requires --confirm", denied.stderr)
        status = subprocess.run([
            sys.executable, str(kit), "--root", str(self.root), "live-status", self.id
        ], capture_output=True, text=True)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("LIVE CODEX COORDINATOR", status.stdout)


if __name__ == "__main__":
    unittest.main()
