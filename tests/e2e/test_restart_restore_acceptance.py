"""Real process restart and isolated backup restore preserve tabs and settings."""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect

from tests.e2e.conftest import _free_port
from tests.e2e.test_settings_drafts import _login


def test_tabs_and_context_settings_survive_process_restart_and_backup_restore(page, tmp_path):
    state = tmp_path / "state"
    root = state / "workspace"
    root.mkdir(parents=True)
    (root / "README.md").write_text("# Synthetic restore acceptance")
    port = _free_port()
    token = "synthetic-restart-acceptance-token"
    env = {key: value for key, value in os.environ.items()
           if not key.endswith(("_API_KEY", "_TOKEN")) and not key.startswith("MUSELAB_")}
    env.update({
        "HOME": str(state / "home"),
        "MUSELAB_TOKEN": token, "MUSELAB_HOST": "127.0.0.1", "MUSELAB_PORT": str(port),
        "MUSELAB_ROOT": str(root), "MUSELAB_SESSIONS_DIR": str(state / "sessions"),
        "MUSELAB_CONFIG_DIR": str(state / "config"), "MUSELAB_ENV_OVERRIDE": "1",
        "MUSELAB_MEMORY_DIR": str(state / "memory"), "XDG_STATE_HOME": str(state / "xdg"),
        "MUSELAB_DEFAULT_MODEL": "deepseek-v4-pro", "MUSELAB_MODEL": "deepseek-v4-pro",
        "DEEPSEEK_API_KEY": "synthetic-no-network-key",
    })
    Path(env["HOME"]).mkdir()
    base = f"http://127.0.0.1:{port}"
    repo = Path(__file__).resolve().parents[2]
    process = None
    log = (tmp_path / "server.log").open("wb")

    def start():
        nonlocal process
        process = subprocess.Popen([sys.executable, "-m", "backend.main"],
                                   cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            assert process.poll() is None, "isolated backend exited"
            try:
                urllib.request.urlopen(base + "/static/app.js", timeout=.5).close()
                return
            except Exception:
                time.sleep(.1)
        raise AssertionError("isolated backend startup timed out")

    def stop():
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def tab_state():
        return page.evaluate("""() => {
          const a=document.querySelector('#app')._x_dataStack[0];
          return {ids:a.openTabIds.slice(), current:a.currentId};
        }""")

    try:
        start()
        _login(page, base, token)
        initial = page.locator(".chat-tab").count()
        page.locator(".chat-tab-new").click()
        expect(page.locator(".chat-tab")).to_have_count(initial + 1)
        page.locator(".chat-tab-new").click()
        expect(page.locator(".chat-tab")).to_have_count(initial + 2)
        page.locator(".chat-tab .chat-tab-name").first.click()
        expected = tab_state()
        response = page.request.put(base + "/api/settings/context-limits",
            headers={"X-Auth-Token": token},
            data={"scope": "providers", "key": "ducc", "tokens": 350000})
        assert response.status == 200
        page.reload()
        _login(page, base, token)
        assert tab_state() == expected
        stop()
        start()
        _login(page, base, token)
        assert tab_state() == expected
        stop()
        # Back up only this test's stopped, synthetic state; restore to the
        # original paths so absolute workspace identities remain meaningful.
        backup = tmp_path / "backup"
        shutil.copytree(state, backup)
        state.rename(tmp_path / "retired-state")
        shutil.copytree(backup, state)
        start()
        _login(page, base, token)
        assert tab_state() == expected
        response = page.request.get(base + "/api/settings", headers={"X-Auth-Token": token})
        assert response.json()["context_limits"]["providers"]["ducc"] == 350000
    finally:
        stop()
        log.close()
