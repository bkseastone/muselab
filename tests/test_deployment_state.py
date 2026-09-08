"""State must survive a fresh interpreter/container, including bootstrap env."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def run_config(tmp_path, code, *, override=False, explicit_env=False):
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(REPO),
        "MUSELAB_ROOT": str(root),
        "MUSELAB_TOKEN": "deployment-config-test-token-1234567890",
        "MUSELAB_SESSIONS_DIR": str(tmp_path / "sessions"),
        "MUSELAB_MEMORY_DIR": str(tmp_path / "memory"),
        "MUSELAB_CONFIG_DIR": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "MUSELAB_BUSY_SEND_MODE": "adjust",
        "MUSELAB_ENV_OVERRIDE": "1" if override else "0",
    })
    env.pop("MUSELAB_ENV_PATH", None)
    if explicit_env:
        env["MUSELAB_ENV_PATH"] = str(tmp_path / "external" / "runtime.env")
    result = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_ui_config_and_vendor_transcript_survive_new_process(tmp_path):
    wrote = run_config(tmp_path, '''
import json
from backend import settings, api_settings, endpoints
assert settings.MCP_CONFIG_PATH == api_settings.MCP_CONFIG_PATH
api_settings._save_mcp({"mcpServers": {"test-disabled": {"command": "true", "disabled": True}}})
api_settings._write_env({"MUSELAB_BUSY_SEND_MODE": "queue"})
endpoints._save_overrides({"providers": {}, "deleted": [], "anthropic_models": ["claude-config-fixture"]})
transcript = endpoints._VENDOR_CONFIG_DIR / "projects" / "fixture" / "test.jsonl"
transcript.parent.mkdir(parents=True)
transcript.write_text('{"synthetic":"durable"}\\n', encoding="utf-8")
print(json.dumps({"wrote": True}))
''', override=True)
    assert wrote == {"wrote": True}
    read = run_config(tmp_path, '''
import json, os
from backend import settings, api_settings, endpoints
transcript = endpoints._VENDOR_CONFIG_DIR / "projects" / "fixture" / "test.jsonl"
print(json.dumps({
 "mode": os.environ["MUSELAB_BUSY_SEND_MODE"],
 "mcp": api_settings._load_mcp()["mcpServers"]["test-disabled"]["disabled"],
 "models": endpoints._load_overrides()["anthropic_models"],
 "transcript": json.loads(transcript.read_text(encoding="utf-8"))["synthetic"],
 "same_mcp_path": settings.MCP_CONFIG_PATH == api_settings.MCP_CONFIG_PATH,
}))
''', override=True)
    assert read == {"mode": "queue", "mcp": True, "models": ["claude-config-fixture"],
                    "transcript": "durable", "same_mcp_path": True}
    assert (tmp_path / "config" / ".env").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("override, expected", [(False, "adjust"), (True, "queue")])
def test_explicit_env_path_is_read_and_keeps_default_precedence(tmp_path, override, expected):
    folder = tmp_path / "external"
    folder.mkdir()
    (folder / "runtime.env").write_text("MUSELAB_BUSY_SEND_MODE=queue\n", encoding="utf-8")
    read = run_config(tmp_path, '''
import json, os
from backend import settings, api_settings
print(json.dumps({"mode": os.environ["MUSELAB_BUSY_SEND_MODE"], "same": settings.ENV_PATH == api_settings.ENV_PATH}))
''', override=override, explicit_env=True)
    assert read == {"mode": expected, "same": True}


def test_docker_smoke_payloads_use_real_settings_routes(client, auth, monkeypatch, tmp_path):
    """The CI container probe must use valid APIs, without calling any provider."""
    import io
    import runpy
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit
    from backend import api_settings, settings

    mcp = tmp_path / "smoke-config" / "mcp.json"
    monkeypatch.setattr(api_settings, "MCP_CONFIG_PATH", mcp)
    monkeypatch.setattr(settings, "MCP_CONFIG_PATH", mcp)
    monkeypatch.setenv("MUSELAB_TOKEN", auth["X-Auth-Token"])

    def local_transport(req, timeout):
        response = client.request(req.get_method(), urlsplit(req.full_url).path,
                                  content=req.data, headers=dict(req.header_items()))
        if response.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, response.status_code, "fixture response", {}, None)
        return io.BytesIO(response.content)

    monkeypatch.setattr(urllib.request, "urlopen", local_transport)
    for mode in ("seed", "verify"):
        monkeypatch.setattr(sys, "argv", ["docker-state-smoke.py", mode])
        runpy.run_path(str(REPO / "scripts/docker-state-smoke.py"), run_name="__main__")
