import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from services.pull_request_comment_service import PullRequestCommentService


class _MockConfig:
    def get_provider(self):
        return "bitbucket"

    def get_bitbucket_username(self):
        return "test_bb_user"

    def get_bitbucket_api_token(self):
        return "test_bb_token"

    def get_bitbucket_workspace(self):
        return "test_workspace"

    def get_github_owner(self):
        return "test_gh_owner"

    def get_github_repo(self):
        return "test_gh_repo"

    def get_github_token(self):
        return "test_gh_token"


class PullRequestCommentServiceTests(unittest.TestCase):
    def setUp(self):
        self.config = _MockConfig()
        self.service = PullRequestCommentService(self.config)
        self.bb_repo = {
            "provider": "bitbucket",
            "owner": "test_workspace",
            "slug": "test_repo",
        }
        self.gh_repo = {
            "provider": "github",
            "owner": "test_gh_owner",
            "slug": "test_gh_repo",
        }

    # -------------------------------------------------------------------------
    # Read Comments Tests
    # -------------------------------------------------------------------------

    @patch("services.pull_request_comment_service.requests.get")
    def test_get_pull_request_comments_bitbucket(self, mock_get):
        pr_response = MagicMock()
        pr_response.ok = True
        pr_response.json.return_value = {
            "id": 42,
            "title": "Sample Bitbucket PR",
            "state": "OPEN",
            "author": {"display_name": "Reviewer 1"},
        }

        comments_response = MagicMock()
        comments_response.ok = True
        comments_response.json.return_value = {
            "values": [
                {
                    "id": 100,
                    "content": {"raw": "Root comment"},
                    "user": {"display_name": "Dev 1", "username": "dev1"},
                    "created_on": "2026-03-01T12:00:00Z",
                    "inline": {"path": "test.py", "to": 10},
                    "links": {"html": {"href": "https://bitbucket.org/100"}},
                },
                {
                    "id": 101,
                    "parent": {"id": 100},
                    "content": {"raw": "Reply comment"},
                    "user": {"display_name": "Dev 2", "username": "dev2"},
                    "created_on": "2026-03-01T12:10:00Z",
                    "inline": {"path": "test.py", "to": 10},
                    "links": {"html": {"href": "https://bitbucket.org/101"}},
                }
            ],
            "next": None,
        }
        mock_get.side_effect = [pr_response, comments_response]

        result = self.service.get_pull_request_comments(self.bb_repo, 42)
        self.assertEqual(result["summary"]["total_comments"], 2)
        self.assertEqual(result["summary"]["total_threads"], 1)
        self.assertEqual(result["summary"]["unresolved_threads"], 1)
        thread = result["threads"][0]
        self.assertEqual(thread["thread_id"], 100)
        self.assertEqual(thread["reply_count"], 1)
        self.assertEqual(thread["replies"][0]["body"], "Reply comment")

    # -------------------------------------------------------------------------
    # Add Comment Tests
    # -------------------------------------------------------------------------

    @patch("services.pull_request_comment_service.requests.post")
    def test_add_bitbucket_general_comment(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "id": 201,
            "content": {"raw": "Great job on this PR!"},
            "user": {"display_name": "Me", "username": "me"},
            "created_on": "2026-03-01T13:00:00Z",
            "links": {"html": {"href": "https://bitbucket.org/201"}},
        }
        mock_post.return_value = mock_resp

        res = self.service.add_comment(self.bb_repo, 42, "Great job on this PR!")
        self.assertEqual(res["id"], 201)
        self.assertEqual(res["body"], "Great job on this PR!")
        self.assertEqual(res["comment_type"], "general")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertIn("pullrequests/42/comments", args[0])
        self.assertEqual(kwargs["json"], {"content": {"raw": "Great job on this PR!"}})

    @patch("services.pull_request_comment_service.requests.post")
    def test_add_bitbucket_inline_comment(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "id": 202,
            "content": {"raw": "Fix formatting here"},
            "user": {"display_name": "Me", "username": "me"},
            "inline": {"path": "src/app.py", "to": 15},
            "links": {"html": {"href": "https://bitbucket.org/202"}},
        }
        mock_post.return_value = mock_resp

        res = self.service.add_comment(
            self.bb_repo, 42, "Fix formatting here", file_path="src/app.py", line=15, side="to"
        )
        self.assertEqual(res["id"], 202)
        self.assertEqual(res["line"], 15)
        self.assertEqual(res["file_path"], "src/app.py")
        self.assertEqual(res["comment_type"], "inline")

        kwargs = mock_post.call_args[1]
        self.assertEqual(
            kwargs["json"],
            {
                "content": {"raw": "Fix formatting here"},
                "inline": {"path": "src/app.py", "to": 15},
            },
        )

    @patch("services.pull_request_comment_service.requests.post")
    def test_add_github_general_comment(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "id": 301,
            "body": "LGTM!",
            "user": {"login": "gh_user"},
            "created_at": "2026-03-01T14:00:00Z",
            "html_url": "https://github.com/comment/301",
        }
        mock_post.return_value = mock_resp

        res = self.service.add_comment(self.gh_repo, 10, "LGTM!")
        self.assertEqual(res["id"], 301)
        self.assertEqual(res["body"], "LGTM!")
        self.assertEqual(res["comment_type"], "general")

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        self.assertIn("issues/10/comments", url)

    @patch("services.pull_request_comment_service.requests.get")
    @patch("services.pull_request_comment_service.requests.post")
    def test_add_github_inline_comment(self, mock_post, mock_get):
        pr_resp = MagicMock()
        pr_resp.ok = True
        pr_resp.json.return_value = {"head": {"sha": "abcdef123456"}}
        mock_get.return_value = pr_resp

        comment_resp = MagicMock()
        comment_resp.ok = True
        comment_resp.json.return_value = {
            "id": 302,
            "body": "Check edge case",
            "user": {"login": "gh_user"},
            "path": "src/main.py",
            "line": 25,
            "html_url": "https://github.com/comment/302",
        }
        mock_post.return_value = comment_resp

        res = self.service.add_comment(
            self.gh_repo, 10, "Check edge case", file_path="src/main.py", line=25
        )
        self.assertEqual(res["id"], 302)
        self.assertEqual(res["file_path"], "src/main.py")
        self.assertEqual(res["line"], 25)

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        self.assertIn("pulls/10/comments", url)
        self.assertEqual(mock_post.call_args[1]["json"]["commit_id"], "abcdef123456")

    # -------------------------------------------------------------------------
    # Reply Comment Tests
    # -------------------------------------------------------------------------

    @patch("services.pull_request_comment_service.requests.post")
    def test_reply_bitbucket_comment(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "id": 401,
            "parent": {"id": 100},
            "content": {"raw": "I have addressed this in commit abc"},
            "user": {"display_name": "Me"},
            "links": {"html": {"href": "https://bitbucket.org/401"}},
        }
        mock_post.return_value = mock_resp

        res = self.service.reply_to_comment(self.bb_repo, 42, 100, "I have addressed this in commit abc")
        self.assertEqual(res["id"], 401)
        self.assertEqual(res["parent_id"], 100)

        kwargs = mock_post.call_args[1]
        self.assertEqual(kwargs["json"]["parent"], {"id": 100})
        self.assertEqual(kwargs["json"]["content"], {"raw": "I have addressed this in commit abc"})

    @patch("services.pull_request_comment_service.requests.post")
    def test_reply_github_review_comment(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "id": 501,
            "in_reply_to_id": 302,
            "body": "Fixed!",
            "user": {"login": "gh_user"},
            "html_url": "https://github.com/comment/501",
        }
        mock_post.return_value = mock_resp

        res = self.service.reply_to_comment(self.gh_repo, 10, 302, "Fixed!")
        self.assertEqual(res["id"], 501)
        self.assertEqual(res["parent_id"], 302)

        url = mock_post.call_args[0][0]
        self.assertIn("pulls/10/comments/302/replies", url)

    # -------------------------------------------------------------------------
    # Edit Comment Tests
    # -------------------------------------------------------------------------

    @patch("services.pull_request_comment_service.requests.put")
    def test_edit_bitbucket_comment(self, mock_put):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "id": 201,
            "content": {"raw": "Updated comment body"},
            "user": {"display_name": "Me"},
        }
        mock_put.return_value = mock_resp

        res = self.service.edit_comment(self.bb_repo, 42, 201, "Updated comment body")
        self.assertEqual(res["id"], 201)
        self.assertEqual(res["body"], "Updated comment body")

        url = mock_put.call_args[0][0]
        self.assertIn("pullrequests/42/comments/201", url)
        self.assertEqual(mock_put.call_args[1]["json"], {"content": {"raw": "Updated comment body"}})

    @patch("services.pull_request_comment_service.requests.patch")
    def test_edit_github_comment(self, mock_patch):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "id": 301,
            "body": "Updated review comment",
            "user": {"login": "gh_user"},
        }
        mock_patch.return_value = mock_resp

        res = self.service.edit_comment(self.gh_repo, 10, 301, "Updated review comment")
        self.assertEqual(res["id"], 301)
        self.assertEqual(res["body"], "Updated review comment")

        url = mock_patch.call_args[0][0]
        self.assertIn("pulls/comments/301", url)

    # -------------------------------------------------------------------------
    # Delete Comment Tests
    # -------------------------------------------------------------------------

    @patch("services.pull_request_comment_service.requests.delete")
    def test_delete_bitbucket_comment(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_delete.return_value = mock_resp

        res = self.service.delete_comment(self.bb_repo, 42, 201)
        self.assertTrue(res["success"])
        self.assertEqual(res["comment_id"], 201)
        self.assertEqual(res["provider"], "bitbucket")

        url = mock_delete.call_args[0][0]
        self.assertIn("pullrequests/42/comments/201", url)

    @patch("services.pull_request_comment_service.requests.delete")
    def test_delete_github_comment(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.ok = True
        mock_delete.return_value = mock_resp

        res = self.service.delete_comment(self.gh_repo, 10, 301)
        self.assertTrue(res["success"])
        self.assertEqual(res["comment_id"], 301)
        self.assertEqual(res["provider"], "github")

        url = mock_delete.call_args[0][0]
        self.assertIn("pulls/comments/301", url)

    # -------------------------------------------------------------------------
    # Resolve / Unresolve Comment Tests
    # -------------------------------------------------------------------------

    @patch("services.pull_request_comment_service.requests.post")
    def test_resolve_bitbucket_comment(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        res = self.service.resolve_comment(self.bb_repo, 42, 201)
        self.assertTrue(res["success"])
        self.assertEqual(res["comment_id"], 201)
        self.assertTrue(res["resolved"])
        self.assertEqual(res["provider"], "bitbucket")

        url = mock_post.call_args[0][0]
        self.assertIn("pullrequests/42/comments/201/resolve", url)

    @patch("services.pull_request_comment_service.requests.delete")
    def test_unresolve_bitbucket_comment(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        res = self.service.resolve_comment(self.bb_repo, 42, 201, unresolve=True)
        self.assertTrue(res["success"])
        self.assertEqual(res["comment_id"], 201)
        self.assertFalse(res["resolved"])

        url = mock_delete.call_args[0][0]
        self.assertIn("pullrequests/42/comments/201/resolve", url)

    @patch("services.pull_request_comment_service.requests.get")
    @patch("services.pull_request_comment_service.requests.post")
    def test_resolve_bitbucket_comment_fallback_parent(self, mock_post, mock_get):
        # 1. First POST returns 404 (child reply)
        err_resp = MagicMock()
        err_resp.ok = False
        err_resp.status_code = 404

        # 2. GET returns comment with parent id 100
        c_resp = MagicMock()
        c_resp.ok = True
        c_resp.json.return_value = {"id": 101, "parent": {"id": 100}}
        mock_get.return_value = c_resp

        # 3. Second POST on parent succeeds
        ok_resp = MagicMock()
        ok_resp.ok = True
        mock_post.side_effect = [err_resp, ok_resp]

        res = self.service.resolve_comment(self.bb_repo, 42, 101)
        self.assertTrue(res["success"])
        self.assertEqual(mock_post.call_count, 2)
        second_url = mock_post.call_args_list[1][0][0]
        self.assertIn("pullrequests/42/comments/100/resolve", second_url)

    @patch("services.pull_request_comment_service.requests.post")
    def test_resolve_github_thread_direct_thread_id(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "data": {
                "resolveReviewThread": {
                    "thread": {"id": "PRRT_kwDOA123", "isResolved": True}
                }
            }
        }
        mock_post.return_value = mock_resp

        res = self.service.resolve_comment(self.gh_repo, 10, "PRRT_kwDOA123")
        self.assertTrue(res["success"])
        self.assertEqual(res["thread_id"], "PRRT_kwDOA123")
        self.assertTrue(res["resolved"])
        self.assertEqual(res["provider"], "github")

    @patch("services.pull_request_comment_service.requests.post")
    def test_resolve_github_thread_lookup_by_comment_id(self, mock_post):
        # 1. GraphQL lookup query response
        lookup_resp = MagicMock()
        lookup_resp.ok = True
        lookup_resp.json.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "PRRT_abc987",
                                    "isResolved": False,
                                    "comments": {
                                        "nodes": [{"databaseId": 302}]
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }

        # 2. GraphQL mutation response
        mutation_resp = MagicMock()
        mutation_resp.ok = True
        mutation_resp.json.return_value = {
            "data": {
                "resolveReviewThread": {
                    "thread": {"id": "PRRT_abc987", "isResolved": True}
                }
            }
        }
        mock_post.side_effect = [lookup_resp, mutation_resp]

        res = self.service.resolve_comment(self.gh_repo, 10, 302)
        self.assertTrue(res["success"])
        self.assertEqual(res["thread_id"], "PRRT_abc987")
        self.assertTrue(res["resolved"])
        self.assertEqual(mock_post.call_count, 2)

    # -------------------------------------------------------------------------
    # Validation Tests
    # -------------------------------------------------------------------------

    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            self.service.add_comment(self.bb_repo, 42, "   ")

        with self.assertRaises(ValueError):
            self.service.reply_to_comment(self.bb_repo, 42, None, "Reply")

        with self.assertRaises(ValueError):
            self.service.edit_comment(self.bb_repo, 42, None, "Edit")

        with self.assertRaises(ValueError):
            self.service.delete_comment(self.bb_repo, 42, None)

        with self.assertRaises(ValueError):
            self.service.resolve_comment(self.bb_repo, 42, None)


if __name__ == "__main__":
    unittest.main()
