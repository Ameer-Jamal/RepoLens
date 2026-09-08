# **RepoLens: AI-Ready Repository and PR Context**

### **Overview**

**RepoLens** is a desktop application and local MCP server for collecting repository context that AI tools can use directly. It helps you inspect pull requests, generate diffs, query contribution history, and expose that same data to MCP-compatible agents.

<div>
   <img width="1735" height="906" alt="Banner" src="https://github.com/user-attachments/assets/c06783ee-f186-4201-bce8-fe3e77d832bf" />
   <img width="1074" height="917" alt="image" src="https://github.com/user-attachments/assets/11f8c2eb-4ba7-4490-811e-702f9eb3fee4" />
</div>

---

## **Why It Exists**

Git providers already contain the context an AI needs, but that context is scattered across commits, PRs, branches, repositories, and contribution history. RepoLens gives you one place to gather that context and one MCP server that AI clients can call directly.

---

## **Key Features**

1. **PR and Commit Diffs**: Extract diffs between commits or pull request branches and view the changes in a structured way.

2. **Provider Repository Discovery**: Configure Bitbucket or GitHub credentials in **Settings**, discover accessible repositories, and activate one or many repositories without manual local-folder selection.

3. **Multi-Repository Workflows**: Run PR, branch, ticket, and history workflows across selected repositories with repository context included in results.

4. **Pull Request Filtering**: Filter PRs by state, text search, and developer identity. The developer filter can use `Any developer`, `Me`, or a provider username/display name/email.

5. **Contribution History**: Query merged PRs, commits, or both by developer, date range, repository scope, branch, and text. Bitbucket's fast contributed-repositories scope discovers the person's repositories from workspace PR history before scanning them. Export results to CSV, JSON, or Markdown.

6. **MCP Server Mode**: Expose repository discovery, selected repositories, PR lookup, ticket lookup, diffs, and contribution history as local MCP tools for AI clients.
7. **PR Creation for AI Agents**: Create GitHub or Bitbucket pull requests from already-pushed source branches through the desktop app or MCP.

---

## **How to Use the App**

1. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

2. **Run the desktop app**:

   ```bash
   python3 main.py
   ```

3. **Configure provider access**:

   - Open `Settings`.
   - Choose `Bitbucket` or `GitHub`.
   - Enter credentials.
   - Discover repositories.
   - Select one or more active repositories.

4. **Use the main tabs**:

   - `PR Lens`: list/search PRs, filter by developer, and generate PR or commit diffs.
   - `Branch Commit Viewer`: inspect branch commits.
   - `Create PR`: create pull requests from configured repositories.
   - `Contribution History`: reconstruct work by developer, repository, branch, and date.
   - `Settings`: manage provider config, repository discovery, selected repos, and output paths.

In `Contribution History`, use **Contributed repos (fast, PR-discovered)** to avoid scanning every Bitbucket repository. RepoLens asks Bitbucket for merged PRs authored by the selected developer across the workspace, extracts the distinct target repositories, and scans only that shortlist. This scope is complete for authored PR history. A repository where the developer only pushed direct commits and never authored a PR cannot be discovered by Bitbucket's workspace PR API; use **All repos** when that exhaustive commit coverage is required.

Contribution results appear as each repository finishes. For the fast Bitbucket scope, RepoLens reuses the workspace pull-request response instead of downloading the same PRs again per repository. The running status includes provider request and retry counts; retries are bounded so a persistent provider error returns partial results instead of silently waiting through several long retry rounds.

---

## **MCP Server**

This repo includes a local stdio MCP server at `mcp_server.py`. Any MCP-compatible AI client can launch it and use RepoLens as a tool source.

Read tools do not change provider data. The explicitly named create, update, and comment tools can create PRs or modify PR metadata and comments. RepoLens may also clone or fetch local repositories when a diff tool needs a checkout, but it does not edit repository files or create commits.

### **Tools**

- `get_active_context`
- `list_repositories`
- `get_selected_repositories`
- `list_pull_requests`
- `list_my_pull_requests`
- `find_pull_requests_by_ticket`
- `get_ticket_diffs`
- `get_pr_diff`
- `get_commit_diff`
- `query_contribution_history` (use `scope_type="contributed_repos"` for fast Bitbucket PR-based repository discovery)
- `find_pr_for_commit` (Link commits to their parent PRs)
- `search_contributions_by_ticket` (Deep search across repositories for a ticket)
- `analyze_file_history` (Unified commit and PR history for a specific file)
- `get_pr_context` (Unified tool to get PR metadata and diff from a URL, ticket, or title, with optional comment threads)
- `get_pr_comments` (Fetch comments and review threads on a PR with file paths, line numbers, code context snippets, and AI-ready summary)
- `add_pr_comment` (Add a general or inline code review comment to a pull request)
- `reply_to_pr_comment` (Reply to an existing comment thread on a pull request)
- `reply_to_pr_comments` (Reply to multiple threads on one pull request in a single call)
- `edit_pr_comment` (Edit an existing pull request comment)
- `delete_pr_comment` (Delete a comment from a pull request)
- `resolve_pr_comment` (Resolve or reopen a comment thread on a pull request)
- `unresolve_pr_comment` (Reopen / unresolve an existing comment thread on a pull request)
- `list_developer_candidates`
- `create_pull_request` (Create one GitHub or Bitbucket PR from an existing remote branch)
- `get_git_repository_context` (Infer provider, repository, remote, branch, and auth context from a local Git checkout)

For AI-driven PR creation, a good workflow is:

1. Use normal Git commands to create a focused branch, commit changes, and push that branch.
2. Call `get_git_repository_context` with the checkout path and branch names to confirm provider/repository context and remote branch availability.
3. Use `repo_dir` or explicit `provider`/`workspace`/`slug` when calling PR tools so the MCP does not depend on the desktop app's active repository.
4. Call `create_pull_request` with `source_branch`, `target_branch`, `title`, and `description`.

`list_pull_requests`, `get_pr_diff`, `get_commit_diff`, `get_pr_comments`, `add_pr_comment`, `reply_to_pr_comment`, `reply_to_pr_comments`, `edit_pr_comment`, `delete_pr_comment`, `resolve_pr_comment`, `unresolve_pr_comment`, `create_pull_request`, and `update_pull_request` accept `repo_dir` for local-checkout-based repository inference. `create_pull_request` does not edit files, create commits, push branches, or mutate RepoLens app configuration. For MCP automation, prefer environment variables such as `REPOLENS_GITHUB_TOKEN`, `REPOLENS_BITBUCKET_USERNAME` (your Atlassian email), and `REPOLENS_BITBUCKET_API_TOKEN` over relying on the desktop app's currently selected repository.

### **General Setup**

Typical flow:

1. Configure provider credentials and selected repositories in the desktop app, or pass them as environment variables.
2. Register `mcp_server.py` with your MCP-compatible client.
3. Restart the client so it starts a fresh MCP server process.
4. Ask the AI to use the RepoLens MCP tools.

You do not need to reinstall/register the MCP server after code changes when the command path stays the same. You do need to restart the MCP client so it reloads the server process and tool definitions.

### **Connect your AI agent**

The fastest path is to run the included installer.

macOS:

```bash
pip install -r requirements.txt
python3 install_mcp.py
```

Windows:

```powershell
pip install -r requirements.txt
py install_mcp.py
```

By default, the installer attempts all supported clients, updates what it can automatically, and tells you to fall back to this README for anything it cannot configure.

The installer uses the exact Python interpreter that launched it, which makes the generated MCP configuration work correctly for both macOS and Windows, including virtual environments.

Useful examples:

```bash
python3 install_mcp.py --client codex --client cursor
python3 install_mcp.py --client cursor --cursor-scope project
python3 install_mcp.py --list-clients
```

Windows equivalents:

```powershell
py install_mcp.py --client codex --client cursor
py install_mcp.py --client cursor --cursor-scope project
py install_mcp.py --list-clients
```

If you prefer manual setup, or the installer cannot configure your client automatically, use the instructions below.

Before registering the server manually, install the Python dependencies and determine the absolute path to `mcp_server.py`.

From the RepoLens project root:

```bash
pip install -r requirements.txt
python3 -c "from pathlib import Path; print(Path('mcp_server.py').resolve())"
```

In the examples below, replace `<ABSOLUTE_PATH_TO_REPOLENS>/mcp_server.py` with that resolved path.

#### **Claude Code**

Claude Code's current MCP docs use `add-json` for local `stdio` servers:

```bash
claude mcp add-json repolens '{"type":"stdio","command":"python3","args":["<ABSOLUTE_PATH_TO_REPOLENS>/mcp_server.py"]}'
```

Then run `claude mcp list` or `/mcp` in Claude Code to confirm the server is available.

RepoLens refuses direct interactive terminal launches by default because a stdio MCP server waits on stdin and can otherwise be left running accidentally. Launch it through an MCP client, or pipe JSON-RPC input into it for low-level debugging.

#### **Claude Desktop**

Add RepoLens to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "repolens": {
      "command": "python3",
      "args": ["<ABSOLUTE_PATH_TO_REPOLENS>/mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop after saving the config.

#### **Codex CLI**

```bash
codex mcp add repolens -- python3 <ABSOLUTE_PATH_TO_REPOLENS>/mcp_server.py
```

Verify with:

```bash
codex mcp list
```

If you use the Codex IDE extension, it shares the same MCP configuration as the CLI.

#### **Cursor**

Cursor supports local MCP servers through `.cursor/mcp.json` in the project or `~/.cursor/mcp.json` globally:

```json
{
  "mcpServers": {
    "repolens": {
      "command": "python3",
      "args": ["<ABSOLUTE_PATH_TO_REPOLENS>/mcp_server.py"]
    }
  }
}
```

Restart Cursor after saving the file.

#### **Gemini CLI**

```bash
gemini mcp add repolens python3 <ABSOLUTE_PATH_TO_REPOLENS>/mcp_server.py
```

Verify with:

```bash
gemini mcp list
```

#### **ChatGPT**

ChatGPT cannot connect directly to a local `stdio` MCP server. As of May 29, 2026, OpenAI documents custom MCP apps as remote connectors only. If you want to use RepoLens with ChatGPT, you need to expose it through a supported remote transport such as OpenAI's Secure MCP Tunnel instead of pointing ChatGPT at `python3 mcp_server.py`.

#### **Other MCP clients**

Use a local `stdio` server configuration with:

- **Command**: `python3`
- **Arguments**: `<ABSOLUTE_PATH_TO_REPOLENS>/mcp_server.py`

### **Environment Variable Overrides**

The server reuses saved desktop-app config by default. You can override config with environment variables:

- `REPOLENS_PROVIDER`
- `REPOLENS_BITBUCKET_USERNAME`
- `REPOLENS_BITBUCKET_API_TOKEN` (legacy `REPOLENS_BITBUCKET_APP_PASSWORD` is accepted temporarily)
- `REPOLENS_BITBUCKET_WORKSPACE`
- `REPOLENS_GITHUB_OWNER`
- `REPOLENS_GITHUB_REPO`
- `REPOLENS_GITHUB_TOKEN`
- `REPOLENS_MANAGED_REPO_ROOT`
- `REPOLENS_SELECTED_REPOSITORIES_JSON`
- `REPOLENS_ACTIVE_REPO_PROVIDER`
- `REPOLENS_ACTIVE_REPO_ID`
- `REPOLENS_ACTIVE_REPO_NAME`
- `REPOLENS_ACTIVE_REPO_OWNER`
- `REPOLENS_ACTIVE_REPO_SLUG`
- `REPOLENS_ACTIVE_REPO_CLONE_URL`
- `REPOLENS_ACTIVE_REPO_HTML_URL`
- `REPOLENS_ACTIVE_REPO_LOCAL_DIR`

Example MCP client environment:

```bash
{
  "mcpServers": {
    "repolens": {
      "command": "python3",
      "args": ["<ABSOLUTE_PATH_TO_REPOLENS>/mcp_server.py"],
      "env": {
        "REPOLENS_PROVIDER": "bitbucket",
        "REPOLENS_BITBUCKET_USERNAME": "you@example.com",
        "REPOLENS_BITBUCKET_API_TOKEN": "your-api-token",
        "REPOLENS_BITBUCKET_WORKSPACE": "your-workspace"
      }
    }
  }
}
```

---

## **AI Test Prompts**

Use this prompt in a new AI client after registering the MCP server:

```text
Use the RepoLens MCP server to test basic functionality.

1. Call get_active_context and tell me the configured provider, active repository, selected repository count, and whether config came from app config or overrides.
2. Call get_selected_repositories and list the selected repository names.
3. Call list_pull_requests for the active repository with filter_mode="open" and summarize the count plus the first 3 PR ids/titles/states.
4. Call find_pull_requests_by_ticket with ticket="RU-25463" and summarize any matching PRs.
5. Do not call get_pr_diff yet unless I explicitly ask, because diffs can be large.
6. If any tool fails, report the exact tool name and error.
```

Developer PR prompt:

```text
Use the RepoLens MCP server to list my open pull requests across selected repositories.

Call list_my_pull_requests with:
scope="selected"
filter_mode="open"

Return the repo, PR id, title, source branch, target branch, and link for each PR.
```

Ticket lookup prompt:

```text
Use the RepoLens MCP server to find pull requests for ticket RU-25463. Search selected repositories. If there are multiple PRs, list each PR id, title, repo, state, source branch, destination branch, and link.
```

Specific repository prompt:

```text
Use the RepoLens MCP server to check open PRs in pdf-repo, even if it is not the active or selected repository.

Call list_pull_requests with:
scope="specific:pdf-repo"
filter_mode="open"

Return PR id, title, state, source branch, destination branch, author, and link.
```

For an explicit owner/workspace, use `scope="specific:WORKSPACE_OR_OWNER/pdf-capturing-service-repo"`.
RepoLens treats `specific:` as an override selector: it searches active, selected, and discovered repositories for a matching slug, name, or `owner/slug`, then uses that matched repository.

Multi-ticket QA impact prompt:

```text
Use the RepoLens MCP server to call get_ticket_diffs with tickets_json=["RU-25463", "RU-00000"] and scope="selected". Analyze the returned PR diffs and create this table:

| Ticket # | QA Impact | Notes/Comments |
|---|---|---|

Rules:
- Combine multiple PRs for the same ticket into one row.
- QA Impact should describe what QA should test, not just which files changed.
- Notes/Comments should include PR ids, repository names, and any uncertainty.
- If no PRs are found for a ticket, say that directly.
- If any diff is truncated or failed, mention that in Notes/Comments.
```

Addressing PR comments with AI prompt:

```text
Use the RepoLens MCP server to inspect and address unresolved comments on PR #42 (or pass reference="<PR_URL_OR_TICKET>").

Call get_pr_comments with:
reference="<PR_URL_OR_NUMBER>"
unresolved_only=true
include_code_context=true

For each unresolved review comment:
1. Note the exact file path and line number.
2. Review the reviewer's feedback and the surrounding code context.
3. Open the file, make the requested fixes or improvements, and verify the changes.
4. Provide a clear summary of how each comment was addressed.
```

---

## **Notes**

- MCP is the protocol/integration point; RepoLens is not tied to one AI client.
- Developer filters use provider-side author search where supported. If the provider API cannot filter by author efficiently, RepoLens narrows by provider-supported filters first and then applies local author matching.
- Managed repositories default to `~/.repolens/repos`.
- Existing settings and repository config from older installs are migrated into the RepoLens namespace on startup.
- Tested and built on macOS.
