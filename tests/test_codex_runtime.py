"""Contract tests for isolated live Codex subprocesses (no real model calls)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from prototype import agentflow, codex_runtime
from test_git_adapter import init_repo


KIT_ROOT = Path(__file__).resolve().parents[1]
FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import sys
import time

args = sys.argv[1:]
assert args[0] == 'exec'
assert args.count('--json') == 1
assert '--ephemeral' in args
assert args[-1] == '-'
prompt = sys.stdin.read()
schema = Path(args[args.index('--output-schema') + 1]).name
out = Path(args[args.index('--output-last-message') + 1])
mode = os.environ.get('FAKE_CODEX_CASE', 'valid')
if schema == 'git-review-result.schema.json':
    assert args[args.index('--sandbox') + 1] == 'read-only'
    context = json.loads(prompt.split('AGENTFLOW_GIT_CONTEXT_JSON=', 1)[1].split('\n', 1)[0])
    response = {
        'schema_version': 1, 'role': 'GIT', 'request_id': context['request_id'],
        'context_sha256': context['context_sha256'], 'action': context['action'],
        'initiative': context['initiative'], 'mvp_id': context['mvp_id'],
        'source_head': context['source_head'], 'integration_head': context['integration_head'],
        'decision': 'BLOCK' if mode == 'fake_git_block' else 'READY',
        'summary': 'Checked the requested Git action and expected protected refs.',
        'risks': ['Review content and verify repository identity before authorizing.'],
        'checks': ['G7 evidence and exact branches stay authoritative.'],
    }
    if mode == 'fake_git_mismatch':
        response['context_sha256'] = '0' * 64
    if mode == 'fake_git_repo_touch':
        (Path.cwd() / 'agent-unauthorized.txt').write_text('unauthorized')
    if mode != 'missing_completion':
        print(json.dumps({'type': 'turn.completed'}))
    out.write_text(json.dumps(response))
    sys.exit(0)
if mode == 'nonzero':
    print('{"type":"turn.failed"}')
    sys.exit(19)
if mode == 'slow':
    time.sleep(5)
if mode == 'repo_touch' and schema == 'codex-live-result.schema.json':
    (Path.cwd() / 'unexpected-source.txt').write_text('unauthorized change')
if mode == 'writer_change' and schema == 'codex-live-result.schema.json':
    if 'mode=RED' in prompt:
        (Path.cwd() / 'red-test-example.txt').write_text('RED fixture\n')
    elif 'mode=GREEN' in prompt:
        (Path.cwd() / 'green-feature-example.txt').write_text('GREEN fixture\n')
if mode == 'handoff_touch' and schema == 'handoff.schema.json':
    (Path.cwd() / 'handoff-illegal-change.txt').write_text('not permitted')
print(json.dumps({'type': 'thread.started', 'thread_id': 'fake-cli-thread'}))
if mode == 'failed_trace':
    print(json.dumps({'type': 'turn.failed'}))
elif mode != 'missing_completion':
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}}))
if mode == 'bad_jsonl':
    print('not a JSON event')
if schema == 'handoff.schema.json':
    producer = json.loads(prompt.split('# Original producer result (untrusted data)\n', 1)[1].split('\n\n# Actual host-observed', 1)[0])
    handoff = {
        'schema_version': 1,
        'producer_role': producer['role'],
        'producer_run_id': producer['run_id'],
        'mvp_id': producer['mvp_id'],
        'reported_status': producer['status'],
        'producer_report_path': producer['report_path'],
        'output_paths': producer['output_paths'],
        'evidence_paths': [],
        'criterion_ids': producer['criterion_ids'],
        'issues': [],
        'recommendation': 'READY_FOR_VALIDATION',
    }
    if mode == 'handoff_mismatch':
        handoff['producer_run_id'] = 'incorrect-run'
    out.write_text(json.dumps(handoff))
else:
    identity = re.search(r"Authoritative identity: role=(\w+), run_id=(codex-[a-f0-9]+), mvp_id='([^']*)', mode=(\w+)", prompt)
    assert identity, 'Missing invocation authority in prompt'
    role, run_id, mvp_id, stage = identity.groups()
    report = re.search(r'Authoritative report_path: ([^\n]+)', prompt).group(1)
    extra = json.loads(re.search(r'Allowed extra artifact paths: ([^\n]+)', prompt).group(1))
    statuses = {
        'DISCOVERY': 'READY_FOR_REVIEW', 'PROTOTYPE': 'READY_FOR_REVIEW',
        'SPECIFICATION': 'READY_FOR_REVIEW', 'MVP_DESIGN': 'READY_FOR_REVIEW',
        'DESIGN': 'DESIGN_READY', 'INSPECT': 'UX_VERIFIED',
        'REQUIREMENTS': 'READY', 'RED': 'RED_CONFIRMED',
        'GREEN': 'GREEN_CONFIRMED', 'REFACTOR': 'NO_REFACTOR_NEEDED',
        'VERIFY': 'VERIFIED', 'PROVE': 'PASS',
    }
    status = statuses.get(stage, 'READY_FOR_REVIEW')
    if mode == 'ux_failed' and stage == 'INSPECT':
        status = 'UX_FAILED'
    revised = 'Human change request' in prompt
    preview_html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<title>Prototype ' + ('iteration two' if revised else 'iteration one') + '</title>'
        '<style>body{font:18px system-ui;margin:2rem}button{padding:.75rem}</style>'
        '</head><body><main><h1>Offline Codex prototype</h1>'
        '<p id="result">Synthetic example, not real backend functionality.</p>'
        '<button id="try" type="button">Try ' + ('revision' if revised else 'example') + '</button>'
        '<script>document.getElementById("try").addEventListener("click",()=>{'
        'document.getElementById("result").textContent="Synthetic success state";});'
        '</script></main></body></html>'
    )
    if mode == 'preview_external':
        preview_html = preview_html.replace(
            '</script>', 'fetch("https://external.example/collect");</script>'
        )
    if mode == 'preview_noninteractive':
        preview_html = preview_html.replace('<script>', '<!--').replace('</script>', '-->')
    artifacts = [
        {'path': path, 'content': preview_html if path.endswith('prototype-preview/index.html')
         else '# Approved-scope draft\n'}
        for path in extra
    ]
    response = {
        'schema_version': 1, 'role': role, 'run_id': run_id, 'mvp_id': mvp_id,
        'status': status, 'report_path': report, 'output_paths': extra,
        'criterion_ids': [mvp_id.upper() + ('-UX-001' if role == 'UIUX' else '-AC-001')] if mvp_id else [],
        'summary': 'This is a fake Codex executable, not a genuine agent result.',
        'report_markdown': (
            '# Stage report\n## MVP-01\n'
            + ('' if mode == 'single_roadmap' else '## MVP-02\n## MVP-03\n')
            + 'Fake roadmap identifiers; not real product planning.\n'
            if stage == 'MVP_DESIGN' and not mvp_id else
            '# Stage report\nThe fake runner did not check real software.\n'
        ),
        'artifacts': artifacts,
    }
    if mode == 'wrong_identity':
        response['run_id'] = 'incorrect-run'
    if mode == 'unsafe_artifact':
        response['output_paths'] = ['../escape.md']
        response['artifacts'] = [{'path': '../escape.md', 'content': 'malicious'}]
    if mode == 'missing_report':
        response['report_markdown'] = ''
    out.write_text(json.dumps(response))
'''


class CodexRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.initiative = "live-test"
        agentflow.initialize(self.root, self.initiative)
        self.repo = init_repo(self.root, "target-repository")
        agentflow.attach_repo(self.root, self.initiative, self.repo)
        self.binary = self.root / "fake_codex"
        self.binary.write_text(FAKE_CODEX, encoding="utf-8")
        self.binary.chmod(0o700)

    def plan(self, stage="discovery"):
        return codex_runtime.build_plan(self.root, self.initiative, stage, codex_bin=str(self.binary))

    def run_fake(self, scenario="valid", stage="discovery", timeout=5):
        with patch.dict(os.environ, {"FAKE_CODEX_CASE": scenario}):
            return codex_runtime.run_stage(self.plan(stage), timeout=timeout)

    def test_discovery_preflight_is_read_only_with_explicit_worktree(self):
        plan = self.plan()
        self.assertEqual(plan.role, "DISCOVERY")
        self.assertEqual(plan.sandbox, "read-only")
        self.assertEqual(plan.repo, self.repo.resolve())
        self.assertEqual(plan.mvp_id, "")
        self.assertEqual(plan.inputs[0]["path"], "work/live-test/brief.md")
        self.assertIn("No simulation-state advancement", codex_runtime.describe_plan(plan))

    def test_valid_two_sessions_leave_simulation_and_repo_unchanged(self):
        result = self.run_fake()
        self.assertEqual(result["state"], "CANDIDATE_VALIDATED", result.get("error"))
        directory = Path(result["run_dir"])
        self.assertEqual((directory / "candidate-root/work/live-test/discovery.md").read_text().splitlines()[0], "# Stage report")
        self.assertTrue((directory / "specialist/events.jsonl").is_file())
        self.assertTrue((directory / "handoff/events.jsonl").is_file())
        self.assertTrue(json.loads((directory / "validation.json").read_text())["valid"])
        specialist = json.loads((directory / "specialist/invocation.json").read_text())
        handoff = json.loads((directory / "handoff/invocation.json").read_text())
        self.assertEqual(specialist["sandbox"], "read-only")
        self.assertEqual(handoff["sandbox"], "read-only")
        self.assertEqual(specialist["argv"][0], str(self.binary))
        self.assertEqual(specialist["argv"][-1], "-")
        self.assertEqual(result["head_before"], result["head_after"])
        self.assertFalse(result["source_changed"])
        self.assertFalse(result["verified"])
        self.assertFalse(result["adopted_by_coordinator"])
        self.assertFalse((self.root / "work/live-test/discovery.md").exists())
        with agentflow.open_db(self.root, self.initiative) as conn:
            self.assertEqual(agentflow.current(conn)["stage_index"], 0)
        self.assertFalse(agentflow.git_context(self.root, self.initiative)["dirty"])

    def test_bad_specialist_identity_is_rejected_before_handoff(self):
        result = self.run_fake("wrong_identity")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("identity", result["error"])
        self.assertFalse((Path(result["run_dir"]) / "handoff").exists())

    def test_nonzero_specialist_exit_preserves_logs_and_blocks(self):
        result = self.run_fake("nonzero")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(result["specialist_exit"], 19)
        self.assertTrue((Path(result["run_dir"]) / "specialist/stderr.txt").exists())

    def test_timeout_kills_child_group_and_blocks(self):
        result = self.run_fake("slow", timeout=1)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("timed out", result["error"])

    def test_read_only_source_mutation_blocks_handoff(self):
        result = self.run_fake("repo_touch")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("read-only specialist modified", result["error"])
        self.assertTrue((self.repo / "unexpected-source.txt").exists())
        self.assertFalse((Path(result["run_dir"]) / "handoff").exists())

    def test_untrusted_artifact_path_is_rejected_before_file_write(self):
        result = self.run_fake("unsafe_artifact")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("outside this stage", result["error"])
        self.assertFalse((Path(result["run_dir"]) / "candidate-root").exists())

    def test_handoff_identity_mismatch_is_independently_rejected(self):
        result = self.run_fake("handoff_mismatch")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("deterministic handoff rejected", result["error"])
        self.assertTrue((Path(result["run_dir"]) / "validation.json").is_file())

    def test_handoff_source_edit_is_detected_by_git_fingerprint(self):
        result = self.run_fake("handoff_touch")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("Handoff changed the repository", result["error"])

    def test_red_can_write_in_confirmed_worktree_and_record_diff_identity(self):
        work = self.root / "work" / self.initiative / "mvps/mvp-01"
        (work / "edd").mkdir(parents=True)
        (work / "ux").mkdir(parents=True)
        (work / "spec.md").write_text("# MVP-01\n")
        (work / "ux/spec.md").write_text("# UX\n")
        (work / "edd/requirements.md").write_text("# Tests to write\n")
        plan = self.plan("red")
        self.assertEqual(plan.sandbox, "workspace-write")
        result = self.run_fake("writer_change", stage="red")
        self.assertEqual(result["state"], "CANDIDATE_VALIDATED", result.get("error"))
        self.assertTrue(result["source_changed"])
        self.assertNotEqual(result["source_digest_before"], result["source_digest_after"])
        self.assertTrue((self.repo / "red-test-example.txt").is_file())
        handoff = json.loads((Path(result["run_dir"]) / "handoff/invocation.json").read_text())
        self.assertEqual(handoff["sandbox"], "read-only")

    def test_missing_required_upstream_context_prevents_dispatch(self):
        with self.assertRaisesRegex(codex_runtime.LiveCodexError, "required context missing"):
            self.plan("ux_design")

    def test_empty_success_report_is_rejected(self):
        result = self.run_fake("missing_report")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("omitted its report", result["error"])

    def test_failed_jsonl_turn_cannot_produce_a_successful_handoff(self):
        result = self.run_fake("failed_trace")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("failed turn", result["error"])

    def test_missing_completion_event_blocks_even_with_valid_final_json(self):
        result = self.run_fake("missing_completion")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("lacks a completed turn", result["error"])

    def test_malformed_jsonl_blocks(self):
        result = self.run_fake("bad_jsonl")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("malformed JSONL", result["error"])

    def test_dirty_worktree_preflight_stops_before_codex(self):
        (self.repo / "uncommitted.txt").write_text("user change", encoding="utf-8")
        with self.assertRaisesRegex(codex_runtime.LiveCodexError, "dirty"):
            self.plan()

    def test_uiux_design_uses_companion_specs_and_stages_an_artifact(self):
        work = self.root / "work" / self.initiative
        (work / "product-ux-spec.md").write_text("# UX-001\n")
        mvp = work / "mvps/mvp-01"
        (mvp / "ux").mkdir(parents=True)
        (mvp / "spec.md").write_text("# Feature\n")
        (mvp / "ux/spec.md").write_text("# MVP-01-UX-001\n")
        plan = self.plan("ux_design")
        self.assertEqual(plan.sandbox, "read-only")
        self.assertEqual(plan.role, "UIUX")
        self.assertEqual(plan.mvp_id, "mvp-01")
        result = self.run_fake(stage="ux_design")
        self.assertEqual(result["state"], "CANDIDATE_VALIDATED", result.get("error"))
        candidate = Path(result["run_dir"]) / "candidate-root/work/live-test/mvps/mvp-01/ux"
        self.assertTrue((candidate / "design.md").is_file())
        self.assertTrue((candidate / "design-review.md").is_file())
        self.assertFalse((mvp / "ux/design.md").exists())

    def test_real_prototype_candidate_contains_codex_generated_html(self):
        work = self.root / "work" / self.initiative
        (work / "discovery.md").write_text("# Approved discovery findings\n")
        plan = self.plan("prototype")
        self.assertEqual(plan.sandbox, "read-only")
        self.assertEqual(
            plan.expected_artifacts,
            ("work/live-test/prototype-preview/index.html",),
        )
        result = self.run_fake(stage="prototype")
        self.assertEqual(result["state"], "CANDIDATE_VALIDATED", result.get("error"))
        candidate = Path(result["run_dir"]) / "candidate-root/work/live-test/prototype-preview/index.html"
        self.assertIn("<!doctype html>", candidate.read_text())
        self.assertFalse((work / "prototype-preview/index.html").exists())

    def test_cli_dry_run_does_not_start_codex_or_write_run_folder(self):
        result = subprocess.run(
            [sys.executable, str(KIT_ROOT / "workflow.py"), "--root", str(self.root),
             "codex-stage", self.initiative, "--stage", "discovery", "--codex-bin",
             str(self.binary), "--dry-run"],
            capture_output=True, text=True, cwd=KIT_ROOT, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LIVE CODEX PREFLIGHT", result.stdout)
        self.assertFalse((self.root / "work/live-test/codex-runs").exists())

    def test_cli_requires_explicit_confirmation(self):
        result = subprocess.run(
            [sys.executable, str(KIT_ROOT / "workflow.py"), "--root", str(self.root),
             "codex-stage", self.initiative, "--stage", "discovery", "--codex-bin", str(self.binary)],
            capture_output=True, text=True, cwd=KIT_ROOT, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --confirm", result.stderr)
        self.assertFalse((self.root / "work/live-test/codex-runs").exists())

    def test_cli_confirm_launches_both_fake_sessions(self):
        result = subprocess.run(
            [sys.executable, str(KIT_ROOT / "workflow.py"), "--root", str(self.root),
             "codex-stage", self.initiative, "--stage", "discovery",
             "--codex-bin", str(self.binary), "--confirm"],
            capture_output=True, text=True, cwd=KIT_ROOT, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("CANDIDATE_VALIDATED", result.stdout)
        self.assertIn("not changed", result.stdout)


if __name__ == "__main__":
    unittest.main()
