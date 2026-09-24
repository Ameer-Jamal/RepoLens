import unittest
from unittest.mock import MagicMock, patch
import requests

from models.contribution_models import RepositoryRef
from services.provider_api import BitbucketProviderClient, GitHubProviderClient


class _MockConfig:
    def get_provider(self):
        return "bitbucket"

    def get_bitbucket_username(self):
        return "test_user"

    def get_bitbucket_api_token(self):
        return "test_token"

    def get_bitbucket_workspace(self):
        return "test_workspace"

    def get_github_owner(self):
        return "test_owner"

    def get_github_repo(self):
        return "test_repo"

    def get_github_token(self):
        return "test_gh_token"


class ProviderApiApprovalsTests(unittest.TestCase):
    def setUp(self):
        self.config = _MockConfig()
        self.bb_client = BitbucketProviderClient(self.config)
        self.gh_client = GitHubProviderClient(self.config)
        self.bb_repo = RepositoryRef(
            provider="bitbucket",
            workspace="test_workspace",
            slug="test_repo",
            display_name="test_repo",
            full_name="test_workspace/test_repo",
        )
        self.gh_repo = RepositoryRef(
            provider="github",
            workspace="test_owner",
            slug="test_repo",
            display_name="test_repo",
            full_name="test_owner/test_repo",
        )

    @patch("requests.post")
    def test_bitbucket_approve_pull_request(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 1, "content": {"raw": "LGTM"}}
        mock_post.return_value = mock_resp

        res = self.bb_client.approve_pull_request(self.bb_repo, "42", comment="LGTM")
        self.assertTrue(res["approved"])
        self.assertEqual(res["pr_id"], "42")
        self.assertTrue(mock_post.called)

    @patch("requests.delete")
    def test_bitbucket_unapprove_pull_request(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        res = self.bb_client.unapprove_pull_request(self.bb_repo, "42")
        self.assertFalse(res["approved"])
        self.assertEqual(res["pr_id"], "42")

    @patch("requests.post")
    def test_bitbucket_request_changes_rejects_failed_action(self, mock_post):
        failed = MagicMock()
        failed.raise_for_status.side_effect = requests.HTTPError("request changes failed")
        mock_post.return_value = failed

        with self.assertRaises(requests.HTTPError):
            self.bb_client.request_changes_on_pr(self.bb_repo, "42", "Please fix this")
        self.assertEqual(mock_post.call_count, 1)

    @patch("requests.post")
    def test_bitbucket_request_changes_reports_partial_comment_failure(self, mock_post):
        action = MagicMock()
        comment = MagicMock()
        comment.raise_for_status.side_effect = requests.HTTPError("comment failed")
        mock_post.side_effect = [action, comment]

        result = self.bb_client.request_changes_on_pr(self.bb_repo, "42", "Please fix this")
        self.assertTrue(result["changes_requested"])
        self.assertIsNone(result["comment"])
        self.assertIn("comment failed", result["comment_error"])

    @patch("requests.post")
    def test_github_approve_pull_request(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 101, "state": "APPROVED"}
        mock_post.return_value = mock_resp

        res = self.gh_client.approve_pull_request(self.gh_repo, "10", comment="Approved!")
        self.assertTrue(res["approved"])
        self.assertEqual(res["review_id"], 101)

    @patch("requests.put")
    @patch("requests.get")
    def test_github_unapprove_uses_authenticated_user_not_repo_owner(self, mock_get, mock_put):
        user = MagicMock()
        user.json.return_value = {"login": "reviewer"}
        reviews = MagicMock()
        reviews.json.return_value = [
            {"id": 10, "state": "APPROVED", "user": {"login": "test_owner"}},
            {"id": 11, "state": "APPROVED", "user": {"login": "reviewer"}},
        ]
        mock_get.side_effect = [user, reviews]

        result = self.gh_client.unapprove_pull_request(self.gh_repo, "42")
        self.assertEqual(result["dismissed_count"], 1)
        self.assertIn("/reviews/11/dismissals", mock_put.call_args.args[0])

    @patch("requests.put")
    @patch("requests.get")
    def test_github_unapprove_does_not_claim_success_without_own_approval(self, mock_get, mock_put):
        user = MagicMock()
        user.json.return_value = {"login": "reviewer"}
        reviews = MagicMock()
        reviews.json.return_value = [{"id": 10, "state": "APPROVED", "user": {"login": "test_owner"}}]
        mock_get.side_effect = [user, reviews]

        with self.assertRaisesRegex(ValueError, "No approval by reviewer"):
            self.gh_client.unapprove_pull_request(self.gh_repo, "42")
        mock_put.assert_not_called()

    @patch("requests.get")
    def test_bitbucket_get_file_content(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.text = "print('hello world')\n"
        mock_get.return_value = mock_resp

        content = self.bb_client.get_file_content(self.bb_repo, "main.py", "v1.0.0")
        self.assertEqual(content, "print('hello world')\n")

    @patch("requests.get")
    def test_github_get_pull_request_diff_text(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.text = "diff --git a/test.py b/test.py\n+new line\n"
        mock_get.return_value = mock_resp

        diff = self.gh_client.get_pull_request_diff_text(self.gh_repo, "10")
        self.assertIn("+new line", diff)


if __name__ == "__main__":
    unittest.main()
