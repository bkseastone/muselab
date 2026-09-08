#!/usr/bin/env python3
"""Check committed frontend hashes/licenses; --audit queries OSV's npm advisories."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'frontend' / 'vendor'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--report', type=Path, help='Save the advisory response as JSON')
    args = parser.parse_args()
    manifest = json.loads((VENDOR / 'manifest.json').read_text())
    actual = {p.relative_to(VENDOR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in VENDOR.rglob('*') if p.is_file() and p.name != 'manifest.json'}
    expected = manifest['assets']
    errors = [f'Asset differs or is not inventoried: {name}'
              for name in sorted(actual.keys() | expected.keys())
              if actual.get(name) != expected.get(name)]
    for name, digest in manifest['build_inputs'].items():
        path = ROOT / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f'Build input differs: {name}; rebuild vendor assets')
    packages = manifest['direct_packages'] + manifest['bundled_packages'] + manifest.get('embedded_packages', [])
    for package in packages + manifest.get('source_fragments', []):
        if not package.get('license_files'):
            errors.append(f'Missing license declaration: {package["name"]}')
        for name in package.get('license_files', []):
            if name not in expected or not (VENDOR / name).is_file():
                errors.append(f'Missing inventoried license text: {name}')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    unique = {(p['name'], p['version']) for p in packages}
    print(f'Vendor inventory OK: {len(actual)} files, {len(unique)} npm package versions.')
    if not args.audit:
        return 0
    queries = [{'package': {'ecosystem': 'npm', 'name': name}, 'version': version}
               for name, version in sorted(unique)]
    request = Request('https://api.osv.dev/v1/querybatch',
                      data=json.dumps({'queries': queries}).encode(),
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=45) as response:
            result = json.load(response)
        if len(result.get('results', [])) != len(queries):
            raise ValueError('Incomplete OSV response')
    except Exception as error:
        print(f'Advisory scan unavailable: {type(error).__name__}', file=sys.stderr)
        return 2
    findings = []
    exclusions = []
    for query, scan in zip(queries, result['results']):
        # Pagination means the result is incomplete, never a clean result.
        if scan.get('next_page_token'):
            findings.append(f'Incomplete advisory result: {query["package"]["name"]}')
        for vuln in scan.get('vulns', []):
            name, version = query['package']['name'], query['version']
            matches = [p for p in packages if p['name'] == name and p['version'] == version]
            reviewed = next((e for e in manifest.get('advisory_exceptions', [])
                             if e['name'] == name and e['version'] == version
                             and e['id'] == vuln['id']
                             and actual.get(e['bundle']) == e['bundle_sha256']
                             and matches and all(
                                 p.get('via') == e['via'] and p.get('source_files')
                                 and not set(p['source_files']).intersection(e['absent_modules'])
                                 and any(m['sha256'] == e['source_map_sha256']
                                         for m in p.get('source_maps', []))
                                 for p in matches)), None)
            if reviewed:
                exclusions.append(reviewed)
            else:
                findings.append(f'{name}@{version}: {vuln["id"]}')
    for entry in exclusions:
        print(f'REVIEWED VERSION MATCH {entry["name"]}@{entry["version"]}: {entry["id"]}; '
              f'affected implementations absent in bundle SHA-256 {entry["bundle_sha256"]}')
        print(f'  {entry["url"]}')
    for fragment in manifest.get('source_fragments', []):
        print(f'NOT VERSION-SCANNABLE: {fragment["name"]}: {fragment["version_note"]}')
    if args.report:
        args.report.write_text(json.dumps({'queries': queries, **result,
                                           'reviewed_exclusions': exclusions,
                                           'unscanned_source_fragments': manifest.get('source_fragments', [])},
                                          indent=2) + '\n')
    if findings:
        print('\n'.join(findings), file=sys.stderr)
        return 1
    print(f'OSV: {len(queries)} package versions checked, {len(exclusions)} reviewed version matches, '
          '0 unreviewed advisory matches.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
