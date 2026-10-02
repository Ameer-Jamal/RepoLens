import sys
import unittest
from unittest.mock import Mock, patch
from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import QApplication, QProgressBar

from ui.PRReviewTab import PRReviewTab
from ui.DiffFilesSidebar import DiffFilesSidebar
from ui.PRCommentCardWidget import PRCommentCardWidget
from ui.DiffStreamWidget import DiffStreamWidget
from ui.TypographyController import TypographyController
from ui.theme import apply_theme


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

    def test_comment_actions_run_in_background_and_do_not_reload_another_pr(self):
        runner = Mock()
        tab = PRReviewTab(self.config, runner)
        pr = {"id": "42"}
        repo = self.config.get_active_repository()
        tab._current_pr, tab._current_repo = pr, repo
        with patch.object(tab.comment_service, "reply_to_comment") as reply, patch.object(
            tab.comment_service, "resolve_comment"
        ) as resolve, patch.object(tab, "load_pull_request") as reload:
            tab._on_reply_submitted("101", "Fixed")
            tab._on_resolve_toggled("101", False)
            reply.assert_not_called()
            resolve.assert_not_called()
            calls = runner.run.call_args_list
            calls[0].args[0]()
            reply.assert_called_once_with(repo, "42", "101", "Fixed")
            calls[1].args[0]()
            resolve.assert_called_once_with(repo, "42", "101", unresolve=False)
            calls[0].kwargs["on_result"]({})
            reload.assert_called_once_with(pr, repo)
            tab._current_pr = {"id": "43"}
            calls[1].kwargs["on_result"]({})
            self.assertEqual(reload.call_count, 1)

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

    def test_inline_comments_on_opposite_sides_attach_to_their_own_lines(self):
        stream = DiffStreamWidget(self.typography)
        stream.set_diff_content(
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
            "@@ -1 +1 @@\n-old\n+new\n",
            {"app.py": [
                {"comment_id": "right", "line": 1, "side": "RIGHT", "body": "New line"},
                {"comment_id": "left", "line": 1, "side": "LEFT", "body": "Old line"},
            ]},
        )
        card = stream._file_cards["app.py"]
        self.assertEqual([widget.root_comment_id for widget in card._comment_widgets], ["left", "right"])
        self.assertEqual([browser._diff_lines[0].line_type for browser in card._text_browsers], ["del", "add"])

    def test_diff_changes_use_gutter_accents_and_fit_large_type(self):
        self.typography.set_font_size(21)
        stream = DiffStreamWidget(self.typography)
        stream.resize(1100, 650)
        stream.set_diff_content(
            "diff --git a/release.yml b/release.yml\n--- a/release.yml\n+++ b/release.yml\n"
            "@@ -1,3 +1,3 @@\n with:\n-  needs: prepare-release\n+  needs: [prepare-release, test-release]\n",
            {},
        )
        card = stream._file_cards["release.yml"]
        browser = card._text_browsers[0]
        rendered = card._render_lines_html(card.diff_file.hunks[0].lines, "", browser._diff_lexer, browser._diff_formatter)
        self.assertIn('class="line-add"', rendered)
        self.assertIn("background-color:#171b25", rendered)
        self.assertNotIn("#174331", rendered)
        self.assertNotIn("#51212c", rendered)
        self.assertNotIn("#272822", rendered)
        stream.show()
        QApplication.processEvents()
        self.assertLessEqual(card.height(), card.sizeHint().height() + 8)
        self.assertGreaterEqual(browser.height(), 3 * 21)
        stream.close()

    def test_diff_copy_viewed_and_display_filters(self):
        stream = DiffStreamWidget(self.typography)
        diff_text = ("diff --git a/app.py b/app.py\nindex abc..def 100644\n--- a/app.py\n+++ b/app.py\n"
                     "@@ -1,3 +1,3 @@\n-old = 1\n+old  = 1\n \n+meaningful = 2\n")
        stream.set_diff_content(diff_text, {})
        card = stream._file_cards["app.py"]
        card._copy_diff()
        self.assertEqual(QApplication.clipboard().text(), diff_text)
        card._copy_path()
        self.assertEqual(QApplication.clipboard().text(), "app.py")
        card.viewed_checkbox.setChecked(True)
        self.assertTrue(card.is_collapsed)
        self.assertIn("app.py", stream._viewed_paths)
        stream.set_ignore_whitespace(True)
        stream.set_hide_blank_lines(True)
        changed = [line for line in card.diff_file.hunks[0].lines if line.content.startswith("old")]
        self.assertEqual(len(changed), 2)
        self.assertTrue(all(id(line) in card._ignored_line_ids for line in changed))
        rendered = card._render_lines_html(card.diff_file.hunks[0].lines, "", card._get_lexer(), card._text_browsers[0]._diff_formatter)
        self.assertIn("meaningful", rendered)
        self.assertEqual(rendered.count("<pre "), 1)
        stream.set_diff_content(diff_text, {})
        self.assertTrue(stream._file_cards["app.py"].viewed_checkbox.isChecked())

    def test_ignore_whitespace_hides_only_whitespace_changes_in_live_view(self):
        stream = DiffStreamWidget(self.typography)
        diff_text = ("diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
                     "@@ -1,2 +1,3 @@\n-value=1\n+value = 1\n+   \n"
                     "-result=1\n+result=2\n")
        stream.set_diff_content(diff_text, {})
        card = stream._file_cards["app.py"]
        self.assertIn("value", card._text_browsers[0].toPlainText())
        stream.set_ignore_whitespace(True)
        visible = " ".join(browser.toPlainText() for browser in card._text_browsers)
        self.assertNotIn("value", visible)
        self.assertIn("result", visible)
        self.assertEqual(len(card._ignored_line_ids), 3)
        stream.set_ignore_whitespace(False)
        self.assertIn("value", card._text_browsers[0].toPlainText())

    def test_whitespace_only_hunk_has_explanation(self):
        stream = DiffStreamWidget(self.typography)
        stream.set_diff_content("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x=1\n+x = 1\n", {})
        card = stream._file_cards["x.py"]
        stream.set_ignore_whitespace(True)
        self.assertFalse(card._text_browsers[0].isVisible())
        self.assertFalse(card._filter_notice.isHidden())

    def test_approval_switches_to_unapprove_without_success_dialog(self):
        tab = PRReviewTab(self.config)
        tab._current_repo = self.config.get_active_repository()
        tab._current_pr = {"id": "42", "title": "Review", "state": "OPEN"}
        with patch("ui.PRReviewTab.QInputDialog.getText", return_value=("", True)), \
             patch("ui.PRReviewTab.QMessageBox.information") as info, \
             patch.object(tab, "_play_approval_chime"), \
             patch.object(tab.pr_service, "approve_pull_request", return_value={"approved": True}) as approve, \
             patch.object(tab.pr_service, "unapprove_pull_request", return_value={"approved": False}) as unapprove:
            tab._on_approve_clicked()
            self.assertIn("Unapprove", tab.approve_btn.text())
            approve.assert_called_once()
            info.assert_not_called()
            tab._on_approve_clicked()
            unapprove.assert_called_once()
            self.assertIn("Approve PR", tab.approve_btn.text())

    def test_pr_review_tab_creation(self):
        tab = PRReviewTab(self.config)
        self.assertIsNotNone(tab)
        self.assertIsNotNone(tab.sidebar)
        self.assertIsNotNone(tab.diff_stream)
        self.assertIsNotNone(tab.repo_combo)
        self.assertIsNotNone(tab.pr_combo)
        self.assertIsNotNone(tab.filter_combo)
        self.assertIsNotNone(tab.load_prs_btn)

    def test_pipeline_shortcut_emits_pr_repository_and_branch(self):
        tab = PRReviewTab(self.config)
        repo = {"provider": "bitbucket", "owner": "workspace", "slug": "repo"}
        tab._current_repo = repo
        tab._current_pr = {"source_branch": "feature/test"}
        received = []
        tab.pipelineRequested.connect(lambda chosen_repo, branch: received.append((chosen_repo, branch)))
        tab._request_pipeline()
        self.assertEqual(received, [(repo, "feature/test")])

    def test_classic_theme_restores_original_styles(self):
        from PyQt5.QtWidgets import QWidget
        widget = QWidget()
        original = "QWidget { background: #0f1117; border: 1px solid #3b82f6; }"
        widget.setStyleSheet(original)
        apply_theme(app, "Aurora")
        self.assertIn("#130f20", widget.styleSheet())
        apply_theme(app, "Classic")
        self.assertEqual(widget.styleSheet(), original)
        self.assertEqual(app.styleSheet(), "")
        apply_theme(app, "Midnight")

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
        with patch.object(tab, "_fetch_pr_review_data", return_value={"diff_text": "", "comments": {}, "ci_status": {}}):
            tab.load_pull_request(pr_data)
        self.assertEqual(tab._current_pr, pr_data)
        self.assertIn("123", tab.title_label.text())

    def test_review_loading_panel_replaced_on_success_and_error(self):
        class DeferredRunner:
            def __init__(self):
                self.calls = []

            def run(self, fn, *args, **kwargs):
                self.calls.append((fn, args, kwargs))

        runner = DeferredRunner()
        tab = PRReviewTab(self.config, task_runner=runner)
        pr = {"id": "42", "title": "Review", "state": "OPEN"}
        repo = self.config.get_active_repository()
        tab.load_pull_request(pr, repo)
        panel = tab.diff_stream._loading_panel
        self.assertIsNotNone(panel)
        progress = panel.findChild(QProgressBar)
        self.assertEqual((progress.minimum(), progress.maximum()), (0, 0))
        self.assertIn("42", panel.findChildren(type(tab.status_bar))[0].text())
        runner.calls[-1][2]["on_result"]({
            "diff_text": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
            "comments": {"threads": []}, "ci_status": {},
        })
        self.assertIsNone(tab.diff_stream._loading_panel)
        self.assertIn("a.py", tab.diff_stream._file_cards)

        tab.load_pull_request(pr, repo)
        with patch("ui.PRReviewTab.QMessageBox.warning"):
            runner.calls[-1][2]["on_error"](RuntimeError("network failed"))
        self.assertIsNone(tab.diff_stream._loading_panel)
        self.assertIn("network failed", tab.status_bar.text())

    def test_developer_filter_is_explicit_and_sent_to_service(self):
        class DeferredRunner:
            def __init__(self):
                self.calls = []

            def run(self, fn, *args, **kwargs):
                self.calls.append((fn, args, kwargs))

        runner = DeferredRunner()
        tab = PRReviewTab(self.config, task_runner=runner)
        tab.author_combo.setEditText("alice")
        self.assertEqual(runner.calls, [])
        tab.load_prs_btn.click()
        self.assertEqual(len(runner.calls), 1)
        with patch.object(tab.pr_service, "aggregate_pull_requests", return_value=([], {})) as list_prs:
            self.assertEqual(runner.calls[0][0](), ([], {}))
        self.assertEqual(list_prs.call_args.kwargs["developer"], "alice")
        runner.calls[0][2]["on_result"](([], {}))
        self.assertEqual(tab.developer_result_label.text(), "0 PRs by alice")
        tab.author_combo.setCurrentIndex(0)
        self.assertEqual(tab._selected_developer_filter(), "")
        self.assertEqual(len(runner.calls), 1)
        tab.author_combo.setCurrentIndex(1)
        self.assertEqual(tab._selected_developer_filter(), "Me")
        tab.load_prs_btn.click()
        with patch.object(tab.pr_service, "aggregate_pull_requests", return_value=([], {})) as list_prs:
            runner.calls[-1][0]()
        self.assertEqual(list_prs.call_args.kwargs["developer"], "Me")

    def test_all_repo_search_keeps_scope_and_waits_for_pr_selection(self):
        class MultiConfig(_MockConfig):
            def get_selected_repositories(self):
                return [
                    {"id": "1", "provider": "bitbucket", "owner": "workspace", "slug": "one"},
                    {"id": "2", "provider": "bitbucket", "owner": "workspace", "slug": "two"},
                ]

        tab = PRReviewTab(MultiConfig())
        self.assertTrue(tab._selected_repo().get("_all_repositories"))
        tab.author_combo.setCurrentIndex(1)
        records = [{"id": "12", "title": "Change", "state": "OPEN", "repo_id": "2",
                    "repo_label": "workspace/two", "provider": "bitbucket"}]
        with patch.object(tab.pr_service, "aggregate_pull_requests", return_value=(records, {})) as fetch:
            tab.load_prs_btn.click()
        self.assertEqual(len(fetch.call_args.args[0]), 2)
        self.assertEqual(fetch.call_args.kwargs["developer"], "Me")
        self.assertIsNone(tab._current_pr)
        self.assertIsNone(tab.pr_combo.currentData())
        self.assertIn("workspace/two", tab.pr_combo.itemText(1))
        with patch.object(tab, "_fetch_pr_review_data", return_value={"diff_text": "", "comments": {}, "ci_status": {}}):
            tab.pr_combo.setCurrentIndex(1)
        self.assertEqual(tab._current_repo["slug"], "two")
        self.assertTrue(tab._selected_repo().get("_all_repositories"))

    def test_load_more_uses_saved_cursor_and_appends_results(self):
        tab = PRReviewTab(self.config)
        first = {"id": "1", "title": "First", "repo_id": "r", "state": "OPEN"}
        second = {"id": "2", "title": "Second", "repo_id": "r", "state": "OPEN"}
        with patch.object(tab.pr_service, "aggregate_pull_requests", side_effect=[
            ([first], {"r": "next"}), ([second], {}),
        ]) as fetch:
            tab.load_repository_prs()
            self.assertTrue(tab.more_prs_btn.isVisible() or not tab.isVisible())
            tab.load_repository_prs(more=True)
        self.assertEqual(fetch.call_args.args[2], {"r": "next"})
        self.assertEqual([tab.pr_combo.itemData(i)["id"] for i in (1, 2)], ["1", "2"])
        self.assertFalse(tab.more_prs_btn.isVisible())

    def test_pr_review_tab_combos_and_sync(self):
        tab = PRReviewTab(self.config)
        prs = [
            {"id": "10", "title": "First PR", "state": "OPEN", "author": "alice"},
            {"id": "11", "title": "Second PR", "state": "MERGED", "author": "bob"},
        ]
        tab.sync_prs(prs)
        self.assertEqual(tab.pr_combo.count(), 3)
        self.assertIn("10", tab.pr_combo.itemText(1))
        self.assertIn("11", tab.pr_combo.itemText(2))

        # Test selecting a PR in combo
        tab._on_pr_combo_changed(2)
        self.assertEqual(tab._current_pr["id"], "11")

    def test_pr_review_tab_on_tab_activated(self):
        tab = PRReviewTab(self.config)
        prs = [{"id": "99", "title": "Active PR", "state": "OPEN", "author": "charlie"}]
        tab.on_tab_activated(prs=prs)
        self.assertEqual(tab.pr_combo.count(), 2)
        self.assertEqual(tab._current_pr["id"], "99")

    def test_service_threads_render_with_text_and_action_ids(self):
        tab = PRReviewTab(self.config)
        repo = self.config.get_active_repository()
        pr = {"id": "42", "title": "Review", "state": "OPEN", "link": "https://bitbucket.org/workspace/repo/pull-requests/42"}
        inline_thread = {
            "thread_id": 101, "file_path": "app.py", "line": 1, "resolved": True,
            "root_comment": {"id": 101, "author_display_name": "Jane", "body": "Fix this line", "created_at": "2026-09-22T10:00:00Z"},
            "replies": [{"id": 102, "author": "Sam", "body": "Will do", "created_at": "2026-09-22T11:00:00Z"}],
        }
        general_thread = {
            "thread_id": 201, "file_path": None, "resolved": False,
            "root_comment": {"id": 201, "author": "Alex", "body": "Overall review", "created_at": "2026-09-22T12:00:00Z"},
            "replies": [],
        }
        payload = {
            "diff_text": "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
            "comments": {"threads": [inline_thread, general_thread], "summary": {"total_threads": 2}},
            "ci_status": {"state": "NO_STATUSES", "statuses": []},
        }
        with patch.object(tab, "_fetch_pr_review_data", return_value=payload):
            tab.load_pull_request(pr, repo)

        inline_card = tab.diff_stream._file_cards["app.py"]._comment_widgets[0]
        self.assertEqual(inline_card.root_comment_id, "101")
        self.assertEqual(inline_card.body_label.text(), "Fix this line")
        self.assertTrue(inline_card._is_resolved)
        self.assertTrue(tab.web_btn.isEnabled())
        self.assertIn("2 review comment", tab.status_bar.text())
        cards = [tab.diff_stream.stream_layout.itemAt(i).widget()
                 for i in range(tab.diff_stream.stream_layout.count())]
        self.assertTrue(any(isinstance(card, PRCommentCardWidget) and
                            card.body_label.text() == "Overall review" for card in cards))

    def test_file_sidebar_size_hint_is_valid_when_rendered(self):
        sidebar = DiffFilesSidebar()
        sidebar.set_files([{"path": "app.py", "change_type": "M", "additions": 1, "deletions": 1}])
        sidebar.show()
        QApplication.processEvents()
        self.assertGreater(sidebar.list_widget.sizeHintForRow(0), 0)
        rect = sidebar.list_widget.visualItemRect(sidebar.list_widget.item(0))
        self.assertLess(rect.height(), 50)
        self.assertLess(rect.top(), 60)
        sidebar.close()

    def test_discovered_repositories_appear_in_review_selector(self):
        class DiscoveryConfig(_MockConfig):
            def get_selected_repositories(self):
                return [{"id": "1", "provider": "bitbucket", "owner": "workspace", "slug": "repo"}]

            def get_cached_discovered_repositories(self, provider, context_key):
                return ([
                    {"id": "1", "provider": "bitbucket", "owner": "workspace", "slug": "repo"},
                    {"id": "2", "provider": "bitbucket", "owner": "workspace", "slug": "other"},
                ], 0)

        with patch("ui.PRReviewTab.RepositoryProvider.validate_provider_config", return_value=(True, "", {})), \
             patch("ui.PRReviewTab.RepositoryProvider.discovery_context_key", return_value="workspace"):
            tab = PRReviewTab(DiscoveryConfig())
        self.assertEqual(tab.repo_combo.count(), 2)
        self.assertEqual(tab.repo_combo.itemData(1)["slug"], "other")

    def test_comment_visibility_typography_and_inline_post(self):
        tab = PRReviewTab(self.config)
        repo = self.config.get_active_repository()
        pr = {"id": "42", "title": "Review", "state": "OPEN", "provider": "bitbucket"}
        inline = {"thread_id": 7, "file_path": "app.py", "line": 1, "resolved": False,
                  "root_comment": {"id": 7, "body": "Inline note", "author": "Jane"}, "replies": []}
        general = {"thread_id": 8, "file_path": None, "resolved": False,
                   "root_comment": {"id": 8, "body": "General note", "author": "Sam"}, "replies": []}
        payload = {
            "diff_text": "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
            "comments": {"threads": [inline, general]}, "ci_status": {"state": "NO_STATUSES", "statuses": []},
        }
        with patch.object(tab, "_fetch_pr_review_data", return_value=payload):
            tab.load_pull_request(pr, repo)
        file_card = tab.diff_stream._file_cards["app.py"]
        inline_card = file_card._comment_widgets[0]
        general_card = next(widget for widget in tab.diff_stream._general_comment_widgets
                            if isinstance(widget, PRCommentCardWidget))

        tab.code_comments_btn.setChecked(False)
        tab.general_comments_btn.setChecked(False)
        self.assertTrue(inline_card.isHidden())
        self.assertTrue(general_card.isHidden())
        tab.code_comments_btn.setChecked(True)
        self.assertFalse(inline_card.isHidden())

        tab.typography.set_font_size(18)
        self.assertIn("font-size: 18px", inline_card.body_label.styleSheet())
        self.assertIn("font-size:18px", file_card._text_browsers[0].toHtml())

        with patch("ui.PRReviewTab.QInputDialog.getMultiLineText", return_value=("Please change this", True)), \
             patch.object(tab.comment_service, "add_comment", return_value={}) as add_comment, \
             patch.object(tab, "load_pull_request"):
            file_card._on_comment_link(QUrl("comment:LEFT:1"))
        self.assertEqual(add_comment.call_args.kwargs["file_path"], "app.py")
        self.assertEqual(add_comment.call_args.kwargs["line"], 1)
        self.assertEqual(add_comment.call_args.kwargs["side"], "LEFT")


if __name__ == "__main__":
    unittest.main()
