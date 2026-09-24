import json
import os
import shutil
import tempfile
import time
from typing import Any, Optional

from PyQt5.QtCore import QSettings


class ConfigManager:
    """Central storage for provider settings and per-repository preferences."""

    ORG_NAME = "RepoLens"
    APP_NAME = "RepoLens"
    OLD_ORG_NAME = "Git" + "Diff" + "Extractor"
    OLD_APP_NAME = "Git" + "Diff" + "Extractor"
    LEGACY_CONFIG_FILE = "repolens_default_config.json"
    OLD_LEGACY_CONFIG_FILES = ("diff" + "_extractor_default_config.json",)
    REPO_CONFIG_FILE = ".repolens.json"
    OLD_REPO_CONFIG_FILES = (".git_" + "diff" + "_extractor.json",)
    DEFAULT_MANAGED_REPO_ROOT = os.path.join(os.path.expanduser("~"), ".repolens", "repos")
    OLD_MANAGED_REPO_ROOT = os.path.join(os.path.expanduser("~"), ".git" + "diff" + "extractor", "repos")

    DEFAULT_REPO_CONFIG = {
        "last_repo_dir": "",
        "last_output_dir": "",
        "origin_branch": "",
        "commit_hashes": "",
        "pr_title": "",
        "pr_description": "",
        "source_branch": "",
        "target_branch": "",
    }

    DEFAULT_PROVIDER_CONFIG = {
        "provider": "bitbucket",
        "bitbucket_username": "",
        # API tokens replaced Bitbucket app passwords in 2026.  Keep the old
        # setting only so existing installations can be migrated safely.
        "bitbucket_api_token": "",
        "bitbucket_app_password": "",
        "bitbucket_workspace": "",
        "github_owner": "",
        "github_repo": "",
        "github_token": "",
        "active_repo_provider": "",
        "active_repo_id": "",
        "active_repo_name": "",
        "active_repo_owner": "",
        "active_repo_slug": "",
        "active_repo_clone_url": "",
        "active_repo_html_url": "",
        "active_repo_local_dir": "",
        "selected_repositories_json": "[]",
        "repo_discovery_cache_json": "{}",
        "contribution_history_state_json": "{}",
        "open_in_editor": "true",
        "copy_to_clipboard": "false",
        "copy_open_ai": "false",
        "editor_app_path": "",
        "ai_targets_json": "{\"openai\": true, \"claude\": false, \"gemini\": false, \"grok\": false}",
        "ai_custom_links_json": "[]",
        "ai_copy_with_prompt": "false",
        "ai_prompt_text": "Review this diff and provide concise feedback:",
        "diff_font_size": "13",
        "theme": "Midnight",
    }
    LEGACY_REPO_PROVIDER_KEYS = (
        "provider",
        "bitbucket_username",
        "bitbucket_app_password",
        "bitbucket_workspace",
        "repo_slug",
        "github_owner",
        "github_repo",
        "github_token",
    )

    def __init__(self):
        self.settings = QSettings(self.ORG_NAME, self.APP_NAME)
        self._migrate_qsettings_namespace()
        self._rewrite_managed_repo_paths_in_settings()
        self.current_repo = self.settings.value("last_repo_dir", "", str)
        self.repo_config = dict(self.DEFAULT_REPO_CONFIG)

        # Support migrating values from the legacy JSON file on first run
        self._migrate_legacy_settings()

        if self.current_repo:
            self._load_repo_config(self.current_repo)

    # ------------------------------------------------------------------
    # Internal helpers
    def _migrate_qsettings_namespace(self) -> None:
        """Copy settings from the pre-RepoLens namespace on first run."""
        if (
            self.settings.value("_namespace_migrated", False, bool)
            or (self.ORG_NAME, self.APP_NAME) == (self.OLD_ORG_NAME, self.OLD_APP_NAME)
        ):
            return

        old_settings = QSettings(self.OLD_ORG_NAME, self.OLD_APP_NAME)
        old_keys = old_settings.allKeys()
        if old_keys:
            current_keys = set(self.settings.allKeys())
            for key in old_keys:
                if key not in current_keys:
                    self.settings.setValue(key, old_settings.value(key))

        self.settings.setValue("_namespace_migrated", True)

    def _rewrite_managed_path(self, value: Any) -> str:
        path = str(value or "")
        if not path:
            return ""
        old_root = os.path.normpath(self.OLD_MANAGED_REPO_ROOT)
        new_root = os.path.normpath(self.DEFAULT_MANAGED_REPO_ROOT)
        normalized = os.path.normpath(path)
        if normalized == old_root or normalized.startswith(old_root + os.sep):
            suffix = normalized[len(old_root):].lstrip(os.sep)
            return os.path.join(new_root, suffix) if suffix else new_root
        return path

    def _rewrite_repo_payload_paths(self, repositories: list) -> list:
        rewritten = []
        for repo in repositories:
            if isinstance(repo, dict):
                repo = dict(repo)
                repo["local_dir"] = self._rewrite_managed_path(repo.get("local_dir", ""))
            rewritten.append(repo)
        return rewritten

    def _rewrite_managed_repo_paths_in_settings(self) -> None:
        """Point migrated settings at the RepoLens-managed checkout location."""
        for key in ("last_repo_dir", "active_repo_local_dir", "managed_repo_root"):
            current = self.settings.value(key, "", str)
            rewritten = self._rewrite_managed_path(current)
            if rewritten != current:
                self.settings.setValue(key, rewritten)

        raw = self.settings.value("selected_repositories_json", "[]", str)
        try:
            repositories = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            repositories = []
        if isinstance(repositories, list):
            rewritten = self._rewrite_repo_payload_paths(repositories)
            if rewritten != repositories:
                self.settings.setValue("selected_repositories_json", json.dumps(rewritten))

    def _migrate_legacy_settings(self) -> None:
        """Import values from the previous JSON configuration if present."""
        if self.settings.value("_legacy_migrated", False, bool):
            return

        legacy_path = self.LEGACY_CONFIG_FILE
        if not os.path.exists(legacy_path):
            legacy_path = next(
                (path for path in self.OLD_LEGACY_CONFIG_FILES if os.path.exists(path)),
                "",
            )

        if not legacy_path:
            self.settings.setValue("_legacy_migrated", True)
            return

        try:
            with open(legacy_path, "r", encoding="utf-8") as legacy_file:
                legacy_data = json.load(legacy_file)
        except Exception as exc:  # noqa: BLE001 - best effort migration
            print(f"Error migrating legacy config: {exc}")
            self.settings.setValue("_legacy_migrated", True)
            return

        if isinstance(legacy_data, dict):
            repo_dir = legacy_data.get("last_repo_dir", "")
            output_dir = legacy_data.get("last_output_dir", "")
            self.settings.setValue("last_repo_dir", repo_dir)
            self.settings.setValue("last_output_dir", output_dir)
            self.current_repo = repo_dir

            # Migrate provider fields to global settings
            for key in (
                "provider",
                "bitbucket_username",
                "bitbucket_app_password",
                "bitbucket_api_token",
                "bitbucket_workspace",
                "github_owner",
                "github_repo",
                "github_token",
            ):
                if key in legacy_data:
                    self.settings.setValue(key, legacy_data.get(key, ""))

            if repo_dir:
                self.repo_config = dict(self.DEFAULT_REPO_CONFIG)
                for key, value in legacy_data.items():
                    if key in self.repo_config:
                        self.repo_config[key] = value
                self.repo_config["last_repo_dir"] = repo_dir
                self._save_repo_config(repo_dir)

        self.settings.setValue("_legacy_migrated", True)

    def _repo_config_path(self, repo_dir: str) -> str:
        return os.path.join(repo_dir, self.REPO_CONFIG_FILE)

    def _legacy_repo_config_path(self, repo_dir: str) -> str:
        for filename in self.OLD_REPO_CONFIG_FILES:
            path = os.path.join(repo_dir, filename)
            if os.path.exists(path):
                return path
        return ""

    def _load_repo_config(self, repo_dir: str) -> None:
        """Load per-repository configuration if available."""
        self.repo_config = dict(self.DEFAULT_REPO_CONFIG)

        if not repo_dir:
            return

        config_path = self._repo_config_path(repo_dir)
        loaded_legacy_path = ""
        if not os.path.exists(config_path):
            loaded_legacy_path = self._legacy_repo_config_path(repo_dir)
            if loaded_legacy_path:
                config_path = loaded_legacy_path

        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as repo_file:
                    data = json.load(repo_file)
                    if isinstance(data, dict):
                        had_legacy_provider_keys = self._migrate_repo_scoped_provider_values(data)
                        self.repo_config.update(
                            {
                                k: data.get(k, v)
                                for k, v in self.DEFAULT_REPO_CONFIG.items()
                            }
                        )
                        if had_legacy_provider_keys or loaded_legacy_path:
                            # Persist a cleaned RepoLens config without provider credentials.
                            self.repo_config["last_repo_dir"] = repo_dir
                            self._save_repo_config(repo_dir)
            except Exception as exc:  # noqa: BLE001 - prefer resilience
                print(f"Error reading repo config '{config_path}': {exc}")
        else:
            # Initialize an empty config for the repository
            self.repo_config["last_repo_dir"] = repo_dir
            self._save_repo_config(repo_dir)

        self.repo_config["last_repo_dir"] = repo_dir

    def _save_repo_config(self, repo_dir: Optional[str] = None) -> None:
        """Persist the current repository configuration to disk."""
        repo_dir = repo_dir or self.current_repo
        if not repo_dir or not os.path.isdir(repo_dir):
            return

        config_path = self._repo_config_path(repo_dir)
        config_dir = os.path.dirname(config_path)

        try:
            if config_dir and not os.path.exists(config_dir):
                os.makedirs(config_dir)

            with tempfile.NamedTemporaryFile(
                "w", delete=False, dir=config_dir or None, encoding="utf-8"
            ) as tmp_file:
                json.dump(self.repo_config, tmp_file, indent=4)
                temp_path = tmp_file.name

            shutil.move(temp_path, config_path)
        except Exception as exc:  # noqa: BLE001 - avoid hard failure
            print(f"Error saving repo config '{config_path}': {exc}")

    def _update_repo_value(self, key: str, value: Any) -> None:
        if key in self.repo_config:
            self.repo_config[key] = value
            self._save_repo_config()

    def _migrate_repo_scoped_provider_values(self, data: dict) -> bool:
        """
        Move legacy provider credentials/config from repo-level config to global settings.
        Returns True when repo data contained legacy provider keys and should be cleaned.
        """
        migrated = False
        for key in self.LEGACY_REPO_PROVIDER_KEYS:
            if key not in data:
                continue
            migrated = True
            current_value = (self._get_global(key) or "").strip()
            legacy_value = str(data.get(key, "") or "").strip()
            if not current_value and legacy_value:
                self._set_global(key, legacy_value)
        return migrated

    def _get_global(self, key: str) -> str:
        default_value = self.DEFAULT_PROVIDER_CONFIG.get(key, "")
        return self.settings.value(key, default_value, str)

    def _set_global(self, key: str, value: str) -> None:
        self.settings.setValue(key, value or "")

    # ------------------------------------------------------------------
    # Global values (stored in QSettings)
    def get_repo_dir(self) -> str:
        return self.settings.value("last_repo_dir", "", str)

    def set_repo_dir(self, repo_dir: str) -> None:
        repo_dir = repo_dir or ""
        self.settings.setValue("last_repo_dir", repo_dir)
        self._set_global("active_repo_local_dir", repo_dir)
        if repo_dir != self.current_repo:
            self.current_repo = repo_dir
            self._load_repo_config(repo_dir)

    def get_output_dir(self) -> str:
        repo_value = self.repo_config.get("last_output_dir", "")
        if repo_value:
            return repo_value
        return self.settings.value("last_output_dir", "", str)

    def set_output_dir(self, output_dir: str) -> None:
        self.settings.setValue("last_output_dir", output_dir)
        self._update_repo_value("last_output_dir", output_dir)

    # ------------------------------------------------------------------
    # Repository specific values
    def get_origin_branch(self) -> str:
        return self.repo_config.get("origin_branch", "")

    def set_origin_branch(self, origin_branch: str) -> None:
        self._update_repo_value("origin_branch", origin_branch)

    def get_commit_hashes(self) -> str:
        return self.repo_config.get("commit_hashes", "")

    def set_commit_hashes(self, commit_hashes: str) -> None:
        self._update_repo_value("commit_hashes", commit_hashes)

    def get_pr_title(self) -> str:
        return self.repo_config.get("pr_title", "")

    def set_pr_title(self, title: str) -> None:
        self._update_repo_value("pr_title", title)

    def get_pr_description(self) -> str:
        return self.repo_config.get("pr_description", "")

    def set_pr_description(self, description: str) -> None:
        self._update_repo_value("pr_description", description)

    def get_source_branch(self) -> str:
        return self.repo_config.get("source_branch", "")

    def set_source_branch(self, branch: str) -> None:
        self._update_repo_value("source_branch", branch)

    def get_target_branch(self) -> str:
        return self.repo_config.get("target_branch", "")

    def set_target_branch(self, branch: str) -> None:
        self._update_repo_value("target_branch", branch)

    # ------------------------------------------------------------------
    # Provider settings (global)
    def get_provider(self) -> str:
        provider = self._get_global("provider")
        return provider or "bitbucket"

    def set_provider(self, provider: str) -> None:
        provider = (provider or "bitbucket").lower()
        self._set_global("provider", provider)

    def get_bitbucket_username(self) -> str:
        return self._get_global("bitbucket_username")

    def set_bitbucket_username(self, username: str) -> None:
        self._set_global("bitbucket_username", username)

    def get_bitbucket_app_password(self) -> str:
        """Compatibility alias for integrations built before API-token support."""
        return self.get_bitbucket_api_token()

    def set_bitbucket_app_password(self, pwd: str) -> None:
        self.set_bitbucket_api_token(pwd)

    def get_bitbucket_api_token(self) -> str:
        return self._get_global("bitbucket_api_token") or self._get_global("bitbucket_app_password")

    def set_bitbucket_api_token(self, token: str) -> None:
        self._set_global("bitbucket_api_token", token)
        # Do not retain a second copy of a newly entered secret.
        self._set_global("bitbucket_app_password", "")

    def get_bitbucket_workspace(self) -> str:
        return self._get_global("bitbucket_workspace")

    def set_bitbucket_workspace(self, workspace: str) -> None:
        self._set_global("bitbucket_workspace", workspace)

    def get_github_owner(self) -> str:
        return self._get_global("github_owner")

    def set_github_owner(self, owner: str) -> None:
        self._set_global("github_owner", owner)

    def get_github_repo(self) -> str:
        return self._get_global("github_repo")

    def set_github_repo(self, repo: str) -> None:
        self._set_global("github_repo", repo)

    def get_github_token(self) -> str:
        return self._get_global("github_token")

    def set_github_token(self, token: str) -> None:
        self._set_global("github_token", token)

    # ------------------------------------------------------------------
    # Active repository metadata (global)
    def set_active_repository(self, repo_info: dict) -> None:
        repo = repo_info or {}
        self._set_global("active_repo_provider", repo.get("provider", ""))
        self._set_global("active_repo_id", str(repo.get("id", "")))
        self._set_global("active_repo_name", repo.get("name", ""))
        self._set_global("active_repo_owner", repo.get("owner", ""))
        self._set_global("active_repo_slug", repo.get("slug", ""))
        self._set_global("active_repo_clone_url", repo.get("clone_url", ""))
        self._set_global("active_repo_html_url", repo.get("html_url", ""))
        self._set_global("active_repo_local_dir", repo.get("local_dir", ""))

        local_dir = repo.get("local_dir", "")
        if local_dir:
            self.set_repo_dir(local_dir)

    def get_active_repository(self) -> dict:
        return {
            "provider": self._get_global("active_repo_provider"),
            "id": self._get_global("active_repo_id"),
            "name": self._get_global("active_repo_name"),
            "owner": self._get_global("active_repo_owner"),
            "slug": self._get_global("active_repo_slug"),
            "clone_url": self._get_global("active_repo_clone_url"),
            "html_url": self._get_global("active_repo_html_url"),
            "local_dir": self._get_global("active_repo_local_dir"),
        }

    def clear_active_repository(self) -> None:
        self.set_active_repository({})
        self.set_repo_dir("")

    def get_selected_repositories(self) -> list:
        raw = self._get_global("selected_repositories_json")
        try:
            parsed = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def set_selected_repositories(self, repositories: list) -> None:
        payload = repositories if isinstance(repositories, list) else []
        self._set_global("selected_repositories_json", json.dumps(payload))

    def _get_discovery_cache(self) -> dict:
        raw = self._get_global("repo_discovery_cache_json")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _set_discovery_cache(self, payload: dict) -> None:
        payload = payload if isinstance(payload, dict) else {}
        self._set_global("repo_discovery_cache_json", json.dumps(payload))

    @staticmethod
    def _provider_cache_key(provider: str, context_key: str) -> str:
        return f"{(provider or '').lower()}::{context_key or ''}"

    def get_cached_discovered_repositories(self, provider: str, context_key: str) -> tuple:
        cache = self._get_discovery_cache()
        key = self._provider_cache_key(provider, context_key)
        entry = cache.get(key) or {}
        if not isinstance(entry, dict):
            return [], 0.0
        repos = entry.get("repositories")
        timestamp = entry.get("timestamp")
        if not isinstance(repos, list):
            repos = []
        try:
            ts = float(timestamp or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        return repos, ts

    def set_cached_discovered_repositories(self, provider: str, context_key: str, repositories: list) -> None:
        cache = self._get_discovery_cache()
        key = self._provider_cache_key(provider, context_key)
        cache[key] = {
            "timestamp": time.time(),
            "repositories": repositories if isinstance(repositories, list) else [],
        }
        self._set_discovery_cache(cache)

    def get_contribution_history_state(self) -> dict:
        raw = self._get_global("contribution_history_state_json")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def set_contribution_history_state(self, state: dict) -> None:
        payload = state if isinstance(state, dict) else {}
        self._set_global("contribution_history_state_json", json.dumps(payload))

    def get_open_in_editor(self) -> bool:
        return self._get_global("open_in_editor").lower() == "true"

    def set_open_in_editor(self, enabled: bool) -> None:
        self._set_global("open_in_editor", "true" if enabled else "false")

    def get_copy_to_clipboard(self) -> bool:
        return self._get_global("copy_to_clipboard").lower() == "true"

    def set_copy_to_clipboard(self, enabled: bool) -> None:
        self._set_global("copy_to_clipboard", "true" if enabled else "false")

    def get_copy_open_ai(self) -> bool:
        return self._get_global("copy_open_ai").lower() == "true"

    def set_copy_open_ai(self, enabled: bool) -> None:
        self._set_global("copy_open_ai", "true" if enabled else "false")

    def get_editor_app_path(self) -> str:
        return self._get_global("editor_app_path")

    def set_editor_app_path(self, value: str) -> None:
        self._set_global("editor_app_path", value)

    def get_ai_targets(self) -> dict:
        raw = self._get_global("ai_targets_json")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        return {
            "openai": bool(parsed.get("openai", True)),
            "claude": bool(parsed.get("claude", False)),
            "gemini": bool(parsed.get("gemini", False)),
            "grok": bool(parsed.get("grok", False)),
        }

    def set_ai_targets(self, targets: dict) -> None:
        payload = {
            "openai": bool((targets or {}).get("openai", False)),
            "claude": bool((targets or {}).get("claude", False)),
            "gemini": bool((targets or {}).get("gemini", False)),
            "grok": bool((targets or {}).get("grok", False)),
        }
        self._set_global("ai_targets_json", json.dumps(payload))

    def get_ai_custom_links(self) -> list[str]:
        raw = self._get_global("ai_custom_links_json")
        try:
            parsed = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            parsed = []
        if not isinstance(parsed, list):
            return []
        return [str(item).strip() for item in parsed if str(item).strip()]

    def set_ai_custom_links(self, links: list[str]) -> None:
        payload = [str(item).strip() for item in (links or []) if str(item).strip()]
        self._set_global("ai_custom_links_json", json.dumps(payload))

    def get_ai_copy_with_prompt(self) -> bool:
        return self._get_global("ai_copy_with_prompt").lower() == "true"

    def set_ai_copy_with_prompt(self, enabled: bool) -> None:
        self._set_global("ai_copy_with_prompt", "true" if enabled else "false")

    def get_ai_prompt_text(self) -> str:
        return self._get_global("ai_prompt_text")

    def set_ai_prompt_text(self, text: str) -> None:
        self._set_global("ai_prompt_text", text or "")

    def get_diff_font_size(self) -> int:
        try:
            return int(self._get_global("diff_font_size") or "13")
        except (TypeError, ValueError):
            return 13

    def set_diff_font_size(self, size: int) -> None:
        clamped = max(10, min(24, size))
        self._set_global("diff_font_size", str(clamped))

    def get_theme(self) -> str:
        theme = self._get_global("theme")
        return theme if theme in ("Midnight", "Aurora", "Forest", "Classic") else "Midnight"

    def set_theme(self, theme: str) -> None:
        if theme not in ("Midnight", "Aurora", "Forest", "Classic"):
            raise ValueError(f"Unknown theme: {theme}")
        self._set_global("theme", theme)

    def get_managed_repo_root(self) -> str:
        stored = self.settings.value("managed_repo_root", "", str)
        if stored:
            rewritten = self._rewrite_managed_path(stored)
            if rewritten != stored:
                self.settings.setValue("managed_repo_root", rewritten)
            return rewritten
        return self.DEFAULT_MANAGED_REPO_ROOT

    def set_managed_repo_root(self, path: str) -> None:
        self.settings.setValue("managed_repo_root", path or "")

    # ------------------------------------------------------------------
    # Compatibility helpers
    def get_repo_slug(self) -> str:
        active_slug = self._get_global("active_repo_slug")
        if active_slug:
            return active_slug
        return self.get_github_repo() if self.get_provider() == "github" else ""

    def set_repo_slug(self, slug: str) -> None:
        slug = (slug or "").strip()
        provider = self.get_provider()
        if provider == "github":
            self.set_github_repo(slug)
        self._set_global("active_repo_slug", slug)
