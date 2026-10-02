import unittest
from unittest.mock import MagicMock, patch

from services.pull_request_service import PullRequestService


class _Config:
    def get_provider(self):
        return "github"

    def get_github_token(self):
        return "secret"

    def get_github_owner(self):
        return "openai"

    def get_bitbucket_username(self):
        return "user"

    def get_bitbucket_api_token(self):
        return "pass"

    def get_bitbucket_workspace(self):
        return "workspace"


class PullRequestServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = PullRequestService(_Config())
        self.repo = {
            "provider": "github",
            "id": "1",
            "owner": "openai",
            "slug": "demo",
            "local_dir": "/tmp/demo",
        }

    @patch("services.pull_request_service.requests.get")
    def test_list_pull_requests_for_repo_attaches_context(self, mock_get):
        response = MagicMock()
        response.json.return_value = [
            {
                "number": 99,
                "title": "Improve tests",
                "state": "open",
                "merged_at": None,
                "updated_at": "2026-01-01T00:00:00Z",
                "head": {"ref": "feature/x", "sha": "abc"},
                "base": {"ref": "main", "sha": "def"},
                "user": {"login": "ajamal"},
                "html_url": "https://example/pr/99",
                "body": "Details",
            }
        ]
        response.headers = {}
        mock_get.return_value = response

        records, next_cursor = self.service.list_pull_requests_for_repo(self.repo, filter_mode="open")

        self.assertEqual(next_cursor, None)
        self.assertEqual(records[0]["repo_label"], "openai/demo")
        self.assertEqual(records[0]["repo_local_dir"], "/tmp/demo")
        self.assertEqual(records[0]["id"], 99)

    @patch("services.pull_request_service.requests.get")
    def test_list_pull_requests_for_repo_filters_developer_across_pages(self, mock_get):
        first_response = MagicMock()
        first_response.json.return_value = [{
            "number": 98, "title": "Someone else's PR", "state": "open", "merged_at": None,
            "head": {"ref": "feature/other"}, "base": {"ref": "main"},
            "user": {"login": "someone-else"},
        }]
        first_response.headers = {
            "Link": '<https://api.github.com/repos/openai/demo/pulls?page=2>; rel="next"'
        }
        second_response = MagicMock()
        second_response.json.return_value = [{
            "number": 99,
            "title": "Mine",
            "state": "open",
            "merged_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
            "head": {"ref": "feature/x", "sha": "abc"},
            "base": {"ref": "main", "sha": "def"},
            "user": {"login": "ajamal"},
            "html_url": "https://example/pr/99",
            "body": "Details",
        }]
        second_response.headers = {}
        mock_get.side_effect = [first_response, second_response]

        records, next_cursor = self.service.list_pull_requests_for_repo(
            self.repo,
            filter_mode="open",
            developer="ajamal",
        )

        self.assertIsNone(next_cursor)
        self.assertEqual([record["id"] for record in records], [99])
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://api.github.com/repos/openai/demo/pulls")

    def test_bitbucket_developer_filter_walks_all_pages(self):
        repo = {"provider": "bitbucket", "id": "2", "owner": "workspace", "slug": "demo"}
        first = [{"id": 1, "author": "Someone Else"}]
        second = [{"id": 2, "author": "Alice Example", "author_username": "alice"}]
        with patch.object(self.service, "_fetch_bitbucket_pull_requests_page",
                          side_effect=[(first, "page-2"), (second, None)]) as fetch:
            records, cursor = self.service.list_pull_requests_for_repo(
                repo, filter_mode="all", developer="alice",
            )
        self.assertEqual([record["id"] for record in records], [2])
        self.assertIsNone(cursor)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[1].kwargs["next_cursor"], "page-2")

    def test_bitbucket_query_expression_omits_author_fields_for_api_compatibility(self):
        expression = PullRequestService._bitbucket_query_expression(
            "RU-123",
            "Zaid",
            ' AND state = "OPEN"',
        )

        self.assertIn('title ~ "RU-123"', expression)
        self.assertNotIn("author.", expression)
        self.assertIn('state = "OPEN"', expression)

    @patch("services.pull_request_service.requests.get")
    def test_search_pull_requests_uses_issue_search_and_pr_fetch(self, mock_get):
        search_response = MagicMock()
        search_response.json.return_value = {
            "items": [
                {
                    "pull_request": {"url": "https://api.github.com/repos/openai/demo/pulls/99"},
                }
            ]
        }
        pr_response = MagicMock()
        pr_response.json.return_value = {
            "number": 99,
            "title": "Fix bug",
            "state": "open",
            "merged_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
            "head": {"ref": "feature/x", "sha": "abc"},
            "base": {"ref": "main", "sha": "def"},
            "user": {"login": "ajamal"},
            "html_url": "https://example/pr/99",
            "body": "Details",
        }
        mock_get.side_effect = [search_response, pr_response]

        records = self.service.search_pull_requests([self.repo], "open", "fix")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], 99)
        self.assertEqual(records[0]["repo_id"], "1")

    @patch("services.pull_request_service.requests.get")
    def test_find_pull_requests_by_ticket_extracts_ticket_and_filters_exact_matches(self, mock_get):
        search_response = MagicMock()
        search_response.json.return_value = {
            "items": [
                {"pull_request": {"url": "https://api.github.com/repos/openai/demo/pulls/99"}},
                {"pull_request": {"url": "https://api.github.com/repos/openai/demo/pulls/100"}},
            ]
        }
        matching_pr_response = MagicMock()
        matching_pr_response.json.return_value = {
            "number": 99,
            "title": "RU-25463: Fix Open redirect issues",
            "state": "open",
            "merged_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
            "head": {"ref": "feature/RU-25463-open-redirect", "sha": "abc"},
            "base": {"ref": "main", "sha": "def"},
            "user": {"login": "ajamal"},
            "html_url": "https://example/pr/99",
            "body": "Details",
        }
        non_matching_pr_response = MagicMock()
        non_matching_pr_response.json.return_value = {
            "number": 100,
            "title": "RU-2546 unrelated nearby ticket",
            "state": "open",
            "merged_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
            "head": {"ref": "feature/RU-2546", "sha": "abc"},
            "base": {"ref": "main", "sha": "def"},
            "user": {"login": "ajamal"},
            "html_url": "https://example/pr/100",
            "body": "Details",
        }
        mock_get.side_effect = [search_response, matching_pr_response, non_matching_pr_response]

        records = self.service.find_pull_requests_by_ticket(
            [self.repo],
            "RU-25463: Fix Open redirect issues",
            filter_mode="all",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], 99)

    def test_find_pull_requests_by_ticket_requires_ticket_id(self):
        with self.assertRaisesRegex(ValueError, "ticket id"):
            self.service.find_pull_requests_by_ticket([self.repo], "no ticket here")

    @patch("services.pull_request_service.requests.get")
    def test_get_pull_request_comments_bitbucket(self, mock_get):
        import tempfile
        import os

        bb_repo = {
            "provider": "bitbucket",
            "id": "bb-1",
            "owner": "workspace",
            "slug": "sample-repo",
            "local_dir": "",
        }

        # 1. PR metadata response
        pr_response = MagicMock()
        pr_response.json.return_value = {
            "id": 42,
            "title": "Add auth service",
            "state": "OPEN",
            "author": {"display_name": "Ameer", "username": "ajamal"},
            "source": {"branch": {"name": "feature/auth"}},
            "destination": {"branch": {"name": "main"}},
            "links": {"html": {"href": "https://bitbucket.org/workspace/sample-repo/pull-requests/42"}},
        }

        # 2. PR comments response
        comments_response = MagicMock()
        comments_response.json.return_value = {
            "values": [
                {
                    "id": 101,
                    "deleted": False,
                    "user": {"display_name": "Alice", "nickname": "alice"},
                    "content": {"raw": "Please add null check"},
                    "created_on": "2026-02-01T10:00:00Z",
                    "updated_on": "2026-02-01T10:00:00Z",
                    "inline": {"path": "src/auth.py", "to": 5, "from": None, "outdated": False},
                    "links": {"html": {"href": "https://bitbucket.org/comment/101"}},
                },
                {
                    "id": 102,
                    "parent": {"id": 101},
                    "deleted": False,
                    "user": {"display_name": "Bob", "nickname": "bob"},
                    "content": {"raw": "Done in latest commit"},
                    "created_on": "2026-02-01T10:15:00Z",
                    "updated_on": "2026-02-01T10:15:00Z",
                    "inline": {"path": "src/auth.py", "to": 5, "from": None, "outdated": False},
                    "links": {"html": {"href": "https://bitbucket.org/comment/102"}},
                },
                {
                    "id": 103,
                    "deleted": False,
                    "user": {"display_name": "Alice", "nickname": "alice"},
                    "content": {"raw": "Overall looks great!"},
                    "created_on": "2026-02-01T11:00:00Z",
                    "updated_on": "2026-02-01T11:00:00Z",
                    "inline": None,
                    "resolution": {"resolved_by": {"display_name": "Alice"}, "resolved_on": "2026-02-01T11:05:00Z"},
                    "links": {"html": {"href": "https://bitbucket.org/comment/103"}},
                },
            ],
            "next": None,
        }

        mock_get.side_effect = [pr_response, comments_response]

        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, "src")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "auth.py"), "w") as f:
                for i in range(1, 10):
                    f.write(f"def line_{i}():\n    pass\n")

            # Call with code context from local checkout
            result = self.service.get_pull_request_comments(
                bb_repo,
                42,
                repo_dir=tmpdir,
                include_code_context=True,
            )

            self.assertEqual(result["summary"]["total_comments"], 3)
            self.assertEqual(result["summary"]["total_threads"], 2)
            self.assertEqual(result["summary"]["unresolved_threads"], 1)
            self.assertEqual(result["summary"]["resolved_threads"], 1)
            self.assertEqual(result["summary"]["inline_threads"], 1)
            self.assertEqual(result["summary"]["general_threads"], 1)

            # Check thread 1 structure
            thread1 = result["threads"][0]
            self.assertEqual(thread1["thread_id"], 101)
            self.assertEqual(thread1["file_path"], "src/auth.py")
            self.assertEqual(thread1["line"], 5)
            self.assertFalse(thread1["resolved"])
            self.assertEqual(thread1["reply_count"], 1)
            self.assertEqual(thread1["replies"][0]["body"], "Done in latest commit")
            self.assertIn(">", thread1["code_snippet"])
            self.assertIn("line_3", thread1["code_snippet"])

            # Check thread 2 (general, resolved)
            thread2 = result["threads"][1]
            self.assertEqual(thread2["thread_id"], 103)
            self.assertTrue(thread2["resolved"])
            self.assertEqual(thread2["resolved_by"], "Alice")

            # Formatted summary checks
            self.assertIn("# PR #42: Add auth service", result["formatted_summary"])
            self.assertIn("Unresolved Threads (1)", result["formatted_summary"])
            self.assertIn("Resolved Threads (1)", result["formatted_summary"])
            self.assertIn("Please add null check", result["formatted_summary"])

            # Test filter unresolved_only
            mock_get.side_effect = [pr_response, comments_response]
            unresolved_res = self.service.get_pull_request_comments(
                bb_repo,
                42,
                unresolved_only=True,
            )
            self.assertEqual(len(unresolved_res["threads"]), 1)
            self.assertEqual(unresolved_res["threads"][0]["thread_id"], 101)

            # Test filter comment_type="inline"
            mock_get.side_effect = [pr_response, comments_response]
            inline_res = self.service.get_pull_request_comments(
                bb_repo,
                42,
                comment_type="inline",
            )
            self.assertEqual(len(inline_res["threads"]), 1)
            self.assertEqual(inline_res["threads"][0]["file_path"], "src/auth.py")

    @patch("services.pull_request_service.requests.post")
    @patch("services.pull_request_service.requests.get")
    def test_get_pull_request_comments_github(self, mock_get, mock_post):
        # 1. PR metadata
        pr_response = MagicMock()
        pr_response.json.return_value = {
            "number": 10,
            "title": "Fix memory leak",
            "state": "open",
            "merged_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
            "head": {"ref": "feature/leak", "sha": "abc"},
            "base": {"ref": "main", "sha": "def"},
            "user": {"login": "octocat"},
            "html_url": "https://github.com/openai/demo/pull/10",
            "body": "Fixes leak in cache",
        }
        pr_response.headers = {}

        # 2. Review comments (inline)
        review_comments_response = MagicMock()
        review_comments_response.json.return_value = [
            {
                "id": 201,
                "path": "cache.py",
                "line": 40,
                "original_line": 40,
                "side": "RIGHT",
                "diff_hunk": "@@ -38,5 +38,6 @@\n def evict():\n+    del self._items[k]",
                "user": {"login": "reviewer1"},
                "body": "Ensure key exists before del",
                "created_at": "2026-02-01T12:00:00Z",
                "updated_at": "2026-02-01T12:00:00Z",
                "html_url": "https://github.com/openai/demo/pull/10#discussion_r201",
                "in_reply_to_id": None,
                "position": 1,
            },
            {
                "id": 202,
                "path": "cache.py",
                "line": 40,
                "original_line": 40,
                "side": "RIGHT",
                "diff_hunk": "@@ -38,5 +38,6 @@\n def evict():\n+    del self._items[k]",
                "user": {"login": "octocat"},
                "body": "Good point, using .pop(k, None)",
                "created_at": "2026-02-01T12:10:00Z",
                "updated_at": "2026-02-01T12:10:00Z",
                "html_url": "https://github.com/openai/demo/pull/10#discussion_r202",
                "in_reply_to_id": 201,
                "position": 1,
            },
        ]
        review_comments_response.headers = {}

        # 3. Issue comments (general)
        issue_comments_response = MagicMock()
        issue_comments_response.json.return_value = [
            {
                "id": 301,
                "user": {"login": "qa_bot"},
                "body": "CI passed successfully",
                "created_at": "2026-02-01T12:05:00Z",
                "updated_at": "2026-02-01T12:05:00Z",
                "html_url": "https://github.com/openai/demo/pull/10#issuecomment-301",
            }
        ]
        issue_comments_response.headers = {}

        # 4. Reviews
        reviews_response = MagicMock()
        reviews_response.json.return_value = [
            {
                "id": 401,
                "user": {"login": "reviewer1"},
                "body": "Looks good once the pop check is addressed",
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-02-01T12:15:00Z",
                "html_url": "https://github.com/openai/demo/pull/10#pullrequestreview-401",
            }
        ]
        reviews_response.headers = {}

        mock_get.side_effect = [
            pr_response,
            review_comments_response,
            issue_comments_response,
            reviews_response,
        ]

        # Mock GraphQL response for resolution status
        graphql_response = MagicMock()
        graphql_response.status_code = 200
        graphql_response.json.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "thread-1",
                                    "isResolved": True,
                                    "isOutdated": False,
                                    "comments": {"nodes": [{"databaseId": 201}, {"databaseId": 202}]},
                                }
                            ]
                        }
                    }
                }
            }
        }
        mock_post.return_value = graphql_response

        result = self.service.get_pull_request_comments(self.repo, 10)

        self.assertEqual(result["summary"]["total_comments"], 4)
        self.assertEqual(result["summary"]["total_threads"], 3)
        self.assertEqual(result["summary"]["resolved_threads"], 1)

        # Thread 201 should be marked resolved via GraphQL
        inline_thread = next(t for t in result["threads"] if t["thread_id"] == 201)
        self.assertTrue(inline_thread["resolved"])
        self.assertEqual(inline_thread["file_path"], "cache.py")
        self.assertEqual(inline_thread["reply_count"], 1)
        self.assertIn("Ensure key exists", inline_thread["root_comment"]["body"])


if __name__ == "__main__":
    unittest.main()
