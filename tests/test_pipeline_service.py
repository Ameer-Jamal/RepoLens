import unittest
from unittest.mock import Mock, patch

import requests

from services.pipeline_service import PipelineService


GH = {"provider": "github", "owner": "acme", "slug": "tests", "default_branch": "main"}
BB = {"provider": "bitbucket", "owner": "acme", "slug": "tests"}


class Config:
    def get_provider(self):
        return "github"

    def get_github_token(self):
        return "token"

    def get_bitbucket_username(self):
        return "person@example.com"

    def get_bitbucket_api_token(self):
        return "token"


class PipelineServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = PipelineService(Config())

    @patch("services.pipeline_service.build_provider_client_for_name")
    def test_github_discovers_dispatch_inputs(self, client_factory):
        client_factory.return_value._headers.return_value = {"Authorization": "Bearer token"}
        client_factory.return_value.branch_exists.return_value = True
        client_factory.return_value.get_file_content.return_value = (
            "on:\n  workflow_dispatch:\n    inputs:\n      suite:\n        type: choice\n"
            "        required: true\n        options: [smoke, full]\n")
        response = Mock()
        response.json.return_value = {"workflows": [{"id": 7, "name": "Tests", "state": "active",
                                                      "path": ".github/workflows/tests.yml"}]}
        response.links = {}
        with patch.object(self.service, "_request", return_value=response):
            pipelines = self.service.list_pipelines(GH, "main")
        self.assertEqual(pipelines[0]["id"], "7")
        self.assertEqual(pipelines[0]["inputs"][0]["options"], ["smoke", "full"])

    @patch("services.pipeline_service.build_provider_client_for_name")
    def test_bitbucket_custom_variables_and_branch_run(self, client_factory):
        client_factory.return_value._auth.return_value = ("person@example.com", "token")
        client_factory.return_value.branch_exists.return_value = True
        client_factory.return_value.get_file_content.return_value = (
            "pipelines:\n  default:\n    - step: {script: [echo test]}\n"
            "  custom:\n    regression:\n      - variables:\n"
            "          - name: suite\n            default: smoke\n"
            "      - step: {script: [echo test]}\n")
        pipelines = self.service.list_pipelines(BB, "main")
        self.assertEqual([p["id"] for p in pipelines], ["default", "regression"])
        self.assertEqual(pipelines[1]["inputs"][0]["default"], "smoke")
        response = Mock()
        response.json.return_value = {"uuid": "{run-1}", "links": {"html": {"href": "https://bitbucket.org/run"}}}
        with patch.object(self.service, "_request", return_value=response) as request:
            result = self.service.run_pipeline(BB, "main", "regression", inputs={"suite": "full"})
        self.assertEqual(result["run_id"], "{run-1}")
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["target"]["selector"], {"type": "custom", "pattern": "regression"})
        self.assertEqual(payload["variables"], [{"key": "suite", "value": "full"}])

    @patch("services.pipeline_service.build_provider_client_for_name")
    def test_github_dispatch_with_and_without_run_id(self, client_factory):
        client_factory.return_value._headers.return_value = {"Authorization": "Bearer token"}
        client_factory.return_value.branch_exists.return_value = True
        pipeline = {"id": "7", "name": "Tests", "kind": "workflow", "inputs": [
            {"name": "suite", "required": True, "default": None, "options": ["smoke", "full"]}]}
        with patch.object(self.service, "list_pipelines", return_value=[pipeline]):
            for body, expected in [({"workflow_run_id": 99, "html_url": "https://github.com/run"}, "99"), ({}, "")]:
                response = Mock()
                response.content = b"body" if body else b""
                response.json.return_value = body
                with patch.object(self.service, "_request", return_value=response) as request:
                    result = self.service.run_pipeline(GH, "main", "7", inputs={"suite": "smoke"})
                self.assertEqual(result["run_id"], expected)
                self.assertEqual(request.call_args.kwargs["json"], {"ref": "main", "inputs": {"suite": "smoke"}})
            with self.assertRaisesRegex(ValueError, "Invalid choice"):
                self.service.run_pipeline(GH, "main", "7", inputs={"suite": "other"})
            with self.assertRaisesRegex(ValueError, "Required input"):
                self.service.run_pipeline(GH, "main", "7", inputs={})
            client_factory.return_value.branch_exists.return_value = False
            with self.assertRaisesRegex(ValueError, "does not exist"):
                self.service.run_pipeline(GH, "missing", "7")

    @patch("services.pipeline_service.build_provider_client_for_name")
    def test_github_dispatch_uses_omitted_defaults_and_normalizes_booleans(self, client_factory):
        client_factory.return_value._headers.return_value = {}
        client_factory.return_value.branch_exists.return_value = True
        pipeline = {"id": "7", "kind": "workflow", "inputs": [
            {"name": "suite", "required": True, "default": "smoke", "options": ["smoke", "full"]},
            {"name": "debug", "type": "boolean", "required": False, "default": False, "options": []},
        ]}
        response = Mock(content=b"")
        with patch.object(self.service, "list_pipelines", return_value=[pipeline]), patch.object(
            self.service, "_request", return_value=response
        ) as request:
            self.service.run_pipeline(GH, "main", "7", inputs={"debug": False})
            self.assertEqual(request.call_args.kwargs["json"]["inputs"], {"debug": "false"})
            with self.assertRaisesRegex(ValueError, "Invalid boolean"):
                self.service.run_pipeline(GH, "main", "7", inputs={"debug": "sometimes"})

    @patch("services.pipeline_service.build_provider_client_for_name")
    def test_malformed_definition_and_log_limit(self, client_factory):
        client_factory.return_value._auth.return_value = ("person@example.com", "token")
        client_factory.return_value.branch_exists.return_value = True
        client_factory.return_value.get_file_content.return_value = "pipelines: [broken"
        with self.assertRaisesRegex(ValueError, "Invalid pipeline definition"):
            self.service.list_pipelines(BB, "main")
        response = Mock()
        response.iter_content.return_value = iter([b"abc", b"def"])
        with patch.object(self.service, "_request", return_value=response):
            result = self.service.get_log(BB, "{run}", "{step}", max_chars=4)
        self.assertEqual(result["text"], "abcd")
        self.assertTrue(result["truncated"])
        response.close.assert_called_once()

    @patch("services.pipeline_service.build_provider_client_for_name")
    def test_bitbucket_log_asks_for_the_raw_stream(self, client_factory):
        client_factory.return_value._auth.return_value = ("person@example.com", "token")
        response = Mock()
        response.iter_content.return_value = iter([b"log"])
        with patch.object(self.service, "_request", return_value=response) as request:
            self.service.get_log(BB, "{run}", "{step}")
        self.assertEqual(request.call_args.kwargs["headers"]["Accept"], "application/octet-stream")

    @patch("services.pipeline_service.build_provider_client_for_name")
    def test_bitbucket_branch_history_matches_branch_selectors_and_stops_at_limit(self, client_factory):
        client_factory.return_value._auth.return_value = ("person@example.com", "token")
        response = Mock()
        response.json.return_value = {"values": [
            {"uuid": "custom", "target": {"ref_name": "develop", "selector": {"type": "custom", "pattern": "smoke"}}},
            {"uuid": "branch", "target": {"ref_name": "develop", "selector": {"type": "branches", "pattern": "develop"}}},
        ], "next": "https://api.bitbucket.org/next"}
        with patch.object(self.service, "_request", return_value=response) as request:
            runs = self.service.list_runs(BB, "branch", "develop", limit=1)
        self.assertEqual([run["run_id"] for run in runs], ["branch"])
        request.assert_called_once()

    @patch("services.pipeline_service.build_provider_client_for_name")
    def test_permission_failure_is_exposed(self, client_factory):
        client_factory.return_value._headers.return_value = {"Authorization": "Bearer token"}
        response = requests.Response()
        response.status_code = 403
        response.url = "https://api.github.com/repos/acme/tests/actions/runs"
        with patch("services.pipeline_service.requests.request", return_value=response):
            with self.assertRaises(requests.HTTPError):
                self.service.list_runs(GH)

    @patch("services.pipeline_service.build_provider_client_for_name")
    def test_paginated_github_workflows(self, client_factory):
        client_factory.return_value._headers.return_value = {"Authorization": "Bearer token"}
        client_factory.return_value.branch_exists.return_value = True
        client_factory.return_value.get_file_content.return_value = "'on': {workflow_dispatch: {}}"
        first = Mock()
        first.json.return_value = {"workflows": [{"id": 1, "name": "One", "state": "active", "path": ".github/workflows/one.yml"}]}
        first.links = {"next": {"url": "https://api.github.com/page2"}}
        second = Mock()
        second.json.return_value = {"workflows": [{"id": 2, "name": "Two", "state": "active", "path": ".github/workflows/two.yml"}]}
        second.links = {}
        with patch.object(self.service, "_request", side_effect=[first, second]):
            result = self.service.list_pipelines(GH, "main")
        self.assertEqual([p["id"] for p in result], ["1", "2"])
