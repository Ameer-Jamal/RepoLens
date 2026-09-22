import unittest
from unittest.mock import MagicMock, patch

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


class ProviderApiCITests(unittest.TestCase):
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

    @patch("services.provider_api._get_json_with_retry")
    def test_bitbucket_commit_statuses_successful(self, mock_get_json):
        mock_get_json.return_value = {
            "values": [
                {
                    "name": "Unit Tests",
                    "key": "unit-tests",
                    "state": "SUCCESSFUL",
                    "description": "All 120 tests passed",
                    "url": "https://ci.example.com/build/1",
                    "type": "test",
                },
                {
                    "name": "Build",
                    "key": "build-artifact",
                    "state": "SUCCESSFUL",
                    "description": "Compilation succeeded",
                    "url": "https://ci.example.com/build/2",
                    "type": "build",
                },
            ]
        }
        res = self.bb_client.get_pull_request_statuses(self.bb_repo, "a1b2c3d")
        self.assertEqual(res["state"], "SUCCESSFUL")
        self.assertEqual(res["total_count"], 2)
        self.assertEqual(res["successful_count"], 2)
        self.assertEqual(res["failed_count"], 0)
        self.assertEqual(len(res["statuses"]), 2)

    @patch("services.provider_api._get_json_with_retry")
    def test_bitbucket_commit_statuses_failing(self, mock_get_json):
        mock_get_json.return_value = {
            "values": [
                {
                    "name": "Linter",
                    "state": "FAILED",
                    "description": "2 syntax errors found",
                    "url": "https://ci.example.com/build/3",
                }
            ]
        }
        res = self.bb_client.get_pull_request_statuses(self.bb_repo, "a1b2c3d")
        self.assertEqual(res["state"], "FAILED")
        self.assertEqual(res["failed_count"], 1)

    @patch("services.provider_api._get_json_with_retry")
    def test_github_check_runs_successful(self, mock_get_json):
        def _mock_side_effect(url, headers=None, timeout=20):
            if "check-runs" in url:
                return {
                    "check_runs": [
                        {
                            "name": "Pytest CI",
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://github.com/run/1",
                            "output": {"title": "All tests passed"},
                        }
                    ]
                }
            return {"statuses": []}

        mock_get_json.side_effect = _mock_side_effect
        res = self.gh_client.get_pull_request_statuses(self.gh_repo, "commit123")
        self.assertEqual(res["state"], "SUCCESSFUL")
        self.assertEqual(res["successful_count"], 1)
        self.assertEqual(res["failed_count"], 0)

    @patch("services.provider_api._get_json_with_retry")
    def test_github_check_runs_failing(self, mock_get_json):
        def _mock_side_effect(url, headers=None, timeout=20):
            if "check-runs" in url:
                return {
                    "check_runs": [
                        {
                            "name": "Integration Tests",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/run/2",
                            "output": {"title": "Test Auth Failed"},
                        }
                    ]
                }
            return {"statuses": []}

        mock_get_json.side_effect = _mock_side_effect
        res = self.gh_client.get_pull_request_statuses(self.gh_repo, "commit123")
        self.assertEqual(res["state"], "FAILED")
        self.assertEqual(res["failed_count"], 1)


if __name__ == "__main__":
    unittest.main()
