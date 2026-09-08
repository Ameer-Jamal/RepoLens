from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from typing import Any, Optional

from ConfigManager import ConfigManager
from services.RepositoryProvider import RepositoryProvider
from services.contribution_history_service import ContributionHistoryService
from services.git_context_service import GitContextService
from models.contribution_models import ContributionHistoryQuery, RepositoryRef
from services.diff_service import DiffService
from headless_config import HeadlessConfig
from services.provider_api import build_provider_client
from services.pull_request_service import PullRequestService
from services.pull_request_comment_service import PullRequestCommentService
from services.pr_creation_service import PullRequestCreateRequest, PullRequestCreationService, PullRequestUpdateRequest
from services.scope_manager import ScopeManager

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - exercised via import in tests when dependency exists
    FastMCP = None


DEFAULT_DIFF_CHAR_LIMIT = 200_000


def _parse_date(value: str) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _clamp_diff_limit(value: int) -> int:
    if value <= 0:
        return DEFAULT_DIFF_CHAR_LIMIT
    return min(value, DEFAULT_DIFF_CHAR_LIMIT)


class RepoLensMCPBackend:
    def __init__(self, config: HeadlessConfig | None = None):
        self.config = config or HeadlessConfig()
        self.provider = build_provider_client(self.config)
        self.scope_manager = ScopeManager(self.config, self.provider)
        self.history_service = ContributionHistoryService(self.provider, self.scope_manager)
        self.pr_service = PullRequestService(self.config)
        self.pr_comment_service = PullRequestCommentService(self.config, pr_service=self.pr_service)
        self.pr_creation_service = PullRequestCreationService(self.config)
        self.git_context_service = GitContextService()
        self.diff_service = DiffService()

    def get_active_context(self) -> dict[str, Any]:
        return {
            "provider": self.config.get_provider(),
            "managed_repo_root": self.config.get_managed_repo_root(),
            "repo_dir": self.config.get_repo_dir(),
            "active_repository": self.config.get_active_repository(),
            "selected_repositories": self.config.get_selected_repositories(),
            "config_sources": self.config.effective_source_summary(),
        }

    def list_repositories(self, force_refresh: bool = False) -> list[dict]:
        provider = self.config.get_provider()
        valid, message, provider_config = RepositoryProvider.validate_provider_config(provider, self.config)
        if not valid or not provider_config:
            raise ValueError(message or "Provider configuration is incomplete.")

        context_key = RepositoryProvider.discovery_context_key(provider, provider_config)
        cached, _timestamp = self.config.get_cached_discovered_repositories(provider, context_key)
        if cached and not force_refresh:
            return cached

        repositories = RepositoryProvider.discover_repositories(provider, provider_config)
        self.config.set_cached_discovered_repositories(provider, context_key, repositories)
        return repositories

    def get_selected_repositories(self) -> list[dict]:
        return self.config.get_selected_repositories()

    def list_pull_requests(
        self,
        *,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
        filter_mode: str = "open",
        search_text: str = "",
        developer: str = "",
        next_cursor: str = "",
    ) -> dict[str, Any]:
        repo = self.resolve_repository(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            allow_direct=True,
        )
        records, cursor = self.pr_service.list_pull_requests_for_repo(
            repo,
            filter_mode=filter_mode,
            next_cursor=next_cursor or None,
            search_text=search_text,
            developer=developer,
        )
        return {
            "repository": self._repo_identity(repo),
            "records": records,
            "next_cursor": cursor or "",
        }

    def list_my_pull_requests(
        self,
        *,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "active",
        repo_dir: str = "",
        filter_mode: str = "open",
    ) -> dict[str, Any]:
        repositories = self._ticket_search_repositories(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )
        records: list[dict] = []
        for repo in repositories:
            repo_records, _cursor = self.pr_service.list_pull_requests_for_repo(
                repo,
                filter_mode=filter_mode,
                developer="me",
            )
            records.extend(repo_records)
        records.sort(key=lambda pr: pr.get("updated_on") or "", reverse=True)
        return {
            "scope": scope,
            "developer": "me",
            "repositories": [self._repo_identity(repo) for repo in repositories],
            "records": records,
            "count": len(records),
        }

    def find_pull_requests_by_ticket(
        self,
        *,
        ticket: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "active",
        repo_dir: str = "",
        filter_mode: str = "all",
    ) -> dict[str, Any]:
        repositories = self._ticket_search_repositories(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )
        ticket_id = self.pr_service.extract_ticket_id(ticket)
        records = self.pr_service.find_pull_requests_by_ticket(
            repositories,
            ticket_id,
            filter_mode=filter_mode,
        )
        return {
            "ticket": ticket_id,
            "scope": scope,
            "repositories": [self._repo_identity(repo) for repo in repositories],
            "records": records,
            "count": len(records),
        }

    def get_ticket_diffs(
        self,
        *,
        tickets_json: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "active",
        repo_dir: str = "",
        filter_mode: str = "all",
        ensure_checkout: bool = True,
        max_chars_per_pr: int = DEFAULT_DIFF_CHAR_LIMIT,
    ) -> dict[str, Any]:
        tickets = self._parse_tickets_json(tickets_json)
        repositories = self._ticket_search_repositories(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )
        limit = _clamp_diff_limit(max_chars_per_pr)
        ticket_results: list[dict[str, Any]] = []

        for ticket in tickets:
            ticket_id = self.pr_service.extract_ticket_id(ticket)
            prs = self.pr_service.find_pull_requests_by_ticket(
                repositories,
                ticket_id,
                filter_mode=filter_mode,
            )
            pr_results = []
            for pr in prs:
                repo = self._resolve_pr_repository(pr, repositories)
                try:
                    repo_dir = self._repo_dir(repo, ensure_checkout=ensure_checkout)
                    diff_result = self.diff_service.generate_pr_diff(pr, repo_dir)
                    diff_text, truncated = self._truncate_text(diff_result.diff_text, limit)
                    pr_results.append(
                        {
                            "pr": pr,
                            "repository": self._repo_identity(repo),
                            "repo_dir": repo_dir,
                            "diff_text": diff_text,
                            "truncated": truncated,
                            "merge_base": diff_result.merge_base,
                            "resolved_source": diff_result.resolved_source,
                            "resolved_destination": diff_result.resolved_destination,
                            "error": "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - return partial ticket results
                    pr_results.append(
                        {
                            "pr": pr,
                            "repository": self._repo_identity(repo),
                            "diff_text": "",
                            "truncated": False,
                            "error": str(exc),
                        }
                    )

            ticket_results.append(
                {
                    "ticket": ticket_id,
                    "input": ticket,
                    "count": len(pr_results),
                    "pull_requests": pr_results,
                }
            )

        return {
            "scope": scope,
            "repositories": [self._repo_identity(repo) for repo in repositories],
            "tickets": ticket_results,
        }

    def get_pr_diff(
        self,
        *,
        pr_id: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
        ensure_checkout: bool = True,
        max_chars: int = DEFAULT_DIFF_CHAR_LIMIT,
    ) -> dict[str, Any]:
        repo = self.resolve_repository(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            allow_direct=True,
        )
        pr = self.pr_service.get_pull_request(repo, pr_id)
        repo_dir = self._repo_dir(repo, ensure_checkout=ensure_checkout)
        result = self.diff_service.generate_pr_diff(pr, repo_dir)
        diff_text, truncated = self._truncate_text(result.diff_text, _clamp_diff_limit(max_chars))
        return {
            "repository": self._repo_identity(repo),
            "pr": pr,
            "repo_dir": repo_dir,
            "diff_text": diff_text,
            "truncated": truncated,
            "merge_base": result.merge_base,
            "resolved_source": result.resolved_source,
            "resolved_destination": result.resolved_destination,
            "source_commit": result.source_commit,
            "destination_commit": result.destination_commit,
            "merge_commit": result.merge_commit,
        }

    def get_commit_diff(
        self,
        *,
        commit_hash: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
        ensure_checkout: bool = True,
        max_chars: int = DEFAULT_DIFF_CHAR_LIMIT,
    ) -> dict[str, Any]:
        repo = self.resolve_repository(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            allow_direct=True,
        )
        repo_dir = self._repo_dir(repo, ensure_checkout=ensure_checkout)
        result = self.diff_service.generate_commit_diff(repo_dir, commit_hash)
        diff_text, truncated = self._truncate_text(result.diff_text, _clamp_diff_limit(max_chars))
        return {
            "repository": self._repo_identity(repo),
            "repo_dir": repo_dir,
            "commit_hash": result.commit_hash,
            "parent_commit": result.parent_commit,
            "diff_text": diff_text,
            "truncated": truncated,
        }

    def query_contribution_history(
        self,
        *,
        developer: str,
        start_date: str = "",
        end_date: str = "",
        scope_type: str = "current_repo",
        contribution_type: str = "merged_prs",
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        group_by: str = "none",
        titles_only: bool = False,
        repositories_json: str = "",
    ) -> dict[str, Any]:
        scope_repositories = None
        resolution_errors = []
        if repositories_json.strip():
            items = json.loads(repositories_json)
            resolved = []
            for item in items:
                if isinstance(item, dict):
                    resolved.append(RepositoryRef.from_dict(item))
                elif isinstance(item, str) and item.strip():
                    try:
                        # Fuzzy resolve string repo names/slugs
                        repo_dict = self.resolve_repository(slug=item.strip())
                        resolved.append(RepositoryRef.from_dict(repo_dict))
                    except ValueError:
                        resolution_errors.append(f"Could not resolve repository: {item}")
                        continue
            scope_repositories = tuple(resolved)

        query = ContributionHistoryQuery(
            developer=developer,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
            scope_type=scope_type,
            contribution_type=contribution_type,
            search_text=search_text,
            branch_filter=branch_filter,
            exclude_bots=exclude_bots,
            group_by=group_by,
            titles_only=titles_only,
            scope_repositories=scope_repositories,
        )
        result = self.history_service.execute_query(query)
        all_errors = resolution_errors + result.partial_errors
        return {
            "scope": {
                "scope_type": result.scope.scope_type,
                "label": result.scope.label,
                "repositories": [repo.to_dict() for repo in result.scope.repositories],
            },
            "records": [record.to_dict() for record in result.records],
            "grouped_records": [
                {
                    "group": group,
                    "records": [record.to_dict() for record in records],
                }
                for group, records in result.grouped_records
            ],
            "partial_errors": all_errors,
            "total_prs": result.total_prs,
            "total_commits": result.total_commits,
            "active_repositories": list(result.active_repositories),
            "top_repositories": list(result.top_repositories),
            "ticket_prefixes": list(result.ticket_prefixes),
            "diagnostic_info": {
                "repositories_scanned_count": len(result.scope.repositories),
                "repositories_scanned": [repo.display_name for repo in result.scope.repositories],
                "resolution_errors": resolution_errors,
            } if not result.records else {},
        }

    def find_pr_for_commit(
        self,
        *,
        commit_hash: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
    ) -> dict[str, Any]:
        repo = self.resolve_repository(provider=provider, workspace=workspace, slug=slug, scope=scope, allow_direct=True)
        repo_ref = RepositoryRef.from_dict(repo)
        prs = self.provider.list_pull_requests_for_commit(repo_ref, commit_hash)
        return {
            "repository": self._repo_identity(repo),
            "commit_hash": commit_hash,
            "pull_requests": prs,
            "count": len(prs),
        }

    def search_contributions_by_ticket(
        self,
        *,
        ticket: str,
        scope_type: str = "all_repos",
        contribution_type: str = "prs_and_commits",
    ) -> dict[str, Any]:
        ticket_id = self.pr_service.extract_ticket_id(ticket)
        return self.query_contribution_history(
            developer="",  # Search all developers
            search_text=ticket_id,
            scope_type=scope_type,
            contribution_type=contribution_type,
        )

    def analyze_file_history(
        self,
        *,
        file_path: str,
        since_date: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
    ) -> dict[str, Any]:
        repo = self.resolve_repository(provider=provider, workspace=workspace, slug=slug, scope=scope, allow_direct=True)
        repo_ref = RepositoryRef.from_dict(repo)
        result = self.history_service.execute_file_history_query(
            repo_ref,
            file_path,
            since_date=_parse_date(since_date),
        )
        return {
            "repository": self._repo_identity(repo),
            "file_path": file_path,
            "records": [record.to_dict() for record in result.records],
            "total_prs": result.total_prs,
            "total_commits": result.total_commits,
        }

    def list_developer_candidates(
        self,
        *,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        repo = self.resolve_repository(provider=provider, workspace=workspace, slug=slug, scope=scope, allow_direct=True)
        repo_ref = RepositoryRef.from_dict(repo)
        return {
            "repository": self._repo_identity(repo),
            "candidates": self.provider.list_developer_candidates(repo_ref, limit=limit),
        }

    def create_pull_request(
        self,
        *,
        title: str,
        source_branch: str,
        target_branch: str,
        description: str = "",
        draft: bool = False,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        repo = self.resolve_repository(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            allow_direct=True,
        )
        return self.pr_creation_service.create_pull_request(
            repo,
            PullRequestCreateRequest(
                title=title,
                description=description,
                source_branch=source_branch,
                target_branch=target_branch,
                draft=draft,
            ),
        )

    def update_pull_request(
        self,
        *,
        pr_id: str | int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        target_branch: Optional[str] = None,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        repo = self.resolve_repository(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            allow_direct=True,
        )
        return self.pr_creation_service.update_pull_request(
            repo,
            PullRequestUpdateRequest(
                pr_id=pr_id,
                title=title,
                description=description,
                target_branch=target_branch,
            ),
        )

    def get_git_repository_context(
        self,
        *,
        repo_dir: str = "",
        source_branch: str = "",
        target_branch: str = "",
    ) -> dict[str, Any]:
        context = self.git_context_service.resolve(
            repo_dir=repo_dir or self.config.get_repo_dir(),
            source_branch=source_branch,
            target_branch=target_branch,
        )
        result = context.to_dict()
        result["auth"] = {
            "github_token_configured": bool((self.config.get_github_token() or "").strip()),
            "bitbucket_username_configured": bool((self.config.get_bitbucket_username() or "").strip()),
            "bitbucket_api_token_configured": bool((self.config.get_bitbucket_api_token() or "").strip()),
        }
        return result

    def resolve_pr_from_reference(
        self,
        reference: str,
        *,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        repo_dir: str = "",
    ) -> tuple[dict, dict]:
        """Resolves a PR from a URL, ticket, or title fragment. Returns (repo, pr_metadata)."""
        reference = reference.strip()
        
        # 1. Try URL
        url_info = self.pr_service.parse_pr_url(reference)
        if url_info:
            repo = self.resolve_repository(
                provider=provider or url_info["provider"],
                workspace=workspace or url_info["workspace"],
                slug=slug or url_info["slug"],
                repo_dir=repo_dir,
                allow_direct=True,
            )
            pr = self.pr_service.get_pull_request(repo, url_info["pr_id"])
            return repo, pr

        # 2. Try Ticket
        ticket_id = self.pr_service.extract_ticket_id(reference)
        if ticket_id:
            selected = self._ticket_search_repositories(
                provider=provider,
                workspace=workspace,
                slug=slug,
                repo_dir=repo_dir,
                scope="active",
            )
            
            prs = self.pr_service.find_pull_requests_by_ticket(selected, ticket_id)
            if prs:
                # If multiple, we take the most recent one for now
                pr = prs[0]
                repo = self.resolve_repository(
                    provider=provider or pr.get("provider"),
                    workspace=workspace,
                    slug=slug or (pr.get("repo_label").split("/")[-1] if "/" in pr.get("repo_label") else pr.get("repo_label")),
                    repo_dir=repo_dir,
                    allow_direct=True,
                )
                return repo, pr

        # 3. Try Numeric PR ID (e.g. "42" or "#42")
        numeric_ref = reference.lstrip("#").strip()
        if numeric_ref.isdigit():
            try:
                repo = self.resolve_repository(
                    provider=provider,
                    workspace=workspace,
                    slug=slug,
                    repo_dir=repo_dir,
                    allow_direct=bool(provider and workspace and slug) or bool(repo_dir),
                )
                pr = self.pr_service.get_pull_request(repo, numeric_ref)
                return repo, pr
            except Exception:
                pass

        # 4. Try Search Text (Title/Branch)
        selected = self._ticket_search_repositories(
            provider=provider,
            workspace=workspace,
            slug=slug,
            repo_dir=repo_dir,
            scope="active",
        )
            
        for repo in selected:
            prs, _ = self.pr_service.list_pull_requests_for_repo(repo, search_text=reference)
            if prs:
                return repo, prs[0]

        raise ValueError(f"Could not resolve Pull Request from reference: {reference}")

    def get_pr_context(
        self,
        *,
        reference: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        repo_dir: str = "",
        ensure_checkout: bool = True,
        max_chars: int = DEFAULT_DIFF_CHAR_LIMIT,
        include_comments: bool = False,
    ) -> dict[str, Any]:
        """A unified tool to get both PR metadata and diff from a URL, ticket, or title."""
        repo, pr = self.resolve_pr_from_reference(
            reference,
            provider=provider,
            workspace=workspace,
            slug=slug,
            repo_dir=repo_dir,
        )
        repo_dir = self._repo_dir(repo, ensure_checkout=ensure_checkout)
        result = self.diff_service.generate_pr_diff(pr, repo_dir)
        diff_text, truncated = self._truncate_text(result.diff_text, _clamp_diff_limit(max_chars))
        
        response: dict[str, Any] = {
            "repository": self._repo_identity(repo),
            "pr": pr,
            "repo_dir": repo_dir,
            "diff_text": diff_text,
            "truncated": truncated,
            "merge_base": result.merge_base,
            "source_commit": result.source_commit,
            "destination_commit": result.destination_commit,
            "merge_commit": result.merge_commit,
        }

        if include_comments:
            comments_res = self.pr_comment_service.get_pull_request_comments(
                repo,
                pr.get("id"),
                repo_dir=repo_dir,
                include_code_context=True,
            )
            response["comments"] = comments_res.get("comments", [])
            response["threads"] = comments_res.get("threads", [])
            response["comment_summary"] = comments_res.get("summary", {})
            response["comments_formatted"] = comments_res.get("formatted_summary", "")

        return response

    def get_pr_comments(
        self,
        *,
        pr_id: str | int = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
        unresolved_only: bool = False,
        comment_type: str = "all",
        file_path: str = "",
        include_code_context: bool = True,
    ) -> dict[str, Any]:
        """Fetch comments and review threads for a pull request with code context and AI summary."""
        target_reference = (reference or "").strip()
        target_pr_id = str(pr_id or "").strip()

        if not target_reference and not target_pr_id:
            raise ValueError("Either 'pr_id' or 'reference' (URL, ticket, or title/PR#) must be provided.")

        if target_reference:
            repo, pr = self.resolve_pr_from_reference(
                target_reference,
                provider=provider,
                workspace=workspace,
                slug=slug,
                repo_dir=repo_dir,
            )
            resolved_pr_id = pr.get("id") or target_pr_id
        else:
            repo = self.resolve_repository(
                provider=provider,
                workspace=workspace,
                slug=slug,
                scope=scope,
                repo_dir=repo_dir,
                allow_direct=True,
            )
            resolved_pr_id = target_pr_id

        effective_repo_dir = repo_dir or repo.get("local_dir") or self.config.get_repo_dir()

        result = self.pr_comment_service.get_pull_request_comments(
            repo,
            resolved_pr_id,
            unresolved_only=unresolved_only,
            comment_type=comment_type,
            file_path=file_path,
            repo_dir=effective_repo_dir,
            include_code_context=include_code_context,
        )
        result["repository"] = self._repo_identity(repo)
        return result

    def add_pr_comment(
        self,
        body: str,
        *,
        pr_id: str | int = "",
        reference: str = "",
        file_path: str = "",
        line: Optional[int] = None,
        side: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Add a general or inline comment to a pull request."""
        target_reference = (reference or "").strip()
        target_pr_id = str(pr_id or "").strip()

        if not target_reference and not target_pr_id:
            raise ValueError("Either 'pr_id' or 'reference' (URL, ticket, or title/PR#) must be provided.")

        if target_reference:
            repo, pr = self.resolve_pr_from_reference(
                target_reference,
                provider=provider,
                workspace=workspace,
                slug=slug,
                repo_dir=repo_dir,
            )
            resolved_pr_id = pr.get("id") or target_pr_id
        else:
            repo = self.resolve_repository(
                provider=provider,
                workspace=workspace,
                slug=slug,
                scope=scope,
                repo_dir=repo_dir,
                allow_direct=True,
            )
            resolved_pr_id = target_pr_id

        comment = self.pr_comment_service.add_comment(
            repo,
            resolved_pr_id,
            body,
            file_path=file_path or None,
            line=line,
            side=side or None,
        )
        return {
            "repository": self._repo_identity(repo),
            "pr_id": resolved_pr_id,
            "comment": comment,
        }

    def reply_to_pr_comment(
        self,
        parent_id: str | int,
        body: str,
        *,
        pr_id: str | int = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Reply to an existing comment thread on a pull request."""
        target_reference = (reference or "").strip()
        target_pr_id = str(pr_id or "").strip()

        if not target_reference and not target_pr_id:
            raise ValueError("Either 'pr_id' or 'reference' (URL, ticket, or title/PR#) must be provided.")

        if target_reference:
            repo, pr = self.resolve_pr_from_reference(
                target_reference,
                provider=provider,
                workspace=workspace,
                slug=slug,
                repo_dir=repo_dir,
            )
            resolved_pr_id = pr.get("id") or target_pr_id
        else:
            repo = self.resolve_repository(
                provider=provider,
                workspace=workspace,
                slug=slug,
                scope=scope,
                repo_dir=repo_dir,
                allow_direct=True,
            )
            resolved_pr_id = target_pr_id

        comment = self.pr_comment_service.reply_to_comment(
            repo,
            resolved_pr_id,
            parent_id,
            body,
        )
        return {
            "repository": self._repo_identity(repo),
            "pr_id": resolved_pr_id,
            "parent_id": parent_id,
            "comment": comment,
        }

    def reply_to_pr_comments(
        self,
        replies: list[dict[str, Any]],
        *,
        pr_id: str | int = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Reply to several existing comment threads on one pull request."""
        if not replies:
            raise ValueError("At least one reply is required.")

        target_reference = (reference or "").strip()
        target_pr_id = str(pr_id or "").strip()
        if not target_reference and not target_pr_id:
            raise ValueError("Either 'pr_id' or 'reference' (URL, ticket, or title/PR#) must be provided.")

        if target_reference:
            repo, pr = self.resolve_pr_from_reference(
                target_reference,
                provider=provider,
                workspace=workspace,
                slug=slug,
                repo_dir=repo_dir,
            )
            resolved_pr_id = pr.get("id") or target_pr_id
        else:
            repo = self.resolve_repository(
                provider=provider,
                workspace=workspace,
                slug=slug,
                scope=scope,
                repo_dir=repo_dir,
                allow_direct=True,
            )
            resolved_pr_id = target_pr_id

        results: list[dict[str, Any]] = []
        for reply in replies:
            comment_id = reply.get("comment_id")
            body = reply.get("body")
            try:
                comment = self.pr_comment_service.reply_to_comment(
                    repo,
                    resolved_pr_id,
                    comment_id,
                    body,
                )
                results.append({
                    "comment_id": comment_id,
                    "success": True,
                    "comment": comment,
                })
            except Exception as exc:  # Keep independent replies from hiding partial success.
                results.append({
                    "comment_id": comment_id,
                    "success": False,
                    "error": str(exc),
                })

        succeeded = sum(1 for result in results if result["success"])
        return {
            "repository": self._repo_identity(repo),
            "pr_id": resolved_pr_id,
            "summary": {
                "requested": len(results),
                "succeeded": succeeded,
                "failed": len(results) - succeeded,
            },
            "results": results,
        }

    def edit_pr_comment(
        self,
        comment_id: str | int,
        body: str,
        *,
        pr_id: str | int = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Edit an existing comment on a pull request."""
        target_reference = (reference or "").strip()
        target_pr_id = str(pr_id or "").strip()

        if not target_reference and not target_pr_id:
            raise ValueError("Either 'pr_id' or 'reference' (URL, ticket, or title/PR#) must be provided.")

        if target_reference:
            repo, pr = self.resolve_pr_from_reference(
                target_reference,
                provider=provider,
                workspace=workspace,
                slug=slug,
                repo_dir=repo_dir,
            )
            resolved_pr_id = pr.get("id") or target_pr_id
        else:
            repo = self.resolve_repository(
                provider=provider,
                workspace=workspace,
                slug=slug,
                scope=scope,
                repo_dir=repo_dir,
                allow_direct=True,
            )
            resolved_pr_id = target_pr_id

        comment = self.pr_comment_service.edit_comment(
            repo,
            resolved_pr_id,
            comment_id,
            body,
        )
        return {
            "repository": self._repo_identity(repo),
            "pr_id": resolved_pr_id,
            "comment_id": comment_id,
            "comment": comment,
        }

    def delete_pr_comment(
        self,
        comment_id: str | int,
        *,
        pr_id: str | int = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Delete a comment from a pull request."""
        target_reference = (reference or "").strip()
        target_pr_id = str(pr_id or "").strip()

        if not target_reference and not target_pr_id:
            raise ValueError("Either 'pr_id' or 'reference' (URL, ticket, or title/PR#) must be provided.")

        if target_reference:
            repo, pr = self.resolve_pr_from_reference(
                target_reference,
                provider=provider,
                workspace=workspace,
                slug=slug,
                repo_dir=repo_dir,
            )
            resolved_pr_id = pr.get("id") or target_pr_id
        else:
            repo = self.resolve_repository(
                provider=provider,
                workspace=workspace,
                slug=slug,
                scope=scope,
                repo_dir=repo_dir,
                allow_direct=True,
            )
            resolved_pr_id = target_pr_id

        result = self.pr_comment_service.delete_comment(
            repo,
            resolved_pr_id,
            comment_id,
        )
        return {
            "repository": self._repo_identity(repo),
            "pr_id": resolved_pr_id,
            **result,
        }

    def resolve_pr_comment(
        self,
        comment_id: str | int,
        *,
        pr_id: str | int = "",
        reference: str = "",
        unresolve: bool = False,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Resolve or reopen/unresolve a comment thread on a pull request."""
        target_reference = (reference or "").strip()
        target_pr_id = str(pr_id or "").strip()

        if not target_reference and not target_pr_id:
            raise ValueError("Either 'pr_id' or 'reference' (URL, ticket, or title/PR#) must be provided.")

        if target_reference:
            repo, pr = self.resolve_pr_from_reference(
                target_reference,
                provider=provider,
                workspace=workspace,
                slug=slug,
                repo_dir=repo_dir,
            )
            resolved_pr_id = pr.get("id") or target_pr_id
        else:
            repo = self.resolve_repository(
                provider=provider,
                workspace=workspace,
                slug=slug,
                scope=scope,
                repo_dir=repo_dir,
                allow_direct=True,
            )
            resolved_pr_id = target_pr_id

        result = self.pr_comment_service.resolve_comment(
            repo,
            resolved_pr_id,
            comment_id,
            unresolve=unresolve,
        )
        return {
            "repository": self._repo_identity(repo),
            "pr_id": resolved_pr_id,
            **result,
        }

    def resolve_repository(
        self,
        *,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
        allow_direct: bool = False,
    ) -> dict:
        desired_provider = (provider or self.config.get_provider() or "").lower()
        desired_workspace = (workspace or "").strip().lower()
        desired_slug = (slug or "").strip().lower()
        specific_repo = self._specific_repo_from_scope(scope)
        if specific_repo:
            specific_workspace, specific_slug = self._split_repo_ref(specific_repo)
            desired_workspace = desired_workspace or specific_workspace
            desired_slug = desired_slug or specific_slug

        if allow_direct and provider and workspace and slug:
            return self._direct_repository(provider=provider, workspace=workspace, slug=slug, repo_dir=repo_dir)

        if allow_direct and repo_dir:
            return self.git_context_service.resolve(repo_dir=repo_dir).repository_dict()

        candidates = []
        active = self.config.get_active_repository()
        if active:
            candidates.append(active)
        candidates.extend(self.config.get_selected_repositories())

        seen_keys = set()
        deduped: list[dict] = []
        for repo in candidates:
            key = (
                (repo.get("provider") or "").lower(),
                (repo.get("owner") or "").lower(),
                (repo.get("slug") or repo.get("name") or "").lower(),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(repo)

        for repo in deduped:
            if desired_provider and (repo.get("provider") or "").lower() != desired_provider:
                continue
            if desired_workspace and (repo.get("owner") or "").lower() != desired_workspace:
                continue
            if desired_slug and (repo.get("slug") or repo.get("name") or "").lower() != desired_slug:
                continue
            return repo

        discovered = self.list_repositories(force_refresh=False)
        for repo in discovered:
            if desired_provider and (repo.get("provider") or "").lower() != desired_provider:
                continue
            if desired_workspace and (repo.get("owner") or "").lower() != desired_workspace:
                continue
            if desired_slug and not self._repository_name_matches(repo, desired_slug):
                continue
            return repo

        raise ValueError("Repository could not be resolved from active, selected, or discovered repositories.")

    @staticmethod
    def _direct_repository(*, provider: str, workspace: str, slug: str, repo_dir: str = "") -> dict[str, str]:
        normalized_provider = (provider or "").strip().lower()
        owner = (workspace or "").strip()
        repo_slug = (slug or "").strip()
        full_name = f"{owner}/{repo_slug}".strip("/")
        html_url = ""
        clone_url = ""
        if normalized_provider == "github":
            html_url = f"https://github.com/{full_name}"
            clone_url = f"https://github.com/{full_name}.git"
        elif normalized_provider == "bitbucket":
            html_url = f"https://bitbucket.org/{full_name}"
            clone_url = f"https://bitbucket.org/{full_name}.git"
        return {
            "provider": normalized_provider,
            "id": full_name,
            "name": repo_slug,
            "owner": owner,
            "slug": repo_slug,
            "full_name": full_name,
            "clone_url": clone_url,
            "html_url": html_url,
            "local_dir": repo_dir,
        }

    def _ticket_search_repositories(
        self,
        *,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "active",
        repo_dir: str = "",
    ) -> list[dict]:
        if repo_dir:
            return [
                self.resolve_repository(
                    provider=provider,
                    workspace=workspace,
                    slug=slug,
                    scope=scope,
                    repo_dir=repo_dir,
                    allow_direct=True,
                )
            ]
        if workspace or slug:
            return [self.resolve_repository(provider=provider, workspace=workspace, slug=slug, allow_direct=True)]

        normalized_scope = (scope or "active").strip().lower()
        if self._specific_repo_from_scope(scope):
            return [self.resolve_repository(provider=provider, scope=scope)]

        if normalized_scope == "selected":
            repositories = self.config.get_selected_repositories()
            if repositories:
                return repositories

        if normalized_scope == "all":
            return self.list_repositories(force_refresh=False)

        return [self.resolve_repository(provider=provider)]

    @staticmethod
    def _specific_repo_from_scope(scope: str) -> str:
        text = (scope or "").strip()
        if not text.lower().startswith("specific:"):
            return ""
        return text.split(":", 1)[1].strip()

    @staticmethod
    def _split_repo_ref(repo_ref: str) -> tuple[str, str]:
        value = (repo_ref or "").strip().strip("/")
        if "/" not in value:
            return "", value.lower()
        owner, slug = value.rsplit("/", 1)
        return owner.strip().lower(), slug.strip().lower()

    @classmethod
    def _repository_name_matches(cls, repo: dict, value: str) -> bool:
        expected = (value or "").strip().lower()
        slug = (repo.get("slug") or repo.get("name") or "").lower()
        name = (repo.get("name") or "").lower()
        return expected in {slug, name} or cls._normalized_repo_name(expected) in {
            cls._normalized_repo_name(slug),
            cls._normalized_repo_name(name),
        }

    @staticmethod
    def _normalized_repo_name(value: str) -> str:
        return "".join(ch for ch in (value or "").lower() if ch.isalnum())

    @staticmethod
    def _parse_tickets_json(value: str) -> list[str]:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError('tickets_json must be a JSON array, for example ["RU-25463"].') from exc
        if not isinstance(parsed, list):
            raise ValueError('tickets_json must be a JSON array, for example ["RU-25463"].')
        tickets = [str(item).strip() for item in parsed if str(item).strip()]
        if not tickets:
            raise ValueError("At least one ticket is required.")
        return tickets

    @staticmethod
    def _resolve_pr_repository(pr: dict, repositories: list[dict]) -> dict:
        repo_id = str(pr.get("repo_id") or "")
        repo_label = str(pr.get("repo_label") or "").lower()
        for repo in repositories:
            if repo_id and str(repo.get("id") or "") == repo_id:
                return repo
            owner = str(repo.get("owner") or "").lower()
            slug = str(repo.get("slug") or repo.get("name") or "").lower()
            if repo_label and repo_label == f"{owner}/{slug}".strip("/"):
                return repo
        if repositories:
            return repositories[0]
        raise ValueError("Unable to resolve repository for pull request.")

    def _repo_dir(self, repo: dict, *, ensure_checkout: bool) -> str:
        repo_dir = (repo.get("local_dir") or "").strip()
        if repo_dir:
            return repo_dir
        if not ensure_checkout:
            raise ValueError("Repository has no local checkout and ensure_checkout is disabled.")
        return RepositoryProvider.ensure_local_checkout(repo, self.config)

    @staticmethod
    def _repo_identity(repo: dict) -> dict[str, str]:
        return {
            "provider": repo.get("provider", ""),
            "owner": repo.get("owner", ""),
            "slug": repo.get("slug") or repo.get("name") or "",
            "full_name": repo.get("full_name") or "",
        }

    @staticmethod
    def _truncate_text(text: str, limit: int) -> tuple[str, bool]:
        if len(text) <= limit:
            return text, False
        return text[:limit], True


def create_mcp_server(backend: RepoLensMCPBackend | None = None):
    if FastMCP is None:
        raise ImportError("The 'mcp' package is required to run the MCP server.")

    backend = backend or RepoLensMCPBackend()
    app = FastMCP("RepoLens MCP", json_response=True)

    @app.tool()
    def get_active_context() -> dict[str, Any]:
        """Return the effective provider, repository context, and config source summary."""
        return backend.get_active_context()

    @app.tool()
    def list_repositories(force_refresh: bool = False) -> list[dict]:
        """List provider-backed repositories visible to the current credentials."""
        return backend.list_repositories(force_refresh=force_refresh)

    @app.tool()
    def get_selected_repositories() -> list[dict]:
        """Return the repositories currently selected in app configuration."""
        return backend.get_selected_repositories()

    @app.tool()
    def list_pull_requests(
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
        filter_mode: str = "open",
        search_text: str = "",
        developer: str = "",
        next_cursor: str = "",
    ) -> dict[str, Any]:
        """List pull requests for a repository, with optional filter and search support."""
        return backend.list_pull_requests(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            filter_mode=filter_mode,
            search_text=search_text,
            developer=developer,
            next_cursor=next_cursor,
        )

    @app.tool()
    def list_my_pull_requests(
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "active",
        repo_dir: str = "",
        filter_mode: str = "open",
    ) -> dict[str, Any]:
        """List pull requests authored by the authenticated provider user."""
        return backend.list_my_pull_requests(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            filter_mode=filter_mode,
        )

    @app.tool()
    def find_pull_requests_by_ticket(
        ticket: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "active",
        repo_dir: str = "",
        filter_mode: str = "all",
    ) -> dict[str, Any]:
        """Find one or more pull requests associated with a ticket id like RU-25463."""
        return backend.find_pull_requests_by_ticket(
            ticket=ticket,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            filter_mode=filter_mode,
        )

    @app.tool()
    def get_ticket_diffs(
        tickets_json: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "active",
        repo_dir: str = "",
        filter_mode: str = "all",
        ensure_checkout: bool = True,
        max_chars_per_pr: int = DEFAULT_DIFF_CHAR_LIMIT,
    ) -> dict[str, Any]:
        """Find PRs for one or more tickets and return grouped pull request diffs."""
        return backend.get_ticket_diffs(
            tickets_json=tickets_json,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            filter_mode=filter_mode,
            ensure_checkout=ensure_checkout,
            max_chars_per_pr=max_chars_per_pr,
        )

    @app.tool()
    def get_pr_diff(
        pr_id: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
        ensure_checkout: bool = True,
        max_chars: int = DEFAULT_DIFF_CHAR_LIMIT,
    ) -> dict[str, Any]:
        """Return a repository pull request diff and metadata."""
        return backend.get_pr_diff(
            pr_id=pr_id,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            ensure_checkout=ensure_checkout,
            max_chars=max_chars,
        )

    @app.tool()
    def get_commit_diff(
        commit_hash: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
        ensure_checkout: bool = True,
        max_chars: int = DEFAULT_DIFF_CHAR_LIMIT,
    ) -> dict[str, Any]:
        """Return a commit diff against its first parent."""
        return backend.get_commit_diff(
            commit_hash=commit_hash,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            ensure_checkout=ensure_checkout,
            max_chars=max_chars,
        )

    @app.tool()
    def query_contribution_history(
        developer: str,
        start_date: str = "",
        end_date: str = "",
        scope_type: str = "current_repo",
        contribution_type: str = "merged_prs",
        search_text: str = "",
        branch_filter: str = "",
        exclude_bots: bool = True,
        group_by: str = "none",
        titles_only: bool = False,
        repositories_json: str = "",
    ) -> dict[str, Any]:
        """Query contribution history. Use contributed_repos for fast Bitbucket PR-based discovery."""
        return backend.query_contribution_history(
            developer=developer,
            start_date=start_date,
            end_date=end_date,
            scope_type=scope_type,
            contribution_type=contribution_type,
            search_text=search_text,
            branch_filter=branch_filter,
            exclude_bots=exclude_bots,
            group_by=group_by,
            titles_only=titles_only,
            repositories_json=repositories_json,
        )

    @app.tool()
    def find_pr_for_commit(
        commit_hash: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
    ) -> dict[str, Any]:
        """Find the pull request(s) associated with a specific commit hash."""
        return backend.find_pr_for_commit(
            commit_hash=commit_hash,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
        )

    @app.tool()
    def search_contributions_by_ticket(
        ticket: str,
        scope_type: str = "all_repos",
        contribution_type: str = "prs_and_commits",
    ) -> dict[str, Any]:
        """Search for PRs and commits across repositories for a specific ticket ID."""
        return backend.search_contributions_by_ticket(
            ticket=ticket,
            scope_type=scope_type,
            contribution_type=contribution_type,
        )

    @app.tool()
    def analyze_file_history(
        file_path: str,
        since_date: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
    ) -> dict[str, Any]:
        """Analyze the history of a specific file, returning associated PRs and commits."""
        return backend.analyze_file_history(
            file_path=file_path,
            since_date=since_date,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
        )

    @app.tool()
    def get_pr_context(
        reference: str,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        repo_dir: str = "",
        ensure_checkout: bool = True,
        max_chars: int = DEFAULT_DIFF_CHAR_LIMIT,
        include_comments: bool = False,
    ) -> dict[str, Any]:
        """A unified tool to get both PR metadata and diff from a URL, ticket, or title, with optional comments."""
        return backend.get_pr_context(
            reference=reference,
            provider=provider,
            workspace=workspace,
            slug=slug,
            repo_dir=repo_dir,
            ensure_checkout=ensure_checkout,
            max_chars=max_chars,
            include_comments=include_comments,
        )

    @app.tool()
    def get_pr_comments(
        pr_id: str = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
        unresolved_only: bool = False,
        comment_type: str = "all",
        file_path: str = "",
        include_code_context: bool = True,
    ) -> dict[str, Any]:
        """Fetch comments and review threads for a pull request with code context and AI-ready summary.

        Args:
            pr_id: Pull request number or ID (e.g. "42").
            reference: PR URL, ticket ID (e.g. RU-25463), or PR number/title search.
            provider: 'github' or 'bitbucket' (defaults to configured provider).
            workspace: Repository owner/workspace.
            slug: Repository slug/name.
            scope: Repo scope ('active', 'selected', or 'specific:<owner>/<slug>').
            repo_dir: Local repository directory path (for local code snippet context).
            unresolved_only: If True, returns only unresolved comments and threads.
            comment_type: 'all', 'inline' (code diff comments only), or 'general' (PR conversation only).
            file_path: Optional filter for comments on a specific file path.
            include_code_context: If True, extracts surrounding code snippets from local checkout.
        """
        return backend.get_pr_comments(
            pr_id=pr_id,
            reference=reference,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
            unresolved_only=unresolved_only,
            comment_type=comment_type,
            file_path=file_path,
            include_code_context=include_code_context,
        )

    @app.tool()
    def add_pr_comment(
        body: str,
        pr_id: str = "",
        reference: str = "",
        file_path: str = "",
        line: Optional[int] = None,
        side: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Add a general comment or an inline code review comment to a pull request.

        Note for AI agents: Write in a natural, concise, human-like engineer tone (1-3 sentences).
        Avoid robotic AI pleasantries, greetings, or meta-commentary (e.g. avoid 'Certainly!',
        'Great suggestion!', 'Thank you for the review!', or 'As an AI...'). State technical details
        or changes directly.

        Args:
            body: The text of the comment to post (write in a concise, natural, human-like engineer tone).
            pr_id: Pull request number or ID (e.g. "42").
            reference: PR URL, ticket ID (e.g. RU-25463), or PR number/title search.
            file_path: Optional relative file path for inline code comments (e.g. "src/main.py").
            line: Optional line number for inline code comments.
            side: Optional diff side ('to'/'right' for added/modified, 'from'/'left' for deleted).
            provider: 'github' or 'bitbucket' (defaults to configured provider).
            workspace: Repository owner/workspace.
            slug: Repository slug/name.
            scope: Repo scope ('active', 'selected', or 'specific:<owner>/<slug>').
            repo_dir: Local repository directory path.
        """
        return backend.add_pr_comment(
            body=body,
            pr_id=pr_id,
            reference=reference,
            file_path=file_path,
            line=line,
            side=side,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )

    @app.tool()
    def reply_to_pr_comment(
        comment_id: str,
        body: str,
        pr_id: str = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Reply to an existing comment thread on a pull request.

        Note for AI agents: Write replies in a natural, concise, human engineer tone (1-3 sentences).
        Avoid robotic AI filler, pleasantries, or boilerplate (e.g. avoid 'Certainly!', 'Great catch!',
        'Thank you for the feedback!', or 'As an AI...'). State what was updated or resolved directly
        (e.g., 'Fixed in abc1234', 'Added the missing null check here', 'Renamed bean to avoid conflict').

        Args:
            comment_id: The ID of the comment to reply to.
            body: The text of the reply (write in a concise, natural, human-like engineer tone).
            pr_id: Pull request number or ID (e.g. "42").
            reference: PR URL, ticket ID (e.g. RU-25463), or PR number/title search.
            provider: 'github' or 'bitbucket' (defaults to configured provider).
            workspace: Repository owner/workspace.
            slug: Repository slug/name.
            scope: Repo scope ('active', 'selected', or 'specific:<owner>/<slug>').
            repo_dir: Local repository directory path.
        """
        return backend.reply_to_pr_comment(
            parent_id=comment_id,
            body=body,
            pr_id=pr_id,
            reference=reference,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )

    @app.tool()
    def reply_to_pr_comments(
        replies: list[dict[str, Any]],
        pr_id: str = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Reply to multiple comment threads on the same pull request in one call.

        Note for AI agents: Write replies in a natural, concise, human engineer tone (1-3 sentences per thread).
        Avoid robotic AI filler, pleasantries, or boilerplate (e.g. avoid 'Certainly!', 'Great catch!',
        'Thank you for the feedback!', or 'As an AI...'). State what was updated or resolved directly.

        Args:
            replies: Items containing 'comment_id' and 'body' (human-like, concise response text).
            pr_id: Pull request number or ID (e.g. "42").
            reference: PR URL, ticket ID, or PR number/title search.
            provider: 'github' or 'bitbucket' (defaults to configured provider).
            workspace: Repository owner/workspace.
            slug: Repository slug/name.
            scope: Repo scope ('active', 'selected', or 'specific:<owner>/<slug>').
            repo_dir: Local repository directory path.
        """
        return backend.reply_to_pr_comments(
            replies=replies,
            pr_id=pr_id,
            reference=reference,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )

    @app.tool()
    def edit_pr_comment(
        comment_id: str,
        body: str,
        pr_id: str = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Edit an existing comment on a pull request.

        Note for AI agents: Write in a natural, concise, human engineer tone. Avoid robotic AI filler
        or boilerplate. State technical context directly.

        Args:
            comment_id: The ID of the comment to edit.
            body: The updated text of the comment (write in a concise, natural, human-like engineer tone).
            pr_id: Pull request number or ID (e.g. "42").
            reference: PR URL, ticket ID (e.g. RU-25463), or PR number/title search.
            provider: 'github' or 'bitbucket' (defaults to configured provider).
            workspace: Repository owner/workspace.
            slug: Repository slug/name.
            scope: Repo scope ('active', 'selected', or 'specific:<owner>/<slug>').
            repo_dir: Local repository directory path.
        """
        return backend.edit_pr_comment(
            comment_id=comment_id,
            body=body,
            pr_id=pr_id,
            reference=reference,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )

    @app.tool()
    def delete_pr_comment(
        comment_id: str,
        pr_id: str = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Delete a comment from a pull request.

        Args:
            comment_id: The ID of the comment to delete.
            pr_id: Pull request number or ID (e.g. "42").
            reference: PR URL, ticket ID (e.g. RU-25463), or PR number/title search.
            provider: 'github' or 'bitbucket' (defaults to configured provider).
            workspace: Repository owner/workspace.
            slug: Repository slug/name.
            scope: Repo scope ('active', 'selected', or 'specific:<owner>/<slug>').
            repo_dir: Local repository directory path.
        """
        return backend.delete_pr_comment(
            comment_id=comment_id,
            pr_id=pr_id,
            reference=reference,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )

    @app.tool()
    def resolve_pr_comment(
        comment_id: str,
        pr_id: str = "",
        reference: str = "",
        unresolve: bool = False,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Resolve or reopen a comment thread on a pull request.

        Args:
            comment_id: The ID of the comment or review thread to resolve.
            pr_id: Pull request number or ID (e.g. "42").
            reference: PR URL, ticket ID (e.g. RU-25463), or PR number/title search.
            unresolve: If True, reopens/unresolves the thread instead of resolving it. Default False.
            provider: 'github' or 'bitbucket' (defaults to configured provider).
            workspace: Repository owner/workspace.
            slug: Repository slug/name.
            scope: Repo scope ('active', 'selected', or 'specific:<owner>/<slug>').
            repo_dir: Local repository directory path.
        """
        return backend.resolve_pr_comment(
            comment_id=comment_id,
            pr_id=pr_id,
            reference=reference,
            unresolve=unresolve,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )

    @app.tool()
    def unresolve_pr_comment(
        comment_id: str,
        pr_id: str = "",
        reference: str = "",
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Reopen / unresolve an existing comment thread on a pull request.

        Args:
            comment_id: The ID of the comment or review thread to unresolve.
            pr_id: Pull request number or ID (e.g. "42").
            reference: PR URL, ticket ID (e.g. RU-25463), or PR number/title search.
            provider: 'github' or 'bitbucket' (defaults to configured provider).
            workspace: Repository owner/workspace.
            slug: Repository slug/name.
            scope: Repo scope ('active', 'selected', or 'specific:<owner>/<slug>').
            repo_dir: Local repository directory path.
        """
        return backend.resolve_pr_comment(
            comment_id=comment_id,
            pr_id=pr_id,
            reference=reference,
            unresolve=True,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )

    @app.tool()
    def list_developer_candidates(
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        """List likely developer identities for a repository."""
        return backend.list_developer_candidates(
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            limit=limit,
        )

    @app.tool()
    def create_pull_request(
        title: str,
        source_branch: str,
        target_branch: str,
        description: str = "",
        draft: bool = False,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Create a GitHub or Bitbucket pull request from an existing remote branch."""
        return backend.create_pull_request(
            title=title,
            source_branch=source_branch,
            target_branch=target_branch,
            description=description,
            draft=draft,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )

    @app.tool()
    def update_pull_request(
        pr_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        target_branch: Optional[str] = None,
        provider: str = "",
        workspace: str = "",
        slug: str = "",
        scope: str = "",
        repo_dir: str = "",
    ) -> dict[str, Any]:
        """Update an existing GitHub or Bitbucket pull request."""
        return backend.update_pull_request(
            pr_id=pr_id,
            title=title,
            description=description,
            target_branch=target_branch,
            provider=provider,
            workspace=workspace,
            slug=slug,
            scope=scope,
            repo_dir=repo_dir,
        )

    @app.tool()
    def get_git_repository_context(
        repo_dir: str = "",
        source_branch: str = "",
        target_branch: str = "",
    ) -> dict[str, Any]:
        """Infer provider, repository, branch, remote, and auth context from a local Git checkout."""
        return backend.get_git_repository_context(
            repo_dir=repo_dir,
            source_branch=source_branch,
            target_branch=target_branch,
        )

    return app


def build_headless_config(cli_args: argparse.Namespace | None = None, env: dict[str, str] | None = None) -> HeadlessConfig:
    cli_overrides = {
        "provider": getattr(cli_args, "provider", ""),
        "bitbucket_username": getattr(cli_args, "bitbucket_username", ""),
        "bitbucket_api_token": (
            getattr(cli_args, "bitbucket_api_token", "")
            or getattr(cli_args, "bitbucket_app_password", "")
        ),
        "bitbucket_workspace": getattr(cli_args, "bitbucket_workspace", ""),
        "github_owner": getattr(cli_args, "github_owner", ""),
        "github_repo": getattr(cli_args, "github_repo", ""),
        "github_token": getattr(cli_args, "github_token", ""),
        "managed_repo_root": getattr(cli_args, "managed_repo_root", ""),
        "selected_repositories_json": getattr(cli_args, "selected_repositories_json", ""),
        "active_repo_provider": getattr(cli_args, "active_repo_provider", ""),
        "active_repo_id": getattr(cli_args, "active_repo_id", ""),
        "active_repo_name": getattr(cli_args, "active_repo_name", ""),
        "active_repo_owner": getattr(cli_args, "active_repo_owner", ""),
        "active_repo_slug": getattr(cli_args, "active_repo_slug", ""),
        "active_repo_clone_url": getattr(cli_args, "active_repo_clone_url", ""),
        "active_repo_html_url": getattr(cli_args, "active_repo_html_url", ""),
        "active_repo_local_dir": getattr(cli_args, "active_repo_local_dir", ""),
    }
    return HeadlessConfig.from_sources(
        base_config=ConfigManager(),
        env=env or os.environ,
        cli_overrides=cli_overrides,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RepoLens MCP server")
    parser.add_argument(
        "--allow-tty-stdio",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--provider")
    parser.add_argument("--bitbucket-username")
    parser.add_argument("--bitbucket-api-token")
    parser.add_argument("--bitbucket-app-password", help=argparse.SUPPRESS)
    parser.add_argument("--bitbucket-workspace")
    parser.add_argument("--github-owner")
    parser.add_argument("--github-repo")
    parser.add_argument("--github-token")
    parser.add_argument("--managed-repo-root")
    parser.add_argument("--selected-repositories-json")
    parser.add_argument("--active-repo-provider")
    parser.add_argument("--active-repo-id")
    parser.add_argument("--active-repo-name")
    parser.add_argument("--active-repo-owner")
    parser.add_argument("--active-repo-slug")
    parser.add_argument("--active-repo-clone-url")
    parser.add_argument("--active-repo-html-url")
    parser.add_argument("--active-repo-local-dir")
    return parser


def should_refuse_tty_stdio(stdin: Any = None) -> bool:
    stream = stdin if stdin is not None else sys.stdin
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    if not args.allow_tty_stdio and should_refuse_tty_stdio():
        parser.exit(
            2,
            "RepoLens MCP uses stdio and must be launched by an MCP client with piped stdin/stdout.\n"
            "Register it with your MCP client instead of running it directly in a terminal.\n"
            "For low-level debugging, pipe JSON-RPC input into the process or pass --allow-tty-stdio.\n",
        )
    app = create_mcp_server(RepoLensMCPBackend(build_headless_config(args)))
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
