import json
from pathlib import Path
from unittest.mock import patch

import pytest

import install_mcp


def test_update_mcp_servers_config_creates_new_file(tmp_path: Path):
    config_path = tmp_path / "mcp.json"

    changed, written_path = install_mcp.update_mcp_servers_config(
        config_path,
        "repolens",
        {"command": "python3", "args": ["/tmp/mcp_server.py"]},
    )

    assert changed is True
    assert written_path == config_path
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["repolens"]["command"] == "python3"


def test_update_mcp_servers_config_is_idempotent(tmp_path: Path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "repolens": {"command": "python3", "args": ["/tmp/mcp_server.py"]},
                }
            }
        ),
        encoding="utf-8",
    )

    changed, _written_path = install_mcp.update_mcp_servers_config(
        config_path,
        "repolens",
        {"command": "python3", "args": ["/tmp/mcp_server.py"]},
    )

    assert changed is False


def test_update_mcp_servers_config_preserves_existing_servers(tmp_path: Path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "existing": {"command": "node", "args": ["server.js"]},
                }
            }
        ),
        encoding="utf-8",
    )

    install_mcp.update_mcp_servers_config(
        config_path,
        "repolens",
        {"command": "python3", "args": ["/tmp/mcp_server.py"]},
    )

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert set(data["mcpServers"]) == {"existing", "repolens"}


def test_select_clients_defaults_to_all():
    assert install_mcp.select_clients([]) == list(install_mcp.SUPPORTED_CLIENTS)


def test_select_clients_rejects_unknown_client():
    with pytest.raises(ValueError):
        install_mcp.select_clients(["unknown"])


def test_install_chatgpt_returns_manual_fallback():
    result = install_mcp.install_chatgpt()

    assert result.success is False
    assert "cannot connect directly to a local stdio MCP server" in result.message
    assert "README" in result.details


def test_cursor_config_path_project_scope_uses_repo_root(tmp_path: Path):
    with patch.object(install_mcp, "repo_root", return_value=tmp_path):
        assert install_mcp.cursor_config_path("project") == tmp_path / ".cursor" / "mcp.json"


def test_claude_desktop_config_path_darwin():
    with patch("install_mcp.platform.system", return_value="Darwin"):
        path = install_mcp.claude_desktop_config_path()
    assert str(path).endswith("Library/Application Support/Claude/claude_desktop_config.json")


def test_default_python_command_prefers_current_interpreter():
    with patch("install_mcp.sys.executable", "/tmp/venv/bin/python"), patch("install_mcp.shutil.which", return_value="/usr/bin/python3"):
        assert install_mcp.default_python_command() == "/tmp/venv/bin/python"


def test_default_python_command_falls_back_to_python_binaries():
    with patch("install_mcp.sys.executable", ""), patch("install_mcp.shutil.which", side_effect=[None, "C:/Python311/python.exe"]):
        assert install_mcp.default_python_command() == "C:/Python311/python.exe"


def test_validate_inputs_missing_script(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="RepoLens MCP server script not found"):
        install_mcp.validate_inputs("python3", tmp_path / "nonexistent.py")


def test_validate_inputs_empty_python(tmp_path: Path):
    script = tmp_path / "server.py"
    script.write_text("# dummy", encoding="utf-8")
    with pytest.raises(ValueError, match="Python command cannot be empty"):
        install_mcp.validate_inputs("   ", script)


def test_validate_inputs_health_check_failure(tmp_path: Path):
    script = tmp_path / "server.py"
    script.write_text("import sys; sys.exit(1)", encoding="utf-8")
    with pytest.raises(RuntimeError, match="failed to load"):
        install_mcp.validate_inputs(install_mcp.default_python_command(), script, check_health=True)


def test_validate_inputs_skip_health_check(tmp_path: Path):
    script = tmp_path / "server.py"
    script.write_text("import sys; sys.exit(1)", encoding="utf-8")
    # Should not raise when check_health is False
    install_mcp.validate_inputs("python3", script, check_health=False)
