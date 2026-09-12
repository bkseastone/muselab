# Promo page

The page is served at /muselab/promo/. Preview it directly in MuseLab's file
preview, or run the following from the repository root and open
http://127.0.0.1:8000/promo/.

    python3 -m http.server 8000 --bind 127.0.0.1

No app credentials or frontend build are needed for the static-server preview.

- index.html is the single source: styles, font data, translations, and
  JavaScript are inline. Keep them inline so a raw-file preview does not need
  sibling CSS or JavaScript routes.
- Chinese text and English data-en translations stay together. Translated
  elements must be leaves so language changes preserve links and markup.
- data-doc selects paired docs/name_zh.md and docs/name.md links; both must exist.
- Screenshots reuse the README's reviewed assets through their absolute public
  HTTPS URLs. They require a network connection in the file preview. Relative
  media paths resolve against MuseLab's raw-file API and will fail there.
- ATLAS contains sample data and simulated task records. Preserve that caption;
  do not replace these assets with private workspace captures.
- Keep model, scheduling, installation, and data-handling claims aligned with
  current documentation. Local hosting does not imply local model inference.
- MuseLab's sandbox intentionally disables browser storage and restricts the
  clipboard. Language switching works for the current preview; copy offers a
  manual-selection fallback when necessary.

Run bash scripts/check-frontend.sh and git diff --check before publishing.
The browser regression in tests/e2e/test_promo_preview.py opens this actual HTML
through MuseLab's authenticated file API and sandboxed preview. It verifies
layout, screenshots, language switching, anchors, FAQ, image enlargement, and
copy feedback. Screenshot responses use the existing repository assets for
deterministic CI; verify their public availability when changing asset URLs.

Also inspect Chinese and English at desktop, tablet, and narrow mobile widths.
Keep the content and navigation usable without JavaScript.
