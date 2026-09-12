"""No-Docker tests for the explicit, non-destructive legacy state migration CLI."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate-docker-state.py"


@pytest.fixture
def migration_cli(tmp_path):
    if sys.platform != "linux":
        pytest.skip("migration publication requires Linux renameat2")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "original-session.json").write_text("synthetic old session")
    config = tmp_path / "fake.json"
    log = tmp_path / "calls.jsonl"
    fake = tmp_path / "docker"
    fake.write_text(f"#!{sys.executable}\n" + '''
import base64, io, json, os, pathlib, sys, tarfile
cfg=json.loads(pathlib.Path(os.environ['FAKE_DOCKER_CONFIG']).read_text())
log=pathlib.Path(os.environ['FAKE_DOCKER_LOG'])
prior=log.read_text().splitlines() if log.exists() else []
with log.open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1]=='inspect':
    if cfg.get('inspect_failure'):
        print('Cannot connect to Docker daemon',file=sys.stderr);sys.exit(1)
    running=cfg.get('running') or (cfg.get('restart_during_copy') and len(prior)>0)
    print(json.dumps([{'Id':'a'*64,'State':{'Running':bool(running),'Status':'running' if running else 'exited','Pid':42 if running else 0}}]))
    sys.exit(0)
if sys.argv[1]!='cp':raise SystemExit('unexpected Docker lifecycle command')
source=sys.argv[2].split(':',1)[1]
if cfg.get('failure')==source:
    print('Cannot connect to Docker daemon',file=sys.stderr);sys.exit(1)
if source in cfg.get('missing',[]):
    print('Error response from daemon: Could not find the file '+source+' in container '+'a'*64,file=sys.stderr);sys.exit(1)
base=source.rsplit('/',1)[-1]
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as tar:
    entries=cfg['files'][source]
    if base=='vendor-cli':
        info=tarfile.TarInfo(base);info.type=tarfile.DIRTYPE;tar.addfile(info)
    for name,encoded in entries.items():
        raw=base64.b64decode(encoded)
        info=tarfile.TarInfo(base+('/'+name if name else ''));info.size=len(raw)
        if cfg.get('unsafe')==source:info.name='../escape'
        tar.addfile(info,io.BytesIO(raw))
''')
    fake.chmod(0o700)
    files = {
        "/app/.env": {"": b"SYNTHETIC_SETTING=retained\n"},
        "/app/mcp.json": {"": b'{"servers":{}}'},
        "/app/provider_overrides.json": {"": b'{"synthetic":true}'},
        "/home/muse/.local/state/muselab/vendor-cli": {
            "projects/synthetic/session.jsonl": b'{"synthetic":"history"}\n',
            "binary": b"\x00\xff\x01",
        },
    }
    base_config = {"files": {source: {name: base64.b64encode(raw).decode()
                                    for name, raw in entries.items()}
                             for source, entries in files.items()}}

    def run(**patch):
        config.write_text(json.dumps({**base_config, **patch}))
        return subprocess.run([sys.executable, str(SCRIPT), '--container', 'synthetic-old',
                               '--sessions-dir', str(sessions), '--docker', str(fake)],
                              capture_output=True, text=True,
                              env={**os.environ, 'FAKE_DOCKER_CONFIG':str(config), 'FAKE_DOCKER_LOG':str(log)})
    return run, sessions, log, files


def test_migration_copies_all_sources_privately_and_retains_container(migration_cli):
    run, sessions, log, files = migration_cli
    result = run()
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['phase'] == 'complete'
    assert (sessions / 'config/.env').read_bytes() == files['/app/.env']['']
    assert (sessions / 'config/mcp.json').read_bytes() == files['/app/mcp.json']['']
    assert (sessions / 'config/provider_overrides.json').read_bytes() == files['/app/provider_overrides.json']['']
    vendor = sessions / 'state/muselab/vendor-cli'
    assert (vendor / 'projects/synthetic/session.jsonl').read_bytes() == files['/home/muse/.local/state/muselab/vendor-cli']['projects/synthetic/session.jsonl']
    assert (vendor / 'binary').read_bytes() == b'\x00\xff\x01'
    assert (sessions / 'original-session.json').read_text() == 'synthetic old session'
    backup = Path(report['backup_dir'])
    assert len(list(backup.glob('*.tar'))) == 4
    for root in [sessions / 'config', sessions / 'state', backup]:
        for path in [root, *root.rglob('*')]:
            assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [call[0] for call in calls] == ['inspect', 'cp', 'cp', 'cp', 'cp', 'inspect']


@pytest.mark.parametrize('scenario,success', [
    ({'missing':['/app/.env']}, True),
    ({'failure':'/app/.env'}, False),
    ({'inspect_failure':True}, False),
    ({'running':True}, False),
    ({'restart_during_copy':True}, False),
    ({'unsafe':'/app/.env'}, False),
])
def test_only_explicit_absence_may_be_skipped(migration_cli, scenario, success):
    run, sessions, _log, _files = migration_cli
    result = run(**scenario)
    assert (result.returncode == 0) == success, result.stderr
    assert not (sessions / 'config/.env').exists()
    if success:
        assert (sessions / 'config/mcp.json').is_file()
        receipt = json.loads((Path(json.loads(result.stdout)['backup_dir']) / 'manifest.json').read_text())
        assert receipt['sources']['env']['present'] is False
    else:
        assert not (sessions / 'config').exists()
        assert not (sessions / 'state/muselab/vendor-cli').exists()
    assert (sessions / 'original-session.json').read_text() == 'synthetic old session'


def test_existing_target_conflict_never_overwrites_and_retains_all_backups(migration_cli):
    run, sessions, _log, _files = migration_cli
    (sessions / 'config').mkdir()
    (sessions / 'config/.env').write_text('keep-existing-target')
    result = run()
    assert result.returncode == 1
    assert 'target already exists' in result.stderr
    assert (sessions / 'config/.env').read_text() == 'keep-existing-target'
    backup, = sessions.glob('.muselab-docker-migration-*')
    assert len(list(backup.glob('*.tar'))) == 4
    assert json.loads((backup / 'manifest.json').read_text())['phase'] == 'failed'
    assert (backup / 'stage/config/.env').exists()


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux renameat2")
def test_atomic_publish_refuses_even_an_empty_existing_directory(tmp_path):
    spec = importlib.util.spec_from_file_location('migration_script', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'file').write_text('retained')
    with pytest.raises(RuntimeError, match='target already exists'):
        module.no_replace(source, target)
    assert (source / 'file').read_text() == 'retained'
    assert list(target.iterdir()) == []


def test_unsupported_platform_stops_before_docker_or_filesystem_changes(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('unsupported_migration', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('Docker invoked'))
    sessions = tmp_path / 'sessions'
    sessions.mkdir()
    with pytest.raises(RuntimeError, match='requires Linux or WSL'):
        module.migrate('docker', 'synthetic-old', sessions)
    assert list(sessions.iterdir()) == []
