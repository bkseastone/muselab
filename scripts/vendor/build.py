#!/usr/bin/env python3
"""Maintainer-only, locked Mermaid/KaTeX rebuild; no runtime build or CDN."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from embedded import collect

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VENDOR = ROOT / 'frontend' / 'vendor'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_and_build(work):
    for name in ('package.json', 'package-lock.json', 'entry.mjs'):
        shutil.copy2(HERE / name, work / name)
    subprocess.run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
                   cwd=work, check=True)
    subprocess.run([str(work / 'node_modules/.bin/esbuild'), 'entry.mjs',
                    '--bundle', '--minify', '--format=iife', '--platform=browser',
                    '--target=es2020', '--legal-comments=inline',
                    '--metafile=metafile.json', '--outfile=mermaid.min.js'],
                   cwd=work, check=True)


def publish(work):
    manifest = json.loads((VENDOR / 'manifest.json').read_text())
    lock = json.loads((work / 'package-lock.json').read_text())['packages']
    meta = json.loads((work / 'metafile.json').read_text())
    # Use output-contributing modules, including nested copies of a package at
    # different versions. Looking only at the top-level npm graph misses these.
    inputs = {name for output in meta['outputs'].values()
              for name, detail in output['inputs'].items()
              if detail['bytesInOutput'] > 0}
    packages = {}
    for name in sorted(inputs):
        path = work / name
        while path != work and path.name != 'node_modules':
            package_file = path / 'package.json'
            if package_file.is_file():
                data = json.loads(package_file.read_text())
                if data.get('name') and data.get('version'):
                    packages[(data['name'], data['version'])] = (path, data)
                    break
            path = path.parent
    bundle_licenses = VENDOR / 'licenses' / 'mermaid'
    if bundle_licenses.exists():
        shutil.rmtree(bundle_licenses)
    bundled = []
    for (name, version), (path, data) in sorted(packages.items()):
        target = bundle_licenses / (name.replace('/', '__') + '@' + version)
        target.mkdir(parents=True)
        license_paths = []
        for item in sorted(path.iterdir()):
            if item.is_file() and re.fullmatch(
                    r'(licen[cs]e|copying|notice)(?:[-.](?:md|txt|mpl|apache))?',
                    item.name, re.IGNORECASE):
                output = target / item.name
                shutil.copy2(item, output)
                license_paths.append(output.relative_to(VENDOR).as_posix())
        if not license_paths and name == 'fastdom':
            # This upstream package publishes its full MIT text in README.md.
            text = (path / 'README.md').read_text().split('## License\n', 1)[1]
            output = target / 'LICENSE-from-README.txt'
            output.write_text(text.strip() + '\n')
            license_paths.append(output.relative_to(VENDOR).as_posix())
        if not license_paths:
            raise RuntimeError(f'Missing full license text: {name}@{version}')
        package_lock = lock[path.relative_to(work).as_posix()]
        bundled.append({
            'name': name, 'version': version,
            'license': data.get('license') or ('MIT' if name == 'khroma' else 'SEE LICENSE'),
            'license_files': license_paths,
            'source': f'https://registry.npmjs.org/{quote(name, safe="")}/{version}',
            'integrity': package_lock['integrity'],
            'bundled_in': 'mermaid.min.js',
        })
    shutil.copy2(work / 'mermaid.min.js', VENDOR / 'mermaid.min.js')
    katex = work / 'node_modules/katex'
    for name in ('katex.min.js', 'katex.min.css'):
        shutil.copy2(katex / 'dist' / name, VENDOR / 'katex' / name)
    shutil.copy2(katex / 'dist/contrib/auto-render.min.js', VENDOR / 'katex/auto-render.min.js')
    shutil.copytree(katex / 'dist/fonts', VENDOR / 'katex/fonts', dirs_exist_ok=True)
    for name, source in [('KaTeX-MIT.txt', katex / 'LICENSE'),
                         ('Mermaid-MIT.txt', work / 'node_modules/mermaid/LICENSE')]:
        shutil.copy2(source, VENDOR / 'licenses' / name)
    manifest['bundled_packages'] = bundled
    manifest['embedded_packages'], manifest['source_fragments'] = collect(work, inputs, VENDOR)
    manifest['build_inputs'] = {
        p.relative_to(ROOT).as_posix(): digest(p)
        for p in sorted(HERE.iterdir()) if p.is_file() and p.suffix != '.md'
    }
    manifest['assets'] = {
        p.relative_to(VENDOR).as_posix(): digest(p)
        for p in sorted(VENDOR.rglob('*'))
        if p.is_file() and p.name != 'manifest.json'
    }
    (VENDOR / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Updated Mermaid/KaTeX; {len(bundled)} bundled package versions inventoried.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--built-dir', type=Path,
                        help='Use an already built, locked directory (maintainer validation only)')
    args = parser.parse_args()
    if args.built_dir:
        # Refuse an unrelated/unlocked graph even when reusing a build.
        for name in ('package.json', 'package-lock.json', 'entry.mjs'):
            if (args.built_dir / name).read_bytes() != (HERE / name).read_bytes():
                raise RuntimeError(f'Build input mismatch: {name}')
        publish(args.built_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='muselab-vendor-') as temporary:
            work = Path(temporary)
            install_and_build(work)
            publish(work)


if __name__ == '__main__':
    main()
