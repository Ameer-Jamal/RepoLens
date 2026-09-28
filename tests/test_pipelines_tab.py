import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication, QMessageBox

from ui.PipelinesTab import PipelinesTab
from ui.SearchableComboBox import SearchableComboBox


app = QApplication.instance() or QApplication(sys.argv)
REPO = {"provider": "github", "owner": "acme", "slug": "tests", "default_branch": "main"}
PIPELINE = {"id": "7", "name": "Tests", "kind": "workflow", "inputs": [
    {"name": "suite", "default": "smoke", "type": "choice", "options": ["smoke", "full"]}]}


class Config:
    def get_selected_repositories(self):
        return [REPO]

    def get_active_repository(self):
        return REPO


class InlineRunner:
    def run(self, fn, *args, **kwargs):
        callback = kwargs.pop("on_result", None)
        on_error = kwargs.pop("on_error", None)
        kwargs.pop("description", None)
        try:
            result = fn(*args, **kwargs)
            if callback:
                callback(result)
        except Exception as exc:
            if on_error:
                on_error(exc)
            else:
                raise


class DeferredRunner:
    def __init__(self):
        self.calls = []

    def run(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))


class PipelinesTabTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.list_branches.return_value = ["main", "feature/test"]
        self.service.list_pipelines.return_value = [PIPELINE]
        self.service.list_runs.return_value = [{"run_id": "99", "name": "Tests", "status": "queued",
                                                 "conclusion": "", "created_at": "today"}]
        self.service.run_pipeline.return_value = {"run_id": "99", "url": "https://github.com/run"}
        self.service.get_run.return_value = {"run_id": "99", "status": "completed", "conclusion": "success",
                                             "url": "https://github.com/run", "steps": [{"id": "5", "name": "test",
                                                                                             "status": "completed", "conclusion": "success"}]}
        self.service.get_log.return_value = {"text": "tests passed", "truncated": False}
        with patch("ui.PipelinesTab.PipelineService", return_value=self.service):
            self.tab = PipelinesTab(Config(), InlineRunner())

    def test_form_updates_and_dispatches_reviewed_values(self):
        self.assertEqual(self.tab.branch_combo.currentText(), "main")
        self.assertEqual(self.tab.parameters.item(0, 0).text(), "suite")
        self.tab.parameters.cellWidget(0, 1).setCurrentText("full")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.tab.run_pipeline()
        self.service.run_pipeline.assert_called_once_with(REPO, "main", "7", kind="workflow", inputs={"suite": "full"})
        self.assertIn("Run 99", self.tab.run_status.text())
        self.tab.steps_list.setCurrentRow(0)
        self.assertEqual(self.tab.log_text.toPlainText(), "tests passed")

    def test_pr_context_selects_repository_and_branch(self):
        self.tab.select_context(REPO, "feature/test")
        self.assertEqual(self.tab.repo_combo.currentData(), REPO)
        self.assertEqual(self.tab.branch_combo.currentText(), "feature/test")
        self.service.list_pipelines.assert_any_call(REPO, "feature/test")

    def test_declined_review_does_not_dispatch(self):
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            self.tab.run_pipeline()
        self.service.run_pipeline.assert_not_called()

    def test_searchable_controls_and_visible_error_recovery(self):
        self.assertIsInstance(self.tab.repo_combo, SearchableComboBox)
        self.assertIsInstance(self.tab.branch_combo, SearchableComboBox)
        self.assertIsInstance(self.tab.pipeline_combo, SearchableComboBox)
        self.tab.branch_combo._filter_items("feature")
        self.assertEqual(self.tab.branch_combo.search_list.count(), 1)
        self.tab._request += 1
        self.tab._set_status("Loading...", busy=True)
        self.tab._error(self.tab._request, RuntimeError("provider unavailable"))
        self.assertTrue(self.tab.progress.isHidden())
        self.assertFalse(self.tab.retry_btn.isHidden())
        self.assertIn("provider unavailable", self.tab.status.text())

    def test_empty_config_auto_discovers_repositories(self):
        class EmptyConfig:
            def get_selected_repositories(self):
                return []

            def get_active_repository(self):
                return {}

            def get_provider(self):
                return "github"

            def get_github_token(self):
                return "token"

            def get_github_owner(self):
                return ""

        with patch("ui.PipelinesTab.PipelineService", return_value=self.service), patch(
            "ui.PipelinesTab.RepositoryProvider.discover_repositories", return_value=[REPO]
        ) as discover:
            tab = PipelinesTab(EmptyConfig(), InlineRunner())
        self.assertEqual(tab.repo_combo.currentData(), REPO)
        discover.assert_called_once()

    def test_default_branch_pipelines_load_without_waiting_for_branch_list(self):
        runner = DeferredRunner()
        with patch("ui.PipelinesTab.PipelineService", return_value=self.service):
            tab = PipelinesTab(Config(), runner)
        self.assertEqual(tab.branch_combo.currentData(), "main")
        branch_call = next(call for call in runner.calls if call[0] is self.service.list_branches)
        pipeline_call = next(call for call in runner.calls if call[0] is self.service.list_pipelines)
        self.assertIn("Loading list", tab.branch_state.text())
        pipeline_call[2]["on_result"]([PIPELINE])
        self.assertEqual(tab.pipeline_combo.currentData()["id"], "7")
        branch_call[2]["on_result"](["main", "feature/test"])
        self.assertEqual(tab.branch_combo.count(), 2)
