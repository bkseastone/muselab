"""Run inside the disposable docker-smoke container, using synthetic state only."""
import hashlib
import json
import os
import sys
from pathlib import Path
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def request(path, *, method="GET", payload=None, auth=True):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["X-Auth-Token"] = os.environ["MUSELAB_TOKEN"]
    data = None if payload is None else json.dumps(payload).encode()
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, method=method, headers=headers), timeout=10) as response:
        return json.load(response)


# Docker executes this script through stdin; local regression tests use runpy.
app_root = Path.cwd() if __file__ == "<stdin>" else Path(__file__).resolve().parents[1]
for notice in ("LICENSE", "THIRD_PARTY_LICENSES.md"):
    assert (app_root / notice).is_file(), f"Missing packaged notice: {notice}"
vendor = app_root / "frontend" / "vendor"
manifest = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
packages = (manifest["direct_packages"] + manifest["bundled_packages"]
            + manifest.get("embedded_packages", []) + manifest.get("source_fragments", []))
for notice in {name for package in packages for name in package["license_files"]}:
    assert hashlib.sha256((vendor / notice).read_bytes()).hexdigest() == manifest["assets"][notice], notice


if sys.argv[1] == "seed":
    try:
        request("/api/settings", auth=False)
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Settings must require authentication")
    request("/api/settings", method="PUT", payload={"busy_send_mode": "queue", "deepseek_api_key": "docker-smoke-synthetic-api-key"})
    request("/api/settings/providers", method="POST", payload={
        "base_url": "http://127.0.0.1:9/anthropic", "prefix": "smoke-fixture-",
        "display": "Synthetic smoke provider", "models": ["smoke-fixture-model"],
    })
    request("/api/settings/mcp/smoke-disabled", method="PUT", payload={
        "name": "smoke-disabled", "command": "true", "args": [], "disabled": True,
    })
    from backend.endpoints import _VENDOR_CONFIG_DIR
    transcript = _VENDOR_CONFIG_DIR / "projects" / "smoke" / "test.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text('{"synthetic":"durable"}\n', encoding="utf-8")
else:
    assert sys.argv[1] == "verify"
    assert request("/api/settings")["defaults"]["busy_send_mode"] == "queue"
    from backend import settings, api_settings, endpoints
    assert os.environ["DEEPSEEK_API_KEY"] == "docker-smoke-synthetic-api-key"
    assert settings.MCP_CONFIG_PATH == api_settings.MCP_CONFIG_PATH
    assert api_settings._load_mcp()["mcpServers"]["smoke-disabled"]["disabled"]
    assert any(p.prefix == "smoke-fixture-" for p in endpoints.catalog())
    transcript = endpoints._VENDOR_CONFIG_DIR / "projects" / "smoke" / "test.jsonl"
    assert json.loads(transcript.read_text(encoding="utf-8"))["synthetic"] == "durable"
print("Synthetic container state contract passed: " + sys.argv[1])
