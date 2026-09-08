from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


SERVER_NAME = "repolens"
SUPPORTED_CLIENTS = (
    "codex",
    "gemini",
    "claude-code",
    "cursor",
    "claude-desktop",
    "chatgpt",
)


@dataclass
class InstallResult:
    client: str
    success: bool
    message: str
    details: str = ""


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def server_script() -> Path:
    direct = repo_root() / "mcp_server.py"
    if direct.exists():
        return direct
    try:
        import mcp_server
        if mcp_server.__file__:
            return Path(mcp_server.__file__).resolve()
    except ImportError:
        pass
    return direct


def readme_path() -> Path:
    return repo_root() / "README.md"


def default_python_command() -> str:
    if sys.executable:
        return sys.executable
    return shutil.which("python3") or shutil.which("python") or "python3"


def build_stdio_entry(python_cmd: str, script_path: Path) -> dict[str, Any]:
    return {
        "command": python_cmd,
        "args": [str(script_path)],
    }


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} contains invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object at the top level.")
    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(rendered)
        temp_name = handle.name
    os.replace(temp_name, path)


def update_mcp_servers_config(path: Path, name: str, entry: dict[str, Any]) -> tuple[bool, Path]:
    data = load_json_file(path)
    existing = data.get("mcpServers")
    if existing is None:
        existing = {}
        data["mcpServers"] = existing
    if not isinstance(existing, dict):
        raise ValueError(f"{path} has a non-object 'mcpServers' field.")
    if existing.get(name) == entry:
        return False, path
    existing[name] = entry
    atomic_write_json(path, data)
    return True, path


def cursor_config_path(scope: str) -> Path:
    if scope == "project":
        return repo_root() / ".cursor" / "mcp.json"
    return Path.home() / ".cursor" / "mcp.json"


def claude_desktop_config_path() -> Path:
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise ValueError("APPDATA is not set; cannot determine Claude Desktop config path.")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def format_command(args: list[str]) -> str:
    return " ".join(args)


def cli_not_found_result(client: str, executable: str) -> InstallResult:
    return InstallResult(
        client=client,
        success=False,
        message=f"{executable} is not installed or not on PATH.",
        details=f"Install {client} first, then rerun this installer. Otherwise follow {readme_path()} for manual setup.",
    )


def install_codex(python_cmd: str, script_path: Path) -> InstallResult:
    executable = shutil.which("codex")
    if not executable:
        return cli_not_found_result("Codex CLI", "codex")
    command = ["codex", "mcp", "add", SERVER_NAME, "--", python_cmd, str(script_path)]
    completed = run_command(command)
    if completed.returncode == 0:
        return InstallResult("codex", True, "Configured Codex CLI MCP server.", completed.stdout.strip())
    stderr = (completed.stderr or completed.stdout).strip()
    return InstallResult(
        "codex",
        False,
        "Failed to configure Codex CLI automatically.",
        f"Command: {format_command(command)}\n{stderr}\n\nFollow {readme_path()} for manual setup.",
    )


def install_gemini(python_cmd: str, script_path: Path) -> InstallResult:
    executable = shutil.which("gemini")
    if not executable:
        return cli_not_found_result("Gemini CLI", "gemini")
    command = ["gemini", "mcp", "add", SERVER_NAME, python_cmd, str(script_path)]
    completed = run_command(command)
    if completed.returncode == 0:
        return InstallResult("gemini", True, "Configured Gemini CLI MCP server.", completed.stdout.strip())
    stderr = (completed.stderr or completed.stdout).strip()
    return InstallResult(
        "gemini",
        False,
        "Failed to configure Gemini CLI automatically.",
        f"Command: {format_command(command)}\n{stderr}\n\nFollow {readme_path()} for manual setup.",
    )


def install_claude_code(python_cmd: str, script_path: Path) -> InstallResult:
    executable = shutil.which("claude")
    if not executable:
        return cli_not_found_result("Claude Code", "claude")
    payload = json.dumps(
        {
            "type": "stdio",
            "command": python_cmd,
            "args": [str(script_path)],
        },
        separators=(",", ":"),
    )
    command = ["claude", "mcp", "add-json", SERVER_NAME, payload]
    completed = run_command(command)
    if completed.returncode == 0:
        return InstallResult("claude-code", True, "Configured Claude Code MCP server.", completed.stdout.strip())
    stderr = (completed.stderr or completed.stdout).strip()
    return InstallResult(
        "claude-code",
        False,
        "Failed to configure Claude Code automatically.",
        f"Command: {format_command(command)}\n{stderr}\n\nFollow {readme_path()} for manual setup.",
    )


def install_cursor(python_cmd: str, script_path: Path, scope: str) -> InstallResult:
    config_path = cursor_config_path(scope)
    try:
        changed, written_path = update_mcp_servers_config(
            config_path,
            SERVER_NAME,
            build_stdio_entry(python_cmd, script_path),
        )
    except Exception as exc:  # noqa: BLE001 - installer should summarize any config failure
        return InstallResult(
            "cursor",
            False,
            "Failed to update Cursor MCP config.",
            f"{exc}\n\nFollow {readme_path()} for manual setup.",
        )
    if changed:
        return InstallResult(
            "cursor",
            True,
            f"Updated Cursor MCP config at {written_path}.",
            "Restart Cursor to load the new server.",
        )
    return InstallResult(
        "cursor",
        True,
        f"Cursor MCP config already contains '{SERVER_NAME}' at {written_path}.",
        "Restart Cursor if it is already open.",
    )


def install_claude_desktop(python_cmd: str, script_path: Path) -> InstallResult:
    try:
        config_path = claude_desktop_config_path()
        changed, written_path = update_mcp_servers_config(
            config_path,
            SERVER_NAME,
            build_stdio_entry(python_cmd, script_path),
        )
    except Exception as exc:  # noqa: BLE001
        return InstallResult(
            "claude-desktop",
            False,
            "Failed to update Claude Desktop config.",
            f"{exc}\n\nFollow {readme_path()} for manual setup.",
        )
    if changed:
        return InstallResult(
            "claude-desktop",
            True,
            f"Updated Claude Desktop config at {written_path}.",
            "Restart Claude Desktop to load the new server.",
        )
    return InstallResult(
        "claude-desktop",
        True,
        f"Claude Desktop config already contains '{SERVER_NAME}' at {written_path}.",
        "Restart Claude Desktop if it is already open.",
    )


def install_chatgpt() -> InstallResult:
    return InstallResult(
        "chatgpt",
        False,
        "ChatGPT cannot connect directly to a local stdio MCP server.",
        f"Use the README guidance in {readme_path()} and OpenAI documentation for a remote MCP path such as Secure MCP Tunnel.",
    )


def select_clients(raw_clients: list[str]) -> list[str]:
    if not raw_clients or raw_clients == ["all"]:
        return list(SUPPORTED_CLIENTS)
    chosen: list[str] = []
    for client in raw_clients:
        if client == "all":
            return list(SUPPORTED_CLIENTS)
        if client not in SUPPORTED_CLIENTS:
            raise ValueError(f"Unsupported client '{client}'. Choose from: {', '.join(SUPPORTED_CLIENTS)}")
        if client not in chosen:
            chosen.append(client)
    return chosen


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install RepoLens as a local MCP server for supported AI clients."
    )
    parser.add_argument(
        "--client",
        action="append",
        dest="clients",
        default=[],
        help=f"Client to configure. Repeat as needed. Supported: {', '.join(SUPPORTED_CLIENTS)}, all",
    )
    parser.add_argument(
        "--python",
        dest="python_cmd",
        default=default_python_command(),
        help="Python command to use when launching mcp_server.py. Defaults to python3 or the current interpreter.",
    )
    parser.add_argument(
        "--cursor-scope",
        choices=("user", "project"),
        default="user",
        help="Where to write Cursor config when --client cursor is selected.",
    )
    parser.add_argument(
        "--list-clients",
        action="store_true",
        help="List supported client ids and exit.",
    )
    return parser


def validate_inputs(python_cmd: str, script_path: Path) -> None:
    if not script_path.exists():
        raise FileNotFoundError(f"RepoLens MCP server script not found: {script_path}")
    if not python_cmd.strip():
        raise ValueError("Python command cannot be empty.")


def install_for_client(client: str, python_cmd: str, script_path: Path, cursor_scope: str) -> InstallResult:
    if client == "codex":
        return install_codex(python_cmd, script_path)
    if client == "gemini":
        return install_gemini(python_cmd, script_path)
    if client == "claude-code":
        return install_claude_code(python_cmd, script_path)
    if client == "cursor":
        return install_cursor(python_cmd, script_path, cursor_scope)
    if client == "claude-desktop":
        return install_claude_desktop(python_cmd, script_path)
    if client == "chatgpt":
        return install_chatgpt()
    raise ValueError(f"Unsupported client '{client}'.")


def print_results(results: list[InstallResult]) -> None:
    print(f"RepoLens MCP installer")
    print(f"Server script: {server_script()}")
    print(f"README fallback: {readme_path()}")
    print("")
    for result in results:
        status = "OK" if result.success else "FAIL"
        print(f"[{status}] {result.client}: {result.message}")
        if result.details:
            print(result.details)
            print("")


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.list_clients:
        print("\n".join(SUPPORTED_CLIENTS))
        return 0

    try:
        clients = select_clients(args.clients)
        script_path = server_script()
        validate_inputs(args.python_cmd, script_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Installer setup failed: {exc}", file=sys.stderr)
        print(f"See {readme_path()} for manual setup instructions.", file=sys.stderr)
        return 2

    results = [install_for_client(client, args.python_cmd, script_path, args.cursor_scope) for client in clients]
    print_results(results)

    successes = sum(1 for result in results if result.success)
    if successes == len(results):
        return 0
    if successes > 0:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
