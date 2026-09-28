import unittest
from unittest.mock import patch

from mcp_server import DEFAULT_DIFF_CHAR_LIMIT, create_mcp_server, main, should_refuse_tty_stdio

try:
    from mcp.shared.memory import create_connected_server_and_client_session
except ImportError:  # pragma: no cover - dependency installation is validated in test execution
    create_connected_server_and_client_session = None


class _FakeBackend:
    def get_active_context(self):
        return {"provider": "github", "active_repository": {"slug": "demo"}}

    def list_repositories(self, force_refresh=False):
        return [{"provider": "github", "owner": "openai", "slug": "demo"}]

    def get_selected_repositories(self):
        return [{"provider": "github", "owner": "openai", "slug": "demo"}]

    def list_pull_requests(self, **kwargs):
        return {"repository": {"slug": kwargs.get("slug", "demo")}, "records": [{"id": 1}], "next_cursor": ""}

    def list_my_pull_requests(self, **kwargs):
        return {"developer": "me", "records": [{"id": 3}], "count": 1}

    def find_pull_requests_by_ticket(self, **kwargs):
        return {"ticket": kwargs["ticket"], "records": [{"id": 2, "title": kwargs["ticket"]}], "count": 1}

    def get_ticket_diffs(self, **kwargs):
        return {
            "tickets": [
                {
                    "ticket": "RU-25463",
                    "count": 1,
                    "pull_requests": [{"pr": {"id": 2}, "diff_text": "diff --git", "truncated": False}],
                }
            ]
        }

    def get_pr_diff(self, **kwargs):
        return {"diff_text": "x" * (DEFAULT_DIFF_CHAR_LIMIT + 10), "truncated": True, "pr": {"id": kwargs["pr_id"]}}

    def get_commit_diff(self, **kwargs):
        return {"commit_hash": kwargs["commit_hash"], "diff_text": "diff --git", "truncated": False}

    def query_contribution_history(self, **kwargs):
        return {"total_prs": 1, "total_commits": 0, "records": [], "grouped_records": []}

    def list_developer_candidates(self, **kwargs):
        return {"candidates": ["ajamal"]}

    def create_pull_request(self, **kwargs):
        return {"url": "https://example.com/pr/1", "number": 1, "draft": kwargs.get("draft", False)}

    def create_pull_request_with_changes(self, **kwargs):
        return {
            "url": "https://example.com/pr/2",
            "number": 2,
            "source_branch": kwargs.get("source_branch", ""),
            "changed_paths": ["src/app.py"],
        }

    def update_pull_request(self, **kwargs):
        return {"url": "https://example.com/pr/1", "number": 1, "title": kwargs.get("title", "")}

    def get_git_repository_context(self, **kwargs):
        return {"provider": "github", "owner": "openai", "slug": "demo", "default_branch": "main"}

    def get_pr_context(self, **kwargs):
        return {"repo_dir": kwargs.get("repo_dir", ""), "pr": {"id": 2430}, "diff_text": "diff --git"}

    def get_pr_comments(self, **kwargs):
        return {
            "summary": {"total_comments": 2, "unresolved_threads": 1},
            "threads": [{"thread_id": 101, "file_path": "auth.py", "line": 5, "resolved": False}],
            "comments": [{"id": 101, "body": "Fix this"}],
            "formatted_summary": "# PR #42 Comments",
        }

    def list_pipelines(self, branch, **kwargs):
        return {"branch": branch, "pipelines": [{"id": "7"}]}

    def run_pipeline(self, branch, pipeline_id, **kwargs):
        return {"run_id": "99", "branch": branch, "pipeline_id": pipeline_id, "inputs": kwargs.get("inputs")}

    def list_pipeline_runs(self, **kwargs):
        return {"runs": [{"run_id": "99"}]}

    def get_pipeline_run(self, run_id, **kwargs):
        return {"run_id": run_id, "steps": [{"id": "5"}]}

    def get_pipeline_log(self, run_id, step_id, **kwargs):
        return {"run_id": run_id, "step_id": step_id, "text": "ok"}


class _FakeStdin:
    def __init__(self, is_tty):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


class MCPServerLaunchTests(unittest.TestCase):
    def test_should_refuse_tty_stdio_for_interactive_stdin(self):
        self.assertTrue(should_refuse_tty_stdio(_FakeStdin(True)))
        self.assertFalse(should_refuse_tty_stdio(_FakeStdin(False)))

    def test_main_exits_when_started_directly_in_terminal(self):
        with patch("sys.argv", ["mcp_server.py"]):
            with patch("mcp_server.should_refuse_tty_stdio", return_value=True):
                with patch("mcp_server.create_mcp_server") as create_server:
                    with self.assertRaises(SystemExit) as context:
                        main()

        self.assertEqual(context.exception.code, 2)
        create_server.assert_not_called()

    def test_main_allows_piped_stdio(self):
        class _FakeApp:
            def run(self, transport):
                self.transport = transport

        fake_app = _FakeApp()
        with patch("sys.argv", ["mcp_server.py"]):
            with patch("mcp_server.should_refuse_tty_stdio", return_value=False):
                with patch("mcp_server.RepoLensMCPBackend"):
                    with patch("mcp_server.create_mcp_server", return_value=fake_app) as create_server:
                        main()

        create_server.assert_called_once()
        self.assertEqual(fake_app.transport, "stdio")

    def test_main_allows_tty_stdio_with_explicit_debug_flag(self):
        class _FakeApp:
            def run(self, transport):
                self.transport = transport

        fake_app = _FakeApp()
        with patch("sys.argv", ["mcp_server.py", "--allow-tty-stdio"]):
            with patch("mcp_server.should_refuse_tty_stdio", return_value=True):
                with patch("mcp_server.RepoLensMCPBackend"):
                    with patch("mcp_server.create_mcp_server", return_value=fake_app):
                        main()

        self.assertEqual(fake_app.transport, "stdio")


@unittest.skipIf(create_connected_server_and_client_session is None, "mcp dependency is unavailable")
class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_callable_over_in_memory_transport(self):
        app = create_mcp_server(_FakeBackend())
        async with create_connected_server_and_client_session(app, raise_exceptions=True) as session:
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            self.assertIn("get_active_context", tool_names)
            self.assertIn("get_pr_diff", tool_names)
            self.assertIn("find_pull_requests_by_ticket", tool_names)
            self.assertIn("get_ticket_diffs", tool_names)
            self.assertIn("list_my_pull_requests", tool_names)
            self.assertIn("create_pull_request", tool_names)
            self.assertIn("create_pull_request_with_changes", tool_names)
            self.assertIn("update_pull_request", tool_names)
            self.assertIn("get_git_repository_context", tool_names)
            self.assertIn("get_pr_context", tool_names)
            self.assertIn("get_pr_comments", tool_names)
            self.assertTrue({"list_pipelines", "run_pipeline", "list_pipeline_runs", "get_pipeline_run", "get_pipeline_log"} <= tool_names)

            pipelines_result = await session.call_tool("list_pipelines", {"branch": "main"})
            self.assertEqual(pipelines_result.structuredContent["pipelines"][0]["id"], "7")
            dispatch_result = await session.call_tool("run_pipeline", {"branch": "main", "pipeline_id": "7", "inputs": {"suite": "smoke"}})
            self.assertEqual(dispatch_result.structuredContent["inputs"], {"suite": "smoke"})
            runs_result = await session.call_tool("list_pipeline_runs", {})
            self.assertEqual(runs_result.structuredContent["runs"][0]["run_id"], "99")
            run_result = await session.call_tool("get_pipeline_run", {"run_id": "99"})
            self.assertEqual(run_result.structuredContent["steps"][0]["id"], "5")
            log_result = await session.call_tool("get_pipeline_log", {"run_id": "99", "step_id": "5"})
            self.assertEqual(log_result.structuredContent["text"], "ok")

            active_result = await session.call_tool("get_active_context", {})
            self.assertEqual(active_result.structuredContent["provider"], "github")

            pr_result = await session.call_tool("get_pr_diff", {"pr_id": "7"})
            self.assertTrue(pr_result.structuredContent["truncated"])
            self.assertEqual(pr_result.structuredContent["pr"]["id"], "7")

            ticket_result = await session.call_tool(
                "find_pull_requests_by_ticket",
                {"ticket": "RU-25463"},
            )
            self.assertEqual(ticket_result.structuredContent["count"], 1)

            ticket_diffs_result = await session.call_tool(
                "get_ticket_diffs",
                {"tickets_json": '["RU-25463"]'},
            )
            self.assertEqual(ticket_diffs_result.structuredContent["tickets"][0]["count"], 1)

            my_prs_result = await session.call_tool("list_my_pull_requests", {})
            self.assertEqual(my_prs_result.structuredContent["count"], 1)

            create_pr_result = await session.call_tool(
                "create_pull_request",
                {"title": "Test PR", "source_branch": "feature/test", "target_branch": "main", "repo_dir": "/tmp/demo"},
            )
            self.assertEqual(create_pr_result.structuredContent["number"], 1)

            create_pr_changes_result = await session.call_tool(
                "create_pull_request_with_changes",
                {
                    "title": "Test PR from changes",
                    "target_branch": "main",
                    "changes_json": '[{"path": "src/app.py", "content": "print(1)"}]',
                    "repo_dir": "/tmp/demo",
                },
            )
            self.assertEqual(create_pr_changes_result.structuredContent["number"], 2)
            self.assertEqual(
                create_pr_changes_result.structuredContent["changed_paths"], ["src/app.py"]
            )

            update_pr_result = await session.call_tool(
                "update_pull_request",
                {"pr_id": "1", "title": "Updated Title", "repo_dir": "/tmp/demo"},
            )
            self.assertEqual(update_pr_result.structuredContent["title"], "Updated Title")

            list_result = await session.call_tool("list_pull_requests", {"repo_dir": "/tmp/demo"})
            self.assertEqual(list_result.structuredContent["records"][0]["id"], 1)

            pr_diff_with_dir = await session.call_tool("get_pr_diff", {"pr_id": "7", "repo_dir": "/tmp/demo"})
            self.assertEqual(pr_diff_with_dir.structuredContent["pr"]["id"], "7")

            pr_context_result = await session.call_tool(
                "get_pr_context",
                {"reference": "https://bitbucket.org/example-workspace/backend-service/pull-requests/2430", "repo_dir": "/tmp/demo"},
            )
            self.assertEqual(pr_context_result.structuredContent["repo_dir"], "/tmp/demo")

            pr_comments_result = await session.call_tool(
                "get_pr_comments",
                {"pr_id": "42", "unresolved_only": True},
            )
            self.assertEqual(pr_comments_result.structuredContent["summary"]["total_comments"], 2)
            self.assertEqual(pr_comments_result.structuredContent["threads"][0]["file_path"], "auth.py")

            git_context_result = await session.call_tool("get_git_repository_context", {})
            self.assertEqual(git_context_result.structuredContent["default_branch"], "main")


if __name__ == "__main__":
    unittest.main()
