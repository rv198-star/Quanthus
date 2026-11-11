import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "scripts/github_worktree_check.py").is_file()
)
SPEC = importlib.util.spec_from_file_location(
    "worktree_check", ROOT / "scripts/github_worktree_check.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WorktreeCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.test")
        self.git("remote", "add", "origin", str(self.root))
        (self.root / ".gitignore").write_text("evidence/\n")
        (self.root / "code.txt").write_text("code\n")
        self.git("add", ".")
        self.git("commit", "-m", "initial")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_ignored_receipt_is_reported_even_when_status_is_clean_and_is_preserved(self):
        evidence = self.root / "evidence/receipt.txt"
        evidence.parent.mkdir()
        evidence.write_text("unique evidence")
        self.assertEqual(self.git("status", "--short"), "")
        result = MODULE.inspect_worktree(self.root)
        self.assertFalse(result["file_and_commit_check_pass"])
        self.assertEqual(result["ignored_review_required"], ["evidence/"])
        self.assertEqual(evidence.read_text(), "unique evidence")

    def test_clean_integrated_worktree_still_requires_external_review(self):
        result = MODULE.inspect_worktree(self.root)
        self.assertTrue(result["file_and_commit_check_pass"])
        self.assertTrue(result["external_dependency_and_process_review_required"])

    def test_modified_untracked_and_unintegrated_commit_are_reported(self):
        (self.root / "code.txt").write_text("new code")
        self.git("commit", "-am", "unintegrated")
        self.git("branch", "--set-upstream-to=origin/main")
        (self.root / "code.txt").write_text("dirty code")
        (self.root / "notes.txt").write_text("untracked")
        result = MODULE.inspect_worktree(self.root)
        self.assertEqual(result["tracked_changes"], ["code.txt"])
        self.assertEqual(result["untracked"], ["notes.txt"])
        self.assertFalse(result["head_in_cached_origin_main"])
        self.assertEqual(result["commits_ahead_of_upstream"], 1)
        self.assertFalse(result["file_and_commit_check_pass"])

    def test_nested_directory_is_rejected(self):
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaises(ValueError):
            MODULE.inspect_worktree(nested)
