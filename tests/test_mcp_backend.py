import json
import pytest
from unittest.mock import MagicMock, patch
from mcp_server import RepoLensMCPBackend
from models.contribution_models import RepositoryRef

@pytest.fixture
def mock_backend():
    with patch("mcp_server.build_provider_client") as mock_build:
        mock_provider = MagicMock()
        mock_build.return_value = mock_provider
        
        # Mock resolve_repository
        backend = RepoLensMCPBackend()
        backend.resolve_repository = MagicMock(return_value={
            "provider": "bitbucket",
            "owner": "example-workspace",
            "slug": "backend-service",
            "full_name": "example-workspace/backend-service",
            "clone_url": "https://bitbucket.org/example-workspace/backend-service.git"
        })
        return backend, mock_provider

def test_find_pr_for_commit(mock_backend):
    backend, provider = mock_backend
    provider.list_pull_requests_for_commit.return_value = [{"id": "123", "title": "Fix bug"}]
    
    result = backend.find_pr_for_commit(commit_hash="abc123")
    
    assert result["count"] == 1
    assert result["pull_requests"][0]["id"] == "123"
    provider.list_pull_requests_for_commit.assert_called_once()

def test_search_contributions_by_ticket(mock_backend):
    backend, provider = mock_backend
    
    with patch.object(backend.history_service, "execute_query") as mock_execute:
        mock_execute.return_value = MagicMock(
            records=[],
            scope=MagicMock(scope_type="all", label="All", repositories=[]),
            partial_errors=[],
            grouped_records=[],
            total_prs=0,
            total_commits=0,
            active_repositories=[],
            top_repositories=[],
            ticket_prefixes=[]
        )
        
        backend.search_contributions_by_ticket(ticket="RU-24133")
        
        args, _ = mock_execute.call_args
        query = args[0]
        assert query.search_text == "RU-24133"

def test_analyze_file_history(mock_backend):
    backend, provider = mock_backend
    
    with patch.object(backend.history_service, "execute_file_history_query") as mock_execute:
        mock_execute.return_value = MagicMock(
            records=[MagicMock(to_dict=lambda: {"id": "1"})],
            total_prs=1,
            total_commits=0
        )
        
        result = backend.analyze_file_history(file_path="src/main.java")
        
        assert result["file_path"] == "src/main.java"
        assert len(result["records"]) == 1
        mock_execute.assert_called_once()

def test_query_contribution_history_fuzzy_resolve(mock_backend):
    backend, provider = mock_backend
    
    backend.resolve_repository.side_effect = lambda **kwargs: {
        "provider": "bitbucket",
        "owner": "example-workspace",
        "slug": kwargs.get("slug"),
        "full_name": f"example-workspace/{kwargs.get('slug')}"
    }
    
    with patch.object(backend.history_service, "execute_query") as mock_execute:
        mock_execute.return_value = MagicMock(
            records=[],
            scope=MagicMock(scope_type="custom", label="custom", repositories=[RepositoryRef(provider="bitbucket", workspace="example-workspace", slug="backend-service", display_name="backend-service", full_name="example-workspace/backend-service")]),
            partial_errors=[],
            grouped_records=[],
            total_prs=0,
            total_commits=0,
            active_repositories=[],
            top_repositories=[],
            ticket_prefixes=[]
        )
        
        backend.query_contribution_history(
            developer="me",
            repositories_json=json.dumps(["backend-service"])
        )
        
        args, _ = mock_execute.call_args
        query = args[0]
        assert len(query.scope_repositories) == 1
        assert query.scope_repositories[0].slug == "backend-service"

def test_get_pr_context_url(mock_backend):
    backend, provider = mock_backend
    
    backend.resolve_repository.return_value = {
        "provider": "bitbucket",
        "owner": "example-workspace",
        "slug": "db-migration-service",
        "full_name": "example-workspace/db-migration-service",
        "clone_url": "https://bitbucket.org/example-workspace/db-migration-service.git"
    }
    
    with patch.object(backend.pr_service, "parse_pr_url") as mock_parse:
        mock_parse.return_value = {
            "provider": "bitbucket",
            "workspace": "example-workspace",
            "slug": "db-migration-service",
            "pr_id": "778"
        }
        
        with patch.object(backend.pr_service, "get_pull_request") as mock_get_pr:
            mock_get_pr.return_value = {"id": 778, "title": "DB Migration"}
            
            with patch.object(backend, "_repo_dir") as mock_repo_dir:
                mock_repo_dir.return_value = "/tmp/repo"
                with patch.object(backend.diff_service, "generate_pr_diff") as mock_diff:
                    mock_diff.return_value = MagicMock(
                        diff_text="diff content",
                        merge_base="base",
                        source_commit="src",
                        destination_commit="dst",
                        merge_commit="mrg"
                    )
                    
                    result = backend.get_pr_context(reference="https://bitbucket.org/example-workspace/db-migration-service/pull-requests/778")
                    
                    assert result["pr"]["id"] == 778
                    assert result["diff_text"] == "diff content"
                    mock_parse.assert_called_once()
                    mock_get_pr.assert_called_once()


def test_get_pr_context_url_with_repo_dir_uses_direct_resolution():
    config = MagicMock()
    config.get_provider.return_value = "bitbucket"
    config.get_active_repository.return_value = {}
    config.get_selected_repositories.return_value = []
    config.get_managed_repo_root.return_value = ""
    config.get_repo_dir.return_value = ""
    config.effective_source_summary.return_value = {}
    with patch("mcp_server.build_provider_client") as mock_build:
        mock_build.return_value = MagicMock()
        backend = RepoLensMCPBackend(config)

    with patch.object(backend.pr_service, "parse_pr_url") as mock_parse:
        mock_parse.return_value = {
            "provider": "bitbucket",
            "workspace": "example-workspace",
            "slug": "backend-service",
            "pr_id": "2430",
        }
        with patch.object(backend.pr_service, "get_pull_request", return_value={"id": 2430}) as mock_get_pr:
            with patch.object(backend.diff_service, "generate_pr_diff") as mock_diff:
                mock_diff.return_value = MagicMock(
                    diff_text="diff content",
                    merge_base="base",
                    source_commit="src",
                    destination_commit="dst",
                    merge_commit="mrg",
                )
                result = backend.get_pr_context(
                    reference="https://bitbucket.org/example-workspace/backend-service/pull-requests/2430",
                    repo_dir="/tmp/backend-service",
                )

    assert result["repo_dir"] == "/tmp/backend-service"
    repo_arg = mock_get_pr.call_args.args[0]
    assert repo_arg["owner"] == "example-workspace"
    assert repo_arg["local_dir"] == "/tmp/backend-service"


def test_create_pull_request(mock_backend):
    backend, _provider = mock_backend
    backend.resolve_repository.return_value = {
        "provider": "github",
        "owner": "openai",
        "slug": "demo",
        "full_name": "openai/demo",
    }
    with patch.object(backend.pr_creation_service, "create_pull_request") as mock_create:
        mock_create.return_value = {"url": "https://github.com/openai/demo/pull/17", "number": 17}

        result = backend.create_pull_request(
            title="Feature",
            source_branch="feature/test",
            target_branch="main",
        )

    assert result["number"] == 17
    mock_create.assert_called_once()


def test_create_pull_request_with_changes(mock_backend):
    backend, _provider = mock_backend
    with patch.object(backend.pr_patch_service, "create_pull_request_from_changes") as mock_create:
        mock_create.return_value = {"number": 9, "url": "https://example.com/pr/9"}

        result = backend.create_pull_request_with_changes(
            title="Fix typo",
            target_branch="main",
            changes_json='[{"path": "README.md", "content": "hello\\n"}]',
            repo_dir="/tmp/demo",
        )

    assert result["number"] == 9
    mock_create.assert_called_once()
    request = mock_create.call_args.args[1]
    assert request.title == "Fix typo"
    assert request.target_branch == "main"
    assert len(request.files) == 1
    assert request.files[0].path == "README.md"


def test_update_pull_request(mock_backend):
    backend, _provider = mock_backend
    backend.resolve_repository.return_value = {
        "provider": "github",
        "owner": "openai",
        "slug": "demo",
        "full_name": "openai/demo",
    }
    with patch.object(backend.pr_creation_service, "update_pull_request") as mock_update:
        mock_update.return_value = {"number": 17, "title": "New Title"}

        result = backend.update_pull_request(
            pr_id=17,
            title="New Title",
        )

    assert result["number"] == 17
    assert result["title"] == "New Title"
    mock_update.assert_called_once()


def test_create_pull_request_uses_direct_repo_args():
    config = MagicMock()
    config.get_provider.return_value = "bitbucket"
    config.get_active_repository.return_value = {
        "provider": "bitbucket",
        "owner": "example-workspace",
        "slug": "mobile-app",
    }
    config.get_selected_repositories.return_value = []
    config.get_managed_repo_root.return_value = ""
    config.get_repo_dir.return_value = ""
    config.effective_source_summary.return_value = {}
    with patch("mcp_server.build_provider_client") as mock_build:
        mock_build.return_value = MagicMock()
        backend = RepoLensMCPBackend(config)

    with patch.object(backend.pr_creation_service, "create_pull_request") as mock_create:
        mock_create.return_value = {"url": "https://github.com/Ameer-Jamal/RepoLens/pull/8", "number": 8}
        result = backend.create_pull_request(
            provider="github",
            workspace="Ameer-Jamal",
            slug="RepoLens",
            title="Feature",
            source_branch="feature/test",
            target_branch="main",
        )

    assert result["number"] == 8
    repo_arg = mock_create.call_args.args[0]
    assert repo_arg["provider"] == "github"
    assert repo_arg["owner"] == "Ameer-Jamal"
    assert repo_arg["slug"] == "RepoLens"


def test_create_pull_request_can_infer_repo_from_repo_dir():
    config = MagicMock()
    config.get_provider.return_value = "bitbucket"
    config.get_active_repository.return_value = {}
    config.get_selected_repositories.return_value = []
    config.get_managed_repo_root.return_value = ""
    config.get_repo_dir.return_value = ""
    config.effective_source_summary.return_value = {}
    with patch("mcp_server.build_provider_client") as mock_build:
        mock_build.return_value = MagicMock()
        backend = RepoLensMCPBackend(config)

    inferred_context = MagicMock()
    inferred_context.repository_dict.return_value = {
        "provider": "github",
        "owner": "Ameer-Jamal",
        "slug": "RepoLens",
        "local_dir": "/tmp/RepoLens",
    }
    with patch.object(backend.git_context_service, "resolve", return_value=inferred_context):
        with patch.object(backend.pr_creation_service, "create_pull_request") as mock_create:
            mock_create.return_value = {"number": 8}
            backend.create_pull_request(
                repo_dir="/tmp/RepoLens",
                title="Feature",
                source_branch="feature/test",
                target_branch="main",
            )

    repo_arg = mock_create.call_args.args[0]
    assert repo_arg["provider"] == "github"
    assert repo_arg["owner"] == "Ameer-Jamal"


def test_get_git_repository_context_returns_auth_status():
    config = MagicMock()
    config.get_github_token.return_value = "token"
    config.get_bitbucket_username.return_value = ""
    config.get_bitbucket_api_token.return_value = ""
    config.get_provider.return_value = "github"
    config.get_managed_repo_root.return_value = ""
    config.get_repo_dir.return_value = "/tmp/RepoLens"
    config.get_active_repository.return_value = {}
    config.get_selected_repositories.return_value = []
    config.effective_source_summary.return_value = {}
    with patch("mcp_server.build_provider_client") as mock_build:
        mock_build.return_value = MagicMock()
        backend = RepoLensMCPBackend(config)

    context = MagicMock()
    context.to_dict.return_value = {"provider": "github", "owner": "Ameer-Jamal", "slug": "RepoLens"}
    with patch.object(backend.git_context_service, "resolve", return_value=context):
        result = backend.get_git_repository_context()

    assert result["auth"]["github_token_configured"] is True
    assert result["auth"]["bitbucket_username_configured"] is False


def test_list_pull_requests_can_infer_repo_from_repo_dir():
    config = MagicMock()
    config.get_provider.return_value = "bitbucket"
    config.get_active_repository.return_value = {}
    config.get_selected_repositories.return_value = []
    config.get_managed_repo_root.return_value = ""
    config.get_repo_dir.return_value = ""
    config.effective_source_summary.return_value = {}
    with patch("mcp_server.build_provider_client") as mock_build:
        mock_build.return_value = MagicMock()
        backend = RepoLensMCPBackend(config)

    inferred_context = MagicMock()
    inferred_context.repository_dict.return_value = {
        "provider": "bitbucket",
        "owner": "example-workspace",
        "slug": "backend-service",
        "local_dir": "/tmp/backend-service",
    }
    with patch.object(backend.git_context_service, "resolve", return_value=inferred_context):
        with patch.object(backend.pr_service, "list_pull_requests_for_repo") as mock_list:
            mock_list.return_value = ([{"id": 1}], "")
            result = backend.list_pull_requests(repo_dir="/tmp/backend-service", filter_mode="OPEN")

    assert result["repository"]["provider"] == "bitbucket"
    assert result["records"] == [{"id": 1}]
    repo_arg = mock_list.call_args.args[0]
    assert repo_arg["owner"] == "example-workspace"


def test_find_pull_requests_by_ticket_uses_direct_repo_args():
    config = MagicMock()
    config.get_provider.return_value = "bitbucket"
    config.get_active_repository.return_value = {"provider": "bitbucket", "owner": "wrong-workspace", "slug": "wrong-service"}
    config.get_selected_repositories.return_value = []
    config.get_managed_repo_root.return_value = ""
    config.get_repo_dir.return_value = ""
    config.effective_source_summary.return_value = {}
    with patch("mcp_server.build_provider_client") as mock_build:
        mock_build.return_value = MagicMock()
        backend = RepoLensMCPBackend(config)

    with patch.object(backend.pr_service, "find_pull_requests_by_ticket") as mock_find:
        mock_find.return_value = [{"id": 2430}]
        result = backend.find_pull_requests_by_ticket(
            ticket="RU-25403",
            provider="bitbucket",
            workspace="example-workspace",
            slug="backend-service",
        )

    assert result["count"] == 1
    repo_arg = mock_find.call_args.args[0][0]
    assert repo_arg["owner"] == "example-workspace"
    assert repo_arg["slug"] == "backend-service"


def test_get_pr_diff_can_infer_repo_from_repo_dir():
    config = MagicMock()
    config.get_provider.return_value = "bitbucket"
    config.get_active_repository.return_value = {}
    config.get_selected_repositories.return_value = []
    config.get_managed_repo_root.return_value = ""
    config.get_repo_dir.return_value = ""
    config.effective_source_summary.return_value = {}
    with patch("mcp_server.build_provider_client") as mock_build:
        mock_build.return_value = MagicMock()
        backend = RepoLensMCPBackend(config)

    inferred_context = MagicMock()
    inferred_context.repository_dict.return_value = {
        "provider": "bitbucket",
        "owner": "example-workspace",
        "slug": "backend-service",
        "local_dir": "/tmp/backend-service",
    }
    with patch.object(backend.git_context_service, "resolve", return_value=inferred_context):
        with patch.object(backend.pr_service, "get_pull_request", return_value={"id": 2429}) as mock_get:
            with patch.object(backend.diff_service, "generate_pr_diff") as mock_diff:
                mock_diff.return_value = MagicMock(
                    diff_text="diff --git",
                    merge_base="base",
                    source_commit="src",
                    destination_commit="dst",
                    merge_commit="merge",
                    resolved_source="src",
                    resolved_destination="dst",
                )
                result = backend.get_pr_diff(pr_id="2429", repo_dir="/tmp/backend-service")

    assert result["repo_dir"] == "/tmp/backend-service"
    assert result["diff_text"] == "diff --git"
    repo_arg = mock_get.call_args.args[0]
    assert repo_arg["slug"] == "backend-service"


def test_update_pull_request_can_infer_repo_from_repo_dir():
    config = MagicMock()
    config.get_provider.return_value = "bitbucket"
    config.get_active_repository.return_value = {}
    config.get_selected_repositories.return_value = []
    config.get_managed_repo_root.return_value = ""
    config.get_repo_dir.return_value = ""
    config.effective_source_summary.return_value = {}
    with patch("mcp_server.build_provider_client") as mock_build:
        mock_build.return_value = MagicMock()
        backend = RepoLensMCPBackend(config)

    inferred_context = MagicMock()
    inferred_context.repository_dict.return_value = {
        "provider": "bitbucket",
        "owner": "example-workspace",
        "slug": "backend-service",
        "local_dir": "/tmp/backend-service",
    }
    with patch.object(backend.git_context_service, "resolve", return_value=inferred_context):
        with patch.object(backend.pr_creation_service, "update_pull_request") as mock_update:
            mock_update.return_value = {"number": 2429}
            result = backend.update_pull_request(
                pr_id="2429",
                repo_dir="/tmp/backend-service",
                title="Updated title",
            )

    assert result["number"] == 2429
    repo_arg = mock_update.call_args.args[0]
    assert repo_arg["owner"] == "example-workspace"


def test_get_pr_comments_with_pr_id(mock_backend):
    backend, provider = mock_backend
    with patch.object(backend.pr_comment_service, "get_pull_request_comments") as mock_comments:
        mock_comments.return_value = {
            "summary": {"total_comments": 2, "unresolved_threads": 1},
            "threads": [{"thread_id": 1, "resolved": False}],
            "comments": [{"id": 1}],
            "formatted_summary": "# PR #42 Comments",
        }

        result = backend.get_pr_comments(pr_id="42", unresolved_only=True, comment_type="inline")

        assert result["summary"]["total_comments"] == 2
        assert result["repository"]["slug"] == "backend-service"
        assert result["formatted_summary"] == "# PR #42 Comments"
        mock_comments.assert_called_once()
        _, kwargs = mock_comments.call_args
        assert kwargs["unresolved_only"] is True
        assert kwargs["comment_type"] == "inline"


def test_get_pr_comments_with_reference(mock_backend):
    backend, provider = mock_backend
    resolved_repo = {
        "provider": "github",
        "owner": "openai",
        "slug": "demo",
        "full_name": "openai/demo",
    }
    resolved_pr = {"id": 99, "title": "Feature"}

    with patch.object(backend, "resolve_pr_from_reference", return_value=(resolved_repo, resolved_pr)):
        with patch.object(backend.pr_comment_service, "get_pull_request_comments") as mock_comments:
            mock_comments.return_value = {
                "summary": {"total_comments": 1},
                "threads": [],
                "comments": [],
                "formatted_summary": "",
            }

            result = backend.get_pr_comments(reference="https://github.com/openai/demo/pull/99")

            assert result["summary"]["total_comments"] == 1
            assert mock_comments.call_args.args[1] == 99


def test_get_pr_comments_missing_args(mock_backend):
    backend, provider = mock_backend
    with pytest.raises(ValueError, match="Either 'pr_id' or 'reference'"):
        backend.get_pr_comments()


def test_get_pr_context_includes_comments_when_requested(mock_backend):
    backend, provider = mock_backend
    resolved_repo = {
        "provider": "github",
        "owner": "openai",
        "slug": "demo",
        "full_name": "openai/demo",
        "local_dir": "/tmp/demo",
    }
    resolved_pr = {"id": 55, "title": "Optimize"}

    with patch.object(backend, "resolve_pr_from_reference", return_value=(resolved_repo, resolved_pr)):
        with patch.object(backend, "_repo_dir", return_value="/tmp/demo"):
            with patch.object(backend.diff_service, "generate_pr_diff") as mock_diff:
                mock_diff.return_value = MagicMock(
                    diff_text="diff --git",
                    merge_base="base",
                    source_commit="src",
                    destination_commit="dst",
                    merge_commit="merge",
                )
                with patch.object(backend.pr_comment_service, "get_pull_request_comments") as mock_comments:
                    mock_comments.return_value = {
                        "summary": {"total_comments": 3, "unresolved_threads": 2},
                        "threads": [{"thread_id": 10}],
                        "comments": [{"id": 10}],
                        "formatted_summary": "Summary of comments",
                    }

                    result = backend.get_pr_context(reference="RU-12345", include_comments=True)

                    assert result["diff_text"] == "diff --git"
                    assert "comments" in result
                    assert "threads" in result
                    assert result["comment_summary"]["total_comments"] == 3
                    assert result["comments_formatted"] == "Summary of comments"


def test_get_pr_context_fallback_to_rest_diff_when_no_checkout(mock_backend):
    backend, provider = mock_backend
    resolved_repo = {
        "provider": "bitbucket",
        "owner": "etqdev",
        "slug": "mt-backend",
        "full_name": "etqdev/mt-backend",
        "local_dir": "",
    }
    resolved_pr = {"id": 2917, "title": "Feature branch"}

    with patch.object(backend, "resolve_pr_from_reference", return_value=(resolved_repo, resolved_pr)):
        with patch.object(backend.pr_service, "get_pull_request_diff_text", return_value="diff --git from rest") as mock_rest_diff:
            with patch.object(backend.pr_comment_service, "get_pull_request_comments") as mock_comments:
                mock_comments.return_value = {
                    "summary": {"total_comments": 1, "unresolved_threads": 0},
                    "threads": [],
                    "comments": [{"id": 100}],
                    "formatted_summary": "Clean",
                }

                result = backend.get_pr_context(
                    reference="https://bitbucket.org/etqdev/mt-backend/pull-requests/2917",
                    ensure_checkout=False,
                    include_comments=True,
                )

                assert result["diff_text"] == "diff --git from rest"
                assert result["repo_dir"] == ""
                assert result["pr"]["id"] == 2917
                assert result["comments"][0]["id"] == 100
                mock_rest_diff.assert_called_once_with(resolved_repo, 2917)


def test_get_pr_diff_fallback_to_rest_diff_when_no_checkout(mock_backend):
    backend, provider = mock_backend
    resolved_repo = {
        "provider": "bitbucket",
        "owner": "etqdev",
        "slug": "mt-backend",
        "full_name": "etqdev/mt-backend",
        "local_dir": "",
    }
    resolved_pr = {"id": "2917", "title": "Feature branch", "source_branch": "feature", "destination_branch": "main"}

    with patch.object(backend, "resolve_repository", return_value=resolved_repo):
        with patch.object(backend.pr_service, "get_pull_request", return_value=resolved_pr):
            with patch.object(backend.pr_service, "get_pull_request_diff_text", return_value="diff --git from rest") as mock_rest_diff:
                result = backend.get_pr_diff(
                    pr_id="2917",
                    ensure_checkout=False,
                )

                assert result["diff_text"] == "diff --git from rest"
                assert result["repo_dir"] == ""
                mock_rest_diff.assert_called_once_with(resolved_repo, "2917")


def test_find_existing_local_dir_from_managed_root(mock_backend, tmp_path):
    backend, _ = mock_backend
    managed_dir = tmp_path / "bitbucket" / "etqdev" / "mt-backend"
    managed_dir.mkdir(parents=True)
    with patch.object(backend.config, "get_managed_repo_root", return_value=str(tmp_path)):
        with patch.object(backend.config, "get_selected_repositories", return_value=[]):
            with patch.object(backend.config, "get_active_repository", return_value={}):
                found = backend._find_existing_local_dir({"provider": "bitbucket", "owner": "etqdev", "slug": "mt-backend"})
                assert found == str(managed_dir)


def test_find_existing_local_dir_from_selected_repos(mock_backend, tmp_path):
    backend, _ = mock_backend
    custom_dir = tmp_path / "Reliance-Repos" / "NXG" / "mt-backend"
    custom_dir.mkdir(parents=True)
    with patch.object(backend.config, "get_managed_repo_root", return_value=str(tmp_path / "managed")):
        with patch.object(
            backend.config,
            "get_selected_repositories",
            return_value=[{"provider": "bitbucket", "owner": "etqdev", "slug": "mt-backend", "local_dir": str(custom_dir)}],
        ):
            with patch.object(backend.config, "get_active_repository", return_value={}):
                found = backend._find_existing_local_dir({"provider": "bitbucket", "owner": "etqdev", "slug": "mt-backend"})
                assert found == str(custom_dir)



def test_add_pr_comment(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_comment_service, "add_comment") as mock_add:
        mock_add.return_value = {"id": 123, "body": "Great job!", "comment_type": "general"}
        res = backend.add_pr_comment(body="Great job!", pr_id="42")
        assert res["pr_id"] == "42"
        assert res["comment"]["id"] == 123
        mock_add.assert_called_once()


def test_add_pr_comments_posts_general_and_inline_in_order(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_comment_service, "add_comment") as mock_add:
        mock_add.side_effect = [{"id": 201}, {"id": 202}]
        result = backend.add_pr_comments(
            comments=[
                {"body": "Overall feedback"},
                {"body": "Fix this line", "file_path": "src/app.py", "line": 12, "side": "to"},
            ],
            pr_id="42",
            slug="backend-service",
        )

    assert result["pr_id"] == "42"
    assert result["summary"] == {"requested": 2, "succeeded": 2, "failed": 0}
    assert [entry["comment"]["id"] for entry in result["results"]] == [201, 202]
    backend.resolve_repository.assert_called_once_with(
        provider="", workspace="", slug="backend-service", scope="", repo_dir="", allow_direct=True
    )
    repo = backend.resolve_repository.return_value
    assert mock_add.call_args_list[0].args == (repo, "42", "Overall feedback")
    assert mock_add.call_args_list[0].kwargs == {"file_path": None, "line": None, "side": None}
    assert mock_add.call_args_list[1].args == (repo, "42", "Fix this line")
    assert mock_add.call_args_list[1].kwargs == {"file_path": "src/app.py", "line": 12, "side": "to"}


def test_add_pr_comments_reports_failures_without_posting_invalid_inline_comments(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_comment_service, "add_comment") as mock_add:
        mock_add.side_effect = [{"id": 201}, RuntimeError("Provider rejected comment")]
        result = backend.add_pr_comments(
            comments=[
                {"body": "First"},
                {"body": "Missing line", "file_path": "src/app.py"},
                {"body": "Third"},
            ],
            pr_id="42",
        )

    assert result["summary"] == {"requested": 3, "succeeded": 1, "failed": 2}
    assert result["results"][0] == {"index": 0, "success": True, "comment": {"id": 201}}
    assert result["results"][1]["index"] == 1
    assert "require both" in result["results"][1]["error"]
    assert result["results"][2] == {"index": 2, "success": False, "error": "Provider rejected comment"}
    assert mock_add.call_count == 2


def test_add_pr_comments_validates_request(mock_backend):
    backend, _ = mock_backend
    with pytest.raises(ValueError, match="At least one comment"):
        backend.add_pr_comments(comments=[], pr_id="42")
    with pytest.raises(ValueError, match="Either 'pr_id' or 'reference'"):
        backend.add_pr_comments(comments=[{"body": "Hello"}])
    backend.resolve_repository.assert_not_called()


def test_add_pr_comments_resolves_reference_once(mock_backend):
    backend, _ = mock_backend
    repo = backend.resolve_repository.return_value
    backend.resolve_pr_from_reference = MagicMock(return_value=(repo, {"id": 42}))
    with patch.object(backend.pr_comment_service, "add_comment", return_value={"id": 201}) as mock_add:
        result = backend.add_pr_comments(
            comments=[{"body": "First"}, {"body": "Second"}],
            reference="https://bitbucket.org/example-workspace/backend-service/pull-requests/42",
            provider="bitbucket",
        )

    assert result["pr_id"] == 42
    backend.resolve_pr_from_reference.assert_called_once()
    backend.resolve_repository.assert_not_called()
    assert mock_add.call_count == 2


def test_reply_to_pr_comment(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_comment_service, "reply_to_comment") as mock_reply:
        mock_reply.return_value = {"id": 124, "parent_id": 123, "body": "Fixed"}
        res = backend.reply_to_pr_comment(parent_id="123", body="Fixed", pr_id="42")
        assert res["pr_id"] == "42"
        assert res["parent_id"] == "123"
        assert res["comment"]["id"] == 124
        mock_reply.assert_called_once()


def test_reply_to_pr_comments_reports_partial_success(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_comment_service, "reply_to_comment") as mock_reply:
        mock_reply.side_effect = [
            {"id": 201, "parent_id": 101, "body": "Done"},
            RuntimeError("Bitbucket rejected the reply"),
        ]

        res = backend.reply_to_pr_comments(
            replies=[
                {"comment_id": "101", "body": "Done"},
                {"comment_id": "102", "body": "Still investigating"},
            ],
            pr_id="42",
        )

        assert res["summary"] == {"requested": 2, "succeeded": 1, "failed": 1}
        assert res["results"][0]["success"] is True
        assert res["results"][1] == {
            "comment_id": "102",
            "success": False,
            "error": "Bitbucket rejected the reply",
        }
        assert mock_reply.call_count == 2


def test_reply_to_pr_comments_requires_replies(mock_backend):
    backend, _ = mock_backend
    with pytest.raises(ValueError, match="At least one reply"):
        backend.reply_to_pr_comments(replies=[], pr_id="42")


def test_edit_pr_comment(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_comment_service, "edit_comment") as mock_edit:
        mock_edit.return_value = {"id": 123, "body": "Updated comment"}
        res = backend.edit_pr_comment(comment_id="123", body="Updated comment", pr_id="42")
        assert res["comment_id"] == "123"
        assert res["comment"]["body"] == "Updated comment"
        mock_edit.assert_called_once()


def test_delete_pr_comment(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_comment_service, "delete_comment") as mock_delete:
        mock_delete.return_value = {"success": True, "comment_id": 123, "deleted": True}
        res = backend.delete_pr_comment(comment_id="123", pr_id="42")
        assert res["success"] is True
        assert res["comment_id"] == 123
        mock_delete.assert_called_once()


def test_resolve_pr_comment(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_comment_service, "resolve_comment") as mock_resolve:
        mock_resolve.return_value = {"success": True, "comment_id": 123, "resolved": True}
        res = backend.resolve_pr_comment(comment_id="123", pr_id="42")
        assert res["success"] is True
        assert res["comment_id"] == 123
        assert res["resolved"] is True
        mock_resolve.assert_called_once_with(
            backend.resolve_repository(),
            "42",
            "123",
            unresolve=False,
        )


def test_unresolve_pr_comment(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_comment_service, "resolve_comment") as mock_resolve:
        mock_resolve.return_value = {"success": True, "comment_id": 123, "resolved": False}
        res = backend.resolve_pr_comment(comment_id="123", pr_id="42", unresolve=True)
        assert res["success"] is True
        assert res["comment_id"] == 123
        assert res["resolved"] is False
        mock_resolve.assert_called_once_with(
            backend.resolve_repository(),
            "42",
            "123",
            unresolve=True,
        )


def test_get_pr_ci_status(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_service, "get_pull_request") as mock_pr, \
         patch.object(backend.pr_service, "get_pull_request_statuses") as mock_status:
        mock_pr.return_value = {"id": 42, "title": "Test PR", "source_commit": "c0ffee"}
        mock_status.return_value = {
            "state": "SUCCESSFUL",
            "total_count": 5,
            "successful_count": 5,
            "failed_count": 0,
            "inprogress_count": 0,
            "statuses": [{"name": "CI", "state": "SUCCESSFUL"}],
        }

        res = backend.get_pr_ci_status(pr_id="42")
        assert res["pr_id"] == "42"
        assert res["commit_hash"] == "c0ffee"
        assert res["ci_status"]["state"] == "SUCCESSFUL"
        assert res["ci_status"]["successful_count"] == 5


def test_approve_pull_request(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_service, "approve_pull_request") as mock_approve:
        mock_approve.return_value = {"approved": True, "pr_id": "42"}
        res = backend.approve_pull_request(pr_id="42", comment="Looks great!")
        assert res["pr_id"] == "42"
        assert res["result"]["approved"] is True
        mock_approve.assert_called_once()


def test_unapprove_pull_request(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_service, "unapprove_pull_request") as mock_unapprove:
        mock_unapprove.return_value = {"approved": False, "pr_id": "42"}
        res = backend.unapprove_pull_request(pr_id="42")
        assert res["pr_id"] == "42"
        assert res["result"]["approved"] is False
        mock_unapprove.assert_called_once()


def test_request_changes_on_pr(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_service, "request_changes_on_pr") as mock_req:
        mock_req.return_value = {"changes_requested": True, "pr_id": "42"}
        res = backend.request_changes_on_pr(pr_id="42", comment="Please fix unit test")
        assert res["pr_id"] == "42"
        assert res["result"]["changes_requested"] is True
        mock_req.assert_called_once()


def test_get_file_content_at_ref(mock_backend):
    backend, _ = mock_backend
    with patch.object(backend.pr_service, "get_file_content_at_ref") as mock_content:
        mock_content.return_value = "def hello(): pass\n"
        res = backend.get_file_content_at_ref(file_path="src/app.py", ref="feature/test")
        assert res["file_path"] == "src/app.py"
        assert res["ref"] == "feature/test"
        assert res["content"] == "def hello(): pass\n"
        mock_content.assert_called_once()
