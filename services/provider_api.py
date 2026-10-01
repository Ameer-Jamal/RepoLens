from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
import re
import threading
import time
from typing import Callable, Optional
from urllib.parse import quote, unquote

import requests

from ConfigManager import ConfigManager
from models.contribution_models import RepositoryRef, normalize_record_datetime


CancelCheck = Optional[Callable[[], bool]]
_BITBUCKET_HOST = "api.bitbucket.org"
_BITBUCKET_CONCURRENCY = threading.Semaphore(1)
_BITBUCKET_RATE_LOCK = threading.Lock()
_BITBUCKET_NEXT_REQUEST_TS = 0.0
_BITBUCKET_STATS_LOCK = threading.Lock()
_BITBUCKET_REQUEST_COUNT = 0
_BITBUCKET_RETRY_COUNT = 0


def _should_cancel(cancel_check: CancelCheck) -> bool:
    return bool(cancel_check and cancel_check())


def bitbucket_request_stats() -> tuple[int, int]:
    with _BITBUCKET_STATS_LOCK:
        return _BITBUCKET_REQUEST_COUNT, _BITBUCKET_RETRY_COUNT


def _record_bitbucket_request(*, retry: bool = False) -> None:
    global _BITBUCKET_REQUEST_COUNT, _BITBUCKET_RETRY_COUNT
    with _BITBUCKET_STATS_LOCK:
        _BITBUCKET_REQUEST_COUNT += 1
        if retry:
            _BITBUCKET_RETRY_COUNT += 1


def _apply_bitbucket_rate_limit(url: str):
    global _BITBUCKET_NEXT_REQUEST_TS
    if _BITBUCKET_HOST not in (url or ""):
        return
    with _BITBUCKET_RATE_LOCK:
        now = time.monotonic()
        if now < _BITBUCKET_NEXT_REQUEST_TS:
            time.sleep(_BITBUCKET_NEXT_REQUEST_TS - now)
        # Keep a conservative gap between outbound Bitbucket requests.
        _BITBUCKET_NEXT_REQUEST_TS = time.monotonic() + 0.35


def _is_rate_limited_exception(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


def _get_bitbucket_page_with_resume(
    url: str,
    *,
    auth,
    timeout: int = 20,
    max_resume_attempts: int = 0,
):
    attempt = 0
    while True:
        try:
            return _get_json_with_retry(url, auth=auth, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limited_exception(exc) and attempt < max_resume_attempts:
                # Cursor-aware continuation: keep the same page URL and retry later.
                cooldown = min(90.0, 6.0 * (2 ** attempt))
                time.sleep(cooldown)
                attempt += 1
                continue
            raise


def _get_json_with_retry(
    url: str,
    *,
    params=None,
    auth=None,
    headers=None,
    timeout: int = 20,
    max_attempts: int = 4,
):
    last_error = None
    is_bitbucket = _BITBUCKET_HOST in (url or "")
    for attempt in range(max_attempts):
        _apply_bitbucket_rate_limit(url)
        if is_bitbucket:
            _record_bitbucket_request(retry=attempt > 0)
            with _BITBUCKET_CONCURRENCY:
                response = requests.get(url, params=params, auth=auth, headers=headers, timeout=timeout)
        else:
            response = requests.get(url, params=params, auth=auth, headers=headers, timeout=timeout)

        if response.status_code in {429, 500, 502, 503, 504} and attempt < (max_attempts - 1):
            retry_after = response.headers.get("Retry-After")
            try:
                sleep_seconds = float(retry_after) if retry_after else (0.8 * (2 ** attempt))
            except ValueError:
                sleep_seconds = 0.8 * (2 ** attempt)
            if response.status_code == 429:
                sleep_seconds = max(sleep_seconds, 3.0 + (attempt * 1.5))
            time.sleep(min(10.0, max(0.7, sleep_seconds)))
            continue
        try:
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if isinstance(exc, requests.HTTPError):
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status and 400 <= status < 500 and status != 429:
                    raise
            if attempt < (max_attempts - 1):
                time.sleep(min(45.0, 0.8 * (2 ** attempt)))
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError("Failed to fetch provider data.")


@dataclass(frozen=True)
class ProviderUser:
    username: str
    display_name: str = ""
    email: str = ""


@dataclass(frozen=True)
class ContributionRepositoryDiscovery:
    repositories: tuple[RepositoryRef, ...]
    pull_requests_by_repository: dict[str, tuple[dict, ...]]


class ProviderClient(ABC):
    provider_name = ""

    def __init__(self, config: ConfigManager):
        self.config = config

    @abstractmethod
    def validate_credentials(self) -> ProviderUser:
        raise NotImplementedError

    @abstractmethod
    def list_repositories(self, cancel_check: CancelCheck = None) -> list[RepositoryRef]:
        raise NotImplementedError

    @abstractmethod
    def current_repository(self) -> Optional[RepositoryRef]:
        raise NotImplementedError

    def list_contributed_repositories(
        self,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        cancel_check: CancelCheck = None,
    ) -> list[RepositoryRef]:
        raise ValueError(
            f"Fast contributed-repository discovery is not supported for {self.provider_name}."
        )

    def discover_contributed_repositories(
        self,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        cancel_check: CancelCheck = None,
    ) -> ContributionRepositoryDiscovery:
        repositories = self.list_contributed_repositories(
            developer,
            start_date,
            end_date,
            cancel_check=cancel_check,
        )
        return ContributionRepositoryDiscovery(tuple(repositories), {})

    @abstractmethod
    def list_merged_pull_requests(
        self,
        repository: RepositoryRef,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_pull_request_commits(
        self,
        repository: RepositoryRef,
        pr_record: dict,
        cancel_check: CancelCheck = None,
    ) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    def list_commits(
        self,
        repository: RepositoryRef,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_pull_requests_for_commit(
        self,
        repository: RepositoryRef,
        commit_hash: str,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_commits_for_file(
        self,
        repository: RepositoryRef,
        file_path: str,
        since_date: Optional[date] = None,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_developer_candidates(
        self,
        repository: RepositoryRef,
        limit: int = 50,
        cancel_check: CancelCheck = None,
    ) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def branch_exists(self, repository: RepositoryRef, branch_name: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def create_pull_request(
        self,
        repository: RepositoryRef,
        *,
        title: str,
        description: str,
        source_branch: str,
        target_branch: str,
        draft: bool = False,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def update_pull_request(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        target_branch: Optional[str] = None,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def context_key(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_pull_request_statuses(
        self,
        repository: RepositoryRef,
        commit_hash: str,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def approve_pull_request(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
        comment: str = "",
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def unapprove_pull_request(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def request_changes_on_pr(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
        comment: str,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_file_content(
        self,
        repository: RepositoryRef,
        file_path: str,
        ref: str,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_pull_request_diff_text(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
    ) -> str:
        raise NotImplementedError

    @staticmethod
    def _matches_developer(*candidates: str, developer: str) -> bool:
        developer = (developer or "").strip()
        if not developer:
            return True
        terms = [part.strip().lower() for part in re.split(r"[,\n;]+", developer) if part.strip()]
        if not terms:
            return True
        values = [value.strip().lower() for value in candidates if value]
        for term in terms:
            if any(term in value for value in values):
                return True
            # Normalize identity aliases so "Ameer Jamal", "ameerjamal", and
            # similar punctuation/spacing variants match consistently across repos.
            normalized_term = re.sub(r"[^a-z0-9]+", "", term)
            if not normalized_term:
                continue
            for value in values:
                normalized_value = re.sub(r"[^a-z0-9]+", "", value)
                if not normalized_value:
                    continue
                if normalized_term in normalized_value or normalized_value in normalized_term:
                    return True
        return False

    @staticmethod
    def _matches_search(*candidates: str, search_text: str) -> bool:
        search_text = (search_text or "").strip().lower()
        if not search_text:
            return True
        haystack = " ".join(value.strip().lower() for value in candidates if value)
        return search_text in haystack

    @staticmethod
    def _matches_branch(branch_filter: str, *branches: str) -> bool:
        branch_filter = (branch_filter or "").strip().lower()
        if not branch_filter:
            return True
        normalized = [branch.strip().lower() for branch in branches if branch]
        return any(branch_filter in branch for branch in normalized)

    @staticmethod
    def _is_date_in_range(
        record_date,
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> bool:
        if not record_date:
            return False
        dt = normalize_record_datetime(record_date)
        if not dt:
            return False
        value = dt.date()
        if start_date and value < start_date:
            return False
        if end_date and value > end_date:
            return False
        return True

    @staticmethod
    def _exclude_bot(author: str, exclude_bots: bool) -> bool:
        if not exclude_bots:
            return False
        author = (author or "").strip().lower()
        return author.endswith("[bot]") or "bot" in author


class BitbucketProviderClient(ProviderClient):
    provider_name = "bitbucket"

    def _auth(self) -> tuple[str, str]:
        return (
            (self.config.get_bitbucket_username() or "").strip(),
            (self.config.get_bitbucket_api_token() or "").strip(),
        )

    def _workspace(self) -> str:
        return (self.config.get_bitbucket_workspace() or "").strip()

    def validate_credentials(self) -> ProviderUser:
        username, password = self._auth()
        workspace = self._workspace()
        if not username or not password:
            raise ValueError("Atlassian account email and Bitbucket API token are required.")
        if not workspace:
            raise ValueError("Bitbucket workspace is required.")

        # Validate against repository access in the configured workspace instead of
        # the user profile endpoint, which may be forbidden for constrained API tokens that
        # only have repository scopes.
        try:
            data = _get_json_with_retry(
                f"https://api.bitbucket.org/2.0/repositories/{workspace}",
                params={"pagelen": 1},
                auth=(username, password),
                timeout=20,
            )
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            if status in {401, 403}:
                raise ValueError(
                    "Bitbucket credentials were rejected for workspace access. "
                    "Verify username/app-password and ensure repository read access "
                    f"for workspace '{workspace}'."
                ) from exc
            raise
        if not isinstance(data, dict):
            raise ValueError("Bitbucket workspace validation failed.")
        return ProviderUser(
            username=username,
            display_name="",
            email="",
        )

    def context_key(self) -> str:
        return self._workspace() or "default"

    def current_repository(self) -> Optional[RepositoryRef]:
        active_repo = self.config.get_active_repository() or {}
        provider = (active_repo.get("provider") or "").strip().lower()
        if provider == "bitbucket" and active_repo.get("owner") and active_repo.get("slug"):
            workspace = active_repo.get("owner") or ""
            slug = active_repo.get("slug") or ""
            full_name = active_repo.get("full_name") or f"{workspace}/{slug}"
            return RepositoryRef(
                provider="bitbucket",
                workspace=workspace,
                slug=slug,
                display_name=full_name,
                full_name=full_name,
                web_url=active_repo.get("html_url") or f"https://bitbucket.org/{workspace}/{slug}",
            )

        workspace = self._workspace()
        slug = (self.config.get_repo_slug() or "").strip()
        if not workspace or not slug:
            return None
        full_name = f"{workspace}/{slug}"
        return RepositoryRef(
            provider="bitbucket",
            workspace=workspace,
            slug=slug,
            display_name=full_name,
            full_name=full_name,
            web_url=f"https://bitbucket.org/{workspace}/{slug}",
        )

    def list_repositories(self, cancel_check: CancelCheck = None) -> list[RepositoryRef]:
        username, password = self._auth()
        workspace = self._workspace()
        if not workspace:
            raise ValueError("Bitbucket workspace is required.")

        repositories: list[RepositoryRef] = []
        next_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}?pagelen=100"

        while next_url:
            if _should_cancel(cancel_check):
                break
            try:
                data = _get_bitbucket_page_with_resume(next_url, auth=(username, password), timeout=20)
            except Exception as exc:  # noqa: BLE001
                if repositories and _is_rate_limited_exception(exc):
                    break
                raise
            for repo in data.get("values", []):
                slug = repo.get("slug") or ""
                full_name = repo.get("full_name") or f"{workspace}/{slug}"
                repositories.append(
                    RepositoryRef(
                        provider="bitbucket",
                        workspace=workspace,
                        slug=slug,
                        display_name=full_name,
                        full_name=full_name,
                        web_url=((repo.get("links") or {}).get("html") or {}).get("href", ""),
                    )
                )
            next_url = data.get("next")

        return repositories

    def list_contributed_repositories(
        self,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        cancel_check: CancelCheck = None,
    ) -> list[RepositoryRef]:
        discovery = self.discover_contributed_repositories(
            developer=developer,
            start_date=start_date,
            end_date=end_date,
            cancel_check=cancel_check,
        )
        return list(discovery.repositories)

    def discover_contributed_repositories(
        self,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        cancel_check: CancelCheck = None,
    ) -> ContributionRepositoryDiscovery:
        """Discover target repositories from PRs authored by a user in the workspace."""
        username, password = self._auth()
        workspace = self._workspace()
        developer_terms = [
            value.strip()
            for value in re.split(r"[,;\n]+", developer or "")
            if value.strip()
        ]
        if not developer_terms:
            raise ValueError(
                "A developer is required for the fast contributed-repositories scope."
            )
        if not workspace:
            raise ValueError("Bitbucket workspace is required.")

        selected_users = self._resolve_contribution_users(
            developer_terms,
            auth=(username, password),
            cancel_check=cancel_check,
        )
        repositories: dict[str, RepositoryRef] = {}
        pull_requests_by_repository: dict[str, list[dict]] = {}
        query_parts = []
        if start_date:
            query_parts.append(f'updated_on >= "{start_date.isoformat()}T00:00:00Z"')
        if end_date:
            exclusive_end = end_date + timedelta(days=1)
            query_parts.append(f'updated_on < "{exclusive_end.isoformat()}T00:00:00Z"')

        for selected_user in selected_users:
            if _should_cancel(cancel_check):
                break
            params = ["state=MERGED", "pagelen=100", "sort=-updated_on"]
            if query_parts:
                params.append(f"q={requests.utils.quote(' AND '.join(query_parts), safe='')}")
            encoded_user = requests.utils.quote(selected_user, safe="")
            next_url = (
                f"https://api.bitbucket.org/2.0/workspaces/{workspace}/"
                f"pullrequests/{encoded_user}?{'&'.join(params)}"
            )
            while next_url:
                if _should_cancel(cancel_check):
                    break
                data = _get_bitbucket_page_with_resume(
                    next_url,
                    auth=(username, password),
                    timeout=20,
                )
                for pull_request in data.get("values", []):
                    repository = self._repository_from_workspace_pull_request(
                        pull_request,
                        default_workspace=workspace,
                    )
                    if repository:
                        repositories[repository.key] = repository
                        if self._workspace_pr_matches_query(
                            pull_request,
                            start_date=start_date,
                            end_date=end_date,
                            search_text=search_text,
                            branch_filter=branch_filter,
                            exclude_bots=exclude_bots,
                        ):
                            pull_requests_by_repository.setdefault(repository.key, []).append(
                                pull_request
                            )
                next_url = data.get("next")

        sorted_repositories = tuple(
            sorted(repositories.values(), key=lambda repo: repo.display_name.lower())
        )
        return ContributionRepositoryDiscovery(
            repositories=sorted_repositories,
            pull_requests_by_repository={
                key: tuple(records)
                for key, records in pull_requests_by_repository.items()
            },
        )

    def _workspace_pr_matches_query(
        self,
        pull_request: dict,
        *,
        start_date: Optional[date],
        end_date: Optional[date],
        search_text: str,
        branch_filter: str,
        exclude_bots: bool,
    ) -> bool:
        author_obj = pull_request.get("author") or {}
        author = author_obj.get("display_name") or ""
        username = author_obj.get("username") or ""
        nickname = author_obj.get("nickname") or ""
        summary = (
            (pull_request.get("summary") or {}).get("raw")
            or pull_request.get("description")
            or ""
        )
        source_branch = (
            ((pull_request.get("source") or {}).get("branch") or {}).get("name") or ""
        )
        destination_branch = (
            ((pull_request.get("destination") or {}).get("branch") or {}).get("name") or ""
        )
        if self._exclude_bot(author or nickname or username, exclude_bots):
            return False
        return (
            self._matches_search(
                pull_request.get("title") or "",
                summary,
                search_text=search_text,
            )
            and self._matches_branch(branch_filter, source_branch, destination_branch)
            and self._is_date_in_range(pull_request.get("updated_on"), start_date, end_date)
        )

    def _resolve_contribution_users(
        self,
        developer_terms: list[str],
        *,
        auth,
        cancel_check: CancelCheck = None,
    ) -> list[str]:
        """Resolve display names and configured email aliases to stable Bitbucket UUIDs."""
        resolved: list[str] = []
        unresolved = list(developer_terms)
        try:
            if not _should_cancel(cancel_check):
                current_user = _get_json_with_retry(
                    "https://api.bitbucket.org/2.0/user",
                    auth=auth,
                    timeout=20,
                )
                current_candidates = (
                    current_user.get("display_name") or "",
                    current_user.get("nickname") or "",
                    current_user.get("username") or "",
                    current_user.get("account_id") or "",
                    current_user.get("uuid") or "",
                    self._auth()[0],
                )
                current_identifier = (
                    current_user.get("uuid")
                    or current_user.get("username")
                    or current_user.get("nickname")
                    or ""
                )
                unresolved = []
                for term in developer_terms:
                    if current_identifier and self._matches_developer(
                        *current_candidates,
                        developer=term,
                    ):
                        resolved.append(current_identifier)
                    else:
                        unresolved.append(term)
        except Exception:  # noqa: BLE001 - user-profile scope is optional
            pass

        # For another developer, translate a display name/nickname to a UUID.
        # This is still a workspace-level paginated read, not one request per repo.
        if unresolved and not _should_cancel(cancel_check):
            matched_terms: set[str] = set()
            next_url = (
                f"https://api.bitbucket.org/2.0/workspaces/{self._workspace()}/"
                "members?pagelen=100"
            )
            try:
                while next_url and len(matched_terms) < len(unresolved):
                    if _should_cancel(cancel_check):
                        break
                    data = _get_bitbucket_page_with_resume(next_url, auth=auth, timeout=20)
                    for membership in data.get("values", []):
                        user = membership.get("user") or membership
                        identifier = (
                            user.get("uuid")
                            or user.get("username")
                            or user.get("nickname")
                            or ""
                        )
                        if not identifier:
                            continue
                        candidates = (
                            user.get("display_name") or "",
                            user.get("nickname") or "",
                            user.get("username") or "",
                            user.get("account_id") or "",
                            user.get("uuid") or "",
                        )
                        for term in unresolved:
                            if term.lower() in matched_terms:
                                continue
                            if self._matches_developer(*candidates, developer=term):
                                resolved.append(identifier)
                                matched_terms.add(term.lower())
                    next_url = data.get("next")
            except Exception:  # noqa: BLE001 - workspace-member scope is optional
                pass
            resolved.extend(
                term for term in unresolved if term.lower() not in matched_terms
            )

        deduped: list[str] = []
        seen: set[str] = set()
        for value in resolved:
            key = value.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(value.strip())
        return deduped

    @staticmethod
    def _repository_from_workspace_pull_request(
        pull_request: dict,
        *,
        default_workspace: str,
    ) -> Optional[RepositoryRef]:
        destination = pull_request.get("destination") or {}
        repo = destination.get("repository") or {}
        full_name = (repo.get("full_name") or "").strip()
        workspace = ((repo.get("workspace") or {}).get("slug") or "").strip()
        slug = (repo.get("slug") or "").strip()
        if full_name and "/" in full_name:
            workspace_from_name, slug_from_name = full_name.split("/", 1)
            workspace = workspace or workspace_from_name
            slug = slug or slug_from_name
        workspace = workspace or default_workspace
        if not slug:
            return None
        full_name = full_name or f"{workspace}/{slug}"
        html_url = ((repo.get("links") or {}).get("html") or {}).get("href", "")
        return RepositoryRef(
            provider="bitbucket",
            workspace=workspace,
            slug=slug,
            display_name=full_name,
            full_name=full_name,
            web_url=html_url or f"https://bitbucket.org/{full_name}",
        )

    def list_merged_pull_requests(
        self,
        repository: RepositoryRef,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        username, password = self._auth()
        
        # Build Bitbucket query expression for server-side filtering
        query_parts = ['state="MERGED"']
        if start_date:
            # Buffer by 1 day to account for timezone differences in the initial fetch,
            # we still filter strictly locally.
            query_parts.append(f'updated_on >= "{start_date.isoformat()}T00:00:00Z"')
            
        q_param = " AND ".join(query_parts)
        next_url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/pullrequests?q={requests.utils.quote(q_param)}&pagelen=50"
        )
        records: list[dict] = []

        while next_url:
            if _should_cancel(cancel_check):
                break
            try:
                data = _get_bitbucket_page_with_resume(next_url, auth=(username, password), timeout=20)
            except Exception as exc:  # noqa: BLE001
                if records and _is_rate_limited_exception(exc):
                    break
                raise
            
            values = data.get("values", [])
            if not values:
                break
                
            for pr in values:
                author_obj = pr.get("author") or {}
                author = (author_obj.get("display_name")) or ""
                username_value = (author_obj.get("username")) or ""
                nickname_value = (author_obj.get("nickname")) or ""
                account_id_value = (author_obj.get("account_id")) or ""
                summary = (pr.get("summary") or {}).get("raw") or pr.get("description") or ""
                source_branch = ((pr.get("source") or {}).get("branch") or {}).get("name") or ""
                destination_branch = ((pr.get("destination") or {}).get("branch") or {}).get("name") or ""
                merged_on = pr.get("updated_on")
                
                if self._exclude_bot(author or nickname_value or username_value, exclude_bots):
                    continue
                if not self._matches_developer(
                    author,
                    username_value,
                    nickname_value,
                    account_id_value,
                    developer=developer,
                ):
                    continue
                if not self._matches_search(pr.get("title") or "", summary, search_text=search_text):
                    continue
                if not self._matches_branch(branch_filter, source_branch, destination_branch):
                    continue
                if not self._is_date_in_range(merged_on, start_date, end_date):
                    continue
                records.append(pr)
            
            if next_url is not None:
                next_url = data.get("next")
        return records

    def list_pull_request_commits(
        self,
        repository: RepositoryRef,
        pr_record: dict,
        cancel_check: CancelCheck = None,
    ) -> set[str]:
        username, password = self._auth()
        pr_id = pr_record.get("id")
        if not pr_id:
            return set()
        next_url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/pullrequests/{pr_id}/commits?pagelen=100"
        )
        commit_hashes: set[str] = set()
        while next_url:
            if _should_cancel(cancel_check):
                break
            try:
                data = _get_bitbucket_page_with_resume(next_url, auth=(username, password), timeout=20)
            except Exception as exc:  # noqa: BLE001
                if commit_hashes and _is_rate_limited_exception(exc):
                    break
                raise
            for commit in data.get("values", []):
                hash_value = commit.get("hash")
                if hash_value:
                    commit_hashes.add(hash_value.lower())
            next_url = data.get("next")
        return commit_hashes

    def list_commits(
        self,
        repository: RepositoryRef,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        username, password = self._auth()
        next_url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/commits?pagelen=100"
        )
        records: list[dict] = []
        while next_url:
            if _should_cancel(cancel_check):
                break
            try:
                data = _get_bitbucket_page_with_resume(next_url, auth=(username, password), timeout=20)
            except Exception as exc:  # noqa: BLE001
                if records and _is_rate_limited_exception(exc):
                    break
                raise
            
            values = data.get("values", [])
            if not values:
                break
                
            for commit in values:
                commit_date = commit.get("date")
                
                # Early exit: Bitbucket commits are strictly descending
                if start_date and commit_date:
                    dt = normalize_record_datetime(commit_date)
                    if dt and dt.date() < start_date:
                        next_url = None
                        break

                author_user = (((commit.get("author") or {}).get("user")) or {})
                author_display = author_user.get("display_name") or ""
                author_nickname = author_user.get("nickname") or ""
                author_account_id = author_user.get("account_id") or ""
                raw_author = ((commit.get("author") or {}).get("raw")) or ""
                if self._exclude_bot(author_display or author_nickname or raw_author, exclude_bots):
                    continue
                if not self._matches_developer(
                    author_display,
                    author_nickname,
                    author_account_id,
                    raw_author,
                    developer=developer,
                ):
                    continue
                if not self._matches_search(commit.get("message") or "", search_text=search_text):
                    continue
                if branch_filter:
                    branch_name = commit.get("branch") or ""
                    if not self._matches_branch(branch_filter, branch_name):
                        continue
                if not self._is_date_in_range(commit_date, start_date, end_date):
                    continue
                records.append(commit)
            
            if next_url is not None:
                next_url = data.get("next")
        return records

    def list_pull_requests_for_commit(
        self,
        repository: RepositoryRef,
        commit_hash: str,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        username, password = self._auth()
        next_url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/commit/{commit_hash}/pullrequests"
        )
        records: list[dict] = []
        while next_url:
            if _should_cancel(cancel_check):
                break
            try:
                data = _get_bitbucket_page_with_resume(next_url, auth=(username, password), timeout=20)
            except Exception as exc:  # noqa: BLE001
                if records and _is_rate_limited_exception(exc):
                    break
                raise
            records.extend(data.get("values", []))
            next_url = data.get("next")
        return records

    def list_commits_for_file(
        self,
        repository: RepositoryRef,
        file_path: str,
        since_date: Optional[date] = None,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        username, password = self._auth()
        # Commits are sorted descending by default.
        next_url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/commits?pagelen=100"
        )
        if file_path:
            next_url += f"&path={requests.utils.quote(file_path)}"

        records: list[dict] = []
        while next_url:
            if _should_cancel(cancel_check):
                break
            try:
                data = _get_bitbucket_page_with_resume(next_url, auth=(username, password), timeout=20)
            except Exception as exc:  # noqa: BLE001
                if records and _is_rate_limited_exception(exc):
                    break
                raise
            
            values = data.get("values", [])
            if not values:
                break
                
            for commit in values:
                commit_date = commit.get("date")
                if since_date and commit_date:
                    dt = normalize_record_datetime(commit_date)
                    if dt and dt.date() < since_date:
                        next_url = None
                        break
                records.append(commit)
            
            if next_url is not None:
                next_url = data.get("next")
        return records

    def list_developer_candidates(
        self,
        repository: RepositoryRef,
        limit: int = 50,
        cancel_check: CancelCheck = None,
    ) -> list[str]:
        username, password = self._auth()
        seen: set[str] = set()
        candidates: list[str] = []

        # Keep suggestions responsive: sample recent open PRs first, then recent
        # merged PRs. Full history scans belong to contribution queries.
        for state in ("OPEN", "MERGED"):
            next_url = (
                f"https://api.bitbucket.org/2.0/repositories/"
                f"{repository.workspace}/{repository.slug}/pullrequests"
                f"?state={state}&pagelen=50&sort=-updated_on"
            )
            pages_read = 0
            while next_url and len(candidates) < limit and pages_read < 1:
                if _should_cancel(cancel_check):
                    break
                try:
                    data = _get_bitbucket_page_with_resume(next_url, auth=(username, password), timeout=20)
                except Exception as exc:  # noqa: BLE001
                    if candidates and _is_rate_limited_exception(exc):
                        break
                    raise
                pages_read += 1
                for pr in data.get("values", []):
                    author = pr.get("author") or {}
                    for value in (
                        author.get("display_name") or "",
                        author.get("username") or "",
                        author.get("nickname") or "",
                    ):
                        item = (value or "").strip()
                        key = item.lower()
                        if not item or key in seen:
                            continue
                        seen.add(key)
                        candidates.append(item)
                        if len(candidates) >= limit:
                            break
                    if len(candidates) >= limit:
                        break
                next_url = data.get("next")
            if len(candidates) >= limit or _should_cancel(cancel_check):
                break
        return candidates

    def branch_exists(self, repository: RepositoryRef, branch_name: str) -> bool:
        username, password = self._auth()
        branch = (branch_name or "").strip()
        if not branch:
            return False
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/refs/branches/{requests.utils.quote(branch, safe='')}"
        )
        response = requests.get(url, auth=(username, password), timeout=20)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def create_pull_request(
        self,
        repository: RepositoryRef,
        *,
        title: str,
        description: str,
        source_branch: str,
        target_branch: str,
        draft: bool = False,
    ) -> dict:
        username, password = self._auth()
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/pullrequests"
        )
        payload = {
            "title": title,
            "description": description,
            "source": {"branch": {"name": source_branch}},
            "destination": {"branch": {"name": target_branch}},
            "close_source_branch": False,
            "draft": draft,
        }
        response = requests.post(url, auth=(username, password), json=payload, timeout=20)
        response.raise_for_status()
        return response.json()

    def update_pull_request(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        target_branch: Optional[str] = None,
    ) -> dict:
        username, password = self._auth()
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/pullrequests/{pr_id}"
        )
        payload = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if target_branch is not None:
            payload["destination"] = {"branch": {"name": target_branch}}

        response = requests.put(url, auth=(username, password), json=payload, timeout=20)
        response.raise_for_status()
        return response.json()

    def get_pull_request_statuses(
        self,
        repository: RepositoryRef,
        commit_hash: str,
    ) -> dict:
        commit_hash = (commit_hash or "").strip()
        if not commit_hash:
            return {
                "state": "UNKNOWN",
                "total_count": 0,
                "successful_count": 0,
                "failed_count": 0,
                "inprogress_count": 0,
                "statuses": [],
            }
        username, password = self._auth()
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/commit/{commit_hash}/statuses"
        )
        try:
            data = _get_json_with_retry(url, auth=(username, password), timeout=20)
        except Exception:
            return {
                "state": "UNKNOWN",
                "total_count": 0,
                "successful_count": 0,
                "failed_count": 0,
                "inprogress_count": 0,
                "statuses": [],
            }
        items = data.get("values", []) if isinstance(data, dict) else []
        statuses = []
        successful_count = 0
        failed_count = 0
        inprogress_count = 0

        for item in items:
            raw_state = (item.get("state") or "").upper()
            norm_state = "UNKNOWN"
            if raw_state == "SUCCESSFUL":
                norm_state = "SUCCESSFUL"
                successful_count += 1
            elif raw_state in {"FAILED", "STOPPED"}:
                norm_state = "FAILED"
                failed_count += 1
            elif raw_state in {"INPROGRESS", "NEW"}:
                norm_state = "INPROGRESS"
                inprogress_count += 1

            statuses.append({
                "name": item.get("name") or item.get("key") or "Build",
                "state": norm_state,
                "raw_state": raw_state,
                "description": item.get("description") or "",
                "url": item.get("url") or "",
                "type": item.get("type") or "commit_status",
                "created_on": item.get("created_on") or "",
                "updated_on": item.get("updated_on") or "",
            })

        if failed_count > 0:
            overall_state = "FAILED"
        elif inprogress_count > 0:
            overall_state = "INPROGRESS"
        elif items and successful_count == len(items):
            overall_state = "SUCCESSFUL"
        else:
            overall_state = "UNKNOWN" if items else "NO_STATUSES"

        return {
            "state": overall_state,
            "total_count": len(items),
            "successful_count": successful_count,
            "failed_count": failed_count,
            "inprogress_count": inprogress_count,
            "statuses": statuses,
        }

    def approve_pull_request(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
        comment: str = "",
    ) -> dict:
        username, password = self._auth()
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/pullrequests/{pr_id}/approve"
        )
        response = requests.post(url, auth=(username, password), timeout=20)
        response.raise_for_status()
        comment_data = None
        comment_error = None
        if comment and comment.strip():
            comment_url = (
                f"https://api.bitbucket.org/2.0/repositories/"
                f"{repository.workspace}/{repository.slug}/pullrequests/{pr_id}/comments"
            )
            try:
                c_resp = requests.post(
                    comment_url,
                    auth=(username, password),
                    json={"content": {"raw": comment.strip()}},
                    timeout=20,
                )
                c_resp.raise_for_status()
                comment_data = c_resp.json()
            except Exception as exc:
                comment_error = str(exc)
        return {
            "approved": True,
            "pr_id": str(pr_id),
            "comment": comment_data,
            "comment_error": comment_error,
        }

    def unapprove_pull_request(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
    ) -> dict:
        username, password = self._auth()
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/pullrequests/{pr_id}/approve"
        )
        response = requests.delete(url, auth=(username, password), timeout=20)
        response.raise_for_status()
        return {
            "approved": False,
            "pr_id": str(pr_id),
        }

    def request_changes_on_pr(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
        comment: str,
    ) -> dict:
        username, password = self._auth()
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/pullrequests/{pr_id}/request-changes"
        )
        response = requests.post(url, auth=(username, password), timeout=20)
        response.raise_for_status()

        # Post review comment
        comment_url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/pullrequests/{pr_id}/comments"
        )
        comment_error = None
        try:
            c_resp = requests.post(
                comment_url,
                auth=(username, password),
                json={"content": {"raw": f"**Changes Requested**\n\n{comment.strip()}"}},
                timeout=20,
            )
            c_resp.raise_for_status()
            comment_data = c_resp.json()
        except Exception as exc:
            comment_error = str(exc)
            comment_data = None
        return {
            "changes_requested": True,
            "pr_id": str(pr_id),
            "comment": comment_data,
            "comment_error": comment_error,
        }

    def _resolve_slash_branch(self, repository: RepositoryRef, ref: str) -> str:
        """The src endpoint cannot resolve a branch name containing a slash, so use its commit hash."""
        name = unquote(ref)
        if "/" not in name:
            return ref
        username, password = self._auth()
        response = requests.get(
            f"https://api.bitbucket.org/2.0/repositories/{repository.workspace}/{repository.slug}"
            f"/refs/branches/{quote(name, safe='')}",
            auth=(username, password),
            timeout=25,
        )
        if response.status_code == 404:
            return ref  # not a branch (for example a tag), so leave it as given
        response.raise_for_status()
        return response.json()["target"]["hash"]

    def get_file_content(
        self,
        repository: RepositoryRef,
        file_path: str,
        ref: str,
    ) -> str:
        username, password = self._auth()
        cleaned_path = file_path.lstrip("/")
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/src/"
            f"{self._resolve_slash_branch(repository, ref)}/{cleaned_path}"
        )
        response = requests.get(url, auth=(username, password), timeout=25)
        response.raise_for_status()
        # Bitbucket serves source files as text/plain without a charset. requests
        # defaults to Latin-1 in that case and corrupts valid UTF-8 YAML.
        try:
            return response.content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{file_path} at {ref} is not valid UTF-8 text.") from exc

    def get_pull_request_diff_text(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
    ) -> str:
        username, password = self._auth()
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{repository.workspace}/{repository.slug}/pullrequests/{pr_id}/diff"
        )
        response = requests.get(url, auth=(username, password), timeout=30)
        response.raise_for_status()
        return response.text


class GitHubProviderClient(ProviderClient):
    provider_name = "github"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        token = (self.config.get_github_token() or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _owner(self) -> str:
        return (self.config.get_github_owner() or "").strip()

    def validate_credentials(self) -> ProviderUser:
        headers = self._headers()
        response = requests.get("https://api.github.com/user", headers=headers, timeout=20)
        if response.status_code == 401:
            raise ValueError("GitHub token is required to query contribution history.")
        response.raise_for_status()
        payload = response.json()
        return ProviderUser(
            username=payload.get("login") or self._owner(),
            display_name=payload.get("name") or "",
            email=payload.get("email") or "",
        )

    def context_key(self) -> str:
        return self._owner() or "default"

    def current_repository(self) -> Optional[RepositoryRef]:
        active_repo = self.config.get_active_repository() or {}
        provider = (active_repo.get("provider") or "").strip().lower()
        if provider == "github" and active_repo.get("owner") and active_repo.get("slug"):
            owner = active_repo.get("owner") or ""
            repo = active_repo.get("slug") or ""
            full_name = active_repo.get("full_name") or f"{owner}/{repo}"
            return RepositoryRef(
                provider="github",
                workspace=owner,
                slug=repo,
                display_name=full_name,
                full_name=full_name,
                web_url=active_repo.get("html_url") or f"https://github.com/{owner}/{repo}",
            )

        owner = self._owner()
        repo = (self.config.get_github_repo() or "").strip()
        if not owner or not repo:
            return None
        full_name = f"{owner}/{repo}"
        return RepositoryRef(
            provider="github",
            workspace=owner,
            slug=repo,
            display_name=full_name,
            full_name=full_name,
            web_url=f"https://github.com/{owner}/{repo}",
        )

    def list_repositories(self, cancel_check: CancelCheck = None) -> list[RepositoryRef]:
        owner = self._owner()
        if not owner:
            raise ValueError("GitHub owner/org is required.")
        headers = self._headers()
        page = 1
        repositories: list[RepositoryRef] = []
        request_urls = [
            f"https://api.github.com/orgs/{owner}/repos",
            f"https://api.github.com/users/{owner}/repos",
        ]
        seen_full_names: set[str] = set()
        for request_url in request_urls:
            page = 1
            while True:
                if _should_cancel(cancel_check):
                    break
                response = requests.get(
                    request_url,
                    params={"per_page": 100, "page": page, "sort": "updated"},
                    headers=headers,
                    timeout=20,
                )
                if response.status_code == 404:
                    break
                response.raise_for_status()
                values = response.json()
                if not values:
                    break
                for repo in values:
                    full_name = repo.get("full_name") or f"{owner}/{repo.get('name') or ''}"
                    if full_name.lower() in seen_full_names:
                        continue
                    seen_full_names.add(full_name.lower())
                    repositories.append(
                        RepositoryRef(
                            provider="github",
                            workspace=owner,
                            slug=repo.get("name") or "",
                            display_name=full_name,
                            full_name=full_name,
                            web_url=repo.get("html_url") or "",
                        )
                    )
                page += 1
        return repositories

    def list_merged_pull_requests(
        self,
        repository: RepositoryRef,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        headers = self._headers()
        page = 1
        records: list[dict] = []
        while True:
            if _should_cancel(cancel_check):
                break
            values = _get_json_with_retry(
                f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls",
                params={"state": "closed", "per_page": 100, "page": page, "sort": "updated", "direction": "desc"},
                headers=headers,
                timeout=20,
            )
            if not values:
                break
            
            for pr in values:
                merged_at = pr.get("merged_at")
                
                author = ((pr.get("user") or {}).get("login")) or ""
                if self._exclude_bot(author, exclude_bots):
                    continue
                if not merged_at:
                    continue
                if not self._matches_developer(author, developer=developer):
                    continue
                if not self._matches_search(pr.get("title") or "", pr.get("body") or "", search_text=search_text):
                    continue
                if not self._matches_branch(
                    branch_filter,
                    (pr.get("head") or {}).get("ref") or "",
                    (pr.get("base") or {}).get("ref") or "",
                ):
                    continue
                if not self._is_date_in_range(merged_at, start_date, end_date):
                    continue
                records.append(pr)
            page += 1
        return records

    def list_pull_request_commits(
        self,
        repository: RepositoryRef,
        pr_record: dict,
        cancel_check: CancelCheck = None,
    ) -> set[str]:
        headers = self._headers()
        number = pr_record.get("number")
        if not number:
            return set()
        page = 1
        hashes: set[str] = set()
        while True:
            if _should_cancel(cancel_check):
                break
            values = _get_json_with_retry(
                f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls/{number}/commits",
                params={"per_page": 100, "page": page},
                headers=headers,
                timeout=20,
            )
            if not values:
                break
            for commit in values:
                sha = commit.get("sha")
                if sha:
                    hashes.add(sha.lower())
            page += 1
        return hashes

    def list_commits(
        self,
        repository: RepositoryRef,
        developer: str,
        start_date: Optional[date],
        end_date: Optional[date],
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        headers = self._headers()
        page = 1
        records: list[dict] = []
        params = {"per_page": 100, "page": page}
        if branch_filter:
            params["sha"] = branch_filter
        if start_date:
            params["since"] = f"{start_date.isoformat()}T00:00:00Z"
        if end_date:
            params["until"] = f"{end_date.isoformat()}T23:59:59Z"

        while True:
            if _should_cancel(cancel_check):
                break
            params["page"] = page
            values = _get_json_with_retry(
                f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/commits",
                params=params,
                headers=headers,
                timeout=20,
            )
            if not values:
                break
            
            for commit in values:
                inner_commit = commit.get("commit") or {}
                author_obj = inner_commit.get("author") or {}
                commit_date = author_obj.get("date")
                
                # Early exit: GitHub commits are sorted desc.
                if start_date and commit_date:
                    dt = normalize_record_datetime(commit_date)
                    if dt and dt.date() < start_date:
                        return records

                author_login = ((commit.get("author") or {}).get("login")) or ""
                author_email = author_obj.get("email") or ""
                author_name = author_obj.get("name") or author_login
                if self._exclude_bot(author_login or author_name, exclude_bots):
                    continue
                if not self._matches_developer(author_login, author_name, author_email, developer=developer):
                    continue
                if not self._matches_search(inner_commit.get("message") or "", search_text=search_text):
                    continue
                if not self._is_date_in_range(commit_date, start_date, end_date):
                    continue
                records.append(commit)
            page += 1
        return records

    def list_pull_requests_for_commit(
        self,
        repository: RepositoryRef,
        commit_hash: str,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        headers = self._headers()
        # GitHub endpoint to list PRs associated with a commit
        url = f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/commits/{commit_hash}/pulls"
        try:
            return _get_json_with_retry(url, headers=headers, timeout=20) or []
        except Exception:  # noqa: BLE001
            return []

    def list_commits_for_file(
        self,
        repository: RepositoryRef,
        file_path: str,
        since_date: Optional[date] = None,
        cancel_check: CancelCheck = None,
    ) -> list[dict]:
        headers = self._headers()
        page = 1
        records: list[dict] = []
        params = {"per_page": 100, "page": page, "path": file_path}
        if since_date:
            params["since"] = f"{since_date.isoformat()}T00:00:00Z"

        while True:
            if _should_cancel(cancel_check):
                break
            params["page"] = page
            values = _get_json_with_retry(
                f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/commits",
                params=params,
                headers=headers,
                timeout=20,
            )
            if not values:
                break
            records.extend(values)
            page += 1
        return records

    def list_developer_candidates(
        self,
        repository: RepositoryRef,
        limit: int = 50,
        cancel_check: CancelCheck = None,
    ) -> list[str]:
        headers = self._headers()
        seen: set[str] = set()
        candidates: list[str] = []

        for state in ("open", "closed"):
            page = 1
            while len(candidates) < limit and page <= 1:
                if _should_cancel(cancel_check):
                    break
                values = _get_json_with_retry(
                    f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls",
                    params={"state": state, "per_page": 100, "page": page, "sort": "updated", "direction": "desc"},
                    headers=headers,
                    timeout=20,
                )
                if not values:
                    break
                for pr in values:
                    login = ((pr.get("user") or {}).get("login")) or ""
                    item = login.strip()
                    key = item.lower()
                    if not item or key in seen:
                        continue
                    seen.add(key)
                    candidates.append(item)
                    if len(candidates) >= limit:
                        break
                page += 1
            if len(candidates) >= limit or _should_cancel(cancel_check):
                break
        return candidates

    def branch_exists(self, repository: RepositoryRef, branch_name: str) -> bool:
        branch = (branch_name or "").strip()
        if not branch:
            return False
        response = requests.get(
            f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/branches/{requests.utils.quote(branch, safe='')}",
            headers=self._headers(),
            timeout=20,
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def create_pull_request(
        self,
        repository: RepositoryRef,
        *,
        title: str,
        description: str,
        source_branch: str,
        target_branch: str,
        draft: bool = False,
    ) -> dict:
        response = requests.post(
            f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls",
            headers=self._headers(),
            json={
                "title": title,
                "body": description,
                "head": source_branch,
                "base": target_branch,
                "draft": draft,
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def update_pull_request(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        target_branch: Optional[str] = None,
    ) -> dict:
        url = f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls/{pr_id}"
        payload = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["body"] = description
        if target_branch is not None:
            payload["base"] = target_branch

        response = requests.patch(
            url,
            headers=self._headers(),
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def get_pull_request_statuses(
        self,
        repository: RepositoryRef,
        commit_hash: str,
    ) -> dict:
        commit_hash = (commit_hash or "").strip()
        if not commit_hash:
            return {
                "state": "UNKNOWN",
                "total_count": 0,
                "successful_count": 0,
                "failed_count": 0,
                "inprogress_count": 0,
                "statuses": [],
            }

        statuses = []
        successful_count = 0
        failed_count = 0
        inprogress_count = 0

        # 1. Query check runs
        check_runs_url = (
            f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/commits/{commit_hash}/check-runs"
        )
        try:
            data = _get_json_with_retry(check_runs_url, headers=self._headers(), timeout=20)
            runs = data.get("check_runs", []) if isinstance(data, dict) else []
            for run in runs:
                status = (run.get("status") or "").lower()
                conclusion = (run.get("conclusion") or "").lower()

                if status == "completed":
                    if conclusion in {"success", "neutral", "skipped"}:
                        norm_state = "SUCCESSFUL"
                        successful_count += 1
                    else:
                        norm_state = "FAILED"
                        failed_count += 1
                else:
                    norm_state = "INPROGRESS"
                    inprogress_count += 1

                statuses.append({
                    "name": run.get("name") or "Check Run",
                    "state": norm_state,
                    "raw_state": conclusion or status,
                    "description": (run.get("output") or {}).get("title") or run.get("conclusion") or status,
                    "url": run.get("html_url") or "",
                    "type": "check_run",
                    "created_on": run.get("started_at") or "",
                    "updated_on": run.get("completed_at") or "",
                })
        except Exception:
            pass

        # 2. Query combined commit status
        status_url = (
            f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/commits/{commit_hash}/status"
        )
        try:
            status_data = _get_json_with_retry(status_url, headers=self._headers(), timeout=20)
            raw_statuses = status_data.get("statuses", []) if isinstance(status_data, dict) else []
            for item in raw_statuses:
                st = (item.get("state") or "").lower()
                if st == "success":
                    norm_state = "SUCCESSFUL"
                    successful_count += 1
                elif st in {"failure", "error"}:
                    norm_state = "FAILED"
                    failed_count += 1
                else:
                    norm_state = "INPROGRESS"
                    inprogress_count += 1

                statuses.append({
                    "name": item.get("context") or "Status",
                    "state": norm_state,
                    "raw_state": st,
                    "description": item.get("description") or "",
                    "url": item.get("target_url") or "",
                    "type": "commit_status",
                    "created_on": item.get("created_at") or "",
                    "updated_on": item.get("updated_at") or "",
                })
        except Exception:
            pass

        if failed_count > 0:
            overall_state = "FAILED"
        elif inprogress_count > 0:
            overall_state = "INPROGRESS"
        elif statuses and successful_count == len(statuses):
            overall_state = "SUCCESSFUL"
        else:
            overall_state = "UNKNOWN" if statuses else "NO_STATUSES"

        return {
            "state": overall_state,
            "total_count": len(statuses),
            "successful_count": successful_count,
            "failed_count": failed_count,
            "inprogress_count": inprogress_count,
            "statuses": statuses,
        }

    def approve_pull_request(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
        comment: str = "",
    ) -> dict:
        url = f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls/{pr_id}/reviews"
        payload = {
            "event": "APPROVE",
            "body": comment.strip() if comment else "Approved via RepoLens",
        }
        response = requests.post(url, headers=self._headers(), json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        return {
            "approved": True,
            "pr_id": str(pr_id),
            "review_id": data.get("id"),
        }

    def unapprove_pull_request(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
    ) -> dict:
        current_user = self.validate_credentials().username.lower()
        reviews_url = f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls/{pr_id}/reviews"
        response = requests.get(reviews_url, headers=self._headers(), params={"per_page": 100}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        reviews = payload if isinstance(payload, list) else []
        dismissed_count = 0
        for r in reviews:
            user_login = ((r.get("user") or {}).get("login") or "").lower()
            if r.get("state") == "APPROVED" and user_login == current_user:
                review_id = r.get("id")
                dismiss_url = f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls/{pr_id}/reviews/{review_id}/dismissals"
                d_resp = requests.put(
                    dismiss_url,
                    headers=self._headers(),
                    json={"message": "Approval dismissed via RepoLens"},
                    timeout=20,
                )
                d_resp.raise_for_status()
                dismissed_count += 1
        if dismissed_count == 0:
            raise ValueError(f"No approval by {current_user} was found on PR #{pr_id}.")
        return {
            "approved": False,
            "pr_id": str(pr_id),
            "dismissed_count": dismissed_count,
        }

    def request_changes_on_pr(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
        comment: str,
    ) -> dict:
        url = f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls/{pr_id}/reviews"
        payload = {
            "event": "REQUEST_CHANGES",
            "body": comment.strip() if comment else "Changes requested via RepoLens",
        }
        response = requests.post(url, headers=self._headers(), json=payload, timeout=20)
        response.raise_for_status()
        return {
            "changes_requested": True,
            "pr_id": str(pr_id),
            "review_id": response.json().get("id"),
        }

    def get_file_content(
        self,
        repository: RepositoryRef,
        file_path: str,
        ref: str,
    ) -> str:
        cleaned_path = file_path.lstrip("/")
        url = f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/contents/{cleaned_path}"
        headers = dict(self._headers())
        headers["Accept"] = "application/vnd.github.raw+json"
        response = requests.get(url, headers=headers, params={"ref": ref}, timeout=25)
        response.raise_for_status()
        return response.text

    def get_pull_request_diff_text(
        self,
        repository: RepositoryRef,
        pr_id: str | int,
    ) -> str:
        url = f"https://api.github.com/repos/{repository.workspace}/{repository.slug}/pulls/{pr_id}"
        headers = dict(self._headers())
        headers["Accept"] = "application/vnd.github.v3.diff"
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.text


def build_provider_client(config: ConfigManager) -> ProviderClient:
    provider = (config.get_provider() or "bitbucket").lower()
    return build_provider_client_for_name(provider, config)


def build_provider_client_for_name(provider: str, config: ConfigManager) -> ProviderClient:
    provider = (provider or "bitbucket").lower()
    if provider == "github":
        return GitHubProviderClient(config)
    return BitbucketProviderClient(config)
