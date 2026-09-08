"""Inventory packages already bundled by the upstream Mermaid parser build."""
import base64
import hashlib
import io
import json
import re
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen


def _package_record(item, vendor):
    (name, version), detail = item
    url = f'https://registry.npmjs.org/{quote(name, safe="")}/{version}'
    with urlopen(url, timeout=30) as response:
        package = json.load(response)
    with urlopen(package['dist']['tarball'], timeout=30) as response:
        archive = response.read(32 * 1024 * 1024 + 1)
    if len(archive) > 32 * 1024 * 1024:
        raise RuntimeError(f'License source exceeds budget: {name}@{version}')
    algorithm, expected = package['dist']['integrity'].split('-', 1)
    actual = base64.b64encode(hashlib.new(algorithm, archive).digest()).decode()
    if actual != expected:
        raise RuntimeError(f'License source integrity mismatch: {name}@{version}')
    target = vendor / 'licenses' / 'mermaid-embedded' / (name.replace('/', '__') + '@' + version)
    target.mkdir(parents=True, exist_ok=True)
    licenses = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        for member in tar.getmembers():
            # Read known top-level texts only; never extract an archive path.
            if member.isfile() and re.fullmatch(
                    r'package/(licen[cs]e|notice|copying)(?:[-.](?:md|txt|mpl|apache))?',
                    member.name, re.IGNORECASE):
                if member.size > 256 * 1024:
                    raise RuntimeError('Unexpected license size')
                output = target / Path(member.name).name
                output.write_bytes(tar.extractfile(member).read())
                licenses.append(output.relative_to(vendor).as_posix())
    if not licenses:
        raise RuntimeError(f'Missing embedded license: {name}@{version}')
    return {'name': name, 'version': version, 'source': url,
            'license': package.get('license', 'SEE LICENSE'),
            'license_files': sorted(licenses), 'integrity': package['dist']['integrity'],
            'bundled_in': 'mermaid.min.js', 'via': '@mermaid-js/parser@1.2.1',
            'source_files': sorted(detail['sources']), 'source_maps': detail['maps']}


def collect(work, inputs, vendor):
    packages = {}
    source_fragments = []
    for name in sorted(inputs):
        if 'node_modules/@mermaid-js/parser/' not in name:
            continue
        source_map = work / (name + '.map')
        if not source_map.is_file():
            continue
        data = json.loads(source_map.read_text(encoding='utf-8'))
        map_record = {'path': source_map.relative_to(work).as_posix(),
                      'sha256': hashlib.sha256(source_map.read_bytes()).hexdigest()}
        for source, content in zip(data['sources'], data.get('sourcesContent', [])):
            match = re.search(r'\.pnpm/([^/]+)/node_modules/(.+)', source)
            if match:
                package_spec = match[1].split('_')[0].replace('+', '/')
                package_name, version = package_spec.rsplit('@', 1)
                detail = packages.setdefault((package_name, version), {'sources': set(), 'maps': []})
                detail['sources'].add(match[2].removeprefix(package_name + '/'))
                if map_record not in detail['maps']:
                    detail['maps'].append(map_record)
            elif 'webpack://LIB/node_modules/path-browserify/index.js' == source:
                # The upstream map names this source but supplies no npm version.
                # Retain its own complete notice and make the audit limit explicit.
                notice = content.split("'use strict'", 1)[0]
                output = vendor / 'licenses' / 'path-browserify-source-MIT.txt'
                output.write_text(notice, encoding='utf-8')
                source_fragments.append({
                    'name': 'path-browserify source fragment', 'version': None,
                    'source': source, 'source_sha256': hashlib.sha256(content.encode()).hexdigest(),
                    'license_files': [output.relative_to(vendor).as_posix()],
                    'version_note': 'Upstream source identifies Node.js v8.11.1 POSIX path implementation; exact npm version is not provided.',
                    'via': '@mermaid-js/parser@1.2.1', 'source_map': map_record,
                })
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda item: _package_record(item, vendor), sorted(packages.items())))
    return records, source_fragments
