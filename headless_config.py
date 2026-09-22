from __future__ import annotations

import json
from typing import Any

from ConfigManager import ConfigManager


_ENV_TO_KEY = {
    "REPOLENS_PROVIDER": "provider",
    "REPOLENS_BITBUCKET_USERNAME": "bitbucket_username",
    # Backward-compatible alias. New deployments should use API_TOKEN.
    "REPOLENS_BITBUCKET_APP_PASSWORD": "bitbucket_api_token",
    "REPOLENS_BITBUCKET_API_TOKEN": "bitbucket_api_token",
    "REPOLENS_BITBUCKET_WORKSPACE": "bitbucket_workspace",
    "REPOLENS_GITHUB_OWNER": "github_owner",
    "REPOLENS_GITHUB_REPO": "github_repo",
    "REPOLENS_GITHUB_TOKEN": "github_token",
    "REPOLENS_MANAGED_REPO_ROOT": "managed_repo_root",
    "REPOLENS_SELECTED_REPOSITORIES_JSON": "selected_repositories_json",
    "REPOLENS_ACTIVE_REPO_PROVIDER": "active_repo_provider",
    "REPOLENS_ACTIVE_REPO_ID": "active_repo_id",
    "REPOLENS_ACTIVE_REPO_NAME": "active_repo_name",
    "REPOLENS_ACTIVE_REPO_OWNER": "active_repo_owner",
    "REPOLENS_ACTIVE_REPO_SLUG": "active_repo_slug",
    "REPOLENS_ACTIVE_REPO_CLONE_URL": "active_repo_clone_url",
    "REPOLENS_ACTIVE_REPO_HTML_URL": "active_repo_html_url",
    "REPOLENS_ACTIVE_REPO_LOCAL_DIR": "active_repo_local_dir",
}


class HeadlessConfig:
    def __init__(self, base_config: ConfigManager | None = None, overrides: dict[str, Any] | None = None):
        self.base_config = base_config or ConfigManager()
        raw_overrides = overrides or {}
        self.overrides = {
            str(key): value
            for key, value in raw_overrides.items()
            if value not in (None, "")
        }

    @classmethod
    def from_sources(
        cls,
        *,
        base_config: ConfigManager | None = None,
        env: dict[str, str] | None = None,
        cli_overrides: dict[str, Any] | None = None,
    ) -> "HeadlessConfig":
        env = env or {}
        overrides: dict[str, Any] = {}
        for env_key, config_key in _ENV_TO_KEY.items():
            value = env.get(env_key)
            if value not in (None, ""):
                overrides[config_key] = value
        for key, value in (cli_overrides or {}).items():
            if value not in (None, ""):
                overrides[key] = value
        return cls(base_config=base_config, overrides=overrides)

    def get_provider(self) -> str:
        return self._value("provider", self.base_config.get_provider()) or "bitbucket"

    def get_bitbucket_username(self) -> str:
        return self._value("bitbucket_username", self.base_config.get_bitbucket_username())

    def get_bitbucket_app_password(self) -> str:
        return self.get_bitbucket_api_token()

    def get_bitbucket_api_token(self) -> str:
        return self._value("bitbucket_api_token", self.base_config.get_bitbucket_api_token())

    def get_bitbucket_workspace(self) -> str:
        return self._value("bitbucket_workspace", self.base_config.get_bitbucket_workspace())

    def get_github_owner(self) -> str:
        return self._value("github_owner", self.base_config.get_github_owner())

    def get_github_repo(self) -> str:
        return self._value("github_repo", self.base_config.get_github_repo())

    def get_github_token(self) -> str:
        return self._value("github_token", self.base_config.get_github_token())

    def get_managed_repo_root(self) -> str:
        return self._value("managed_repo_root", self.base_config.get_managed_repo_root())

    def get_repo_dir(self) -> str:
        return self._value("active_repo_local_dir", self.base_config.get_repo_dir())

    def get_active_repository(self) -> dict:
        base = dict(self.base_config.get_active_repository() or {})
        mapping = {
            "provider": "active_repo_provider",
            "id": "active_repo_id",
            "name": "active_repo_name",
            "owner": "active_repo_owner",
            "slug": "active_repo_slug",
            "clone_url": "active_repo_clone_url",
            "html_url": "active_repo_html_url",
            "local_dir": "active_repo_local_dir",
        }
        for field, key in mapping.items():
            if key in self.overrides:
                base[field] = self.overrides[key]
        return base

    def get_selected_repositories(self) -> list:
        raw = self.overrides.get("selected_repositories_json")
        if raw is None:
            return self.base_config.get_selected_repositories()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("selected_repositories_json override must be valid JSON.") from exc
        return parsed if isinstance(parsed, list) else []

    def effective_source_summary(self) -> dict[str, str]:
        keys = [
            "provider",
            "bitbucket_username",
            "bitbucket_api_token",
            "bitbucket_workspace",
            "github_owner",
            "github_repo",
            "github_token",
            "managed_repo_root",
            "selected_repositories_json",
            "active_repo_provider",
            "active_repo_owner",
            "active_repo_slug",
            "active_repo_local_dir",
        ]
        summary: dict[str, str] = {}
        for key in keys:
            summary[key] = "override" if key in self.overrides else "app_config"
        return summary

    def _value(self, key: str, fallback: Any) -> Any:
        if key in self.overrides:
            return self.overrides[key]
        return fallback

    def get_diff_font_size(self) -> int:
        val = self._value("diff_font_size", None)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
        return self.base_config.get_diff_font_size()

    def __getattr__(self, item: str) -> Any:
        return getattr(self.base_config, item)
