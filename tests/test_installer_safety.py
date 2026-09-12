"""Exercise installer decisions and generated files with harmless OS doubles."""
import importlib.util
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def renderer():
    spec = importlib.util.spec_from_file_location("render_service", REPO / "scripts/render-service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plists_preserve_special_path_characters(tmp_path):
    values = {"REPO_PATH": '/tmp/R&D/中文 "quoted"/a|b\\c', "UV_PATH": '/tmp/u&v/bin/uv',
              "HOME_DIR": '/tmp/h<o>me', "PATH_DIRS": '/tmp/R&D/bin:/usr/bin',
              "STATUS_BAR_BINARY": '/tmp/Muse & Helper', "ENV_PATH": '/tmp/R&D/.env'}
    module = renderer()
    backend = plistlib.loads(module.render(REPO / "scripts/templates/com.muselab.plist.tmpl", values, plist=True))
    helper = plistlib.loads(module.render(REPO / "scripts/templates/com.muselab.statusbar.plist.tmpl", values, plist=True))
    assert backend["WorkingDirectory"] == values["REPO_PATH"]
    assert backend["ProgramArguments"][0] == values["UV_PATH"]
    assert backend["EnvironmentVariables"]["HOME"] == values["HOME_DIR"]
    assert helper["ProgramArguments"] == [values["STATUS_BAR_BINARY"], "--env", values["ENV_PATH"]]


def test_systemd_accepts_quoted_paths(tmp_path):
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd-analyze unavailable on this platform")
    folder = tmp_path / 'R&D 中文 "q" $cash %spec'
    folder.mkdir()
    executable = folder / 'u$v%test'
    shutil.copyfile('/bin/true', executable)
    executable.chmod(0o700)
    env_file = folder / '.env'
    env_file.write_text('TEST=synthetic\n', encoding='utf-8')
    unit = tmp_path / 'muselab-render-fixture.service'
    values = {'REPO_PATH': str(folder), 'UV_PATH': str(executable), 'ENV_PATH': str(env_file)}
    unit.write_bytes(renderer().render(REPO / 'scripts/templates/muselab.service.tmpl', values, plist=False))
    result = subprocess.run(['systemd-analyze', 'verify', str(unit)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('related,same_checkout,extra_holder,allowed', [
    (True, True, False, True), (False, True, False, False),
    (True, False, False, False), (True, True, True, False),
])
@pytest.mark.parametrize('platform', ['linux', 'macos'])
def test_only_service_descendants_in_same_checkout_are_owned(tmp_path, related, same_checkout, extra_holder, allowed, platform):
    script = r'''
source "$SAFETY_SCRIPT"
ps() { if [[ "$2" == 321 ]]; then echo "$CHILD_PARENT"; else echo 1; fi; }
systemctl() { case "$*" in *MainPID*) echo 320;; *WorkingDirectory*) echo "$SERVICE_CWD";; esac; }
launchctl() { echo '320 0 com.muselab'; }
lsof() { printf 'p320\nn%s\n' "$SERVICE_CWD"; }
muselab_port_is_owned "$PLATFORM" /test/muselab "$HOLDERS"
'''
    env = {**os.environ, 'SAFETY_SCRIPT': str(REPO / 'scripts/installer-safety.sh'),
           'CHILD_PARENT': '320' if related else '999', 'SERVICE_CWD': '/test/muselab' if same_checkout else '/other/muselab',
           'HOLDERS': '321 444' if extra_holder else '321', 'PLATFORM': platform}
    result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == (0 if allowed else 1), result.stderr


def test_macos_port_branch_does_not_signal_unrelated_listener(tmp_path):
    text = (REPO / 'scripts/install-macos.sh').read_text(encoding='utf-8')
    start = text.index('  if lsof -nP -iTCP:')
    end = text.index('  ok "port $PORT available', start)
    branch = text[start:end]
    marker = tmp_path / 'actions'
    script = r'''
set -eu
source "$SAFETY_SCRIPT"
REPO=/test/muselab
PORT=8765
ask() { echo Y; }
warn() { :; }
err() { :; }
ok() { :; }
sleep() { :; }
ps() { case "$*" in *ppid*) echo 999;; *) echo unrelated-editor;; esac; }
launchctl() { if [[ "$1" == list ]]; then echo '320 0 com.muselab'; else echo changed >> "$ACTION_FILE"; fi; }
lsof() { case "$*" in *' -d cwd '*) echo n/test/muselab;; *-tiTCP*) echo 321;; *) echo listener;; esac; }
kill() { echo killed >> "$ACTION_FILE"; }
''' + branch
    env = {**os.environ, 'SAFETY_SCRIPT': str(REPO / 'scripts/installer-safety.sh'), 'ACTION_FILE': str(marker)}
    result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert not marker.exists()


def test_noninteractive_handoff_works_without_controlling_tty(tmp_path):
    folder = tmp_path / 'scripts'
    folder.mkdir()
    (folder / 'install-linux.sh').write_text('#!/bin/sh\nprintf handed-off\n', encoding='utf-8')
    text = (REPO / 'scripts/quick-install.sh').read_text(encoding='utf-8')
    handoff = text[text.index('cd "$DEST"'):]
    result = subprocess.run(['bash', '-c', 'set -eu\nNONINT=1\nOS=linux\n' + handoff],
                            env={**os.environ, 'DEST': str(tmp_path)}, start_new_session=True,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'handed-off'
