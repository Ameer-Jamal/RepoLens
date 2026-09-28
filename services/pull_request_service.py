from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import requests

from services.PRAggregationService import PRAggregationService
from services.provider_api import ProviderClient
from services.pull_request_comment_service import PullRequestCommentService


_TICKET_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b", re.IGNORECASE)


class PullRequestService:
    def __init__(self, config):
        self.config = config
        self.comment_service = PullRequestCommentService(self.config, pr_service=self)

    def aggregate_pull_requests(
        self,
        selected_repos: list[dict],
        filter_mode: str,
        cursor_state: dict[str, str],
        reset: bool,
        developer: str = "",
    ) -> tuple[list[dict], dict[str, str]]:
        aggregated_records: list[dict] = []
        next_tokens: dict[str, str] = {}
        repo_fetch_pairs = PRAggregationService.repos_for_page(selected_repos, cursor_state, reset)
        resolved_developer = self.resolve_developer_filter(developer)

        def fetch(pair):
            repo, repo_next = pair
            records, token = self.list_pull_requests_for_repo(
                repo, filter_mode=filter_mode, next_cursor=repo_next or None,
                developer=resolved_developer,
            )
            return repo, records, token

        if len(repo_fetch_pairs) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(repo_fetch_pairs))) as pool:
                fetched = list(pool.map(fetch, repo_fetch_pairs))
        else:
            fetched = [fetch(pair) for pair in repo_fetch_pairs]

        for repo, repo_records, repo_next_token in fetched:
            aggregated_records.extend(repo_records)
            next_tokens[PRAggregationService.repo_cursor_key(repo)] = repo_next_token or ""

        aggregated_records.sort(key=lambda pr: pr.get("updated_on") or "", reverse=True)
        normalized_state = PRAggregationService.normalize_next_state(selected_repos, next_tokens)
        return aggregated_records, normalized_state

    def list_pull_requests_for_repo(
        self,
        repo: dict,
        *,
        filter_mode: str = "open",
        next_cursor: Optional[str] = None,
        search_text: str = "",
        developer: str = "",
    ) -> tuple[list[dict], Optional[str]]:
        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        resolved_developer = self.resolve_developer_filter(developer)
        if search_text:
            return self.search_pull_requests([repo], filter_mode, search_text, developer=resolved_developer), None
        if resolved_developer:
            # A developer view must include older PRs beyond the first provider
            # page. Filter mapped author identities after walking every page.
            records: list[dict] = []
            cursor = next_cursor
            seen_cursors: set[str] = set()
            while True:
                if provider == "github":
                    config = self._get_github_config(repo)
                    page, next_page = self._fetch_github_pull_requests_page(filter_mode, config, next_cursor=cursor)
                else:
                    config = self._get_bitbucket_config(repo)
                    page, next_page = self._fetch_bitbucket_pull_requests_page(filter_mode, config, next_cursor=cursor)
                page = [self._attach_repo_context(pr, repo) for pr in page]
                records.extend(self._filter_by_developer(page, resolved_developer))
                if not next_page or next_page in seen_cursors:
                    break
                seen_cursors.add(next_page)
                cursor = next_page
            return records, None
        if provider == "github":
            config = self._get_github_config(repo)
            records, cursor = self._fetch_github_pull_requests_page(filter_mode, config, next_cursor=next_cursor)
        else:
            config = self._get_bitbucket_config(repo)
            records, cursor = self._fetch_bitbucket_pull_requests_page(filter_mode, config, next_cursor=next_cursor)
        records = [self._attach_repo_context(pr, repo) for pr in records]
        return self._filter_by_developer(records, resolved_developer), cursor

    def search_pull_requests(
        self,
        selected_repos: list[dict],
        filter_mode: str,
        query: str,
        developer: str = "",
    ) -> list[dict]:
        provider = (self.config.get_provider() or "bitbucket").lower()
        results: list[dict] = []
        resolved_developer = self.resolve_developer_filter(developer)
        if provider == "bitbucket":
            username = (self.config.get_bitbucket_username() or "").strip()
            password = (self.config.get_bitbucket_api_token() or "").strip()
            if not username or not password:
                return results

            state_clause = ""
            if filter_mode == "open":
                state_clause = ' AND state = "OPEN"'
            elif filter_mode == "merged":
                state_clause = ' AND state = "MERGED"'

            expression = self._bitbucket_query_expression(query, "", state_clause)

            for repo in selected_repos:
                workspace = (repo.get("owner") or "").strip()
                slug = (repo.get("slug") or "").strip()
                if not workspace or not slug:
                    continue
                next_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/pullrequests"
                params = {"pagelen": 50}
                if expression:
                    params["q"] = expression
                while next_url:
                    response = requests.get(
                        next_url,
                        params=params,
                        auth=(username, password),
                        timeout=15,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    for pr in payload.get("values", []):
                        results.append(self._attach_repo_context(self._map_bitbucket_pr(pr), repo))
                    next_url = payload.get("next")
                    params = None
            return self._filter_by_developer(results, resolved_developer)

        token = (self.config.get_github_token() or "").strip()
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"

        state_term = ""
        if filter_mode == "open":
            state_term = "state:open"
        elif filter_mode == "merged":
            state_term = "state:closed"

        for repo in selected_repos:
            owner = (repo.get("owner") or "").strip()
            slug = (repo.get("slug") or repo.get("name") or "").strip()
            if not owner or not slug:
                continue
            search_query = self._github_search_query(owner, slug, state_term, query, resolved_developer)
            response = requests.get(
                "https://api.github.com/search/issues",
                params={"q": search_query, "per_page": 30},
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
            for issue in response.json().get("items", []):
                pr_url = (issue.get("pull_request") or {}).get("url")
                if not pr_url:
                    continue
                pr_response = requests.get(pr_url, headers=headers, timeout=15)
                pr_response.raise_for_status()
                results.append(self._attach_repo_context(self._map_github_pr(pr_response.json()), repo))

        return self._filter_by_developer(results, resolved_developer)

    def find_pull_requests_by_ticket(
        self,
        selected_repos: list[dict],
        ticket: str,
        *,
        filter_mode: str = "all",
    ) -> list[dict]:
        ticket_id = self.extract_ticket_id(ticket)
        if not ticket_id:
            raise ValueError("A ticket id like RU-25463 is required.")

        results = self.search_pull_requests(selected_repos, filter_mode, ticket_id)
        exact_matches = [
            pr
            for pr in results
            if self._pull_request_matches_ticket(pr, ticket_id)
        ]
        exact_matches.sort(
            key=lambda pr: (
                pr.get("updated_on") or "",
                str(pr.get("repo_label") or ""),
                str(pr.get("id") or ""),
            ),
            reverse=True,
        )
        return exact_matches

    def resolve_developer_filter(self, developer: str) -> str:
        value = (developer or "").strip()
        if value.lower() not in {"me", "@me", "self", "mine"}:
            return value
        provider = self._provider_client()
        user = provider.validate_credentials()
        aliases = [
            user.username,
            user.display_name,
            user.email,
        ]
        return ", ".join(alias for alias in aliases if alias)

    def get_pull_request(self, repo: dict, pr_id: str | int) -> dict:
        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        if provider == "github":
            config = self._get_github_config(repo)
            response = requests.get(
                f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls/{pr_id}",
                headers=config["headers"],
                timeout=15,
            )
            response.raise_for_status()
            return self._attach_repo_context(self._map_github_pr(response.json()), repo)

        config = self._get_bitbucket_config(repo)
        response = requests.get(
            f"https://api.bitbucket.org/2.0/repositories/{config['workspace']}/{config['slug']}/pullrequests/{pr_id}",
            auth=(config["username"], config["password"]),
            timeout=15,
        )
        response.raise_for_status()
        return self._attach_repo_context(self._map_bitbucket_pr(response.json()), repo)

    def _get_bitbucket_config(self, repo: dict) -> dict:
        username = (self.config.get_bitbucket_username() or "").strip()
        password = (self.config.get_bitbucket_api_token() or "").strip()
        workspace = (repo.get("owner") or self.config.get_bitbucket_workspace() or "").strip()
        slug = (repo.get("slug") or "").strip()
        if not username or not password:
            raise ValueError("Atlassian account email and Bitbucket API token are required.")
        if not workspace or not slug:
            raise ValueError("Bitbucket workspace and repository slug are required.")
        return {
            "username": username,
            "password": password,
            "workspace": workspace,
            "slug": slug,
        }

    def _get_github_config(self, repo: dict) -> dict:
        owner = (repo.get("owner") or self.config.get_github_owner() or "").strip()
        repo_name = (repo.get("slug") or repo.get("name") or "").strip()
        if not owner or not repo_name:
            raise ValueError("GitHub owner and repository are required.")
        headers = {"Accept": "application/vnd.github+json"}
        token = (self.config.get_github_token() or "").strip()
        if token:
            headers["Authorization"] = f"token {token}"
        return {
            "owner": owner,
            "repo": repo_name,
            "headers": headers,
        }

    def _fetch_bitbucket_pull_requests_page(
        self,
        filter_mode: str,
        config: dict,
        next_cursor: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        if next_cursor:
            request_url = next_cursor
            request_params = None
        else:
            states = ["OPEN"] if filter_mode == "open" else ["MERGED"] if filter_mode == "merged" else ["OPEN", "MERGED"]
            request_url = f"https://api.bitbucket.org/2.0/repositories/{config['workspace']}/{config['slug']}/pullrequests"
            request_params = [("pagelen", "50")]
            request_params.extend(("state", state) for state in states)

        response = requests.get(
            request_url,
            params=request_params,
            auth=(config["username"], config["password"]),
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return [self._map_bitbucket_pr(pr) for pr in data.get("values", [])], data.get("next")

    def _fetch_github_pull_requests_page(
        self,
        filter_mode: str,
        config: dict,
        next_cursor: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        if next_cursor:
            request_url = next_cursor
            request_params = None
        else:
            state_param = "open" if filter_mode == "open" else "closed" if filter_mode == "merged" else "all"
            request_url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls"
            request_params = {"per_page": 50, "state": state_param}

        response = requests.get(
            request_url,
            params=request_params,
            headers=config["headers"],
            timeout=15,
        )
        response.raise_for_status()
        records: list[dict] = []
        for pr_record in response.json():
            is_merged = bool(pr_record.get("merged_at"))
            state = pr_record.get("state", "open")
            if filter_mode == "merged" and not is_merged:
                continue
            if filter_mode == "open" and state != "open":
                continue
            records.append(self._map_github_pr(pr_record))
        return records, self._github_next_link(response.headers.get("Link"))

    @staticmethod
    def _attach_repo_context(pr: dict, repo: dict) -> dict:
        repo_copy = dict(pr)
        owner = (repo.get("owner") or "").strip()
        slug = (repo.get("slug") or repo.get("name") or "").strip()
        repo_copy["repo_id"] = str(repo.get("id", ""))
        repo_copy["repo_label"] = f"{owner}/{slug}".strip("/")
        repo_copy["repo_local_dir"] = (repo.get("local_dir") or "").strip()
        return repo_copy

    @staticmethod
    def _matches_developer(*candidates: str, developer: str) -> bool:
        developer = (developer or "").strip()
        if not developer:
            return True
        terms = [part.strip().lower() for part in re.split(r"[,\n;]+", developer) if part.strip()]
        values = [value.strip().lower() for value in candidates if value]
        for term in terms:
            if any(term in value for value in values):
                return True
            normalized_term = re.sub(r"[^a-z0-9]+", "", term)
            if not normalized_term:
                continue
            for value in values:
                normalized_value = re.sub(r"[^a-z0-9]+", "", value)
                if normalized_value and (
                    normalized_term in normalized_value or normalized_value in normalized_term
                ):
                    return True
        return False

    def _filter_by_developer(self, records: list[dict], developer: str) -> list[dict]:
        if not (developer or "").strip():
            return records
        return [
            pr
            for pr in records
            if self._matches_developer(
                pr.get("author") or "",
                pr.get("author_username") or "",
                pr.get("author_nickname") or "",
                pr.get("author_email") or "",
                pr.get("author_account_id") or "",
                developer=developer,
            )
        ]

    def _provider_client(self) -> ProviderClient:
        from services.provider_api import build_provider_client

        return build_provider_client(self.config)

    @staticmethod
    def _bitbucket_query_expression(query: str, developer: str, state_clause: str) -> str:
        clauses: list[str] = []
        query = (query or "").strip()
        if query:
            escaped_query = query.replace('"', '\\"')
            clauses.append(
                f'(title ~ "{escaped_query}" OR source.branch.name ~ "{escaped_query}" '
                f'OR destination.branch.name ~ "{escaped_query}")'
            )
        expression = " AND ".join(clauses) if clauses else ""
        if state_clause:
            expression = f"{expression}{state_clause}" if expression else state_clause.replace(" AND ", "", 1)
        return expression

    @staticmethod
    def _github_search_query(owner: str, slug: str, state_term: str, query: str, developer: str) -> str:
        terms = [f"repo:{owner}/{slug}", "is:pr"]
        if state_term:
            terms.append(state_term)
        query = (query or "").strip()
        if query:
            terms.append(query)
        developer_terms = [
            part.strip()
            for part in re.split(r"[,\n;]+", developer or "")
            if part.strip()
        ]
        if developer_terms:
            # GitHub search only supports exact login for author. Use the first alias
            # and keep local filtering as a backstop for non-login aliases.
            terms.append(f"author:{developer_terms[0]}")
        return " ".join(terms).strip()

    @staticmethod
    def extract_ticket_id(value: str) -> str:
        match = _TICKET_PATTERN.search(value or "")
        return match.group(0).upper() if match else ""

    @staticmethod
    def _pull_request_matches_ticket(pr: dict, ticket_id: str) -> bool:
        haystack = " ".join(
            str(pr.get(field) or "")
            for field in (
                "title",
                "source_branch",
                "destination_branch",
                "description",
                "link",
            )
        )
        return ticket_id.upper() in haystack.upper()

    @staticmethod
    def _map_bitbucket_pr(pr_record: dict) -> dict:
        source = pr_record.get("source") or {}
        destination = pr_record.get("destination") or {}
        links = pr_record.get("links") or {}
        merge_commit = pr_record.get("merge_commit") or {}
        summary = pr_record.get("summary") or {}
        return {
            "id": pr_record.get("id"),
            "title": pr_record.get("title"),
            "state": (pr_record.get("state") or "").upper(),
            "author": ((pr_record.get("author") or {}).get("display_name")),
            "author_username": ((pr_record.get("author") or {}).get("username")),
            "author_nickname": ((pr_record.get("author") or {}).get("nickname")),
            "author_account_id": ((pr_record.get("author") or {}).get("account_id")),
            "source_branch": (source.get("branch") or {}).get("name"),
            "destination_branch": (destination.get("branch") or {}).get("name"),
            "source_commit": (source.get("commit") or {}).get("hash"),
            "destination_commit": (destination.get("commit") or {}).get("hash"),
            "merge_commit": merge_commit.get("hash"),
            "link": (links.get("html") or {}).get("href"),
            "description": summary.get("raw") or pr_record.get("description"),
            "updated_on": pr_record.get("updated_on"),
            "provider": "bitbucket",
        }

    @staticmethod
    def _map_github_pr(pr_record: dict) -> dict:
        head = pr_record.get("head") or {}
        base = pr_record.get("base") or {}
        user = pr_record.get("user") or {}
        merged = bool(pr_record.get("merged_at"))
        state = "MERGED" if merged else (pr_record.get("state") or "open").upper()
        return {
            "id": pr_record.get("number"),
            "title": pr_record.get("title"),
            "state": state,
            "author": user.get("login"),
            "author_username": user.get("login"),
            "source_branch": head.get("ref"),
            "destination_branch": base.get("ref"),
            "source_commit": head.get("sha"),
            "destination_commit": base.get("sha"),
            "merge_commit": pr_record.get("merge_commit_sha"),
            "link": pr_record.get("html_url"),
            "description": pr_record.get("body"),
            "updated_on": pr_record.get("updated_at"),
            "provider": "github",
        }

    def _github_next_link(self, link_header: Optional[str]) -> Optional[str]:
        if not link_header:
            return None
        for part in link_header.split(","):
            section = part.strip().split(";")
            if len(section) < 2:
                continue
            if section[1].strip() == 'rel="next"':
                return section[0].strip().strip("<>")
        return None

    def parse_pr_url(self, url: str) -> Optional[dict[str, str]]:
        """Parses a PR URL and returns a dict with provider, workspace, slug, and pr_id."""
        url = url.strip()
        # Bitbucket: https://bitbucket.org/{workspace}/{slug}/pull-requests/{id}
        bb_match = re.search(r"bitbucket\.org/([^/]+)/([^/]+)/pull-requests/(\d+)", url)
        if bb_match:
            return {
                "provider": "bitbucket",
                "workspace": bb_match.group(1),
                "slug": bb_match.group(2),
                "pr_id": bb_match.group(3),
            }
        
        # GitHub: https://github.com/{owner}/{repo}/pull/{id}
        gh_match = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
        if gh_match:
            return {
                "provider": "github",
                "workspace": gh_match.group(1),
                "slug": gh_match.group(2),
                "pr_id": gh_match.group(3),
            }
        
        return None

    def get_pull_request_comments(
        self,
        repo: dict,
        pr_id: str | int,
        *,
        unresolved_only: bool = False,
        comment_type: str = "all",
        file_path: str = "",
        repo_dir: str = "",
        include_code_context: bool = True,
    ) -> dict[str, Any]:
        return self.comment_service.get_pull_request_comments(
            repo,
            pr_id,
            unresolved_only=unresolved_only,
            comment_type=comment_type,
            file_path=file_path,
            repo_dir=repo_dir,
            include_code_context=include_code_context,
        )

    def _to_repo_ref(self, repo: dict):
        from models.contribution_models import RepositoryRef
        return RepositoryRef.from_dict(repo)

    def _provider_client_for_repo(self, repo: dict):
        from services.provider_api import build_provider_client_for_name
        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        return build_provider_client_for_name(provider, self.config)

    def get_pull_request_statuses(self, repo: dict, commit_hash: str) -> dict[str, Any]:
        client = self._provider_client_for_repo(repo)
        repo_ref = self._to_repo_ref(repo)
        return client.get_pull_request_statuses(repo_ref, commit_hash)

    def approve_pull_request(self, repo: dict, pr_id: str | int, comment: str = "") -> dict[str, Any]:
        client = self._provider_client_for_repo(repo)
        repo_ref = self._to_repo_ref(repo)
        return client.approve_pull_request(repo_ref, pr_id, comment=comment)

    def unapprove_pull_request(self, repo: dict, pr_id: str | int) -> dict[str, Any]:
        client = self._provider_client_for_repo(repo)
        repo_ref = self._to_repo_ref(repo)
        return client.unapprove_pull_request(repo_ref, pr_id)

    def request_changes_on_pr(self, repo: dict, pr_id: str | int, comment: str) -> dict[str, Any]:
        client = self._provider_client_for_repo(repo)
        repo_ref = self._to_repo_ref(repo)
        return client.request_changes_on_pr(repo_ref, pr_id, comment=comment)

    def get_file_content_at_ref(self, repo: dict, file_path: str, ref: str) -> str:
        client = self._provider_client_for_repo(repo)
        repo_ref = self._to_repo_ref(repo)
        return client.get_file_content(repo_ref, file_path, ref)

    def get_pull_request_diff_text(self, repo: dict, pr_id: str | int) -> str:
        client = self._provider_client_for_repo(repo)
        repo_ref = self._to_repo_ref(repo)
        return client.get_pull_request_diff_text(repo_ref, pr_id)

    # Legacy / backward-compatibility aliases
    _map_bitbucket_comment = staticmethod(PullRequestCommentService._map_bitbucket_comment)
    _map_github_review_comment = staticmethod(PullRequestCommentService._map_github_review_comment)
    _map_github_issue_comment = staticmethod(PullRequestCommentService._map_github_issue_comment)
    _map_github_review_summary = staticmethod(PullRequestCommentService._map_github_review_summary)
    _extract_local_code_context = staticmethod(PullRequestCommentService._extract_local_code_context)
    _build_comment_threads = staticmethod(PullRequestCommentService._build_comment_threads)
    _format_ai_comments_summary = staticmethod(PullRequestCommentService._format_ai_comments_summary)
