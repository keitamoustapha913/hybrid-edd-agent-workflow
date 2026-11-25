"""Acceptance tests for versioned, simulated G2 preview / feedback / renewal."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prototype"))

import agentflow  # noqa: E402
import dashboard  # noqa: E402
import prototype_review  # noqa: E402


class G2ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identifier = "preview-orders"
        agentflow.initialize(self.root, self.identifier)

    def _start_g2(self):
        agentflow.advance(self.root, self.identifier)
        self._approve_pending("G1_DISCOVERY")
        view = dashboard.load_view(self.root, self.identifier, agentflow)
        self.assertEqual(view.pending_gate, "G2_PROTOTYPE")
        return view

    def _approve_pending(self, expected):
        with agentflow.open_db(self.root, self.identifier) as conn:
            row = agentflow.current(conn)
            self.assertEqual(row["pending_gate"], expected)
            return agentflow.approve(self.root, self.identifier, expected, row["pending_sha"])

    def _review_dir(self, number):
        return self.root / "work" / self.identifier / "prototype-review" / f"iteration-{number:03d}"

    def test_first_preview_has_local_html_manifest_and_review_contract(self):
        view = self._start_g2()
        self.assertEqual(view.prototype["iteration_id"], "iteration-001")
        self.assertEqual(view.prototype["integrity"], "UNCHANGED")
        self.assertTrue(view.artifact_integrity.startswith("UNCHANGED"))
        directory = self._review_dir(1)
        preview = directory / "preview/index.html"
        self.assertIn("Export visible CSV", preview.read_text(encoding="utf-8"))
        self.assertIn("SIMULATED EXPERIMENT", preview.read_text(encoding="utf-8"))
        self.assertIn("NOT_EXECUTED", (directory / "checks.json").read_text(encoding="utf-8"))
        manifest = json.loads((directory / "preview-manifest.json").read_text())
        self.assertEqual(manifest["preview_sha256"], agentflow.sha256(preview))
        parent = (self.root / "work" / self.identifier / "project-ticket.md").read_text()
        self.assertIn("## Prototype G2 iterations", parent)
        self.assertIn("iteration-001", parent)

    def test_browser_preview_opens_only_explicit_local_file(self):
        view = self._start_g2()
        urls = []
        message = dashboard.open_preview(self.root, view, opener=lambda url: urls.append(url) or True)
        self.assertIn("Opened local preview", message)
        self.assertEqual(len(urls), 1)
        self.assertEqual(urls[0], (self._review_dir(1) / "preview/index.html").as_uri())

    def test_feedback_rerun_generates_new_preview_and_renewed_g2(self):
        old = self._start_g2()
        prior_sha = old.pending_sha
        response = dashboard.perform_action(
            self.root, self.identifier, agentflow, "changes", "Move export to the left of search.",
        )
        self.assertIn("Changes requested", response)
        current = dashboard.load_view(self.root, self.identifier, agentflow)
        self.assertEqual(current.pending_gate, "G2_PROTOTYPE")
        self.assertEqual(current.prototype["iteration_id"], "iteration-002")
        self.assertNotEqual(prior_sha, current.pending_sha)
        self.assertEqual(current.prototype["integrity"], "UNCHANGED")
        self.assertEqual(current.prototype["feedback_id"], "FB-ITERATION-001")
        self.assertIn("Move export", current.prototype["feedback_text"])
        self.assertIn("Move export", (self._review_dir(2) / "preview/index.html").read_text())
        first_review = json.loads((self._review_dir(1) / "review.json").read_text())
        self.assertEqual(first_review["status"], "CHANGES_REQUESTED")
        feedback = json.loads((self._review_dir(1) / "feedback.json").read_text())
        self.assertEqual(feedback["type"], "human_change_request")
        with self.assertRaisesRegex(ValueError, "stale"):
            agentflow.approve(self.root, self.identifier, "G2_PROTOTYPE", prior_sha)

    def test_feedback_is_escaped_in_new_local_html(self):
        self._start_g2()
        value = '<img src=x onerror="alert(1)"> & review'
        agentflow.request_changes(self.root, self.identifier, value)
        agentflow.advance(self.root, self.identifier)
        content = (self._review_dir(2) / "preview/index.html").read_text()
        self.assertNotIn(value, content)
        self.assertIn("&lt;img", content)
        self.assertIn("&amp; review", content)

    def test_tampered_preview_and_evidence_block_g2_but_allow_rerun(self):
        old = self._start_g2()
        preview = self._review_dir(1) / "preview/index.html"
        preview.write_text(preview.read_text() + "\n<!-- tampered -->\n")
        view = dashboard.load_view(self.root, self.identifier, agentflow)
        self.assertEqual(view.prototype["integrity"], "INVALID")
        self.assertIn("CHANGED PREVIEW", view.artifact_integrity)
        with self.assertRaisesRegex(ValueError, "stale prototype preview"):
            agentflow.approve(self.root, self.identifier, "G2_PROTOTYPE", old.pending_sha)
        with self.assertRaisesRegex(ValueError, "integrity"):
            dashboard.open_preview(self.root, view, opener=lambda _: True)
        dashboard.perform_action(self.root, self.identifier, agentflow, "changes", "Regenerate trusted preview")
        self.assertEqual(dashboard.load_view(self.root, self.identifier, agentflow).prototype["integrity"], "UNCHANGED")

    def test_changed_previous_feedback_invalidates_renewed_g2(self):
        self._start_g2()
        dashboard.perform_action(self.root, self.identifier, agentflow, "changes", "Clarify confirmation")
        feedback = self._review_dir(1) / "feedback.json"
        feedback.write_text(feedback.read_text() + "\nAltered")
        view = dashboard.load_view(self.root, self.identifier, agentflow)
        self.assertEqual(view.prototype["integrity"], "INVALID")
        with self.assertRaisesRegex(ValueError, "stale prototype preview"):
            self._approve_pending("G2_PROTOTYPE")

    def test_symlinked_preview_is_not_opened_or_approved(self):
        self._start_g2()
        preview = self._review_dir(1) / "preview/index.html"
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "fake.html"
            target.write_text("fake")
            preview.unlink()
            preview.symlink_to(target)
            view = dashboard.load_view(self.root, self.identifier, agentflow)
            self.assertEqual(view.prototype["integrity"], "INVALID")
            with self.assertRaises(ValueError):
                dashboard.open_preview(self.root, view, opener=lambda _: True)
            with self.assertRaises(ValueError):
                self._approve_pending("G2_PROTOTYPE")

    def test_approval_accepts_exact_bundle_then_specification_uses_it(self):
        self._start_g2()
        self._approve_pending("G2_PROTOTYPE")
        view = dashboard.load_view(self.root, self.identifier, agentflow)
        self.assertEqual(view.pending_gate, "G3_PRODUCT_SPEC")
        acceptance = self.root / "work" / self.identifier / "prototype-acceptance.md"
        self.assertIn("REVIEW BEFORE REUSE", acceptance.read_text())
        self.assertIn("iteration-001", acceptance.read_text())
        specification = (self.root / "work" / self.identifier / "product-spec.md").read_text()
        self.assertIn("prototype-acceptance.md", specification)
        self.assertIn("Carry forward *reviewed findings*", specification)
        decision = json.loads((self._review_dir(1) / "approval.json").read_text())
        self.assertEqual(decision["decision"], "APPROVED")
        self.assertEqual(view.prototype["status"], "APPROVED")
        ticket = (self.root / "work" / self.identifier / "project-ticket.md").read_text()
        self.assertIn("G2 accepted prototype findings", ticket)
        self.assertIn("APPROVED", ticket)

    def test_view_and_resume_do_not_create_duplicate_iteration(self):
        self._start_g2()
        before = list((self._review_dir(1).parent).glob("iteration-*"))
        for _ in range(4):
            dashboard.load_view(self.root, self.identifier, agentflow)
            agentflow.advance(self.root, self.identifier)
        self.assertEqual(before, list((self._review_dir(1).parent).glob("iteration-*")))
        with agentflow.open_db(self.root, self.identifier) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM prototype_iterations").fetchone()[0], 1)

    def test_invalid_prototype_handoff_does_not_create_preview(self):
        agentflow.advance(self.root, self.identifier)
        self._approve_pending("G1_DISCOVERY")
        # Explicitly return to Prototype and run its invalid handoff to prove ordering.
        with agentflow.open_db(self.root, self.identifier) as conn:
            conn.execute("UPDATE workflow SET status='RUNNING', stage_index=1, "
                         "pending_gate=NULL, pending_path=NULL, pending_sha=NULL WHERE singleton=1")
        result = agentflow.advance(self.root, self.identifier, invalid_handoff=True)
        self.assertIn("BLOCKED", "\n".join(result))
        # A previous valid iteration remains; invalid handoff cannot create the next preview.
        self.assertFalse(self._review_dir(2).exists())

    def test_snapshot_shows_iteration_and_actions(self):
        self._start_g2()
        full = dashboard.snapshot_text(self.root, self.identifier, agentflow, width=110, height=40)
        self.assertIn("PROTOTYPE / G2 REVIEW", full)
        self.assertIn("iteration-001", full)
        self.assertIn("[O] Preview", full)


if __name__ == "__main__":
    unittest.main()
