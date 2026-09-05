#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for file in frontend/*.js frontend/data/*.js frontend/i18n/*.js \
            frontend/modules/*.js frontend/modules/*.mjs; do
  [[ -f "$file" ]] || continue
  node --check "$file"
done
node -e "JSON.parse(require('fs').readFileSync('frontend/assets/manifest.webmanifest','utf8'))"
