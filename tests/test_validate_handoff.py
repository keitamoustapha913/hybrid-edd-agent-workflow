import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_handoff import validate  # noqa: E402


class ValidateHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "work/demo/mvps/mvp-01/edd").mkdir(parents=True)
        self.report = "work/demo/mvps/mvp-01/edd/green.md"
        (self.root / self.report).write_text("Actual phase report", encoding="utf-8")
        self.producer = {
            "schema_version": 1, "role": "GREEN", "run_id": "run-123",
            "mvp_id": "mvp-01", "status": "GREEN_CONFIRMED",
            "report_path": self.report, "output_paths": [],
            "criterion_ids": ["MVP-01-AC-001"], "summary": "Tests passed"
        }
        self.handoff = {
            "schema_version": 1, "producer_role": "GREEN", "producer_run_id": "run-123",
            "mvp_id": "mvp-01", "reported_status": "GREEN_CONFIRMED",
            "producer_report_path": self.report, "output_paths": [], "evidence_paths": [],
            "criterion_ids": ["MVP-01-AC-001"], "issues": [],
            "recommendation": "READY_FOR_VALIDATION"
        }

    def check(self):
        return validate(self.root, self.producer, self.handoff, ["work/demo"])

    def test_valid_handoff(self):
        outcome = self.check()
        self.assertTrue(outcome["valid"], outcome["errors"])
        self.assertEqual(len(outcome["checked_files"][self.report]), 64)

    def test_invalid_role_status(self):
        self.producer["status"] = "PASS"
        self.handoff["reported_status"] = "PASS"
        self.assertFalse(self.check()["valid"])

    def test_wrong_run_id(self):
        self.handoff["producer_run_id"] = "another-run"
        self.assertFalse(self.check()["valid"])

    def test_missing_file(self):
        self.handoff["producer_report_path"] = "work/demo/mvps/mvp-01/edd/missing.md"
        self.handoff["issues"] = [{"code": "PATH_REMAP", "severity": "WARNING", "detail": "Different path"}]
        self.assertFalse(self.check()["valid"])

    def test_parent_traversal(self):
        self.handoff["evidence_paths"] = ["work/demo/../../outside.txt"]
        self.assertFalse(self.check()["valid"])

    def test_symlink_escape(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, dir=Path(self.temp.name).parent) as handle:
            handle.write("private")
            outside = Path(handle.name)
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        (self.root / "work/demo/symlink.txt").symlink_to(outside)
        self.handoff["evidence_paths"] = ["work/demo/symlink.txt"]
        self.assertFalse(self.check()["valid"])

    def test_blocking_issue(self):
        self.handoff["issues"] = [{"code": "MISSING_EVIDENCE", "severity": "ERROR", "detail": "No test log"}]
        self.assertFalse(self.check()["valid"])

    def test_requires_explicit_path_remap(self):
        alternate = "work/demo/mvps/mvp-01/edd/alternate.md"
        (self.root / alternate).write_text("Observed alternate", encoding="utf-8")
        self.handoff["producer_report_path"] = alternate
        self.assertFalse(self.check()["valid"])
        self.handoff["issues"] = [{"code": "PATH_REMAP", "severity": "WARNING", "detail": "Alternate observed"}]
        self.assertTrue(self.check()["valid"])

    def test_unknown_json_key(self):
        self.handoff["made_up"] = True
        self.assertFalse(self.check()["valid"])

    def test_criterion_mismatch(self):
        self.handoff["criterion_ids"] = ["MVP-01-AC-002"]
        self.assertFalse(self.check()["valid"])

    def test_uiux_mode_is_bound_to_report_not_just_role_text(self):
        directory = self.root / "work/demo/mvps/mvp-01/ux"
        directory.mkdir()
        report = "work/demo/mvps/mvp-01/ux/design-review.md"
        (self.root / report).write_text("UI design artifacts")
        self.producer.update(role="UIUX", status="DESIGN_READY", report_path=report,
                             criterion_ids=["MVP-01-UX-001"])
        self.handoff.update(producer_role="UIUX", reported_status="DESIGN_READY",
                            producer_report_path=report, criterion_ids=["MVP-01-UX-001"])
        self.assertTrue(self.check()["valid"])
        self.producer["status"] = "UX_VERIFIED"
        self.handoff["reported_status"] = "UX_VERIFIED"
        self.assertIn("mode-specific", " ".join(self.check()["errors"]))
        inspection = "work/demo/mvps/mvp-01/ux/inspection.md"
        (self.root / inspection).write_text("UX inspection observations")
        self.producer["report_path"] = inspection
        self.handoff["producer_report_path"] = inspection
        self.assertTrue(self.check()["valid"])


if __name__ == "__main__":
    unittest.main()
