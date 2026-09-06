"""Exercise upgrade failure/promotion against disposable on-disk environments."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def upgrade_fixture(tmp_path, *, symlink=False):
    project = tmp_path / 'project with spaces'
    (project / 'scripts').mkdir(parents=True)
    shutil.copy2(ROOT / 'scripts/upgrade.sh', project / 'scripts/upgrade.sh')
    for name in ('pyproject.toml', 'uv.lock', '.env'):
        (project / name).write_text(f'original {name}\n', encoding='utf-8')
    previous = tmp_path / 'previous env' if symlink else project / '.venv'
    previous.mkdir()
    (previous / 'marker').write_text('original environment', encoding='utf-8')
    if symlink:
        (project / '.venv').symlink_to(previous)
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    uv = bindir / 'uv'
    uv.write_text(f'''#!{sys.executable}
import os, pathlib, sys
if os.environ.get('AUDIT_FAIL_INSTALL') == '1':
    sys.exit(3)
p = pathlib.Path(os.environ['UV_PROJECT_ENVIRONMENT']) / 'bin'
p.mkdir(parents=True, exist_ok=True)
wrapper = p / 'python'
wrapper.write_text({('#!' + sys.executable + chr(10) + 'import os,sys\nif sys.argv[1:3] == ["-m", "pytest"]:\n    print("candidate fixture tests")\n    sys.exit(int(os.environ.get("AUDIT_FAIL_TEST", "0")))\nos.execv(' + repr(sys.executable) + ', [' + repr(sys.executable) + '] + sys.argv[1:])\n')!r}, encoding='utf-8')
wrapper.chmod(0o700)
''', encoding='utf-8')
    uv.chmod(0o700)
    return project, previous, {**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ['PATH']}


@pytest.mark.parametrize('failure', ['AUDIT_FAIL_INSTALL', 'AUDIT_FAIL_TEST'])
def test_failure_preserves_active_environment_and_user_files(tmp_path, failure):
    project, previous, env = upgrade_fixture(tmp_path)
    result = subprocess.run(['bash', 'scripts/upgrade.sh'], cwd=project,
                            env={**env, failure: '1'}, capture_output=True, text=True)
    assert result.returncode != 0
    assert not (project / '.venv').is_symlink()
    assert (previous / 'marker').read_text(encoding='utf-8') == 'original environment'
    assert not (project / '.venv-builds/.upgrade-lock').exists()
    for name in ('pyproject.toml', 'uv.lock', '.env'):
        assert (project / name).read_text(encoding='utf-8') == f'original {name}\n'


@pytest.mark.parametrize('symlink', [False, True])
def test_success_promotes_candidate_and_retains_previous(tmp_path, symlink):
    project, previous, env = upgrade_fixture(tmp_path, symlink=symlink)
    result = subprocess.run(['bash', 'scripts/upgrade.sh'], cwd=project,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    current = project / '.venv'
    assert current.is_symlink() and (current / 'bin/python').is_file()
    backups = list((project / '.venv-builds').glob('previous.*'))
    assert len(backups) == 1
    assert (backups[0] / 'marker').read_text(encoding='utf-8') == 'original environment'
    if symlink:
        assert previous.exists() and backups[0].resolve() == previous
    for name in ('pyproject.toml', 'uv.lock', '.env'):
        assert (project / name).read_text(encoding='utf-8') == f'original {name}\n'


def test_check_only_and_concurrent_upgrade_never_promote(tmp_path):
    project, previous, env = upgrade_fixture(tmp_path)
    result = subprocess.run(['bash', 'scripts/upgrade.sh', '--check'], cwd=project,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert previous.is_dir() and not previous.is_symlink()
    lock = project / '.venv-builds/.upgrade-lock'
    lock.mkdir()
    result = subprocess.run(['bash', 'scripts/upgrade.sh'], cwd=project,
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0 and lock.is_dir()
    assert previous.is_dir() and not previous.is_symlink()
