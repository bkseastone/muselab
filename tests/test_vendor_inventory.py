"""The offline vendor gate and scoped advisory reviews must fail closed."""
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'scripts/check-vendor.py'
    spec = importlib.util.spec_from_file_location('vendor_checker', script)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    monkeypatch.setattr(checker, 'ROOT', tmp_path)
    monkeypatch.setattr(checker, 'VENDOR', tmp_path)
    (tmp_path / 'bundle.js').write_text('safe subset', encoding='utf-8')
    (tmp_path / 'LICENSE').write_text('upstream license fixture', encoding='utf-8')
    assets = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    package = {'name': 'lodash-es', 'version': '4.17.23', 'license_files': ['LICENSE'],
               'via': 'parser-fixture', 'source_files': ['get.js'],
               'source_maps': [{'sha256': 'reviewed-map'}]}
    manifest = {'assets': assets, 'build_inputs': {}, 'direct_packages': [],
                'bundled_packages': [], 'embedded_packages': [package],
                'advisory_exceptions': [{'id': 'GHSA-reviewed', 'name': 'lodash-es',
                    'version': '4.17.23', 'bundle': 'bundle.js',
                    'bundle_sha256': assets['bundle.js'], 'via': 'parser-fixture',
                    'source_map_sha256': 'reviewed-map', 'absent_modules': ['template.js'],
                    'url': 'https://example.invalid/advisory'}]}
    monkeypatch.setattr(sys, 'argv', ['check-vendor.py', '--audit'])
    response = {'results': [{'vulns': [{'id': 'GHSA-reviewed'}]}]}
    monkeypatch.setattr(checker, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps(response).encode()))
    return checker, manifest, response, tmp_path


def run_inventory(fixture):
    checker, manifest, _, root = fixture
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return checker.main()


def test_reviewed_source_exclusion_is_visible(inventory, capsys):
    assert run_inventory(inventory) == 0
    output = capsys.readouterr().out
    assert 'REVIEWED VERSION MATCH' in output
    assert '1 reviewed version matches' in output


@pytest.mark.parametrize('change', ['bundle', 'affected_module', 'map', 'full_package', 'new_advisory'])
def test_changed_evidence_and_new_advisories_cannot_reuse_review(inventory, change):
    _, manifest, response, root = inventory
    if change == 'bundle':
        (root / 'bundle.js').write_text('changed subset', encoding='utf-8')
        manifest['assets']['bundle.js'] = hashlib.sha256((root / 'bundle.js').read_bytes()).hexdigest()
    elif change == 'affected_module':
        manifest['embedded_packages'][0]['source_files'].append('template.js')
    elif change == 'map':
        manifest['embedded_packages'][0]['source_maps'][0]['sha256'] = 'changed-map'
    elif change == 'full_package':
        manifest['direct_packages'].append({'name': 'lodash-es', 'version': '4.17.23',
                                            'license_files': ['LICENSE']})
    else:
        response['results'][0]['vulns'].append({'id': 'GHSA-new'})
    assert run_inventory(inventory) == 1


def test_uninventoried_asset_fails_offline(inventory, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['check-vendor.py'])
    (inventory[3] / 'unrecorded.js').write_text('unexpected', encoding='utf-8')
    assert run_inventory(inventory) == 1
