import os
import platform
import subprocess
import time

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGroupBox,
    QHBoxLayout,
    QFormLayout,
    QRadioButton,
    QButtonGroup,
    QCheckBox,
    QLineEdit,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QFileDialog,
    QTextEdit,
    QToolButton,
    QComboBox,
    QTabWidget,
    QSizePolicy,
)

from services.RepositoryProvider import RepositoryProvider


class SettingsTab(QWidget):
    """Central location for provider selection, credentials, and repository discovery."""

    settingsUpdated = pyqtSignal()
    providerChanged = pyqtSignal(str)
    activeRepositoriesChanged = pyqtSignal(list)
    themeChanged = pyqtSignal()

    def __init__(self, config_manager, task_runner=None):
        super().__init__()
        self.config = config_manager
        self.task_runner = task_runner

        self.bitbucket_radio = None
        self.github_radio = None
        self.bitbucket_group = None
        self.github_group = None

        self.bb_user_input = None
        self.bb_password_input = None
        self.bb_workspace_input = None

        self.github_owner_input = None
        self.github_token_input = None

        self.repo_list = None
        self.selected_repo_list = None
        self.repo_search_input = None
        self.repo_status = None
        self.discover_button = None
        self.refresh_button = None
        self.activate_button = None
        self.clear_checked_button = None
        self.output_input = None
        self.output_browse_button = None
        self.open_reports_button = None
        self.open_in_editor_checkbox = None
        self.copy_to_clipboard_checkbox = None
        self.copy_open_ai_checkbox = None
        self.editor_app_input = None
        self.ai_openai_checkbox = None
        self.ai_claude_checkbox = None
        self.ai_gemini_checkbox = None
        self.ai_grok_checkbox = None
        self.ai_custom_links_input = None
        self.ai_copy_with_prompt_checkbox = None
        self.ai_prompt_text_edit = None
        self.theme_combo = None

        self._repos = []
        self._activation_in_progress = False
        self._selected_repo_map = {}

        self._build_ui()
        self.apply_repo_config()

    # ------------------------------------------------------------------
    # UI construction
    def _info_button(self, text: str) -> QToolButton:
        button = QToolButton(self)
        button.setText("i")
        button.setAutoRaise(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(
            "QToolButton {"
            "color: #0b63ce;"
            "font-weight: 700;"
            "border: 1px solid #0b63ce;"
            "border-radius: 9px;"
            "min-width: 18px;"
            "max-width: 18px;"
            "min-height: 18px;"
            "max-height: 18px;"
            "padding: 0px;"
            "}"
        )
        button.clicked.connect(lambda: QMessageBox.information(self, "Info", text))
        return button

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(15, 15, 15, 15)
        tabs = QTabWidget(self)
        root_layout.addWidget(tabs)

        connections_page = QWidget(tabs)
        layout = QVBoxLayout(connections_page)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(14)
        tabs.addTab(connections_page, "Connections")

        provider_group = QGroupBox("Provider", self)
        provider_layout = QHBoxLayout(provider_group)
        provider_layout.setContentsMargins(12, 12, 12, 12)
        provider_layout.setSpacing(20)

        self.bitbucket_radio = QRadioButton("Bitbucket", provider_group)
        self.github_radio = QRadioButton("GitHub", provider_group)

        self.bitbucket_radio.toggled.connect(self._on_provider_toggled)
        self.github_radio.toggled.connect(self._on_provider_toggled)

        provider_buttons = QButtonGroup(provider_group)
        provider_buttons.addButton(self.bitbucket_radio)
        provider_buttons.addButton(self.github_radio)

        provider_layout.addWidget(self.bitbucket_radio)
        provider_layout.addWidget(self.github_radio)
        provider_layout.addStretch()
        layout.addWidget(provider_group)
        provider_info_row = QHBoxLayout()
        provider_info_label = QLabel("Help", self)
        provider_info_row.addWidget(provider_info_label)
        provider_info_row.addWidget(
            self._info_button(
                "Choose your provider, enter credentials, then use Repository Discovery to load and select repos."
            )
        )
        provider_info_row.addStretch()
        layout.addLayout(provider_info_row)

        # Bitbucket configuration
        self.bitbucket_group = QGroupBox("Bitbucket Settings", self)
        bb_form = QFormLayout(self.bitbucket_group)
        bb_form.setLabelAlignment(Qt.AlignRight)
        bb_form.setFormAlignment(Qt.AlignLeft)
        bb_form.setContentsMargins(12, 12, 12, 12)
        bb_form.setHorizontalSpacing(12)
        bb_form.setVerticalSpacing(8)

        self.bb_user_input = QLineEdit(self)
        self.bb_user_input.setPlaceholderText("Atlassian account email")
        self.bb_user_input.editingFinished.connect(self._store_bitbucket_username)
        bb_form.addRow(QLabel("Atlassian email:"), self.bb_user_input)

        self.bb_password_input = QLineEdit(self)
        self.bb_password_input.setEchoMode(QLineEdit.Password)
        self.bb_password_input.setPlaceholderText("Bitbucket API token")
        self.bb_password_input.editingFinished.connect(self._store_bitbucket_password)
        bb_form.addRow(QLabel("API Token:"), self.bb_password_input)

        self.bb_workspace_input = QLineEdit(self)
        self.bb_workspace_input.setPlaceholderText("Workspace id (for example: example-workspace)")
        self.bb_workspace_input.editingFinished.connect(self._store_bitbucket_workspace)
        bb_form.addRow(QLabel("Workspace:"), self.bb_workspace_input)

        layout.addWidget(self.bitbucket_group)

        # GitHub configuration
        self.github_group = QGroupBox("GitHub Settings", self)
        gh_form = QFormLayout(self.github_group)
        gh_form.setLabelAlignment(Qt.AlignRight)
        gh_form.setFormAlignment(Qt.AlignLeft)
        gh_form.setContentsMargins(12, 12, 12, 12)
        gh_form.setHorizontalSpacing(12)
        gh_form.setVerticalSpacing(8)

        self.github_owner_input = QLineEdit(self)
        self.github_owner_input.setPlaceholderText("Owner/org (optional)")
        self.github_owner_input.editingFinished.connect(self._store_github_owner)
        gh_form.addRow(QLabel("Owner Filter:"), self.github_owner_input)

        self.github_token_input = QLineEdit(self)
        self.github_token_input.setEchoMode(QLineEdit.Password)
        self.github_token_input.setPlaceholderText("GitHub personal access token")
        self.github_token_input.editingFinished.connect(self._store_github_token)
        gh_form.addRow(QLabel("Token:"), self.github_token_input)

        layout.addWidget(self.github_group)
        layout.addStretch()

        repositories_page = QWidget(tabs)
        repositories_layout = QVBoxLayout(repositories_page)
        repositories_layout.setContentsMargins(12, 16, 12, 12)
        tabs.addTab(repositories_page, "Repositories")

        appearance_page = QWidget(tabs)
        layout = QVBoxLayout(appearance_page)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(14)
        tabs.addTab(appearance_page, "Appearance && Files")

        application_group = QGroupBox("Appearance && File Handling", self)
        application_form = QFormLayout(application_group)
        application_form.setLabelAlignment(Qt.AlignRight)
        application_form.setFormAlignment(Qt.AlignLeft)
        application_form.setContentsMargins(12, 12, 12, 12)
        application_form.setHorizontalSpacing(12)
        application_form.setVerticalSpacing(8)

        self.theme_combo = QComboBox(self)
        for label, value in (("Midnight", "Midnight"), ("Aurora", "Aurora"),
                             ("Forest", "Forest"), ("Classic (original)", "Classic")):
            self.theme_combo.addItem(label, value)
        self.theme_combo.setMinimumWidth(240)
        self.theme_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.theme_combo.setStyleSheet("QComboBox { padding-right: 24px; }")
        self.theme_combo.setToolTip("Choose the app's color theme")
        self.theme_combo.currentIndexChanged.connect(
            lambda: self._store_theme(self.theme_combo.currentData())
        )
        application_form.addRow(QLabel("Theme:"), self.theme_combo)

        output_layout = QHBoxLayout()
        self.output_input = QLineEdit(self)
        self.output_input.editingFinished.connect(self._store_output_dir)
        output_layout.addWidget(self.output_input)

        self.output_browse_button = QPushButton("Browse", self)
        self.output_browse_button.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(self.output_browse_button)

        self.open_reports_button = QPushButton("Open Reports", self)
        self.open_reports_button.clicked.connect(self.open_reports_folder)
        output_layout.addWidget(self.open_reports_button)

        application_form.addRow(QLabel("Output Directory:"), output_layout)

        self.open_in_editor_checkbox = QCheckBox("Open in code editor", self)
        self.open_in_editor_checkbox.setToolTip("After generating a diff, open the file in your configured editor.")
        self.open_in_editor_checkbox.toggled.connect(self._store_open_in_editor)
        application_form.addRow(self.open_in_editor_checkbox)
        editor_app_layout = QHBoxLayout()
        self.editor_app_input = QLineEdit(self)
        self.editor_app_input.setPlaceholderText("Optional editor app path/name for 'Open in code editor'")
        self.editor_app_input.editingFinished.connect(self._store_editor_app_path)
        editor_app_layout.addWidget(self.editor_app_input)
        editor_browse_button = QPushButton("Browse", self)
        editor_browse_button.clicked.connect(self.browse_editor_app)
        editor_app_layout.addWidget(editor_browse_button)
        application_form.addRow(QLabel("Open With (Editor):"), editor_app_layout)

        self.copy_to_clipboard_checkbox = QCheckBox("Copy to clipboard", self)
        self.copy_to_clipboard_checkbox.setToolTip("After generating a diff, copy the content to clipboard.")
        self.copy_to_clipboard_checkbox.toggled.connect(self._store_copy_to_clipboard)
        application_form.addRow(self.copy_to_clipboard_checkbox)
        layout.addWidget(application_group)
        layout.addStretch()

        ai_page = QWidget(tabs)
        layout = QVBoxLayout(ai_page)
        layout.setContentsMargins(12, 16, 12, 12)
        tabs.addTab(ai_page, "AI Assistant")
        ai_group = QGroupBox("AI Assistant Options", self)
        ai_form = QFormLayout(ai_group)
        ai_form.setLabelAlignment(Qt.AlignRight)
        ai_form.setHorizontalSpacing(12)
        ai_form.setVerticalSpacing(8)
        ai_header_row = QHBoxLayout()
        ai_header_label = QLabel("AI Assistant Options", self)
        ai_header_label.setStyleSheet("QLabel { font-weight: 600; }")
        ai_header_row.addWidget(ai_header_label)
        ai_header_row.addWidget(
            self._info_button(
                "When enabled, RepoLens copies diff content, optionally prepends your prompt, "
                "and opens only the AI tabs/links you selected."
            )
        )
        ai_header_row.addStretch()
        ai_form.addRow(ai_header_row)
        self.copy_open_ai_checkbox = QCheckBox("Copy and open AI tabs (OpenAI, Claude, Gemini, Grok)", self)
        self.copy_open_ai_checkbox.setToolTip("Copies diff text, then opens browser tabs for supported AI tools.")
        self.copy_open_ai_checkbox.toggled.connect(self._store_copy_open_ai)
        ai_form.addRow(self.copy_open_ai_checkbox)
        ai_targets_row = QHBoxLayout()
        self.ai_openai_checkbox = QCheckBox("OpenAI", self)
        self.ai_openai_checkbox.toggled.connect(self._store_ai_targets)
        ai_targets_row.addWidget(self.ai_openai_checkbox)
        self.ai_claude_checkbox = QCheckBox("Claude", self)
        self.ai_claude_checkbox.toggled.connect(self._store_ai_targets)
        ai_targets_row.addWidget(self.ai_claude_checkbox)
        self.ai_gemini_checkbox = QCheckBox("Gemini", self)
        self.ai_gemini_checkbox.toggled.connect(self._store_ai_targets)
        ai_targets_row.addWidget(self.ai_gemini_checkbox)
        self.ai_grok_checkbox = QCheckBox("Grok", self)
        self.ai_grok_checkbox.toggled.connect(self._store_ai_targets)
        ai_targets_row.addWidget(self.ai_grok_checkbox)
        ai_targets_row.addStretch()
        ai_form.addRow(QLabel("AI Tabs:"), ai_targets_row)
        self.ai_custom_links_input = QLineEdit(self)
        self.ai_custom_links_input.setPlaceholderText("Custom AI links (comma-separated URLs)")
        self.ai_custom_links_input.setToolTip("Enter one or more URLs separated by commas.")
        self.ai_custom_links_input.editingFinished.connect(self._store_ai_custom_links)
        ai_form.addRow(QLabel("Custom Links:"), self.ai_custom_links_input)
        self.ai_copy_with_prompt_checkbox = QCheckBox("Copy with prompt", self)
        self.ai_copy_with_prompt_checkbox.setToolTip("Prepends your prompt above copied diff content.")
        self.ai_copy_with_prompt_checkbox.toggled.connect(self._store_ai_copy_with_prompt)
        ai_form.addRow(self.ai_copy_with_prompt_checkbox)
        self.ai_prompt_text_edit = QTextEdit(self)
        self.ai_prompt_text_edit.setPlaceholderText("Example: Review this diff and list risks, bugs, and improvements.")
        self.ai_prompt_text_edit.setToolTip("Editable prompt used when 'Copy with prompt' is enabled.")
        self.ai_prompt_text_edit.setFixedHeight(90)
        self.ai_prompt_text_edit.textChanged.connect(self._store_ai_prompt_text)
        ai_form.addRow(QLabel("Prompt Text:"), self.ai_prompt_text_edit)

        layout.addWidget(ai_group)
        layout.addStretch()

        discovery_group = QGroupBox("Repository Discovery", self)
        discovery_layout = QVBoxLayout(discovery_group)
        discovery_layout.setContentsMargins(12, 12, 12, 12)
        discovery_layout.setSpacing(8)

        controls = QHBoxLayout()
        self.discover_button = QPushButton("Load Repositories", self)
        self.discover_button.setToolTip("Fetch repositories from the selected provider.")
        self.discover_button.clicked.connect(self.discover_repositories)
        controls.addWidget(self.discover_button)

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(lambda: self.discover_repositories(force_refresh=True))
        controls.addWidget(self.refresh_button)

        self.clear_checked_button = QPushButton("Clear Checked", self)
        self.clear_checked_button.clicked.connect(self._clear_checked_repositories)
        controls.addWidget(self.clear_checked_button)

        self.activate_button = QPushButton("Use Selected Repositories", self)
        self.activate_button.setToolTip("Save checked repositories and set the first one as primary.")
        self.activate_button.setStyleSheet(
            "QPushButton {"
            "background-color: #0b63ce;"
            "color: white;"
            "font-weight: 700;"
            "border: 1px solid #084b9e;"
            "border-radius: 6px;"
            "padding: 6px 12px;"
            "}"
            "QPushButton:hover { background-color: #0958b8; }"
            "QPushButton:pressed { background-color: #074894; }"
            "QPushButton:disabled { background-color: #7aa8df; color: #f3f7ff; }"
        )
        self.activate_button.clicked.connect(self.activate_selected_repositories)
        controls.addWidget(self.activate_button)
        controls.addStretch()

        discovery_layout.addLayout(controls)
        self.repo_status = QLabel("No repositories loaded.", self)
        self.repo_status.setWordWrap(True)
        discovery_layout.addWidget(self.repo_status)

        self.repo_search_input = QLineEdit(self)
        self.repo_search_input.setPlaceholderText("Search discovered repositories")
        self.repo_search_input.textChanged.connect(self._filter_repository_list)
        discovery_layout.addWidget(self.repo_search_input)

        self.repo_list = QListWidget(self)
        self.repo_list.itemChanged.connect(self._on_repo_item_changed)
        discovery_layout.addWidget(self.repo_list)

        selected_label = QLabel("Selected Repositories", self)
        discovery_layout.addWidget(selected_label)
        self.selected_repo_list = QListWidget(self)
        discovery_layout.addWidget(self.selected_repo_list)

        repositories_layout.addWidget(discovery_group)

    # ------------------------------------------------------------------
    # Configuration helpers
    def apply_repo_config(self):
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.config.get_theme())))
        self.theme_combo.blockSignals(False)
        provider = self.config.get_provider()
        self._set_provider(provider)

        self._set_line_edit(self.bb_user_input, self.config.get_bitbucket_username())
        self._set_line_edit(self.bb_password_input, self.config.get_bitbucket_api_token())
        self._set_line_edit(self.bb_workspace_input, self.config.get_bitbucket_workspace())

        self._set_line_edit(self.github_owner_input, self.config.get_github_owner())
        self._set_line_edit(self.github_token_input, self.config.get_github_token())
        self._set_line_edit(self.output_input, self.config.get_output_dir())

        self.open_in_editor_checkbox.blockSignals(True)
        self.open_in_editor_checkbox.setChecked(self.config.get_open_in_editor())
        self.open_in_editor_checkbox.blockSignals(False)

        self.copy_to_clipboard_checkbox.blockSignals(True)
        self.copy_to_clipboard_checkbox.setChecked(self.config.get_copy_to_clipboard())
        self.copy_to_clipboard_checkbox.blockSignals(False)
        self.copy_open_ai_checkbox.blockSignals(True)
        self.copy_open_ai_checkbox.setChecked(self.config.get_copy_open_ai())
        self.copy_open_ai_checkbox.blockSignals(False)
        self._set_line_edit(self.editor_app_input, self.config.get_editor_app_path())
        ai_targets = self.config.get_ai_targets()
        for key, widget in (
            ("openai", self.ai_openai_checkbox),
            ("claude", self.ai_claude_checkbox),
            ("gemini", self.ai_gemini_checkbox),
            ("grok", self.ai_grok_checkbox),
        ):
            block = widget.blockSignals(True)
            widget.setChecked(bool(ai_targets.get(key, False)))
            widget.blockSignals(block)
        self._set_line_edit(self.ai_custom_links_input, ", ".join(self.config.get_ai_custom_links()))
        self.ai_copy_with_prompt_checkbox.blockSignals(True)
        self.ai_copy_with_prompt_checkbox.setChecked(self.config.get_ai_copy_with_prompt())
        self.ai_copy_with_prompt_checkbox.blockSignals(False)
        self.ai_prompt_text_edit.blockSignals(True)
        self.ai_prompt_text_edit.setPlainText(self.config.get_ai_prompt_text())
        self.ai_prompt_text_edit.blockSignals(False)
        self._update_ai_section_enabled()

        self._selected_repo_map = {
            str((repo or {}).get("id", "")): repo
            for repo in self.config.get_selected_repositories()
            if (repo or {}).get("id")
        }
        self._update_visibility()
        self._apply_selected_repo_selection()

    def _store_theme(self, theme: str):
        if not theme:
            return
        self.config.set_theme(theme)
        self.themeChanged.emit()

    def _apply_selected_repo_selection(self):
        selected_repos = list(self._selected_repo_map.values())
        selected_ids = set(self._selected_repo_map.keys())

        active_repo = self.config.get_active_repository()
        active_name = active_repo.get("name") or ""
        active_owner = active_repo.get("owner") or ""
        active_provider = active_repo.get("provider") or ""

        if selected_repos:
            self.repo_status.setText(
                f"Ready. Selected repositories: {len(selected_repos)} | "
                f"Primary: {active_owner}/{active_name} ({active_provider})"
            )
        elif active_name:
            self.repo_status.setText(f"Primary repository: {active_owner}/{active_name} ({active_provider})")
        else:
            self.repo_status.setText("No repository selection saved.")

        self.repo_list.blockSignals(True)
        for i in range(self.repo_list.count()):
            item = self.repo_list.item(i)
            payload = item.data(Qt.UserRole) or {}
            repo_id = str(payload.get("id", ""))
            item.setCheckState(Qt.Checked if repo_id in selected_ids else Qt.Unchecked)
        self.repo_list.blockSignals(False)
        self._sync_selected_repo_panel()

    @staticmethod
    def _set_line_edit(line_edit, value):
        if line_edit is None:
            return
        block = line_edit.blockSignals(True)
        line_edit.setText(value or "")
        line_edit.blockSignals(block)

    def _set_provider(self, provider):
        provider = (provider or "bitbucket").lower()
        block_bb = self.bitbucket_radio.blockSignals(True)
        block_gh = self.github_radio.blockSignals(True)
        if provider == "github":
            self.github_radio.setChecked(True)
        else:
            self.bitbucket_radio.setChecked(True)
        self.bitbucket_radio.blockSignals(block_bb)
        self.github_radio.blockSignals(block_gh)
        self._update_visibility()

    def _update_visibility(self):
        provider = "bitbucket" if self.bitbucket_radio.isChecked() else "github"
        if self.bitbucket_group:
            self.bitbucket_group.setVisible(provider == "bitbucket")
        if self.github_group:
            self.github_group.setVisible(provider == "github")

    def _set_discovery_busy(self, busy: bool, status: str = ""):
        self.discover_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.clear_checked_button.setEnabled((not busy) and (not self._activation_in_progress))
        self.activate_button.setEnabled((not busy) and (not self._activation_in_progress))
        self.repo_list.setEnabled((not busy) and (not self._activation_in_progress))
        self.selected_repo_list.setEnabled((not busy) and (not self._activation_in_progress))
        self.repo_search_input.setEnabled((not busy) and (not self._activation_in_progress))
        if status:
            self.repo_status.setText(status)

    # ------------------------------------------------------------------
    # Store handlers
    def _on_provider_toggled(self, checked):
        if not checked:
            return
        provider = "bitbucket" if self.bitbucket_radio.isChecked() else "github"
        self.config.set_provider(provider)
        self._update_visibility()
        self.providerChanged.emit(provider)
        self.settingsUpdated.emit()

    def _store_bitbucket_username(self):
        self.config.set_bitbucket_username(self.bb_user_input.text().strip())
        self.settingsUpdated.emit()

    def _store_bitbucket_password(self):
        self.config.set_bitbucket_api_token(self.bb_password_input.text().strip())
        self.settingsUpdated.emit()

    def _store_bitbucket_workspace(self):
        self.config.set_bitbucket_workspace(self.bb_workspace_input.text().strip())
        self.settingsUpdated.emit()

    def _store_github_owner(self):
        self.config.set_github_owner(self.github_owner_input.text().strip())
        self.settingsUpdated.emit()

    def _store_github_token(self):
        self.config.set_github_token(self.github_token_input.text().strip())
        self.settingsUpdated.emit()

    def _store_output_dir(self):
        self.config.set_output_dir(self.output_input.text().strip())
        self.settingsUpdated.emit()

    def _store_open_in_editor(self, checked):
        self.config.set_open_in_editor(checked)
        self.settingsUpdated.emit()

    def _store_copy_to_clipboard(self, checked):
        self.config.set_copy_to_clipboard(checked)
        self.settingsUpdated.emit()

    def _store_copy_open_ai(self, checked):
        self.config.set_copy_open_ai(checked)
        self._update_ai_section_enabled()
        self.settingsUpdated.emit()

    def _store_editor_app_path(self):
        self.config.set_editor_app_path(self.editor_app_input.text().strip())
        self.settingsUpdated.emit()

    def _store_ai_targets(self):
        self.config.set_ai_targets(
            {
                "openai": self.ai_openai_checkbox.isChecked(),
                "claude": self.ai_claude_checkbox.isChecked(),
                "gemini": self.ai_gemini_checkbox.isChecked(),
                "grok": self.ai_grok_checkbox.isChecked(),
            }
        )
        self.settingsUpdated.emit()

    def _store_ai_custom_links(self):
        raw = self.ai_custom_links_input.text().strip()
        links = [item.strip() for item in raw.split(",") if item.strip()]
        self.config.set_ai_custom_links(links)
        self.settingsUpdated.emit()

    def _store_ai_copy_with_prompt(self, checked):
        self.config.set_ai_copy_with_prompt(checked)
        self.settingsUpdated.emit()

    def _store_ai_prompt_text(self):
        self.config.set_ai_prompt_text(self.ai_prompt_text_edit.toPlainText())
        self.settingsUpdated.emit()

    def _update_ai_section_enabled(self):
        enabled = self.copy_open_ai_checkbox.isChecked()
        for widget in (
            self.ai_openai_checkbox,
            self.ai_claude_checkbox,
            self.ai_gemini_checkbox,
            self.ai_grok_checkbox,
            self.ai_custom_links_input,
            self.ai_copy_with_prompt_checkbox,
            self.ai_prompt_text_edit,
        ):
            widget.setEnabled(enabled)

    def browse_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if not directory:
            return
        self._set_line_edit(self.output_input, directory)
        self.config.set_output_dir(directory)
        self.settingsUpdated.emit()

    def browse_editor_app(self):
        app_path, _ = QFileDialog.getOpenFileName(self, "Select Editor Application")
        if not app_path:
            return
        self._set_line_edit(self.editor_app_input, app_path)
        self.config.set_editor_app_path(app_path)
        self.settingsUpdated.emit()

    def open_reports_folder(self):
        output_dir = self.output_input.text().strip() or self.config.get_output_dir()
        if not output_dir:
            QMessageBox.warning(self, "Input Error", "Set an output directory before opening saved reports.")
            return
        if not os.path.isdir(output_dir):
            QMessageBox.warning(self, "Input Error", f"Output directory does not exist:\n{output_dir}")
            return
        self._open_path(output_dir)

    @staticmethod
    def _open_path(path):
        if platform.system() == "Darwin":
            subprocess.run(["open", path])
        elif platform.system() == "Windows":
            os.startfile(path)
        else:
            subprocess.run(["xdg-open", path])

    def discover_repositories(self, force_refresh=False):
        provider = (self.config.get_provider() or "bitbucket").lower()
        valid, message, provider_config = RepositoryProvider.validate_provider_config(provider, self.config)
        if not valid:
            QMessageBox.warning(self, "Configuration Error", message)
            self._focus_provider_config(provider)
            return

        context_key = RepositoryProvider.discovery_context_key(provider, provider_config)

        def render_repos(repos, source_label):
            self._repos = repos
            self.repo_list.clear()
            self.repo_search_input.clear()

            selected_ids = set(self._selected_repo_map.keys())

            self.repo_list.blockSignals(True)
            for repo in repos:
                label = f"{repo.get('owner', '')}/{repo.get('slug', repo.get('name', ''))}"
                item = QListWidgetItem(label)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setData(Qt.UserRole, repo)
                repo_id = str(repo.get("id", ""))
                item.setCheckState(Qt.Checked if repo_id in selected_ids else Qt.Unchecked)
                self.repo_list.addItem(item)
            self.repo_list.blockSignals(False)
            self._sync_selected_repo_panel()

            if repos:
                self.repo_status.setText(
                    f"Loaded {len(repos)} repositories for {provider} ({source_label}). Select repositories and click 'Use Selected Repositories'."
                )
            else:
                self.repo_status.setText(f"No repositories found for {provider}.")

        cached_repos, cached_timestamp = self.config.get_cached_discovered_repositories(provider, context_key)
        if not force_refresh and cached_repos:
            age_seconds = max(0, int(time.time() - cached_timestamp))
            render_repos(cached_repos, f"cached {age_seconds}s ago")
            return

        def handle_result(repos):
            self.config.set_cached_discovered_repositories(provider, context_key, repos)
            render_repos(repos, "fresh")

        def handle_error(exc: Exception):
            QMessageBox.critical(self, "Discovery Error", f"Failed to discover repositories:\n{exc}")
            self.repo_status.setText("Repository discovery failed.")

        if not self.task_runner:
            self._set_discovery_busy(True, "Discovering repositories…")
            try:
                handle_result(RepositoryProvider.discover_repositories(provider, provider_config))
            except Exception as exc:  # noqa: BLE001
                handle_error(exc)
            finally:
                self._set_discovery_busy(False)
            return

        self._set_discovery_busy(True, "Discovering repositories…")
        self.task_runner.run(
            lambda: RepositoryProvider.discover_repositories(provider, provider_config),
            description="Discover Repositories",
            on_result=handle_result,
            on_error=handle_error,
            on_finished=lambda: self._set_discovery_busy(False),
        )

    def activate_selected_repositories(self):
        repos = list(self._selected_repo_map.values())
        if not repos:
            QMessageBox.warning(self, "Selection Required", "Select at least one repository.")
            return

        primary = repos[0]
        owner = primary.get("owner", "")
        slug = primary.get("slug", primary.get("name", ""))
        self._activation_in_progress = True
        self._set_discovery_busy(
            False,
            f"Applying selection ({len(repos)} repositories). Primary will be {owner}/{slug}...",
        )
        self.activeRepositoriesChanged.emit(repos)

    def has_unsaved_repository_selection(self):
        if not self._selected_repo_map:
            return False

        selected_ids = self._repo_ids(self._selected_repo_map.values())
        saved_ids = self._repo_ids(self.config.get_selected_repositories())
        return selected_ids != saved_ids

    def discard_unsaved_repository_selection(self):
        self._selected_repo_map = {
            str((repo or {}).get("id", "")): repo
            for repo in self.config.get_selected_repositories()
            if (repo or {}).get("id")
        }
        self._apply_selected_repo_selection()

    def clear_pending_repository_selection(self):
        self._selected_repo_map = {}
        self.repo_list.blockSignals(True)
        for i in range(self.repo_list.count()):
            item = self.repo_list.item(i)
            item.setCheckState(Qt.Unchecked)
        self.repo_list.blockSignals(False)
        self._sync_selected_repo_panel()

    @staticmethod
    def _repo_ids(repositories):
        return {
            str((repo or {}).get("id", ""))
            for repo in (repositories or [])
            if (repo or {}).get("id")
        }

    def set_repository_activation_finished(self, success: bool, repos: list = None, active_repo: dict = None):
        self._activation_in_progress = False
        self._set_discovery_busy(False)

        if success:
            saved_repos = repos if isinstance(repos, list) else self.config.get_selected_repositories()
            primary = active_repo or self.config.get_active_repository()
            owner = primary.get("owner", "")
            slug = primary.get("slug", primary.get("name", ""))
            provider = primary.get("provider", "")
            self._apply_selected_repo_selection()
            self.repo_status.setText(
                f"Selected repositories: {len(saved_repos)} | Primary: {owner}/{slug} ({provider})"
            )
            self._selected_repo_map = {
                str((repo or {}).get("id", "")): repo
                for repo in saved_repos
                if (repo or {}).get("id")
            }
            self._apply_selected_repo_selection()

    # ------------------------------------------------------------------
    # Focus helpers
    def focus_bitbucket_credentials(self):
        if self.bb_user_input:
            self.bb_user_input.setFocus()
            self.bb_user_input.selectAll()

    def focus_bitbucket_workspace(self):
        if self.bb_workspace_input:
            self.bb_workspace_input.setFocus()
            self.bb_workspace_input.selectAll()

    def focus_github_owner(self):
        if self.github_owner_input:
            self.github_owner_input.setFocus()
            self.github_owner_input.selectAll()

    def focus_github_token(self):
        if self.github_token_input:
            self.github_token_input.setFocus()
            self.github_token_input.selectAll()

    def _focus_provider_config(self, provider: str):
        if provider == "bitbucket":
            if not (self.config.get_bitbucket_username() or "").strip() or not (
                self.config.get_bitbucket_api_token() or ""
            ).strip():
                self.focus_bitbucket_credentials()
            else:
                self.focus_bitbucket_workspace()
            return

        if not (self.config.get_github_token() or "").strip():
            self.focus_github_token()
        else:
            self.focus_github_owner()

    def _filter_repository_list(self):
        query = (self.repo_search_input.text() or "").strip().lower()
        for i in range(self.repo_list.count()):
            item = self.repo_list.item(i)
            label = item.text().lower()
            item.setHidden(bool(query) and query not in label)

    def _on_repo_item_changed(self, item):
        repo = item.data(Qt.UserRole) or {}
        repo_id = str(repo.get("id", ""))
        if not repo_id:
            return

        if item.checkState() == Qt.Checked:
            self._selected_repo_map[repo_id] = repo
        else:
            self._selected_repo_map.pop(repo_id, None)
        self._sync_selected_repo_panel()

    def _sync_selected_repo_panel(self):
        selected = list(self._selected_repo_map.values())
        self.selected_repo_list.clear()
        for repo in selected:
            label = f"{repo.get('owner', '')}/{repo.get('slug', repo.get('name', ''))}"
            self.selected_repo_list.addItem(label)

    def _clear_checked_repositories(self):
        self.clear_pending_repository_selection()
