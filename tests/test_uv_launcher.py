"""Tests for the short, source-based uv launcher and guided setup."""

import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from prototype import agentflow


KIT_ROOT = Path(__file__).resolve().parents[1]


class GuidedLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.launched = []

    def guide(self, answers):
        responses = iter(answers)
        prompts = []

        def ask(prompt):
            prompts.append(prompt)
            return next(responses)

        def launch(root, initiative, module):
            self.launched.append((root, initiative, module))
            return 0

        with redirect_stdout(StringIO()):
            result = agentflow.guided_start(self.root, input_fn=ask, launch_fn=launch)
        return result, prompts

    def test_new_project_creates_local_parent_without_git_or_github(self):
        result, prompts = self.guide(["sample-project", ""])
        self.assertEqual(result, 0)
        self.assertEqual(self.launched[0][1], "sample-project")
        self.assertTrue((self.root / "work/sample-project/project-ticket.md").is_file())
        self.assertTrue((self.root / "work/sample-project/brief.md").is_file())
        self.assertIsNone(agentflow.git_context(self.root, "sample-project"))
        self.assertEqual(len(prompts), 2)

    def test_existing_project_is_resumed_without_reinitializing_brief(self):
        agentflow.initialize(self.root, "existing-project")
        brief = self.root / "work/existing-project/brief.md"
        brief.write_text("User-edited brief\n", encoding="utf-8")
        result, prompts = self.guide(["", ""])
        self.assertEqual(result, 0)
        self.assertIn("existing-project", prompts[0])
        self.assertEqual(self.launched[0][1], "existing-project")
        self.assertEqual(brief.read_text(encoding="utf-8"), "User-edited brief\n")

    def test_git_integration_creation_requires_explicit_yes(self):
        from test_git_adapter import init_repo

        git_repo = init_repo(self.root, "application")
        result, prompts = self.guide(["project-git", str(git_repo), "y"])
        self.assertEqual(result, 0)
        self.assertEqual(self.launched[0][1], "project-git")
        self.assertTrue(any("integration branch" in prompt for prompt in prompts))
        project = agentflow.project_context(self.root, "project-git")
        self.assertEqual(project["integration_branch"], "agentflow/project-git/integration")
        self.assertTrue((self.root / "work/project-git/project-ticket.md").is_file())

    def test_git_integration_is_not_created_when_declined(self):
        from test_git_adapter import init_repo

        git_repo = init_repo(self.root, "application")
        result, _ = self.guide(["project-git", str(git_repo), "n"])
        self.assertEqual(result, 0)
        self.assertIsNotNone(agentflow.git_context(self.root, "project-git"))
        self.assertIsNone(agentflow.project_context(self.root, "project-git"))

    def test_no_terminal_gives_guidance_not_a_traceback(self):
        completed = subprocess.run(
            [sys.executable, str(KIT_ROOT / "workflow.py")],
            cwd=KIT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("interactive terminal", completed.stderr)
        self.assertIn("uv run workflow.py --help", completed.stderr)

    def test_source_launcher_supports_help_without_installation(self):
        completed = subprocess.run(
            [sys.executable, str(KIT_ROOT / "workflow.py"), "--help"],
            cwd=KIT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("integration-create", completed.stdout)


if __name__ == "__main__":
    unittest.main()
