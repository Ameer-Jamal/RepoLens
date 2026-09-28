from __future__ import annotations

import os
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QUrl
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
    QSizePolicy,
    QCheckBox,
    QApplication,
    QCompleter,
)

from services.pull_request_service import PullRequestService
from services.pull_request_comment_service import PullRequestCommentService
from services.diff_service import DiffService
from services.RepositoryProvider import RepositoryProvider
from services.provider_api import RepositoryRef, build_provider_client
from ui.DiffFilesSidebar import DiffFilesSidebar
from ui.DiffStreamWidget import DiffStreamWidget
from ui.SearchableComboBox import SearchableComboBox
from ui.TypographyController import TypographyController
from ui.theme import apply_theme


class AuthorComboBox(QComboBox):
    popupOpening = pyqtSignal()

    def showPopup(self):
        self.popupOpening.emit()
        super().showPopup()


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
    pipelineRequested = pyqtSignal(object, str)

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
        self._pr_list_request = 0
        self._review_request = 0
        self._approved_prs: set[tuple[str, str, str, str]] = set()
        self._developer_candidates: set[str] = set()
        self._author_scope_loaded: set[tuple[str, ...]] = set()
        self._author_request = 0
        self._pr_cursors: dict[str, str] = {}
        self._listed_prs: list[dict[str, Any]] = []

        self._build_ui()
        self.populate_repositories()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # -------------------------------------------------------------
        # Top Header Bar: Controls, Metadata, Badges, Typography & Actions
        # -------------------------------------------------------------
        top_card = QFrame(self)
        top_card.setObjectName("reviewTopCard")
        top_card.setStyleSheet(
            "QFrame#reviewTopCard {"
            "  background-color: #161821;"
            "  border: 1px solid #222734;"
            "  border-radius: 8px;"
            "  padding: 8px 12px;"
            "}"
        )
        top_layout = QVBoxLayout(top_card)
        top_layout.setContentsMargins(6, 6, 6, 6)
        top_layout.setSpacing(8)

        # Search scope is explicit: repository, author, then state.
        row0 = QHBoxLayout()
        row0.setSpacing(8)

        repo_lbl = QLabel("Repository", self)
        repo_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600;")
        row0.addWidget(repo_lbl)

        self.repo_combo = SearchableComboBox(self, search_placeholder="Search discovered repositories...")
        self.repo_combo.setMinimumWidth(220)
        self.repo_combo.setStyleSheet(
            "QComboBox {"
            "  background-color: #1a1d27; color: #f1f5f9; border: 1px solid #2e384d; border-radius: 5px; padding: 4px 8px; font-size: 11px;"
            "}"
            "QComboBox:hover { border-color: #3b82f6; }"
            "QComboBox QAbstractItemView { background-color: #161821; color: #f1f5f9; selection-background-color: #2563eb; selection-color: #ffffff; border: 1px solid #2e384d; }"
        )
        self.repo_combo.currentIndexChanged.connect(self._on_repo_selection_changed)
        row0.addWidget(self.repo_combo)

        self.refresh_repos_btn = QPushButton("↻", self)
        self.refresh_repos_btn.setToolTip("Refresh repositories from the provider")
        self.refresh_repos_btn.setFixedWidth(30)
        self.refresh_repos_btn.setStyleSheet("background: #1e2433; color: #cbd5e1; border: 1px solid #334155; border-radius: 5px;")
        self.refresh_repos_btn.clicked.connect(self.refresh_repositories)
        row0.addWidget(self.refresh_repos_btn)

        author_lbl = QLabel("Author", self)
        author_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600;")
        row0.addWidget(author_lbl)
        self.author_combo = AuthorComboBox(self)
        self.author_combo.setEditable(True)
        self.author_combo.setInsertPolicy(QComboBox.NoInsert)
        self.author_combo.setMinimumWidth(190)
        self.author_combo.setStyleSheet(
            "QComboBox { background-color: #1a1d27; color: #f1f5f9; border: 1px solid #2e384d; border-radius: 5px; padding: 4px 8px; font-size: 11px; }"
            "QComboBox:hover { border-color: #3b82f6; }"
            "QComboBox QLineEdit { background: transparent; color: #f1f5f9; border: none; padding: 0; }"
            "QComboBox QAbstractItemView { background-color: #161821; color: #f1f5f9; selection-background-color: #2563eb; selection-color: #ffffff; border: 1px solid #2e384d; }"
        )
        self.author_combo.addItem("Anyone", "")
        self.author_combo.addItem("My PRs", "Me")
        self.author_combo.setCurrentIndex(0)
        self.author_combo.lineEdit().setPlaceholderText("Choose or type a developer")
        completer = QCompleter(self.author_combo.model(), self.author_combo)
        completer.setCompletionColumn(0)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.author_combo.setCompleter(completer)
        self.author_combo.currentIndexChanged.connect(self._on_search_filter_changed)
        self.author_combo.lineEdit().textEdited.connect(self._on_search_filter_changed)
        self.author_combo.lineEdit().returnPressed.connect(self.load_repository_prs)
        self.author_combo.popupOpening.connect(self._load_author_candidates)
        row0.addWidget(self.author_combo)

        filter_lbl = QLabel("Status", self)
        filter_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600;")
        row0.addWidget(filter_lbl)

        self.filter_combo = QComboBox(self)
        self.filter_combo.setMinimumWidth(140)
        self.filter_combo.setStyleSheet(
            "QComboBox {"
            "  background-color: #1a1d27; color: #f1f5f9; border: 1px solid #2e384d; border-radius: 5px; padding: 4px 8px; font-size: 11px;"
            "}"
            "QComboBox:hover { border-color: #3b82f6; }"
            "QComboBox QAbstractItemView { background-color: #161821; color: #f1f5f9; selection-background-color: #2563eb; selection-color: #ffffff; border: 1px solid #2e384d; }"
        )
        self.filter_combo.addItem("Open PRs", "open")
        self.filter_combo.addItem("Merged PRs", "merged")
        self.filter_combo.addItem("All PRs", "all")
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        row0.addWidget(self.filter_combo)

        self.load_prs_btn = QPushButton("Find PRs", self)
        self.load_prs_btn.setStyleSheet(
            "QPushButton { background-color: #1e3a5f; color: #eff6ff; border: 1px solid #3b82f6; border-radius: 5px; padding: 5px 13px; font-size: 11px; font-weight: 700; }"
            "QPushButton:hover { background-color: #2563eb; }"
            "QPushButton:disabled { background-color: #161821; color: #475569; border-color: #1e2433; }"
        )
        self.load_prs_btn.clicked.connect(self.load_repository_prs)
        row0.addWidget(self.load_prs_btn)
        top_layout.addLayout(row0)

        results_row = QHBoxLayout()
        results_row.setSpacing(8)
        pr_lbl = QLabel("Results", self)
        pr_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600;")
        results_row.addWidget(pr_lbl)

        self.pr_combo = SearchableComboBox(self, search_placeholder="Search PR number, title, branch, author...")
        self.pr_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.pr_combo.setMinimumWidth(260)
        self.pr_combo.setStyleSheet(
            "QComboBox {"
            "  background-color: #1a1d27; color: #f1f5f9; border: 1px solid #2e384d; border-radius: 5px; padding: 4px 8px; font-size: 11px;"
            "}"
            "QComboBox:hover { border-color: #3b82f6; }"
            "QComboBox QAbstractItemView { background-color: #161821; color: #f1f5f9; selection-background-color: #2563eb; selection-color: #ffffff; border: 1px solid #2e384d; }"
        )
        self.pr_combo.currentIndexChanged.connect(self._on_pr_combo_changed)
        results_row.addWidget(self.pr_combo, 1)
        self.more_prs_btn = QPushButton("Load more", self)
        self.more_prs_btn.setStyleSheet("background: #1e2433; color: #93c5fd; border: 1px solid #334155; border-radius: 5px; padding: 4px 9px;")
        self.more_prs_btn.clicked.connect(lambda: self.load_repository_prs(more=True))
        self.more_prs_btn.hide()
        results_row.addWidget(self.more_prs_btn)
        self.developer_result_label = QLabel("Choose filters, then Find PRs", self)
        self.developer_result_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        results_row.addWidget(self.developer_result_label)
        top_layout.addLayout(results_row)

        divider = QFrame(self)
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setStyleSheet("background-color: #222734; border: none; min-height: 1px; max-height: 1px; margin: 2px 0;")
        top_layout.addWidget(divider)

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

        self.pipeline_btn = QPushButton("Run pipeline", self)
        self.pipeline_btn.setEnabled(False)
        self.pipeline_btn.clicked.connect(self._request_pipeline)
        row2.addWidget(self.pipeline_btn)

        row2.addStretch()

        # Typography Zoom Controls
        font_label = QLabel("Font:", self)
        font_label.setStyleSheet("color: #cbd5e1;")
        row2.addWidget(font_label)
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

        diff_options = QHBoxLayout()
        diff_options.setSpacing(24)
        self.ignore_whitespace_checkbox = QCheckBox("Ignore whitespace changes", self)
        self.hide_blank_lines_checkbox = QCheckBox("Hide blank lines", self)
        self.ignore_whitespace_checkbox.setStyleSheet("color: #cbd5e1;")
        self.hide_blank_lines_checkbox.setStyleSheet("color: #cbd5e1;")
        self.ignore_whitespace_checkbox.setMinimumWidth(175)
        diff_options.addWidget(self.ignore_whitespace_checkbox)
        diff_options.addWidget(self.hide_blank_lines_checkbox)
        diff_options.addStretch()
        top_layout.addLayout(diff_options)

        comments_row = QHBoxLayout()
        comments_row.setSpacing(8)
        comments_label = QLabel("Show comments:", self)
        comments_label.setStyleSheet("color: #cbd5e1;")
        comments_row.addWidget(comments_label)
        self.general_comments_btn = QPushButton("General comments", self)
        self.code_comments_btn = QPushButton("Code comments", self)
        for button in (self.general_comments_btn, self.code_comments_btn):
            button.setCheckable(True)
            button.setChecked(True)
            button.setStyleSheet(
                "QPushButton { background: #1e2433; color: #94a3b8; border: 1px solid #334155; border-radius: 5px; padding: 4px 10px; }"
                "QPushButton:checked { background: #1e3a5f; color: #e0f2fe; border-color: #3b82f6; }"
            )
            comments_row.addWidget(button)
        comments_row.addStretch()
        line_hint = QLabel("Click [+] beside a diff line to comment", self)
        line_hint.setStyleSheet("color: #94a3b8; font-size: 11px;")
        comments_row.addWidget(line_hint)
        top_layout.addLayout(comments_row)
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
        self.typography.fontSizeChanged.connect(self.sidebar.set_font_size)
        self.sidebar.set_font_size(self.typography.font_size)
        self.splitter.addWidget(self.sidebar)

        self.diff_stream = DiffStreamWidget(self.typography, self.splitter)
        self.diff_stream.replySubmitted.connect(self._on_reply_submitted)
        self.diff_stream.resolveToggled.connect(self._on_resolve_toggled)
        self.diff_stream.addCommentRequested.connect(self._on_add_comment_requested)
        self.ignore_whitespace_checkbox.toggled.connect(self.diff_stream.set_ignore_whitespace)
        self.hide_blank_lines_checkbox.toggled.connect(self.diff_stream.set_hide_blank_lines)
        self.splitter.addWidget(self.diff_stream)
        self.general_comments_btn.toggled.connect(self.diff_stream.set_general_comments_visible)
        self.code_comments_btn.toggled.connect(self.diff_stream.set_code_comments_visible)

        self.splitter.setSizes([280, 800])
        main_layout.addWidget(self.splitter, stretch=1)

        # Status footer bar
        self.status_bar = QLabel("Ready.", self)
        self.status_bar.setStyleSheet("color: #64748b; font-size: 11px; padding: 2px 4px;")
        main_layout.addWidget(self.status_bar)

    @staticmethod
    def _repo_label(repo: Optional[dict[str, Any]]) -> str:
        if not repo:
            return "No repository"
        owner = repo.get("owner") or ""
        slug = repo.get("slug") or repo.get("name") or repo.get("repo") or ""
        label = f"{owner}/{slug}".strip("/")
        return label or repo.get("name") or "Unnamed Repository"

    def populate_repositories(self, selected_repo: Optional[dict[str, Any]] = None):
        """Populate the repository dropdown from configuration."""
        previous_repo = self.repo_combo.currentData()
        previous_key = (
            str(previous_repo.get("provider") or "").lower(),
            self._repo_label(previous_repo),
        ) if isinstance(previous_repo, dict) else None
        repos = []
        if hasattr(self.config_manager, "get_selected_repositories"):
            repos = self.config_manager.get_selected_repositories() or []
        selected_keys = {
            (str(r.get("provider") or "").lower(), str(r.get("id") or r.get("owner") or ""), str(r.get("slug") or ""))
            for r in repos
        }
        try:
            provider = (self.config_manager.get_provider() or "bitbucket").lower()
            valid, _, provider_config = RepositoryProvider.validate_provider_config(provider, self.config_manager)
            if valid:
                context_key = RepositoryProvider.discovery_context_key(provider, provider_config)
                discovered, _ = self.config_manager.get_cached_discovered_repositories(provider, context_key)
                for candidate in discovered:
                    key = (str(candidate.get("provider") or "").lower(),
                           str(candidate.get("id") or candidate.get("owner") or ""),
                           str(candidate.get("slug") or ""))
                    if key not in selected_keys:
                        repos.append(candidate)
                        selected_keys.add(key)
        except (AttributeError, TypeError, ValueError):
            pass
        if not repos and hasattr(self.config_manager, "get_active_repository"):
            active = self.config_manager.get_active_repository()
            if active and (active.get("slug") or active.get("name")):
                repos = [active]

        self.repo_combo.blockSignals(True)
        self.repo_combo.clear()

        if not repos:
            self.repo_combo.addItem("No repositories configured", None)
            self.repo_combo.blockSignals(False)
            if previous_key:
                self._pr_list_request += 1
                self._clear_review()
                self.populate_pull_requests([])
            return

        all_scope = {"_all_repositories": True}
        selected_count = len(self.config_manager.get_selected_repositories() or []) if hasattr(self.config_manager, "get_selected_repositories") else 0
        if selected_count > 1:
            self.repo_combo.addItem(f"All selected repositories ({selected_count})", all_scope)
        target_id = str((selected_repo or {}).get("id") or "")
        target_label = self._repo_label(selected_repo) if selected_repo else ""
        target_provider = str((selected_repo or {}).get("provider") or "").lower()
        selected_index = 0
        if (selected_repo or previous_repo or {}).get("_all_repositories") and selected_count > 1:
            target_label = ""

        for idx, r in enumerate(repos):
            lbl = self._repo_label(r)
            self.repo_combo.addItem(lbl, r)
            combo_index = self.repo_combo.count() - 1
            self.repo_combo.setItemData(combo_index, f"{r.get('name') or ''} {r.get('provider') or ''}", Qt.UserRole + 1)
            same_provider = not target_provider or str(r.get("provider") or "").lower() == target_provider
            if same_provider and target_id and str(r.get("id") or "") == target_id:
                selected_index = combo_index
            elif same_provider and target_label and lbl == target_label:
                selected_index = combo_index

        self.repo_combo.setCurrentIndex(selected_index)
        self.repo_combo.blockSignals(False)
        current_repo = self.repo_combo.currentData()
        current_key = (str(current_repo.get("provider") or "").lower(), self._repo_label(current_repo))
        if previous_key and previous_key != current_key:
            self._pr_list_request += 1
            self._clear_review()
            self.populate_pull_requests([])

    def refresh_repositories(self):
        provider = (self.config_manager.get_provider() or "bitbucket").lower()
        valid, message, provider_config = RepositoryProvider.validate_provider_config(provider, self.config_manager)
        if not valid:
            self.status_bar.setText(message)
            return
        context_key = RepositoryProvider.discovery_context_key(provider, provider_config)
        selected_repo = self._selected_repo()
        self.refresh_repos_btn.setEnabled(False)
        self.status_bar.setText("Refreshing repositories...")

        def on_result(repos):
            self.config_manager.set_cached_discovered_repositories(provider, context_key, repos)
            self.populate_repositories(selected_repo)
            self.status_bar.setText(f"Found {len(repos)} repositories.")

        def on_error(exc):
            self.status_bar.setText(f"Repository discovery failed: {exc}")

        def on_finished():
            self.refresh_repos_btn.setEnabled(True)

        if self.task_runner:
            self.task_runner.run(
                lambda: RepositoryProvider.discover_repositories(provider, provider_config),
                description="Refresh review repositories", on_result=on_result,
                on_error=on_error, on_finished=on_finished,
            )
        else:
            try:
                on_result(RepositoryProvider.discover_repositories(provider, provider_config))
            except Exception as exc:
                on_error(exc)
            finally:
                on_finished()

    def _selected_repo(self) -> Optional[dict[str, Any]]:
        data = self.repo_combo.currentData()
        if isinstance(data, dict):
            return data
        if hasattr(self.config_manager, "get_active_repository"):
            active = self.config_manager.get_active_repository()
            if active and (active.get("slug") or active.get("name")):
                return active
        return None

    def _scope_repositories(self) -> list[dict[str, Any]]:
        scope = self._selected_repo()
        if not scope:
            return []
        if scope.get("_all_repositories"):
            return list(self.config_manager.get_selected_repositories() or [])
        return [scope]

    def _on_repo_selection_changed(self, index: int):
        self._on_search_filter_changed()

    def _on_filter_changed(self, index: int):
        self._on_search_filter_changed()

    def _on_search_filter_changed(self, *_args):
        self._pr_list_request += 1
        self.load_prs_btn.setEnabled(True)
        self.load_prs_btn.setText("Find PRs")
        self._pr_cursors = {}
        self._listed_prs = []
        self.more_prs_btn.hide()
        self._clear_review()
        self.populate_pull_requests([])
        self.developer_result_label.setText("Choose filters, then Find PRs")
        self.status_bar.setText("Choose repository, author, and status; then Find PRs.")

    def _selected_developer_filter(self) -> str:
        text = self.author_combo.currentText().strip()
        if text.casefold() == "anyone":
            return ""
        if text.casefold() == "my prs":
            return "Me"
        return text

    def _remember_developers(self, records: list[dict[str, Any]]):
        for pr in records:
            for key in ("author_username", "author_nickname", "author_display", "author"):
                value = str(pr.get(key) or "").strip()
                if value:
                    self._developer_candidates.add(value)
        current_text = self.author_combo.currentText()
        blocked = self.author_combo.blockSignals(True)
        existing = {self.author_combo.itemText(i).casefold() for i in range(self.author_combo.count())}
        for value in sorted(self._developer_candidates, key=str.casefold):
            if value.casefold() not in existing:
                self.author_combo.addItem(value, value)
                existing.add(value.casefold())
        self.author_combo.setEditText(current_text)
        self.author_combo.blockSignals(blocked)

    def _load_author_candidates(self):
        repos = self._scope_repositories()
        if not repos:
            return
        scope_key = tuple(sorted(f"{r.get('provider')}:{r.get('id') or self._repo_label(r)}" for r in repos))
        if scope_key in self._author_scope_loaded:
            return
        self._author_scope_loaded.add(scope_key)
        self._author_request += 1
        request_id = self._author_request

        def fetch():
            def candidates(repo):
                try:
                    provider = build_provider_client(self.config_manager)
                    return provider.list_developer_candidates(RepositoryRef.from_dict(repo), limit=40)
                except Exception:
                    return []
            with ThreadPoolExecutor(max_workers=min(4, len(repos))) as pool:
                values = {value for group in pool.map(candidates, repos) for value in group}
            return sorted(value for value in values if value)

        def loaded(values):
            if request_id == self._author_request:
                self._remember_developers([{"author": value} for value in values])

        def failed(_exc):
            self._author_scope_loaded.discard(scope_key)

        if self.task_runner:
            self.task_runner.run(fetch, description="Load PR author choices", on_result=loaded, on_error=failed)
        else:
            # A tab without a worker can still suggest authors from loaded PRs.
            self._author_scope_loaded.discard(scope_key)

    def _clear_review(self):
        self._review_request += 1
        self._current_pr = None
        self._current_repo = None
        self._current_ci_status = None
        self.title_label.setText("Select a pull request to review")
        self.state_badge.setVisible(False)
        self.branch_pill.setVisible(False)
        self.author_lbl.setVisible(False)
        self.web_btn.setEnabled(False)
        self.approve_btn.setEnabled(False)
        self.ci_badge.setText("CI Status: Not loaded")
        self.ci_badge.setEnabled(False)
        self.pipeline_btn.setEnabled(False)
        self.sidebar.set_files([])
        self.diff_stream.clear()

    def populate_pull_requests(self, prs: list[dict[str, Any]]):
        """Populate the PR combo box with a list of pull requests."""
        self.pr_combo.blockSignals(True)
        self.pr_combo.clear()
        if not prs:
            self.pr_combo.addItem("No pull requests found", None)
            self.pr_combo.blockSignals(False)
            return

        self.pr_combo.addItem("Choose a PR to review...", None)

        for pr in prs:
            pr_id = str(pr.get("id") or "")
            title = (pr.get("title") or "").strip()
            state = (pr.get("state") or "").upper()
            author = pr.get("author_display") or pr.get("author") or ""
            author_suffix = f" ({author})" if author else ""
            repo_prefix = f"{pr.get('repo_label')} · " if (self._selected_repo() or {}).get("_all_repositories") and pr.get("repo_label") else ""
            label = f"{repo_prefix}#{pr_id} · {title} [{state}]{author_suffix}"
            self.pr_combo.addItem(label, pr)
            index = self.pr_combo.count() - 1
            self.pr_combo.setItemData(index, " ".join(str(pr.get(key) or "") for key in (
                "source_branch", "destination_branch", "repo_label", "description", "author_username",
            )), Qt.UserRole + 1)

        self.pr_combo.blockSignals(False)

    def _on_pr_combo_changed(self, index: int):
        if index < 0:
            return
        pr_data = self.pr_combo.itemData(index)
        if isinstance(pr_data, dict):
            self.load_pull_request(pr_data)

    def sync_prs(self, prs: list[dict[str, Any]]):
        """Sync pull requests loaded elsewhere (e.g. PR Lens) into the PR dropdown."""
        if not prs or self._selected_developer_filter():
            return
        current_data = self.pr_combo.currentData()
        if not current_data or self.pr_combo.count() == 0:
            repo = self._selected_repo()
            if repo and not repo.get("_all_repositories"):
                repo_id = str(repo.get("id") or "")
                repo_label = self._repo_label(repo).lower()
                prs = [pr for pr in prs if
                       (not pr.get("repo_id") and not pr.get("repo_label"))
                       or
                       (repo_id and str(pr.get("repo_id") or "") == repo_id)
                       or str(pr.get("repo_label") or "").lower() == repo_label]
            self.populate_pull_requests(prs)

    def on_tab_activated(self, prs: Optional[list[dict[str, Any]]] = None):
        """Called when the PR Review tab becomes active."""
        self.populate_repositories(self.repo_combo.currentData())

        if prs and not self._listed_prs and (self.pr_combo.count() == 0 or not self.pr_combo.currentData()):
            self.sync_prs(prs)

        # If no PR is currently loaded, try to load one
        if not self._current_pr:
            if not self._listed_prs and self.pr_combo.count() > 1 and isinstance(self.pr_combo.itemData(1), dict):
                first_pr = self.pr_combo.itemData(1)
                self.load_pull_request(first_pr)
            elif not self._listed_prs and self._selected_repo():
                self.load_repository_prs()

    def load_repository_prs(self, more: bool = False):
        """Fetch PRs for the selected scope; retain per-repository page cursors."""
        repos = self._scope_repositories()
        if not repos:
            self.status_bar.setText("No repository selected. Please configure repositories in Settings.")
            return

        filter_mode = self.filter_combo.currentData() or "open"
        developer = self._selected_developer_filter()
        self._pr_list_request += 1
        request_id = self._pr_list_request
        repo_label = self.repo_combo.currentText()
        scope = f" by {developer}" if developer else ""
        if not more:
            self._pr_cursors = {}
            self._listed_prs = []
            self.populate_pull_requests([])
        self.status_bar.setText(f"Searching {repo_label} for {filter_mode} PRs{scope}...")
        self.load_prs_btn.setEnabled(False)
        self.load_prs_btn.setText("Searching...")
        self.more_prs_btn.setEnabled(False)
        self.developer_result_label.setText("Searching all pages..." if developer else "Loading recent PRs...")

        def _fetch():
            return self.pr_service.aggregate_pull_requests(
                repos, filter_mode, self._pr_cursors, reset=not more, developer=developer,
            )

        def _on_loaded(result):
            if request_id != self._pr_list_request:
                return
            records, cursors = result
            self._pr_cursors = cursors
            seen = {(str(pr.get("provider")), str(pr.get("repo_id") or pr.get("repo_label")), str(pr.get("id"))) for pr in self._listed_prs}
            for pr in records:
                key = (str(pr.get("provider")), str(pr.get("repo_id") or pr.get("repo_label")), str(pr.get("id")))
                if key not in seen:
                    self._listed_prs.append(pr)
                    seen.add(key)
            self.load_prs_btn.setEnabled(True)
            self.load_prs_btn.setText("Find PRs")
            self.more_prs_btn.setEnabled(True)
            self.more_prs_btn.setVisible(any(cursors.values()) and not developer)
            self._remember_developers(records)
            self.populate_pull_requests(self._listed_prs)
            count = len(self._listed_prs)
            count_label = f"{count} PR" if count == 1 else f"{count} PRs"
            self.developer_result_label.setText(
                f"{count_label} by {developer}" if developer else count_label
            )
            self.status_bar.setText(f"Found {count_label}{scope} in {repo_label}. Select a PR to review.")

        def _on_error(exc: Exception):
            if request_id != self._pr_list_request:
                return
            self.load_prs_btn.setEnabled(True)
            self.load_prs_btn.setText("Find PRs")
            self.more_prs_btn.setEnabled(True)
            self.developer_result_label.setText("Search failed")
            self.status_bar.setText(f"Failed to load PRs: {exc}")
            QMessageBox.warning(self, "PR Load Error", f"Unable to fetch pull requests for {repo_label}:\n{exc}")

        if self.task_runner:
            self.task_runner.run(
                _fetch,
                description=f"Fetch PRs for {repo_label}",
                on_result=_on_loaded,
                on_error=_on_error,
            )
        else:
            try:
                _on_loaded(_fetch())
            except Exception as exc:
                _on_error(exc)

    def load_pull_request(self, pr_data: dict[str, Any], repo_data: Optional[dict[str, Any]] = None):
        """Load pull request details, diff, comments, and CI status."""
        previous_key = self._approval_key() if self._current_pr and self._current_repo else None
        self.sidebar.set_files([])
        self.diff_stream.clear()
        self._current_ci_status = None
        self.ci_badge.setText("CI Status: Loading...")
        self.ci_badge.setEnabled(False)
        self._current_pr = pr_data
        self._current_repo = repo_data or self._resolve_repo_from_pr(pr_data)
        self.pipeline_btn.setEnabled(bool(self._current_repo and pr_data.get("source_branch")))
        if previous_key != self._approval_key():
            self.diff_stream.reset_viewed()
        self._review_request += 1
        request_id = self._review_request

        # Keep the all-repositories search scope while opening a result.
        repo_id = str(self._current_repo.get("id") or "")
        repo_label = self._repo_label(self._current_repo)
        self.repo_combo.blockSignals(True)
        if not (self._selected_repo() or {}).get("_all_repositories"):
            for i in range(self.repo_combo.count()):
                r = self.repo_combo.itemData(i)
                if isinstance(r, dict):
                    if (repo_id and str(r.get("id") or "") == repo_id) or self._repo_label(r) == repo_label:
                        self.repo_combo.setCurrentIndex(i)
                        break
        self.repo_combo.blockSignals(False)

        # Synchronize pr_combo
        pr_id = str(pr_data.get("id") or "")
        found = False
        self.pr_combo.blockSignals(True)
        for i in range(self.pr_combo.count()):
            p = self.pr_combo.itemData(i)
            if isinstance(p, dict) and str(p.get("id") or "") == pr_id:
                self.pr_combo.setCurrentIndex(i)
                found = True
                break
        if not found and pr_id:
            title = (pr_data.get("title") or "").strip()
            state = (pr_data.get("state") or "").upper()
            author = pr_data.get("author_display") or pr_data.get("author") or ""
            author_suffix = f" ({author})" if author else ""
            label = f"#{pr_id} · {title} [{state}]{author_suffix}"
            self.pr_combo.addItem(label, pr_data)
            self.pr_combo.setCurrentIndex(self.pr_combo.count() - 1)
        self.pr_combo.blockSignals(False)

        # Update Header Metadata
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

        self.web_btn.setEnabled(bool(pr_data.get("link") or pr_data.get("html_url") or (pr_data.get("links") or {}).get("html", {}).get("href")))
        self.approve_btn.setEnabled(state == "OPEN")
        self._update_approve_button()

        # Asynchronously fetch diff, comments, and CI status
        self.status_bar.setText("Loading diff, comments, and CI status...")
        self.diff_stream.show_loading(f"Loading PR #{pr_id} review")
        if self.task_runner:
            self.task_runner.run(
                self._fetch_pr_review_data,
                self._current_repo,
                pr_data,
                description=f"Load PR #{pr_id} Review",
                on_result=lambda data: self._on_review_data_loaded(data) if request_id == self._review_request else None,
                on_error=lambda error: self._on_review_data_error(error) if request_id == self._review_request else None,
            )
        else:
            try:
                data = self._fetch_pr_review_data(self._current_repo, pr_data)
                self._on_review_data_loaded(data)
            except Exception as exc:
                self._on_review_data_error(exc)

    def _resolve_repo_from_pr(self, pr_data: dict[str, Any]) -> dict[str, Any]:
        repo_id = str(pr_data.get("repo_id") or "")
        repo_label = str(pr_data.get("repo_label") or "").lower()
        pr_provider = str(pr_data.get("provider") or "").lower()

        # Check selected repositories first
        repos = []
        if hasattr(self.config_manager, "get_selected_repositories"):
            repos = self.config_manager.get_selected_repositories() or []
        for repo in repos:
            if pr_provider and str(repo.get("provider") or "").lower() != pr_provider:
                continue
            if repo_id and str(repo.get("id") or "") == repo_id:
                return repo
            owner = str(repo.get("owner") or "").lower()
            slug = str(repo.get("slug") or repo.get("name") or repo.get("repo") or "").lower()
            if repo_label and repo_label == f"{owner}/{slug}".strip("/"):
                return repo

        # Prefer the PR's own repository when the configured selection has changed.
        if repo_label or pr_data.get("slug"):
            return {
                "provider": pr_data.get("provider") or self.config_manager.get_provider(),
                "owner": pr_data.get("owner") or (repo_label.split("/")[0] if "/" in repo_label else ""),
                "slug": pr_data.get("slug") or (repo_label.split("/")[-1] if "/" in repo_label else ""),
                "local_dir": pr_data.get("repo_local_dir") or "",
            }

        # Check active repo from config
        if hasattr(self.config_manager, "get_active_repository"):
            active_repo = self.config_manager.get_active_repository()
            if active_repo and (active_repo.get("slug") or active_repo.get("name")):
                return active_repo

        return {
            "provider": pr_data.get("provider") or (getattr(self.config_manager, "get_provider", lambda: "bitbucket")() or "bitbucket"),
            "owner": pr_data.get("owner") or (repo_label.split("/")[0] if "/" in repo_label else ""),
            "slug": pr_data.get("slug") or (repo_label.split("/")[-1] if "/" in repo_label else ""),
            "local_dir": pr_data.get("repo_local_dir") or (getattr(self.config_manager, "get_repo_dir", lambda: "")() or ""),
        }

    def _fetch_pr_review_data(self, repo: dict[str, Any], pr: dict[str, Any]) -> dict[str, Any]:
        repo_dir = (repo or {}).get("local_dir") or ""
        pr_id = str(pr.get("id") or "")
        warnings: list[str] = []

        # The provider diff matches the exact PR even when the local checkout is stale.
        diff_text = ""
        try:
            diff_text = self.pr_service.get_pull_request_diff_text(repo, pr_id)
        except Exception as exc:
            warnings.append(f"Provider diff: {exc}")
            if repo_dir and os.path.isdir(repo_dir):
                try:
                    diff_text = self.diff_service.generate_pr_diff(pr, repo_dir).diff_text
                except Exception as local_exc:
                    warnings.append(f"Local diff: {local_exc}")

        # 2. Fetch Comments
        comments_result = {}
        try:
            comments_result = self.comment_service.get_pull_request_comments(
                repo,
                pr_id,
                unresolved_only=False,
                include_code_context=False,
            )
        except Exception as exc:
            warnings.append(f"Comments: {exc}")
            comments_result = {"threads": [], "count": 0}

        # 3. Fetch CI Status
        commit_hash = pr.get("source_commit") or ""
        ci_status = {}
        if commit_hash:
            try:
                ci_status = self.pr_service.get_pull_request_statuses(repo, commit_hash)
            except Exception as exc:
                warnings.append(f"CI status: {exc}")
                ci_status = {"state": "UNKNOWN", "total_count": 0, "statuses": []}
        else:
            ci_status = {"state": "UNKNOWN", "total_count": 0, "statuses": []}

        return {
            "diff_text": diff_text,
            "comments": comments_result,
            "ci_status": ci_status,
            "warnings": warnings,
        }

    def _on_review_data_loaded(self, data: dict[str, Any]):
        diff_text = data.get("diff_text") or ""
        comments_dict = data.get("comments") or {}
        ci_data = data.get("ci_status") or {}
        self._current_ci_status = ci_data

        # Group comments by file path
        comments_by_file: dict[str, list[dict[str, Any]]] = {}
        general_comments: list[dict[str, Any]] = []
        for thread in comments_dict.get("threads", []):
            fp = thread.get("file_path") or ""
            if fp:
                comments_by_file.setdefault(fp, []).append(thread)
            else:
                general_comments.append(thread)

        # Populate Diff Stream
        file_summaries = self.diff_stream.set_diff_content(diff_text, comments_by_file, general_comments)

        # Populate Sidebar
        self.sidebar.set_files(file_summaries)
        app = QApplication.instance()
        if app and hasattr(self.config_manager, "get_theme"):
            apply_theme(app, self.config_manager.get_theme())

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

        total_threads = (comments_dict.get("summary") or {}).get("total_threads", len(comments_dict.get("threads", [])))
        warnings = data.get("warnings") or []
        if diff_text:
            self.status_bar.setText(f"Loaded {len(file_summaries)} changed file(s), {total_threads} review comment(s).")
        else:
            self.status_bar.setText(f"Diff is empty or could not be generated from repository ({total_threads} review comments).")
        if warnings:
            self.status_bar.setText(self.status_bar.text() + " " + " | ".join(warnings))

    def _on_review_data_error(self, error: Any):
        error_msg = str(error)
        self.status_bar.setText(f"Failed to load PR review: {error_msg}")
        self.diff_stream.show_error(f"Could not load this PR review: {error_msg}")

    def _on_file_selected(self, file_path: str):
        self.diff_stream.scroll_to_file(file_path)

    def _on_add_comment_requested(self, file_path: str, line: int, side: str):
        if not self._current_pr or not self._current_repo:
            return
        side_label = "old" if side == "LEFT" else "new"
        body, accepted = QInputDialog.getMultiLineText(
            self,
            "Add inline review comment",
            f"{file_path}:{line} ({side_label} side)\n\nComment:",
        )
        body = body.strip()
        if not accepted or not body:
            return

        pr = self._current_pr
        repo = self._current_repo
        pr_id = str(pr.get("id") or "")
        self.status_bar.setText(f"Posting comment on {file_path}:{line}...")

        def post_comment():
            return self.comment_service.add_comment(
                repo, pr_id, body, file_path=file_path, line=line, side=side,
            )

        def on_result(_result):
            if self._current_pr is pr:
                self.status_bar.setText(f"Comment posted on {file_path}:{line}.")
                self.load_pull_request(pr, repo)

        def on_error(exc):
            if self._current_pr is pr:
                self.status_bar.setText(f"Could not post comment: {exc}")
                QMessageBox.warning(self, "Comment Error", f"Could not post the inline comment:\n{exc}")

        if self.task_runner:
            self.task_runner.run(post_comment, description=f"Post comment on PR #{pr_id}",
                                 on_result=on_result, on_error=on_error)
        else:
            try:
                on_result(post_comment())
            except Exception as exc:
                on_error(exc)

    def _show_ci_dialog(self):
        if self._current_ci_status:
            dlg = CIStatusDialog(self._current_ci_status, self)
            dlg.exec_()

    def _request_pipeline(self):
        if self._current_repo and self._current_pr:
            self.pipelineRequested.emit(self._current_repo, self._current_pr.get("source_branch") or "")

    def _open_in_web(self):
        if not self._current_pr:
            return
        url = (
            (self._current_pr.get("links") or {}).get("html", {}).get("href")
            or self._current_pr.get("link")
            or self._current_pr.get("html_url")
        )
        if url:
            webbrowser.open(url)

    def _approval_key(self):
        repo = self._current_repo or {}
        pr = self._current_pr or {}
        return (str(repo.get("provider") or "").lower(),
                str(repo.get("owner") or repo.get("workspace") or "").lower(),
                str(repo.get("slug") or "").lower(), str(pr.get("id") or ""))

    def _update_approve_button(self):
        self.approve_btn.setText("↶ Unapprove PR" if self._approval_key() in self._approved_prs else "✓ Approve PR")

    def _approval_feedback(self):
        self._play_approval_chime()
        previous = self.approve_btn.styleSheet()
        self.approve_btn.setStyleSheet(previous + "QPushButton { border: 2px solid #bfdbfe; }")
        QTimer.singleShot(700, lambda: self.approve_btn.setStyleSheet(previous))

    def _play_approval_chime(self):
        try:
            from PyQt5.QtMultimedia import QSoundEffect
            if not hasattr(self, "_approval_sound"):
                self._approval_sound = QSoundEffect(self)
                sound_path = os.path.join(os.path.dirname(__file__), "approval_chime.wav")
                self._approval_sound.setSource(QUrl.fromLocalFile(sound_path))
                self._approval_sound.setVolume(0.4)
            self._approval_sound.play()
        except (ImportError, RuntimeError, OSError):
            QApplication.beep()

    def _on_approve_clicked(self):
        if not self._current_pr or not self._current_repo:
            return
        pr_id = str(self._current_pr.get("id") or "")
        title = self._current_pr.get("title") or ""
        key = self._approval_key()
        unapprove = key in self._approved_prs
        comment = ""
        if not unapprove:
            comment, ok = QInputDialog.getText(
                self, "Approve Pull Request",
                f"Approve PR #{pr_id} ({title})?\n\nOptional approval note (or leave blank):",
            )
            if not ok:
                return

        repo = self._current_repo
        self.approve_btn.setEnabled(False)
        self.status_bar.setText("Removing approval..." if unapprove else "Submitting PR approval...")

        def submit():
            if unapprove:
                return self.pr_service.unapprove_pull_request(repo, pr_id)
            return self.pr_service.approve_pull_request(repo, pr_id, comment=comment)

        def on_result(result):
            if unapprove:
                self._approved_prs.discard(key)
                message = f"Removed approval for PR #{pr_id}."
            else:
                self._approved_prs.add(key)
                message = f"Approved PR #{pr_id}."
                if result.get("comment_error"):
                    message += f" Approval note was not posted: {result['comment_error']}"
            if self._approval_key() == key:
                self.approve_btn.setEnabled(True)
                self._update_approve_button()
                self.status_bar.setText(message)
                self._approval_feedback()

        def on_error(exc):
            if self._approval_key() == key:
                self.approve_btn.setEnabled(True)
                self.status_bar.setText(f"Approval action failed: {exc}")
                QMessageBox.warning(self, "Approval Error", f"Could not update PR #{pr_id} approval:\n{exc}")

        if self.task_runner:
            self.task_runner.run(submit, description=f"Update PR #{pr_id} approval",
                                 on_result=on_result, on_error=on_error)
        else:
            try:
                on_result(submit())
            except Exception as exc:
                on_error(exc)

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
