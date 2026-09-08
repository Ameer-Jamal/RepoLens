from __future__ import annotations

import os
from typing import Any, Optional

import requests


class PullRequestCommentService:
    """Independent service for managing Pull Request comments and review threads."""

    def __init__(self, config: Any, pr_service: Any = None):
        self.config = config
        self.pr_service = pr_service

    # -------------------------------------------------------------------------
    # Provider Config Helpers
    # -------------------------------------------------------------------------

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

    @staticmethod
    def _github_next_link(link_header: Optional[str]) -> Optional[str]:
        if not link_header:
            return None
        for part in link_header.split(","):
            segments = part.split(";")
            if len(segments) < 2:
                continue
            url_part = segments[0].strip().strip("<>")
            rel_part = segments[1].strip()
            if 'rel="next"' in rel_part:
                return url_part
        return None

    def _get_pr_metadata(self, repo: dict, pr_id: str | int) -> dict:
        """Fetch basic PR metadata for context formatting."""
        if self.pr_service and hasattr(self.pr_service, "get_pull_request"):
            try:
                return self.pr_service.get_pull_request(repo, pr_id)
            except Exception:
                pass

        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        if provider == "github":
            try:
                config = self._get_github_config(repo)
                response = requests.get(
                    f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls/{pr_id}",
                    headers=config["headers"],
                    timeout=15,
                )
                if response.ok:
                    data = response.json()
                    user = data.get("user") or {}
                    return {
                        "id": data.get("number") or pr_id,
                        "title": data.get("title") or f"Pull Request #{pr_id}",
                        "state": (data.get("state") or "open").upper(),
                        "author": user.get("login") or "",
                        "head_sha": data.get("head", {}).get("sha") or "",
                    }
            except Exception:
                pass
        else:
            try:
                config = self._get_bitbucket_config(repo)
                response = requests.get(
                    f"https://api.bitbucket.org/2.0/repositories/{config['workspace']}/{config['slug']}/pullrequests/{pr_id}",
                    auth=(config["username"], config["password"]),
                    timeout=15,
                )
                if response.ok:
                    data = response.json()
                    author = data.get("author") or {}
                    return {
                        "id": data.get("id") or pr_id,
                        "title": data.get("title") or f"Pull Request #{pr_id}",
                        "state": (data.get("state") or "OPEN").upper(),
                        "author": author.get("display_name") or author.get("username") or "",
                    }
            except Exception:
                pass

        return {"id": pr_id, "title": f"Pull Request #{pr_id}", "state": "OPEN", "author": ""}

    # -------------------------------------------------------------------------
    # Read PR Comments & Review Threads
    # -------------------------------------------------------------------------

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
        """Fetch normalized comments and threads for a PR with code context and AI summary."""
        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        effective_repo_dir = repo_dir or repo.get("local_dir") or ""
        pr = self._get_pr_metadata(repo, pr_id)

        reviews_data: list[dict] = []
        if provider == "github":
            raw_review_comments, raw_issue_comments, raw_reviews = self._fetch_github_comments(repo, pr_id)
            resolution_map = self._fetch_github_resolution_status(repo, pr_id)
            normalized_comments = [
                self._map_github_review_comment(c, resolution_map)
                for c in raw_review_comments
            ]
            normalized_comments.extend(
                self._map_github_issue_comment(c)
                for c in raw_issue_comments
            )
            for r in raw_reviews:
                rev = self._map_github_review_summary(r)
                if rev:
                    reviews_data.append(rev)
                    normalized_comments.append(rev)
        else:
            raw_comments = self._fetch_bitbucket_comments(repo, pr_id)
            child_parent_ids = {
                c.get("parent", {}).get("id")
                for c in raw_comments
                if c.get("parent", {}).get("id")
            }
            normalized_comments = [
                self._map_bitbucket_comment(c)
                for c in raw_comments
                if not c.get("deleted") or c.get("id") in child_parent_ids
            ]

        # Extract local code snippet for inline comments if requested
        if include_code_context and effective_repo_dir:
            for c in normalized_comments:
                if c.get("file_path") and c.get("line"):
                    c["code_snippet"] = self._extract_local_code_context(
                        effective_repo_dir, c["file_path"], c["line"]
                    )

        # Build threads and apply filters
        threads, filtered_comments = self._build_comment_threads(
            normalized_comments,
            unresolved_only=unresolved_only,
            comment_type=comment_type,
            file_path_filter=file_path,
        )

        all_threads, _ = self._build_comment_threads(normalized_comments)
        files_with_comments = sorted(list({
            t["file_path"] for t in all_threads if t.get("file_path")
        }))
        unresolved_count = sum(1 for t in all_threads if not t["resolved"])
        resolved_count = sum(1 for t in all_threads if t["resolved"])
        inline_count = sum(1 for t in all_threads if t["comment_type"] == "inline")
        general_count = sum(1 for t in all_threads if t["comment_type"] != "inline")

        summary = {
            "total_comments": len(normalized_comments),
            "total_threads": len(all_threads),
            "unresolved_threads": unresolved_count,
            "resolved_threads": resolved_count,
            "inline_threads": inline_count,
            "general_threads": general_count,
            "filtered_threads": len(threads),
            "filtered_comments": len(filtered_comments),
            "files_with_comments": files_with_comments,
        }

        formatted_summary = self._format_ai_comments_summary(repo, pr, threads, summary)

        return {
            "pull_request": pr,
            "summary": summary,
            "threads": threads,
            "comments": filtered_comments,
            "reviews": reviews_data,
            "formatted_summary": formatted_summary,
        }

    # -------------------------------------------------------------------------
    # Write PR Comments (Add, Reply, Edit, Delete)
    # -------------------------------------------------------------------------

    def add_comment(
        self,
        repo: dict,
        pr_id: str | int,
        body: str,
        *,
        file_path: Optional[str] = None,
        line: Optional[int] = None,
        side: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add a general or inline comment to a pull request."""
        comment_body = (body or "").strip()
        if not comment_body:
            raise ValueError("Comment body cannot be empty.")

        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        if provider == "github":
            return self._add_github_comment(
                repo, pr_id, comment_body, file_path=file_path, line=line, side=side
            )
        return self._add_bitbucket_comment(
            repo, pr_id, comment_body, file_path=file_path, line=line, side=side
        )

    def reply_to_comment(
        self,
        repo: dict,
        pr_id: str | int,
        parent_id: str | int,
        body: str,
    ) -> dict[str, Any]:
        """Reply to an existing comment thread on a pull request."""
        comment_body = (body or "").strip()
        if not comment_body:
            raise ValueError("Reply body cannot be empty.")
        if not parent_id:
            raise ValueError("Parent comment ID is required to post a reply.")

        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        if provider == "github":
            return self._reply_github_comment(repo, pr_id, parent_id, comment_body)
        return self._reply_bitbucket_comment(repo, pr_id, parent_id, comment_body)

    def edit_comment(
        self,
        repo: dict,
        pr_id: str | int,
        comment_id: str | int,
        body: str,
    ) -> dict[str, Any]:
        """Edit an existing comment on a pull request."""
        comment_body = (body or "").strip()
        if not comment_body:
            raise ValueError("Updated comment body cannot be empty.")
        if not comment_id:
            raise ValueError("Comment ID is required to edit a comment.")

        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        if provider == "github":
            return self._edit_github_comment(repo, pr_id, comment_id, comment_body)
        return self._edit_bitbucket_comment(repo, pr_id, comment_id, comment_body)

    def delete_comment(
        self,
        repo: dict,
        pr_id: str | int,
        comment_id: str | int,
    ) -> dict[str, Any]:
        """Delete a comment from a pull request."""
        if not comment_id:
            raise ValueError("Comment ID is required to delete a comment.")

        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        if provider == "github":
            return self._delete_github_comment(repo, pr_id, comment_id)
        return self._delete_bitbucket_comment(repo, pr_id, comment_id)

    def resolve_comment(
        self,
        repo: dict,
        pr_id: str | int,
        comment_id: str | int,
        *,
        unresolve: bool = False,
    ) -> dict[str, Any]:
        """Resolve or reopen/unresolve a comment / review thread on a pull request."""
        if not comment_id:
            raise ValueError("Comment ID is required to resolve a comment.")

        provider = (repo.get("provider") or self.config.get_provider() or "bitbucket").lower()
        if provider == "github":
            return self._resolve_github_thread(repo, pr_id, comment_id, unresolve=unresolve)
        return self._resolve_bitbucket_comment(repo, pr_id, comment_id, unresolve=unresolve)

    # -------------------------------------------------------------------------
    # Bitbucket Implementation Details
    # -------------------------------------------------------------------------

    def _fetch_bitbucket_comments(self, repo: dict, pr_id: str | int) -> list[dict]:
        config = self._get_bitbucket_config(repo)
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{config['workspace']}/{config['slug']}/pullrequests/{pr_id}/comments"
        )
        params = {"pagelen": 100}
        comments: list[dict] = []
        while url:
            response = requests.get(
                url,
                params=params,
                auth=(config["username"], config["password"]),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            comments.extend(payload.get("values", []))
            url = payload.get("next")
            params = None
        return comments

    def _add_bitbucket_comment(
        self,
        repo: dict,
        pr_id: str | int,
        body: str,
        *,
        file_path: Optional[str] = None,
        line: Optional[int] = None,
        side: Optional[str] = None,
    ) -> dict[str, Any]:
        config = self._get_bitbucket_config(repo)
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{config['workspace']}/{config['slug']}/pullrequests/{pr_id}/comments"
        )
        payload: dict[str, Any] = {"content": {"raw": body}}
        if file_path and line is not None:
            inline_data: dict[str, Any] = {"path": file_path}
            if side and side.lower() in ("from", "left"):
                inline_data["from"] = int(line)
            else:
                inline_data["to"] = int(line)
            payload["inline"] = inline_data

        response = requests.post(
            url,
            json=payload,
            auth=(config["username"], config["password"]),
            timeout=20,
        )
        response.raise_for_status()
        return self._map_bitbucket_comment(response.json())

    def _reply_bitbucket_comment(
        self,
        repo: dict,
        pr_id: str | int,
        parent_id: str | int,
        body: str,
    ) -> dict[str, Any]:
        config = self._get_bitbucket_config(repo)
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{config['workspace']}/{config['slug']}/pullrequests/{pr_id}/comments"
        )
        payload = {
            "content": {"raw": body},
            "parent": {"id": int(parent_id)},
        }
        response = requests.post(
            url,
            json=payload,
            auth=(config["username"], config["password"]),
            timeout=20,
        )
        response.raise_for_status()
        return self._map_bitbucket_comment(response.json())

    def _edit_bitbucket_comment(
        self,
        repo: dict,
        pr_id: str | int,
        comment_id: str | int,
        body: str,
    ) -> dict[str, Any]:
        config = self._get_bitbucket_config(repo)
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{config['workspace']}/{config['slug']}/pullrequests/{pr_id}/comments/{comment_id}"
        )
        payload = {"content": {"raw": body}}
        response = requests.put(
            url,
            json=payload,
            auth=(config["username"], config["password"]),
            timeout=20,
        )
        response.raise_for_status()
        return self._map_bitbucket_comment(response.json())

    def _delete_bitbucket_comment(
        self,
        repo: dict,
        pr_id: str | int,
        comment_id: str | int,
    ) -> dict[str, Any]:
        config = self._get_bitbucket_config(repo)
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{config['workspace']}/{config['slug']}/pullrequests/{pr_id}/comments/{comment_id}"
        )
        response = requests.delete(
            url,
            auth=(config["username"], config["password"]),
            timeout=20,
        )
        response.raise_for_status()
        return {
            "success": True,
            "comment_id": int(comment_id),
            "deleted": True,
            "provider": "bitbucket",
        }

    def _resolve_bitbucket_comment(
        self,
        repo: dict,
        pr_id: str | int,
        comment_id: str | int,
        *,
        unresolve: bool = False,
    ) -> dict[str, Any]:
        config = self._get_bitbucket_config(repo)
        url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{config['workspace']}/{config['slug']}/pullrequests/{pr_id}/comments/{comment_id}/resolve"
        )
        if unresolve:
            response = requests.delete(
                url,
                auth=(config["username"], config["password"]),
                timeout=20,
            )
        else:
            response = requests.post(
                url,
                auth=(config["username"], config["password"]),
                timeout=20,
            )

        # Fallback: If comment_id was a reply, the resolve endpoint may require the root comment ID
        if not response.ok and response.status_code == 404:
            comment_url = (
                f"https://api.bitbucket.org/2.0/repositories/"
                f"{config['workspace']}/{config['slug']}/pullrequests/{pr_id}/comments/{comment_id}"
            )
            c_res = requests.get(comment_url, auth=(config["username"], config["password"]), timeout=15)
            if c_res.ok:
                parent_id = c_res.json().get("parent", {}).get("id")
                if parent_id and str(parent_id) != str(comment_id):
                    parent_url = (
                        f"https://api.bitbucket.org/2.0/repositories/"
                        f"{config['workspace']}/{config['slug']}/pullrequests/{pr_id}/comments/{parent_id}/resolve"
                    )
                    if unresolve:
                        response = requests.delete(
                            parent_url,
                            auth=(config["username"], config["password"]),
                            timeout=20,
                        )
                    else:
                        response = requests.post(
                            parent_url,
                            auth=(config["username"], config["password"]),
                            timeout=20,
                        )

        response.raise_for_status()
        return {
            "success": True,
            "comment_id": int(comment_id) if str(comment_id).isdigit() else comment_id,
            "resolved": not unresolve,
            "provider": "bitbucket",
        }

    # -------------------------------------------------------------------------
    # GitHub Implementation Details
    # -------------------------------------------------------------------------

    def _fetch_github_comments(
        self, repo: dict, pr_id: str | int
    ) -> tuple[list[dict], list[dict], list[dict]]:
        config = self._get_github_config(repo)
        headers = config["headers"]

        review_comments: list[dict] = []
        url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls/{pr_id}/comments"
        params = {"per_page": 100}
        while url:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            review_comments.extend(response.json())
            url = self._github_next_link(response.headers.get("Link"))
            params = None

        issue_comments: list[dict] = []
        url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/issues/{pr_id}/comments"
        params = {"per_page": 100}
        while url:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            issue_comments.extend(response.json())
            url = self._github_next_link(response.headers.get("Link"))
            params = None

        reviews: list[dict] = []
        url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls/{pr_id}/reviews"
        params = {"per_page": 100}
        while url:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            reviews.extend(response.json())
            url = self._github_next_link(response.headers.get("Link"))
            params = None

        return review_comments, issue_comments, reviews

    def _fetch_github_resolution_status(self, repo: dict, pr_id: str | int) -> dict[int, bool]:
        config = self._get_github_config(repo)
        token = (self.config.get_github_token() or "").strip()
        if not token:
            return {}

        try:
            pr_number = int(pr_id)
        except (ValueError, TypeError):
            return {}

        query = """
        query($owner: String!, $repo: String!, $pr: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $pr) {
              reviewThreads(first: 100) {
                nodes {
                  isResolved
                  comments(first: 100) {
                    nodes {
                      databaseId
                    }
                  }
                }
              }
            }
          }
        }
        """
        variables = {
            "owner": config["owner"],
            "repo": config["repo"],
            "pr": pr_number,
        }

        try:
            response = requests.post(
                "https://api.github.com/graphql",
                json={"query": query, "variables": variables},
                headers={
                    "Authorization": f"bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if not response.ok:
                return {}

            data = response.json()
            threads = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
                .get("nodes", [])
            )
            resolved_map: dict[int, bool] = {}
            for t in threads:
                is_resolved = bool(t.get("isResolved", False))
                for c in t.get("comments", {}).get("nodes", []):
                    db_id = c.get("databaseId")
                    if db_id:
                        resolved_map[db_id] = is_resolved
            return resolved_map
        except Exception:
            return {}

    def _add_github_comment(
        self,
        repo: dict,
        pr_id: str | int,
        body: str,
        *,
        file_path: Optional[str] = None,
        line: Optional[int] = None,
        side: Optional[str] = None,
    ) -> dict[str, Any]:
        config = self._get_github_config(repo)
        headers = config["headers"]

        if file_path and line is not None:
            # Inline comment on PR requires commit_id
            pr_meta = self._get_pr_metadata(repo, pr_id)
            commit_id = pr_meta.get("head_sha")
            if not commit_id:
                pr_res = requests.get(
                    f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls/{pr_id}",
                    headers=headers,
                    timeout=15,
                )
                if pr_res.ok:
                    commit_id = pr_res.json().get("head", {}).get("sha")

            if not commit_id:
                raise ValueError("Could not determine head commit SHA for GitHub inline review comment.")

            payload: dict[str, Any] = {
                "body": body,
                "commit_id": commit_id,
                "path": file_path,
                "line": int(line),
            }
            if side:
                payload["side"] = side.upper()

            url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls/{pr_id}/comments"
            response = requests.post(url, json=payload, headers=headers, timeout=20)
            response.raise_for_status()
            return self._map_github_review_comment(response.json())

        # General issue comment on PR
        url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/issues/{pr_id}/comments"
        payload = {"body": body}
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        return self._map_github_issue_comment(response.json())

    def _reply_github_comment(
        self,
        repo: dict,
        pr_id: str | int,
        parent_id: str | int,
        body: str,
    ) -> dict[str, Any]:
        config = self._get_github_config(repo)
        headers = config["headers"]

        review_reply_url = (
            f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls/{pr_id}/comments/{parent_id}/replies"
        )
        response = requests.post(
            review_reply_url,
            json={"body": body},
            headers=headers,
            timeout=20,
        )
        if response.status_code != 404:
            response.raise_for_status()
            return self._map_github_review_comment(response.json())

        issue_url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/issues/{pr_id}/comments"
        fallback_res = requests.post(issue_url, json={"body": body}, headers=headers, timeout=20)
        fallback_res.raise_for_status()
        return self._map_github_issue_comment(fallback_res.json())

    def _edit_github_comment(
        self,
        repo: dict,
        pr_id: str | int,
        comment_id: str | int,
        body: str,
    ) -> dict[str, Any]:
        config = self._get_github_config(repo)
        headers = config["headers"]

        review_comment_url = (
            f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls/comments/{comment_id}"
        )
        response = requests.patch(
            review_comment_url,
            json={"body": body},
            headers=headers,
            timeout=20,
        )
        if response.status_code != 404:
            response.raise_for_status()
            return self._map_github_review_comment(response.json())

        issue_comment_url = (
            f"https://api.github.com/repos/{config['owner']}/{config['repo']}/issues/comments/{comment_id}"
        )
        response = requests.patch(
            issue_comment_url,
            json={"body": body},
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        return self._map_github_issue_comment(response.json())

    def _delete_github_comment(
        self,
        repo: dict,
        pr_id: str | int,
        comment_id: str | int,
    ) -> dict[str, Any]:
        config = self._get_github_config(repo)
        headers = config["headers"]

        review_comment_url = (
            f"https://api.github.com/repos/{config['owner']}/{config['repo']}/pulls/comments/{comment_id}"
        )
        response = requests.delete(review_comment_url, headers=headers, timeout=20)
        if response.status_code != 404:
            response.raise_for_status()
            return {
                "success": True,
                "comment_id": int(comment_id),
                "deleted": True,
                "provider": "github",
            }

        issue_comment_url = (
            f"https://api.github.com/repos/{config['owner']}/{config['repo']}/issues/comments/{comment_id}"
        )
        response = requests.delete(issue_comment_url, headers=headers, timeout=20)
        response.raise_for_status()
        return {
            "success": True,
            "comment_id": int(comment_id),
            "deleted": True,
            "provider": "github",
        }

    def _resolve_github_thread(
        self,
        repo: dict,
        pr_id: str | int,
        comment_id: str | int,
        *,
        unresolve: bool = False,
    ) -> dict[str, Any]:
        config = self._get_github_config(repo)
        token = (self.config.get_github_token() or "").strip()
        if not token:
            raise ValueError("GitHub token is required to resolve review threads.")

        thread_id = str(comment_id).strip()

        # If comment_id is not already a GraphQL Node ID (starts with PRRT_), lookup the reviewThread ID
        if not thread_id.startswith("PRRT_"):
            try:
                pr_number = int(pr_id)
            except (ValueError, TypeError):
                raise ValueError("Valid pull request number is required to lookup review thread.")

            query = """
            query($owner: String!, $repo: String!, $pr: Int!) {
              repository(owner: $owner, name: $repo) {
                pullRequest(number: $pr) {
                  reviewThreads(first: 100) {
                    nodes {
                      id
                      isResolved
                      comments(first: 100) {
                        nodes {
                          databaseId
                        }
                      }
                    }
                  }
                }
              }
            }
            """
            lookup_res = requests.post(
                "https://api.github.com/graphql",
                json={
                    "query": query,
                    "variables": {
                        "owner": config["owner"],
                        "repo": config["repo"],
                        "pr": pr_number,
                    },
                },
                headers={
                    "Authorization": f"bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            lookup_res.raise_for_status()
            data = lookup_res.json()
            threads = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
                .get("nodes", [])
            )

            matched_thread_id = None
            for t in threads:
                if t.get("id") == thread_id:
                    matched_thread_id = t.get("id")
                    break
                for c in t.get("comments", {}).get("nodes", []):
                    if str(c.get("databaseId")) == str(comment_id):
                        matched_thread_id = t.get("id")
                        break
                if matched_thread_id:
                    break

            if not matched_thread_id:
                raise ValueError(
                    f"Could not find review thread for comment ID '{comment_id}'. "
                    "Note: Only PR review/diff threads can be resolved in GitHub (issue comments cannot be resolved)."
                )
            thread_id = matched_thread_id

        mutation_name = "unresolveReviewThread" if unresolve else "resolveReviewThread"
        mutation = f"""
        mutation($threadId: ID!) {{
          {mutation_name}(input: {{ threadId: $threadId }}) {{
            thread {{
              id
              isResolved
            }}
          }}
        }}
        """
        response = requests.post(
            "https://api.github.com/graphql",
            json={
                "query": mutation,
                "variables": {"threadId": thread_id},
            },
            headers={
                "Authorization": f"bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        response.raise_for_status()
        res_data = response.json()
        if "errors" in res_data:
            err_msg = "; ".join(e.get("message", "Unknown error") for e in res_data["errors"])
            raise RuntimeError(f"GitHub GraphQL error resolving thread: {err_msg}")

        thread_info = res_data.get("data", {}).get(mutation_name, {}).get("thread", {})
        return {
            "success": True,
            "thread_id": thread_info.get("id") or thread_id,
            "comment_id": comment_id,
            "resolved": thread_info.get("isResolved", not unresolve),
            "provider": "github",
        }

    # -------------------------------------------------------------------------
    # Mapping & Thread Processing
    # -------------------------------------------------------------------------

    @staticmethod
    def _map_bitbucket_comment(raw: dict) -> dict:
        inline = raw.get("inline") or {}
        is_inline = bool(inline)
        line = inline.get("to") or inline.get("from")
        side = "to" if inline.get("to") is not None else ("from" if inline.get("from") is not None else None)
        file_path = inline.get("path")
        outdated = bool(inline.get("outdated", False))

        content = raw.get("content") or {}
        body = content.get("raw") or ""

        user = raw.get("user") or {}
        author = user.get("display_name") or user.get("nickname") or user.get("username") or "Unknown"
        author_username = user.get("username") or user.get("nickname") or ""

        resolution = raw.get("resolution") or {}
        resolved = bool(resolution)
        resolved_by = resolution.get("resolved_by", {}).get("display_name") if resolved else None
        resolved_on = resolution.get("resolved_on") if resolved else None

        parent = raw.get("parent") or {}
        html_url = raw.get("links", {}).get("html", {}).get("href") or ""

        if raw.get("deleted") and not body:
            body = "*(comment deleted)*"

        return {
            "id": raw.get("id"),
            "parent_id": parent.get("id"),
            "comment_type": "inline" if is_inline else "general",
            "author": author,
            "author_username": author_username,
            "author_display_name": user.get("display_name") or "",
            "body": body,
            "created_at": raw.get("created_on") or "",
            "updated_at": raw.get("updated_on") or "",
            "html_url": html_url,
            "file_path": file_path,
            "line": line,
            "original_line": line,
            "start_line": None,
            "side": side,
            "diff_hunk": None,
            "code_snippet": None,
            "resolved": resolved,
            "resolved_by": resolved_by,
            "resolved_on": resolved_on,
            "outdated": outdated,
            "deleted": bool(raw.get("deleted")),
        }

    @staticmethod
    def _map_github_review_comment(raw: dict, resolved_map: dict[int, bool] | None = None) -> dict:
        user = raw.get("user") or {}
        cid = raw.get("id")
        resolved = (resolved_map or {}).get(cid, False)

        return {
            "id": cid,
            "parent_id": raw.get("in_reply_to_id"),
            "comment_type": "inline",
            "author": user.get("login") or "Unknown",
            "author_username": user.get("login") or "",
            "author_display_name": user.get("login") or "",
            "body": raw.get("body") or "",
            "created_at": raw.get("created_at") or "",
            "updated_at": raw.get("updated_at") or "",
            "html_url": raw.get("html_url") or "",
            "file_path": raw.get("path"),
            "line": raw.get("line") or raw.get("original_line"),
            "original_line": raw.get("original_line"),
            "start_line": raw.get("start_line") or raw.get("original_start_line"),
            "side": raw.get("side") or "RIGHT",
            "diff_hunk": raw.get("diff_hunk"),
            "code_snippet": None,
            "resolved": resolved,
            "resolved_by": None,
            "resolved_on": None,
            "outdated": raw.get("position") is None,
            "review_id": raw.get("pull_request_review_id"),
            "deleted": False,
        }

    @staticmethod
    def _map_github_issue_comment(raw: dict) -> dict:
        user = raw.get("user") or {}
        return {
            "id": raw.get("id"),
            "parent_id": None,
            "comment_type": "general",
            "author": user.get("login") or "Unknown",
            "author_username": user.get("login") or "",
            "author_display_name": user.get("login") or "",
            "body": raw.get("body") or "",
            "created_at": raw.get("created_at") or "",
            "updated_at": raw.get("updated_at") or "",
            "html_url": raw.get("html_url") or "",
            "file_path": None,
            "line": None,
            "original_line": None,
            "start_line": None,
            "side": None,
            "diff_hunk": None,
            "code_snippet": None,
            "resolved": False,
            "resolved_by": None,
            "resolved_on": None,
            "outdated": False,
            "deleted": False,
        }

    @staticmethod
    def _map_github_review_summary(raw: dict) -> Optional[dict]:
        user = raw.get("user") or {}
        body = (raw.get("body") or "").strip()
        state = raw.get("state") or "COMMENTED"
        if not body and state == "COMMENTED":
            return None

        text = body if body else f"*(Review {state})*"
        return {
            "id": raw.get("id"),
            "parent_id": None,
            "comment_type": "review_summary",
            "author": user.get("login") or "Unknown",
            "author_username": user.get("login") or "",
            "author_display_name": user.get("login") or "",
            "body": text,
            "created_at": raw.get("submitted_at") or "",
            "updated_at": raw.get("submitted_at") or "",
            "html_url": raw.get("html_url") or "",
            "file_path": None,
            "line": None,
            "original_line": None,
            "start_line": None,
            "side": None,
            "diff_hunk": None,
            "code_snippet": None,
            "resolved": state == "APPROVED",
            "resolved_by": None,
            "resolved_on": None,
            "outdated": False,
            "review_id": raw.get("id"),
            "deleted": False,
        }

    @staticmethod
    def _extract_local_code_context(
        repo_dir: str,
        file_path: str,
        line: int,
        context_lines: int = 3,
    ) -> Optional[str]:
        if not repo_dir or not file_path or not line or line <= 0:
            return None
        try:
            full_path = os.path.join(repo_dir, file_path)
            if not os.path.isfile(full_path):
                return None
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            start = max(1, line - context_lines)
            end = min(len(lines), line + context_lines)
            formatted = []
            for l_num in range(start, end + 1):
                marker = ">" if l_num == line else " "
                line_content = lines[l_num - 1].rstrip("\r\n")
                formatted.append(f"{marker} {l_num:4d} | {line_content}")
            return "\n".join(formatted)
        except Exception:
            return None

    @staticmethod
    def _build_comment_threads(
        comments: list[dict],
        *,
        unresolved_only: bool = False,
        comment_type: str = "all",
        file_path_filter: str = "",
    ) -> tuple[list[dict], list[dict]]:
        comments_by_id = {c["id"]: c for c in comments if c.get("id") is not None}

        def get_root_id(comment: dict) -> Any:
            curr = comment
            visited = set()
            while curr.get("parent_id") and curr["parent_id"] in comments_by_id and curr["id"] not in visited:
                visited.add(curr["id"])
                curr = comments_by_id[curr["parent_id"]]
            return curr.get("parent_id") if (curr.get("parent_id") and curr.get("parent_id") not in comments_by_id) else curr["id"]

        thread_map: dict[Any, list[dict]] = {}
        for c in comments:
            root_id = get_root_id(c)
            thread_map.setdefault(root_id, []).append(c)

        threads: list[dict] = []
        for root_id, thread_comments in thread_map.items():
            thread_comments.sort(key=lambda x: str(x.get("created_at") or ""))
            root = thread_comments[0]
            for c in thread_comments:
                if c.get("parent_id") is None or c.get("id") == root_id:
                    root = c
                    break

            replies = [c for c in thread_comments if c != root]

            file_path = root.get("file_path")
            line = root.get("line")
            start_line = root.get("start_line")
            side = root.get("side")
            diff_hunk = root.get("diff_hunk")
            code_snippet = root.get("code_snippet")
            comment_kind = root.get("comment_type") or "general"

            if not file_path:
                for c in thread_comments:
                    if c.get("file_path"):
                        file_path = c.get("file_path")
                        line = c.get("line")
                        start_line = c.get("start_line")
                        side = c.get("side")
                        diff_hunk = c.get("diff_hunk")
                        code_snippet = c.get("code_snippet")
                        comment_kind = "inline"
                        break

            is_resolved = any(bool(c.get("resolved")) for c in thread_comments)
            is_outdated = any(bool(c.get("outdated")) for c in thread_comments)
            resolved_by = next((c.get("resolved_by") for c in thread_comments if c.get("resolved_by")), None)
            resolved_on = next((c.get("resolved_on") for c in thread_comments if c.get("resolved_on")), None)

            thread = {
                "thread_id": root.get("id") or root_id,
                "comment_type": comment_kind,
                "file_path": file_path,
                "line": line,
                "start_line": start_line,
                "side": side,
                "diff_hunk": diff_hunk,
                "code_snippet": code_snippet,
                "resolved": is_resolved,
                "resolved_by": resolved_by,
                "resolved_on": resolved_on,
                "outdated": is_outdated,
                "root_comment": root,
                "replies": replies,
                "reply_count": len(replies),
                "latest_comment": thread_comments[-1],
            }
            threads.append(thread)

        filtered_threads: list[dict] = []
        for t in threads:
            if unresolved_only and t["resolved"]:
                continue
            if comment_type == "inline" and t["comment_type"] != "inline":
                continue
            if comment_type == "general" and t["comment_type"] == "inline":
                continue
            if file_path_filter:
                t_path = (t["file_path"] or "").lower()
                filter_path = file_path_filter.strip().lower()
                if filter_path not in t_path:
                    continue
            filtered_threads.append(t)

        def thread_sort_key(t: dict):
            return (
                0 if t["comment_type"] == "inline" else 1,
                t["file_path"] or "",
                t["line"] or 0,
                str(t["root_comment"].get("created_at") or ""),
            )
        filtered_threads.sort(key=thread_sort_key)

        filtered_comments: list[dict] = []
        for t in filtered_threads:
            filtered_comments.append(t["root_comment"])
            filtered_comments.extend(t["replies"])

        return filtered_threads, filtered_comments

    @staticmethod
    def _format_ai_comments_summary(
        repo: dict,
        pr: dict,
        threads: list[dict],
        summary: dict,
    ) -> str:
        repo_label = (
            repo.get("full_name")
            or repo.get("repo_label")
            or f"{repo.get('owner', '')}/{repo.get('slug', '')}".strip("/")
        )
        pr_id = pr.get("id") or ""
        pr_title = pr.get("title") or ""
        pr_state = pr.get("state") or ""
        author = pr.get("author") or ""

        lines = [
            f"# PR #{pr_id}: {pr_title}".strip(),
            f"**Repository**: {repo_label} | **State**: {pr_state} | **Author**: {author}",
            f"**Summary**: {summary['total_comments']} comments across {summary['total_threads']} threads "
            f"({summary['unresolved_threads']} unresolved, {summary['resolved_threads']} resolved)",
        ]

        if summary.get("files_with_comments"):
            files_str = ", ".join(f"`{f}`" for f in summary["files_with_comments"])
            lines.append(f"**Files with Comments**: {files_str}")
        lines.append("")

        if not threads:
            lines.append("No comments matching the requested criteria.")
            return "\n".join(lines)

        unresolved = [t for t in threads if not t["resolved"]]
        resolved = [t for t in threads if t["resolved"]]

        if unresolved:
            lines.append(f"## Unresolved Threads ({len(unresolved)})")
            lines.append("")
            for idx, t in enumerate(unresolved, 1):
                status_tags = ["[UNRESOLVED]"]
                if t["comment_type"] == "inline":
                    status_tags.append(f"[INLINE: `{t['file_path']}`:{t['line']}]")
                else:
                    status_tags.append("[GENERAL]")
                if t.get("outdated"):
                    status_tags.append("[OUTDATED DIFF]")

                lines.append(f"### Thread {idx} {' '.join(status_tags)}")
                if t["comment_type"] == "inline":
                    lines.append(f"- **File**: `{t['file_path']}`")
                    line_info = f"{t['line']}"
                    if t.get("side"):
                        line_info += f" (side: {t['side']})"
                    lines.append(f"- **Line**: {line_info}")

                    if t.get("code_snippet"):
                        lines.append("- **Code Context**:")
                        lines.append("```")
                        lines.append(t["code_snippet"])
                        lines.append("```")
                    elif t.get("diff_hunk"):
                        lines.append("- **Diff Context**:")
                        lines.append("```diff")
                        lines.append(t["diff_hunk"])
                        lines.append("```")

                root = t["root_comment"]
                lines.append(f"- **Reviewer (@{root.get('author')})** ({root.get('created_at', '')}):")
                body_lines = (root.get("body") or "").splitlines()
                quoted_body = "\n".join(f"  > {line}" for line in body_lines) if body_lines else "  > *(empty comment)*"
                lines.append(quoted_body)

                if t.get("replies"):
                    lines.append(f"- **Replies ({len(t['replies'])})**:")
                    for reply in t["replies"]:
                        reply_body = (reply.get("body") or "").strip()
                        lines.append(f"  - **@{reply.get('author')}** ({reply.get('created_at', '')}): {reply_body}")
                lines.append("")

        if resolved:
            lines.append(f"## Resolved Threads ({len(resolved)})")
            lines.append("")
            for idx, t in enumerate(resolved, 1):
                target = f"`{t['file_path']}`:{t['line']}" if t["comment_type"] == "inline" else "General"
                root = t["root_comment"]
                snippet = (root.get("body") or "").strip().replace("\n", " ")[:100]
                lines.append(f"- **[RESOLVED]** {target} by @{root.get('author')}: {snippet}")
            lines.append("")

        lines.append("## Comment Guidelines for AI / Automation")
        lines.append("When adding comments or replying to PR threads, communicate like a natural human software engineer:")
        lines.append("- **Concise & Direct**: Keep comments brief (typically 1-3 sentences) focused strictly on the technical issue, fix, or rationale.")
        lines.append("- **No AI Stereotypes**: Avoid canned AI pleasantries and robotic templates (e.g., do NOT write 'Certainly!', 'Great catch!', 'Thank you for the feedback!', 'I hope this helps!', or 'As an AI model...').")
        lines.append("- **Natural Tone**: State what changed or why plainly (e.g., 'Good catch, added the null check in abc1234', 'Updated bean qualifier to prevent collision', 'Kept this method private since it is only called by the internal parser').")

        return "\n".join(lines).strip()
