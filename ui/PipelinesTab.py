"""Desktop pipeline launcher and run viewer."""

from __future__ import annotations

import webbrowser

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox,
                             QPushButton, QLabel, QTableWidget, QTableWidgetItem, QLineEdit,
                             QCheckBox, QMessageBox, QListWidget, QListWidgetItem, QPlainTextEdit,
                             QGroupBox, QSplitter, QProgressBar, QHeaderView)

from services.pipeline_service import PipelineService
from ui.TaskRunner import TaskRunner
from ui.SearchableComboBox import SearchableComboBox
from services.RepositoryProvider import RepositoryProvider


class PipelinesTab(QWidget):
    def __init__(self, config, task_runner=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.runner = task_runner or TaskRunner(self)
        self.service = PipelineService(config)
        self._request = 0
        self._branch_request = 0
        self._run_request = 0
        self._runs_request = 0
        self._log_request = 0
        self._dispatching = False
        self._current_run = ""
        self._current_url = ""
        self._loading = False
        self._auto_discovery_attempted = set()
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(self.refresh_run)
        self.refresh_repositories()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        heading = QLabel("Pipelines", self)
        heading.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(heading)
        layout.addWidget(QLabel("Choose a repository and remote branch, then review the run before starting it.", self))

        launcher = QGroupBox("Start a run", self)
        launcher_layout = QVBoxLayout(launcher)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(10)
        self.repo_combo = SearchableComboBox(self, search_placeholder="Search repositories by owner or name...")
        self.repo_combo.setMinimumWidth(480)
        self.repo_combo.setMinimumHeight(34)
        self.repo_combo.currentIndexChanged.connect(self._repository_changed)
        repo_row = QHBoxLayout()
        repo_row.addWidget(self.repo_combo, 1)
        self.repo_refresh_btn = QPushButton("Refresh repositories", self)
        self.repo_refresh_btn.clicked.connect(self.discover_repositories)
        repo_row.addWidget(self.repo_refresh_btn)
        form.addRow("Repository", repo_row)
        self.branch_combo = SearchableComboBox(self, search_placeholder="Search remote branches...")
        self.branch_combo.setMinimumWidth(480)
        self.branch_combo.setMinimumHeight(34)
        self.branch_combo.currentIndexChanged.connect(self._branch_changed)
        branch_row = QHBoxLayout()
        branch_row.addWidget(self.branch_combo, 1)
        self.branch_state = QLabel("", self)
        self.branch_state.setMinimumWidth(110)
        branch_row.addWidget(self.branch_state)
        form.addRow("Remote branch", branch_row)
        self.pipeline_combo = SearchableComboBox(self, search_placeholder="Search runnable pipelines...")
        self.pipeline_combo.setMinimumWidth(480)
        self.pipeline_combo.setMinimumHeight(34)
        self.pipeline_combo.currentIndexChanged.connect(self._pipeline_changed)
        form.addRow("Pipeline / workflow", self.pipeline_combo)
        launcher_layout.addLayout(form)

        status_row = QHBoxLayout()
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #93c5fd; font-weight: 600;")
        status_row.addWidget(self.status, 1)
        self.retry_btn = QPushButton("Retry loading", self)
        self.retry_btn.clicked.connect(self._repository_changed)
        self.retry_btn.hide()
        status_row.addWidget(self.retry_btn)
        launcher_layout.addLayout(status_row)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.hide()
        launcher_layout.addWidget(self.progress)

        launcher_layout.addWidget(QLabel("Parameters", self))

        self.parameters = QTableWidget(0, 2, self)
        self.parameters.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.parameters.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.parameters.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.parameters.setMinimumHeight(130)
        self.parameters.setMaximumHeight(200)
        launcher_layout.addWidget(self.parameters)
        row = QHBoxLayout()
        self.add_param_btn = QPushButton("Add variable", self)
        self.add_param_btn.clicked.connect(lambda: self._add_parameter("", ""))
        row.addWidget(self.add_param_btn)
        self.run_btn = QPushButton("Review and run", self)
        self.run_btn.clicked.connect(self.run_pipeline)
        row.addWidget(self.run_btn)
        row.addStretch()
        launcher_layout.addLayout(row)
        preference_row = QHBoxLayout()
        self.remember_values = QCheckBox("Remember values for this pipeline and branch", self)
        self.remember_values.setChecked(False)
        preference_row.addWidget(self.remember_values)
        self.forget_values_btn = QPushButton("Forget saved values", self)
        self.forget_values_btn.clicked.connect(self._forget_values)
        self.forget_values_btn.setEnabled(False)
        preference_row.addWidget(self.forget_values_btn)
        preference_row.addStretch()
        launcher_layout.addLayout(preference_row)
        note = QLabel("Use provider-managed secrets for sensitive values. Remembered values are stored locally in RepoLens settings.", self)
        note.setWordWrap(True)
        launcher_layout.addWidget(note)
        layout.addWidget(launcher)

        history = QGroupBox("Runs and logs", self)
        history_layout = QVBoxLayout(history)
        run_row = QHBoxLayout()
        run_row.addWidget(QLabel("Recent runs", self))
        self.refresh_btn = QPushButton("Refresh", self)
        self.refresh_btn.clicked.connect(self.refresh_runs)
        run_row.addWidget(self.refresh_btn)
        self.open_btn = QPushButton("Open on provider", self)
        self.open_btn.clicked.connect(lambda: webbrowser.open(self._current_url) if self._current_url else None)
        self.open_btn.setEnabled(False)
        run_row.addWidget(self.open_btn)
        run_row.addStretch()
        history_layout.addLayout(run_row)
        viewer = QSplitter(Qt.Vertical, self)
        self.runs_list = QListWidget(self)
        self.runs_list.currentItemChanged.connect(self._run_selected)
        viewer.addWidget(self.runs_list)
        self.run_status = QLabel("Select a run to view its status and jobs.", self)
        history_layout.addWidget(self.run_status)
        self.steps_list = QListWidget(self)
        self.steps_list.currentItemChanged.connect(self._step_selected)
        viewer.addWidget(self.steps_list)
        self.log_text = QPlainTextEdit(self)
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("Select a step or job to view its log.")
        viewer.addWidget(self.log_text)
        viewer.setSizes([130, 100, 220])
        history_layout.addWidget(viewer, 1)
        layout.addWidget(history, 1)

    def _set_status(self, message, *, busy=False, error=False):
        self.status.setText(message)
        self.status.setStyleSheet(f"color: {'#fb7185' if error else '#93c5fd'}; font-weight: 600;")
        self.progress.setVisible(busy)
        self.retry_btn.setVisible(error)
        self._loading = busy
        self.run_btn.setEnabled(not busy and not self._dispatching and isinstance(self.pipeline_combo.currentData(), dict))

    def _clear_run_view(self):
        self._run_request += 1
        self._log_request += 1
        self._current_run = ""
        self._current_url = ""
        if hasattr(self, "timer"):
            self.timer.stop()
        self.steps_list.clear()
        self.log_text.clear()
        self.open_btn.setEnabled(False)
        self.run_status.setText("Select a run to view its status and jobs.")

    def refresh_repositories(self, preferred=None, *, extra=None, force=False):
        current = preferred or self.repo_combo.currentData()
        repos = list(self.config.get_selected_repositories() or [])
        active = self.config.get_active_repository() or {}
        if active.get("slug"):
            repos.insert(0, active)
        provider = (self.config.get_provider() or "").lower() if hasattr(self.config, "get_provider") else ""
        try:
            valid, _, provider_config = RepositoryProvider.validate_provider_config(provider, self.config)
            if valid:
                context = RepositoryProvider.discovery_context_key(provider, provider_config)
                cached, _ = self.config.get_cached_discovered_repositories(provider, context)
                repos.extend(cached or [])
        except (AttributeError, ValueError, TypeError):
            pass
        repos.extend(extra or [])
        unique = {}
        for repo in repos:
            if repo and repo.get("owner") and repo.get("slug"):
                unique[self._repo_key(repo)] = repo
        repos = list(unique.values())
        if preferred and self._repo_key(preferred) not in unique:
            repos.insert(0, preferred)
        previous = self._repo_key(self.repo_combo.currentData())
        self.repo_combo.blockSignals(True)
        self.repo_combo.clear()
        if not repos:
            self.repo_combo.addItem("No repositories found — refresh or configure Settings", None)
        for repo in repos:
            self.repo_combo.addItem(f"{repo.get('owner')}/{repo.get('slug')}", repo)
            index = self.repo_combo.count() - 1
            self.repo_combo.setItemData(index, f"{repo.get('name') or ''} {repo.get('provider') or ''}", Qt.UserRole + 1)
        if current:
            for index in range(self.repo_combo.count()):
                if self._repo_key(self.repo_combo.itemData(index)) == self._repo_key(current):
                    self.repo_combo.setCurrentIndex(index)
                    break
        self.repo_combo.blockSignals(False)
        selected = self.repo_combo.currentData()
        if not selected:
            self.branch_combo.clear()
            self.pipeline_combo.clear()
            self._set_status("No repository is available. Configure provider credentials in Settings, then click Refresh repositories.")
            if provider and provider not in self._auto_discovery_attempted:
                self._auto_discovery_attempted.add(provider)
                self.discover_repositories()
            return
        if force or previous != self._repo_key(selected) or getattr(self, "_pending_branch", ""):
            self._repository_changed()

    def discover_repositories(self):
        provider = (self.config.get_provider() or "").lower()
        valid, message, provider_config = RepositoryProvider.validate_provider_config(provider, self.config)
        if not valid:
            self._set_status(message or "Configure provider credentials in Settings.", error=True)
            return
        self.repo_refresh_btn.setEnabled(False)
        self._set_status("Discovering repositories from the provider...", busy=True)
        def loaded(repos):
            self.repo_refresh_btn.setEnabled(True)
            try:
                context = RepositoryProvider.discovery_context_key(provider, provider_config)
                self.config.set_cached_discovered_repositories(provider, context, repos)
            except AttributeError:
                pass
            self._set_status(f"Found {len(repos)} repositories. Choose one to load branches.")
            self.refresh_repositories(extra=repos)
        def failed(exc):
            self.repo_refresh_btn.setEnabled(True)
            self._set_status(f"Could not discover repositories: {exc}", error=True)
        self.runner.run(RepositoryProvider.discover_repositories, provider, provider_config,
                        description="Discover pipeline repositories", on_result=loaded, on_error=failed)

    @staticmethod
    def _repo_key(repo):
        repo = repo or {}
        return (repo.get("provider"), repo.get("owner"), repo.get("slug"))

    def select_context(self, repo, branch):
        self._pending_branch = branch or ""
        self.refresh_repositories(preferred=repo, force=True)

    def _repository_changed(self, *_):
        self._request += 1
        self._branch_request += 1
        token = self._branch_request
        repo = self.repo_combo.currentData()
        self.branch_combo.blockSignals(True)
        self.branch_combo.clear()
        preferred = getattr(self, "_pending_branch", "") or (repo or {}).get("default_branch") or ""
        self._pending_branch = ""
        if preferred:
            self.branch_combo.addItem(preferred, preferred)
        else:
            self.branch_combo.addItem("Loading remote branches...", None)
        self.branch_combo.blockSignals(False)
        self.branch_combo.setEnabled(bool(preferred))
        self.branch_state.setText("Loading list...")
        self.pipeline_combo.blockSignals(True)
        self.pipeline_combo.clear()
        self.pipeline_combo.addItem("Choose a branch first", None)
        self.pipeline_combo.blockSignals(False)
        self.pipeline_combo.setEnabled(False)
        self.runs_list.clear()
        self._clear_run_view()
        if not repo:
            return
        self._set_status(f"Loading remote branches for {repo.get('owner')}/{repo.get('slug')}...", busy=True)
        def loaded(branches):
            if token != self._branch_request:
                return
            current = self.branch_combo.currentData()
            self.branch_combo.blockSignals(True)
            self.branch_combo.clear()
            if current and current not in branches:
                self.branch_combo.addItem(current, current)
            if not branches:
                self.branch_combo.addItem("No remote branches found", None)
            for branch in branches:
                self.branch_combo.addItem(branch, branch)
            if current:
                index = self.branch_combo.findText(current)
                if index >= 0:
                    self.branch_combo.setCurrentIndex(index)
            self.branch_combo.blockSignals(False)
            self.branch_combo.setEnabled(bool(branches or current))
            self.branch_state.setText(f"{len(branches)} branches")
            if not current and branches:
                self._branch_changed()
            elif not branches and not current:
                self._set_status("No remote branches found for this repository.")
        self.runner.run(self.service.list_branches, repo, description="Load pipeline branches",
                        on_result=loaded, on_error=lambda exc: self._branch_error(token, exc))
        if preferred:
            self._branch_changed()

    def _branch_changed(self, *_):
        self._clear_run_view()
        self._request += 1
        token = self._request
        repo, branch = self.repo_combo.currentData(), self.branch_combo.currentData()
        self.pipeline_combo.blockSignals(True)
        self.pipeline_combo.clear()
        self.pipeline_combo.addItem("Loading runnable pipelines...", None)
        self.pipeline_combo.blockSignals(False)
        self.pipeline_combo.setEnabled(False)
        self.parameters.setRowCount(0)
        if not repo or not branch:
            return
        self._set_status(f"Reading pipeline definitions on {branch}. This may take a moment...", busy=True)
        def loaded(pipelines):
            if token != self._request:
                return
            self.pipeline_combo.blockSignals(True)
            self.pipeline_combo.clear()
            if not pipelines:
                self.pipeline_combo.addItem("No manually runnable pipelines found", None)
            for pipeline in pipelines:
                self.pipeline_combo.addItem(pipeline["name"], pipeline)
            self.pipeline_combo.blockSignals(False)
            self.pipeline_combo.setEnabled(bool(pipelines))
            self._pipeline_changed()
            self._set_status(f"{len(pipelines)} runnable pipeline(s) found on {branch}." if pipelines
                             else "No runnable pipeline is defined on this branch. Check the repository's pipeline configuration.")
        self.runner.run(self.service.list_pipelines, repo, branch, description="Load pipelines",
                        on_result=loaded, on_error=lambda exc: self._error(token, exc))
        self.refresh_runs()

    def _pipeline_changed(self, *_):
        self._clear_run_view()
        pipeline = self.pipeline_combo.currentData() or {}
        self._load_parameter_form()
        self.add_param_btn.setEnabled(pipeline.get("kind") == "custom")
        self.run_btn.setEnabled(bool(pipeline) and not self._loading and not self._dispatching)
        self.refresh_runs()

    def _load_parameter_form(self):
        self.parameters.setRowCount(0)
        repo = self.repo_combo.currentData()
        branch = self.branch_combo.currentData()
        pipeline = self.pipeline_combo.currentData() or {}
        saved = self.config.get_pipeline_parameters(repo, branch, pipeline["id"]) if repo and branch and pipeline else {}
        known = set()
        for field in pipeline.get("inputs", []):
            name = field.get("name") or ""
            if not name:
                continue
            known.add(name)
            value = saved.get(name, field.get("default"))
            if field.get("options") and value not in field["options"]:
                value = field.get("default")
            self._add_parameter(name, value, field)
        if pipeline.get("kind") == "custom":
            for name, value in saved.items():
                if name not in known:
                    self._add_parameter(name, value)
        self.remember_values.setChecked(bool(saved))
        self.remember_values.setEnabled(bool(pipeline))
        self.forget_values_btn.setEnabled(bool(saved))

    def _forget_values(self):
        repo = self.repo_combo.currentData()
        branch = self.branch_combo.currentData()
        pipeline = self.pipeline_combo.currentData()
        if repo and branch and pipeline:
            self.config.clear_pipeline_parameters(repo, branch, pipeline["id"])
            self._load_parameter_form()

    def _add_parameter(self, name, value, field=None):
        row = self.parameters.rowCount()
        self.parameters.insertRow(row)
        key = QTableWidgetItem(name)
        if field:
            key.setFlags(key.flags() & ~Qt.ItemIsEditable)
        self.parameters.setItem(row, 0, key)
        field = field or {}
        kind = field.get("type")
        if kind == "boolean":
            widget = QCheckBox(self)
            widget.setChecked(str(value).lower() == "true")
        elif field.get("options"):
            widget = QComboBox(self)
            widget.addItems([str(v) for v in field["options"]])
            widget.setCurrentText(str(value) if value is not None else "")
        else:
            widget = QLineEdit(self)
            widget.setText(str(value) if value is not None else "")
            widget.setPlaceholderText(field.get("description") or "")
        self.parameters.setCellWidget(row, 1, widget)

    def _values(self):
        values = {}
        for row in range(self.parameters.rowCount()):
            key_item = self.parameters.item(row, 0)
            key = key_item.text().strip() if key_item else ""
            widget = self.parameters.cellWidget(row, 1)
            value = "true" if isinstance(widget, QCheckBox) and widget.isChecked() else (
                "false" if isinstance(widget, QCheckBox) else widget.currentText() if isinstance(widget, QComboBox) else widget.text())
            if key:
                if key in values:
                    raise ValueError(f"Duplicate parameter: {key}")
                values[key] = value
        return values

    def run_pipeline(self):
        if self._dispatching:
            return
        repo = self.repo_combo.currentData()
        pipeline = self.pipeline_combo.currentData()
        branch = self.branch_combo.currentData()
        if not repo or not pipeline or not branch:
            QMessageBox.warning(self, "Pipeline", "Choose a repository, branch, and pipeline.")
            return
        try:
            values = self._values()
        except ValueError as exc:
            QMessageBox.warning(self, "Pipeline", str(exc))
            return
        summary = f"Run {pipeline['name']} in {repo.get('owner')}/{repo.get('slug')} on {branch}?"
        if values:
            summary += "\n\nParameters:\n" + "\n".join(f"{k} = {v}" for k, v in values.items())
        if QMessageBox.question(self, "Review pipeline run", summary, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        remember = self.remember_values.isChecked()
        context = (self._repo_key(repo), branch, pipeline["id"], self._request)
        def still_selected():
            return context == (self._repo_key(self.repo_combo.currentData()), self.branch_combo.currentData(),
                               (self.pipeline_combo.currentData() or {}).get("id"), self._request)
        self._dispatching = True
        self._set_status("Starting pipeline...", busy=True)
        def launched(result):
            self._dispatching = False
            if remember:
                self.config.set_pipeline_parameters(repo, branch, pipeline["id"], values)
                if (self._repo_key(self.repo_combo.currentData()) == self._repo_key(repo)
                        and self.branch_combo.currentData() == branch
                        and (self.pipeline_combo.currentData() or {}).get("id") == pipeline["id"]):
                    self.forget_values_btn.setEnabled(bool(values))
            if not still_selected():
                self.run_btn.setEnabled(not self._loading and isinstance(self.pipeline_combo.currentData(), dict))
                return
            self._set_status(f"Pipeline started. Run ID: {result['run_id'] or 'pending'}")
            self._current_url = result.get("url") or ""
            self.open_btn.setEnabled(bool(self._current_url))
            if result["run_id"]:
                self._current_run = result["run_id"]
                self.refresh_run()
            self.refresh_runs()
        def failed(exc):
            self._dispatching = False
            if not still_selected():
                self.run_btn.setEnabled(not self._loading and isinstance(self.pipeline_combo.currentData(), dict))
                return
            self._set_status(f"Could not start pipeline: {exc}", error=True)
            QMessageBox.warning(self, "Pipeline run failed", str(exc))
        self.runner.run(self.service.run_pipeline, repo, branch, pipeline["id"], kind=pipeline["kind"],
                        inputs=values, description="Start pipeline", on_result=launched, on_error=failed)

    def refresh_runs(self):
        repo = self.repo_combo.currentData()
        if not repo:
            return
        pipeline = self.pipeline_combo.currentData() or {}
        branch = self.branch_combo.currentData() or ""
        self._runs_request += 1
        token = self._runs_request
        key = (self._repo_key(repo), branch, pipeline.get("id"))
        self.runs_list.blockSignals(True)
        self.runs_list.clear()
        self.runs_list.addItem("Loading recent runs...")
        self.runs_list.blockSignals(False)
        def still_selected():
            return token == self._runs_request and key == (
                self._repo_key(self.repo_combo.currentData()), self.branch_combo.currentData() or "",
                (self.pipeline_combo.currentData() or {}).get("id"))
        def loaded(runs):
            if not still_selected():
                return
            selected = self._current_run
            self.runs_list.blockSignals(True)
            self.runs_list.clear()
            for run in runs:
                item = QListWidgetItem(f"{run['created_at']}  {run['name']}  {run['status']} {run['conclusion']}")
                item.setData(Qt.UserRole, run)
                self.runs_list.addItem(item)
                if run["run_id"] == selected:
                    self.runs_list.setCurrentItem(item)
            if not runs:
                self.runs_list.addItem("No recent runs for this selection")
            self.runs_list.blockSignals(False)
        self.runner.run(self.service.list_runs, repo, pipeline.get("id") or "", branch,
                        description="Load pipeline runs", on_result=loaded,
                        on_error=lambda exc: self.run_status.setText(f"Could not load runs: {exc}") if still_selected() else None)

    def _run_selected(self, item, _previous):
        self._clear_run_view()
        if item:
            self._current_run = (item.data(Qt.UserRole) or {}).get("run_id") or ""
            self.log_text.clear()
            self.refresh_run()

    def refresh_run(self):
        repo, run_id = self.repo_combo.currentData(), self._current_run
        if not repo or not run_id:
            return
        self._run_request += 1
        token = self._run_request
        def still_selected():
            return (token == self._run_request and run_id == self._current_run
                    and self._repo_key(repo) == self._repo_key(self.repo_combo.currentData()))
        def loaded(run):
            if not still_selected():
                return
            self.run_status.setText(f"Run {run_id}: {run['status']} {run['conclusion']}")
            self._current_url = run["url"]
            self.open_btn.setEnabled(bool(self._current_url))
            self.steps_list.blockSignals(True)
            selected = self.steps_list.currentItem().data(Qt.UserRole) if self.steps_list.currentItem() else ""
            self.steps_list.clear()
            for step in run["steps"]:
                item = QListWidgetItem(f"{step['name']}  {step['status']} {step['conclusion']}")
                item.setData(Qt.UserRole, step["id"])
                self.steps_list.addItem(item)
                if step["id"] == selected:
                    self.steps_list.setCurrentItem(item)
            self.steps_list.blockSignals(False)
            if not selected:
                self.log_text.clear()
            if run["status"].lower() in {"completed", "complete", "stopped"} or run["conclusion"]:
                self.timer.stop()
            else:
                self.timer.start()
        self.runner.run(self.service.get_run, repo, run_id, description="Load pipeline run",
                        on_result=loaded, on_error=lambda exc: self.run_status.setText(f"Could not load run: {exc}") if still_selected() else None)

    def _step_selected(self, item, _previous):
        self._log_request += 1
        token = self._log_request
        if not item or not self._current_run:
            return
        repo, run_id, step_id = self.repo_combo.currentData(), self._current_run, item.data(Qt.UserRole)
        self.log_text.setPlainText("Loading log...")
        def still_selected():
            current = self.steps_list.currentItem()
            return (token == self._log_request and current and current.data(Qt.UserRole) == step_id
                    and self._current_run == run_id and self._repo_key(repo) == self._repo_key(self.repo_combo.currentData()))
        def loaded(result):
            if still_selected():
                self.log_text.setPlainText(result["text"] + ("\n[Log truncated]" if result["truncated"] else ""))
        self.runner.run(self.service.get_log, repo, run_id, step_id, description="Load pipeline log",
                        on_result=loaded, on_error=lambda exc: self.log_text.setPlainText(f"Could not load log: {exc}") if still_selected() else None)

    def _error(self, token, exc):
        if token == self._request:
            self._set_status(f"Could not load pipelines: {exc}", error=True)

    def _branch_error(self, token, exc):
        if token != self._branch_request:
            return
        self.branch_state.setText("List unavailable")
        if not self.branch_combo.currentData():
            self._set_status(f"Could not load remote branches: {exc}", error=True)
