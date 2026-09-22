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


if __name__ == "__main__":
    unittest.main()
