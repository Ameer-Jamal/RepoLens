import sys
import unittest
from PyQt5.QtWidgets import QApplication

from ui.PRReviewTab import PRReviewTab
from ui.DiffFilesSidebar import DiffFilesSidebar
from ui.PRCommentCardWidget import PRCommentCardWidget
from ui.DiffStreamWidget import DiffStreamWidget
from ui.TypographyController import TypographyController


app = QApplication.instance() or QApplication(sys.argv)


class _MockConfig:
    def get_provider(self):
        return "bitbucket"

    def get_active_repository(self):
        return {"provider": "bitbucket", "owner": "workspace", "slug": "repo"}

    def get_repo_dir(self):
        return "/tmp/fake-repo"

    def get_diff_font_size(self):
        return 13

    def set_diff_font_size(self, size):
        pass


class PRReviewTabTests(unittest.TestCase):
    def setUp(self):
        self.config = _MockConfig()
        self.typography = TypographyController(self.config)

    def test_pr_comment_card_widget(self):
        thread_data = {
            "comment_id": "101",
            "author": "john_doe",
            "author_display": "John Doe",
            "created_on": "2026-09-22T10:00:00Z",
            "body": "This looks like a potential null pointer exception.",
            "is_resolved": False,
            "replies": [
                {
                    "comment_id": "102",
                    "author": "jane_dev",
                    "author_display": "Jane Dev",
                    "created_on": "2026-09-22T10:15:00Z",
                    "body": "Good catch, fixing now.",
                }
            ],
        }
        card = PRCommentCardWidget(thread_data)
        self.assertFalse(card._is_resolved)
        self.assertEqual(card.root_comment_id, "101")
        card.update_resolution_state(True)
        self.assertTrue(card._is_resolved)

    def test_diff_files_sidebar(self):
        sidebar = DiffFilesSidebar()
        sidebar.set_files([
            {"path": "services/auth.py", "change_type": "M", "additions": 5, "deletions": 2, "comment_count": 1},
            {"path": "tests/test_auth.py", "change_type": "A", "additions": 20, "deletions": 0, "comment_count": 0},
        ])
        self.assertEqual(sidebar.list_widget.count(), 2)

    def test_diff_stream_widget(self):
        stream = DiffStreamWidget(self.typography)
        diff_text = """diff --git a/services/auth.py b/services/auth.py
index 1234567..89abcdef 100644
--- a/services/auth.py
+++ b/services/auth.py
@@ -10,3 +10,4 @@ def test():
     a = 1
-    b = 2
+    b = 3
     return a + b
"""
        comments = {
            "services/auth.py": [
                {
                    "comment_id": "99",
                    "line": 10,
                    "author": "reviewer",
                    "body": "Check variable initialization",
                }
            ]
        }
        summaries = stream.set_diff_content(diff_text, comments)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["path"], "services/auth.py")
        self.assertEqual(summaries[0]["comment_count"], 1)

    def test_pr_review_tab_creation(self):
        tab = PRReviewTab(self.config)
        self.assertIsNotNone(tab)
        self.assertIsNotNone(tab.sidebar)
        self.assertIsNotNone(tab.diff_stream)
        self.assertIsNotNone(tab.repo_combo)
        self.assertIsNotNone(tab.pr_combo)
        self.assertIsNotNone(tab.filter_combo)
        self.assertIsNotNone(tab.load_prs_btn)

    def test_pr_review_tab_with_task_runner(self):
        from ui.TaskRunner import TaskRunner
        task_runner = TaskRunner()
        tab = PRReviewTab(self.config, task_runner=task_runner)

        pr_data = {
            "id": "123",
            "title": "Fix auth race condition",
            "state": "OPEN",
            "source_branch": "fix/auth",
            "destination_branch": "main",
            "author_display": "Jane Dev",
            "repo_id": "1",
            "repo_label": "workspace/repo",
        }
        # Should not raise AttributeError: 'TaskRunner' object has no attribute 'start'
        tab.load_pull_request(pr_data)
        self.assertEqual(tab._current_pr, pr_data)
        self.assertIn("123", tab.title_label.text())

    def test_pr_review_tab_combos_and_sync(self):
        tab = PRReviewTab(self.config)
        prs = [
            {"id": "10", "title": "First PR", "state": "OPEN", "author": "alice"},
            {"id": "11", "title": "Second PR", "state": "MERGED", "author": "bob"},
        ]
        tab.sync_prs(prs)
        self.assertEqual(tab.pr_combo.count(), 2)
        self.assertIn("10", tab.pr_combo.itemText(0))
        self.assertIn("11", tab.pr_combo.itemText(1))

        # Test selecting a PR in combo
        tab._on_pr_combo_changed(1)
        self.assertEqual(tab._current_pr["id"], "11")

    def test_pr_review_tab_on_tab_activated(self):
        tab = PRReviewTab(self.config)
        prs = [{"id": "99", "title": "Active PR", "state": "OPEN", "author": "charlie"}]
        tab.on_tab_activated(prs=prs)
        self.assertEqual(tab.pr_combo.count(), 1)
        self.assertEqual(tab._current_pr["id"], "99")


if __name__ == "__main__":
    unittest.main()
