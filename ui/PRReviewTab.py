from __future__ import annotations

import os
import webbrowser
from typing import Any, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QMessageBox,
    QComboBox,
    QInputDialog,
    QFrame,
    QDialog,
    QListWidget,
    QListWidgetItem,
)

from services.pull_request_service import PullRequestService
from services.pull_request_comment_service import PullRequestCommentService
from services.diff_service import DiffService
from ui.DiffFilesSidebar import DiffFilesSidebar
from ui.DiffStreamWidget import DiffStreamWidget
from ui.TypographyController import TypographyController


class CIStatusDialog(QDialog):
    """Pop-up modal displaying individual CI/CD checks and commit statuses."""

    def __init__(self, ci_data: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("CI / Build Status Details")
        self.resize(550, 400)
        self.setStyleSheet("background-color: #161922; color: #f1f5f9;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        state = ci_data.get("state", "UNKNOWN")
        total = ci_data.get("total_count", 0)
        succ = ci_data.get("successful_count", 0)
        fail = ci_data.get("failed_count", 0)

        summary_lbl = QLabel(f"Overall State: {state}  ({succ}/{total} passed, {fail} failed)", self)
        summary_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #38bdf8;")
        layout.addWidget(summary_lbl)

        list_w = QListWidget(self)
        list_w.setStyleSheet(
            "QListWidget { background-color: #111319; border: 1px solid #222734; border-radius: 6px; padding: 6px; }"
            "QListWidget::item { padding: 8px; border-bottom: 1px solid #1e2433; }"
        )

        for s in ci_data.get("statuses", []):
            st_name = s.get("name") or "Check"
            st_state = s.get("state") or "UNKNOWN"
            st_desc = s.get("description") or ""
            icon = "✓" if st_state == "SUCCESSFUL" else ("✗" if st_state == "FAILED" else "⏳")
            item = QListWidgetItem(f"{icon}  {st_name} [{st_state}]\n    {st_desc}")
            if st_state == "SUCCESSFUL":
                item.setForeground(Qt.green)
            elif st_state == "FAILED":
                item.setForeground(Qt.red)
            else:
                item.setForeground(Qt.yellow)
            list_w.addItem(item)

        layout.addWidget(list_w)

        close_btn = QPushButton("Close", self)
        close_btn.setStyleSheet("background-color: #262b37; color: white; border: none; border-radius: 4px; padding: 6px 12px;")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)


class PRReviewTab(QWidget):
    """Dedicated pull request review tab with syntax-highlighted diffs, typography controls, and comment threads."""

    def __init__(self, config_manager, task_runner=None, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.task_runner = task_runner
        self.pr_service = PullRequestService(self.config_manager)
        self.comment_service = PullRequestCommentService(self.config_manager, pr_service=self.pr_service)
        self.diff_service = DiffService()

        self.typography = TypographyController(self.config_manager, self)

        self._current_pr: Optional[dict[str, Any]] = None
        self._current_repo: Optional[dict[str, Any]] = None
        self._current_ci_status: Optional[dict[str, Any]] = None

        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # -------------------------------------------------------------
        # Top Header Bar: Metadata, Badges, Typography Zoom & Actions
        # -------------------------------------------------------------
        top_card = QFrame(self)
        top_card.setStyleSheet(
            "QFrame {"
            "  background-color: #161821;"
            "  border: 1px solid #222734;"
            "  border-radius: 8px;"
            "  padding: 8px 12px;"
            "}"
        )
        top_layout = QVBoxLayout(top_card)
        top_layout.setContentsMargins(6, 6, 6, 6)
        top_layout.setSpacing(6)

        # Row 1: PR Title, Status Badge, Branches, Author
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        self.title_label = QLabel("Select a pull request to review", self)
        self.title_label.setStyleSheet("color: #f8fafc; font-size: 15px; font-weight: 700;")
        row1.addWidget(self.title_label)

        self.state_badge = QLabel("", self)
        self.state_badge.setVisible(False)
        row1.addWidget(self.state_badge)

        self.branch_pill = QLabel("", self)
        self.branch_pill.setStyleSheet(
            "background-color: #1e2433; color: #93c5fd; font-family: monospace; font-size: 11px; border-radius: 4px; padding: 2px 8px;"
        )
        self.branch_pill.setVisible(False)
        row1.addWidget(self.branch_pill)

        self.author_lbl = QLabel("", self)
        self.author_lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")
        self.author_lbl.setVisible(False)
        row1.addWidget(self.author_lbl)

        row1.addStretch()

        # Web Link Button
        self.web_btn = QPushButton("↗ View on Web", self)
        self.web_btn.setStyleSheet(
            "QPushButton { background-color: #1e2433; color: #60a5fa; border: 1px solid #2e384d; border-radius: 5px; padding: 4px 10px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background-color: #273043; color: #93c5fd; }"
        )
        self.web_btn.clicked.connect(self._open_in_web)
        self.web_btn.setEnabled(False)
        row1.addWidget(self.web_btn)

        top_layout.addLayout(row1)

        # Row 2: CI Status Pill, Typography Controls, Actions
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        # CI Status Badge
        self.ci_badge = QPushButton("CI Status: Not loaded", self)
        self.ci_badge.setStyleSheet(
            "QPushButton { background-color: #1e2433; color: #94a3b8; border: 1px solid #2e384d; border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background-color: #273043; }"
        )
        self.ci_badge.clicked.connect(self._show_ci_dialog)
        self.ci_badge.setEnabled(False)
        row2.addWidget(self.ci_badge)

        row2.addStretch()

        # Typography Zoom Controls
        row2.addWidget(QLabel("Font:", self))
        row2.addWidget(self.typography.create_toolbar_widget(self))

        # Collapse / Expand All
        self.collapse_all_btn = QPushButton("Collapse All", self)
        self.collapse_all_btn.setStyleSheet(
            "QPushButton { background-color: #1e2433; color: #94a3b8; border: 1px solid #2e384d; border-radius: 5px; padding: 4px 8px; font-size: 11px; }"
            "QPushButton:hover { background-color: #273043; color: #f1f5f9; }"
        )
        self.collapse_all_btn.clicked.connect(lambda: self.diff_stream.collapse_all())
        row2.addWidget(self.collapse_all_btn)

        self.expand_all_btn = QPushButton("Expand All", self)
        self.expand_all_btn.setStyleSheet(
            "QPushButton { background-color: #1e2433; color: #94a3b8; border: 1px solid #2e384d; border-radius: 5px; padding: 4px 8px; font-size: 11px; }"
            "QPushButton:hover { background-color: #273043; color: #f1f5f9; }"
        )
        self.expand_all_btn.clicked.connect(lambda: self.diff_stream.expand_all())
        row2.addWidget(self.expand_all_btn)

        # Approve PR Button
        self.approve_btn = QPushButton("✓ Approve PR", self)
        self.approve_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #059669;"
            "  color: white;"
            "  border: 1px solid #047857;"
            "  border-radius: 5px;"
            "  padding: 4px 12px;"
            "  font-size: 11px;"
            "  font-weight: 700;"
            "}"
            "QPushButton:hover { background-color: #10b981; }"
            "QPushButton:pressed { background-color: #047857; }"
            "QPushButton:disabled { background-color: #334155; color: #64748b; border-color: #1e293b; }"
        )
        self.approve_btn.setEnabled(False)
        self.approve_btn.clicked.connect(self._on_approve_clicked)
        row2.addWidget(self.approve_btn)

        top_layout.addLayout(row2)
        main_layout.addWidget(top_card)

        # -------------------------------------------------------------
        # Main Splitter: Changed Files Sidebar (left) + Diff Stream (right)
        # -------------------------------------------------------------
        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setStyleSheet(
            "QSplitter::handle { background-color: #222734; width: 4px; }"
        )

        self.sidebar = DiffFilesSidebar(self.splitter)
        self.sidebar.fileSelected.connect(self._on_file_selected)
        self.splitter.addWidget(self.sidebar)

        self.diff_stream = DiffStreamWidget(self.typography, self.splitter)
        self.diff_stream.replySubmitted.connect(self._on_reply_submitted)
        self.diff_stream.resolveToggled.connect(self._on_resolve_toggled)
        self.splitter.addWidget(self.diff_stream)

        self.splitter.setSizes([280, 800])
        main_layout.addWidget(self.splitter, stretch=1)

        # Status footer bar
        self.status_bar = QLabel("Ready.", self)
        self.status_bar.setStyleSheet("color: #64748b; font-size: 11px; padding: 2px 4px;")
        main_layout.addWidget(self.status_bar)

    def load_pull_request(self, pr_data: dict[str, Any], repo_data: Optional[dict[str, Any]] = None):
        """Load pull request details, diff, comments, and CI status."""
        self._current_pr = pr_data
        self._current_repo = repo_data or self._resolve_repo_from_pr(pr_data)

        # Update Header Metadata
        pr_id = str(pr_data.get("id") or "")
        title = pr_data.get("title") or "Untitled PR"
        state = (pr_data.get("state") or "OPEN").upper()
        source = pr_data.get("source_branch") or ""
        target = pr_data.get("destination_branch") or ""
        author = pr_data.get("author_display") or pr_data.get("author") or ""

        self.title_label.setText(f"#{pr_id}  {title}")
        self.state_badge.setText(state)
        state_colors = {
            "OPEN": ("#064e3b", "#34d399"),
            "MERGED": ("#3b0764", "#c084fc"),
            "DECLINED": ("#4c0519", "#fb7185"),
        }
        bg, fg = state_colors.get(state, ("#334155", "#94a3b8"))
        self.state_badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;"
        )
        self.state_badge.setVisible(True)

        if source and target:
            self.branch_pill.setText(f"{source}  ➔  {target}")
            self.branch_pill.setVisible(True)
        else:
            self.branch_pill.setVisible(False)

        if author:
            self.author_lbl.setText(f"by {author}")
            self.author_lbl.setVisible(True)

        self.web_btn.setEnabled(bool(pr_data.get("links", {}).get("html", {}).get("href") or pr_data.get("html_url")))
        self.approve_btn.setEnabled(True)

        # Asynchronously fetch diff, comments, and CI status
        self.status_bar.setText("Loading diff, comments, and CI status...")
        if self.task_runner:
            self.task_runner.start(
                self._fetch_pr_review_data,
                (self._current_repo, pr_data),
                self._on_review_data_loaded,
                self._on_review_data_error,
            )
        else:
            try:
                data = self._fetch_pr_review_data(self._current_repo, pr_data)
                self._on_review_data_loaded(data)
            except Exception as exc:
                self._on_review_data_error(str(exc))

    def _resolve_repo_from_pr(self, pr_data: dict[str, Any]) -> dict[str, Any]:
        # Try active repo from config
        active_repo = self.config_manager.get_active_repository()
        if active_repo:
            return active_repo
        return {
            "provider": pr_data.get("provider") or self.config_manager.get_provider(),
            "owner": pr_data.get("owner") or "",
            "slug": pr_data.get("repo_slug") or "",
            "local_dir": pr_data.get("repo_local_dir") or self.config_manager.get_repo_dir(),
        }

    def _fetch_pr_review_data(self, repo: dict[str, Any], pr: dict[str, Any]) -> dict[str, Any]:
        repo_dir = repo.get("local_dir") or ""
        pr_id = str(pr.get("id") or "")

        # 1. Fetch Diff (try local git diff first, fallback to provider HTTP diff)
        diff_text = ""
        if repo_dir and os.path.isdir(repo_dir):
            try:
                res = self.diff_service.generate_pr_diff(pr, repo_dir)
                diff_text = res.diff_text
            except Exception:
                diff_text = ""

        if not diff_text:
            try:
                diff_text = self.pr_service.get_pull_request_diff_text(repo, pr_id)
            except Exception:
                diff_text = ""

        # 2. Fetch Comments
        comments_result = {}
        try:
            comments_result = self.comment_service.get_pull_request_comments(
                repo,
                pr_id,
                unresolved_only=False,
                include_code_context=False,
            )
        except Exception:
            comments_result = {"threads": [], "count": 0}

        # 3. Fetch CI Status
        commit_hash = pr.get("source_commit") or ""
        ci_status = {}
        try:
            ci_status = self.pr_service.get_pull_request_statuses(repo, commit_hash)
        except Exception:
            ci_status = {"state": "UNKNOWN", "total_count": 0, "statuses": []}

        return {
            "diff_text": diff_text,
            "comments": comments_result,
            "ci_status": ci_status,
        }

    def _on_review_data_loaded(self, data: dict[str, Any]):
        diff_text = data.get("diff_text") or ""
        comments_dict = data.get("comments") or {}
        ci_data = data.get("ci_status") or {}
        self._current_ci_status = ci_data

        # Group comments by file path
        comments_by_file: dict[str, list[dict[str, Any]]] = {}
        for thread in comments_dict.get("threads", []):
            fp = thread.get("file_path") or ""
            if fp:
                comments_by_file.setdefault(fp, []).append(thread)

        # Populate Diff Stream
        file_summaries = self.diff_stream.set_diff_content(diff_text, comments_by_file)

        # Populate Sidebar
        self.sidebar.set_files(file_summaries)

        # Update CI Status Badge
        ci_state = ci_data.get("state", "UNKNOWN")
        total_ci = ci_data.get("total_count", 0)
        succ_ci = ci_data.get("successful_count", 0)
        fail_ci = ci_data.get("failed_count", 0)

        if ci_state == "SUCCESSFUL":
            self.ci_badge.setText(f"✓ CI: {succ_ci}/{total_ci} Passing")
            self.ci_badge.setStyleSheet(
                "QPushButton { background-color: #064e3b; color: #34d399; border: 1px solid #059669; border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 700; }"
            )
        elif ci_state == "FAILED":
            self.ci_badge.setText(f"✗ CI: {fail_ci} Failing")
            self.ci_badge.setStyleSheet(
                "QPushButton { background-color: #4c0519; color: #fb7185; border: 1px solid #e11d48; border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 700; }"
            )
        elif ci_state == "INPROGRESS":
            self.ci_badge.setText("⏳ CI: Running...")
            self.ci_badge.setStyleSheet(
                "QPushButton { background-color: #451a03; color: #fbbf24; border: 1px solid #d97706; border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 700; }"
            )
        else:
            self.ci_badge.setText("CI: No checks")
            self.ci_badge.setStyleSheet(
                "QPushButton { background-color: #1e2433; color: #94a3b8; border: 1px solid #2e384d; border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 600; }"
            )
        self.ci_badge.setEnabled(bool(ci_data.get("statuses")))

        total_threads = comments_dict.get("count", 0)
        self.status_bar.setText(f"Loaded {len(file_summaries)} changed file(s), {total_threads} review comment(s).")

    def _on_review_data_error(self, error_msg: str):
        self.status_bar.setText(f"Failed to load PR review: {error_msg}")
        QMessageBox.warning(self, "Review Loading Error", f"Unable to load PR review data:\n{error_msg}")

    def _on_file_selected(self, file_path: str):
        self.diff_stream.scroll_to_file(file_path)

    def _show_ci_dialog(self):
        if self._current_ci_status:
            dlg = CIStatusDialog(self._current_ci_status, self)
            dlg.exec_()

    def _open_in_web(self):
        if not self._current_pr:
            return
        url = (
            (self._current_pr.get("links") or {}).get("html", {}).get("href")
            or self._current_pr.get("html_url")
        )
        if url:
            webbrowser.open(url)

    def _on_approve_clicked(self):
        if not self._current_pr or not self._current_repo:
            return
        pr_id = str(self._current_pr.get("id") or "")
        title = self._current_pr.get("title") or ""

        comment, ok = QInputDialog.getText(
            self,
            "Approve Pull Request",
            f"Approve PR #{pr_id} ({title})?\n\nOptional approval note (or leave blank):",
        )
        if not ok:
            return

        try:
            self.status_bar.setText("Submitting PR approval...")
            self.pr_service.approve_pull_request(self._current_repo, pr_id, comment=comment)
            QMessageBox.information(self, "PR Approved", f"Successfully approved PR #{pr_id}!")
            self.status_bar.setText(f"Approved PR #{pr_id}.")
        except Exception as exc:
            QMessageBox.critical(self, "Approval Error", f"Failed to approve PR #{pr_id}:\n{exc}")
            self.status_bar.setText("Approval failed.")

    def _on_reply_submitted(self, parent_comment_id: str, reply_body: str):
        if not self._current_pr or not self._current_repo:
            return
        pr_id = str(self._current_pr.get("id") or "")
        try:
            self.status_bar.setText("Posting reply...")
            self.comment_service.reply_to_comment(
                self._current_repo,
                pr_id,
                parent_comment_id,
                reply_body,
            )
            self.status_bar.setText("Reply posted successfully!")
            # Refresh review to update threads
            if self._current_pr:
                self.load_pull_request(self._current_pr, self._current_repo)
        except Exception as exc:
            QMessageBox.warning(self, "Reply Error", f"Failed to post reply:\n{exc}")
            self.status_bar.setText("Failed to post reply.")

    def _on_resolve_toggled(self, comment_id: str, unresolve: bool):
        if not self._current_pr or not self._current_repo:
            return
        pr_id = str(self._current_pr.get("id") or "")
        action = "Reopening" if unresolve else "Resolving"
        try:
            self.status_bar.setText(f"{action} thread...")
            self.comment_service.resolve_comment(
                self._current_repo,
                pr_id,
                comment_id,
                unresolve=unresolve,
            )
            self.status_bar.setText(f"Thread {'reopened' if unresolve else 'resolved'}!")
            if self._current_pr:
                self.load_pull_request(self._current_pr, self._current_repo)
        except Exception as exc:
            QMessageBox.warning(self, "Resolution Error", f"Failed to toggle resolution:\n{exc}")
            self.status_bar.setText("Resolution toggle failed.")
