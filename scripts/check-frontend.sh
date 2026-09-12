#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for file in frontend/*.js frontend/data/*.js frontend/i18n/*.js \
            frontend/modules/*.js frontend/modules/*.mjs; do
  [[ -f "$file" ]] || continue
  node --check "$file"
done
node -e "JSON.parse(require('fs').readFileSync('frontend/assets/manifest.webmanifest','utf8'))"
# Promo stays a single HTML document so it also renders in the file preview.
# Parse inline JavaScript and structured metadata without a frontend build.
python3 - <<'PY'
from html.parser import HTMLParser
from pathlib import Path
import json
import subprocess


class PromoScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.kind = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        attributes = dict(attrs)
        if "src" in attributes:
            raise RuntimeError("promo scripts must be inline for file previews")
        self.kind = attributes.get("type", "text/javascript")
        self.parts = []

    def handle_data(self, data):
        if self.kind is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag != "script" or self.kind is None:
            return
        source = "".join(self.parts)
        if self.kind == "application/ld+json":
            json.loads(source)
        elif self.kind in ("text/javascript", "application/javascript", "module"):
            mode = "module" if self.kind == "module" else "commonjs"
            subprocess.run(
                ["node", "--check", "--input-type=" + mode],
                input=source, text=True, check=True,
            )
        else:
            raise RuntimeError("unrecognized promo script type: " + self.kind)
        self.kind = None
        self.parts = []


PromoScripts().feed(Path("promo/index.html").read_text(encoding="utf-8"))
PY
