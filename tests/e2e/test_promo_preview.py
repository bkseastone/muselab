"""The promo must render through MuseLab's real sandboxed file preview."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Page, expect  # noqa: E402


def test_promo_renders_inside_muselab_file_preview(
    page: Page, backend_url, auth_token, tmp_path,
):
    repo = Path(__file__).resolve().parents[2]
    capture = Path(os.environ.get("MUSELAB_PROMO_CAPTURE_DIR", str(tmp_path)))
    capture.mkdir(parents=True, exist_ok=True)
    failed_paths = []
    page.on("response", lambda response: failed_paths.append(
        urlparse(response.url).path) if response.status >= 400 else None)

    # Public screenshot bytes stay deterministic in CI; the browser still
    # enforces the real HTML preview CSP and its opaque iframe origin.
    def public_asset(route):
        relative = urlparse(route.request.url).path.removeprefix("/muselab/promo/")
        asset = (repo / "promo" / relative).resolve()
        assert asset.is_relative_to((repo / "promo").resolve())
        assert asset.is_file()
        mime = "image/svg+xml" if asset.suffix == ".svg" else "image/png"
        route.fulfill(body=asset.read_bytes(), content_type=mime)

    page.route("https://hesorchen.github.io/muselab/promo/**", public_asset)
    headers = {"X-Auth-Token": auth_token}
    response = page.request.put(
        backend_url + "/api/files/write", headers=headers,
        data={"path": "promo-preview.html",
              "content": (repo / "promo/index.html").read_text(encoding="utf-8")},
    )
    assert response.ok
    # Include siblings when present: the original failure is URL resolution,
    # not an intentionally missing CSS/JS fixture.
    for name in ("styles.css", "app.js"):
        source = repo / "promo" / name
        if source.exists():
            response = page.request.put(
                backend_url + "/api/files/write", headers=headers,
                data={"path": name, "content": source.read_text(encoding="utf-8")},
            )
            assert response.ok

    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(backend_url, wait_until="domcontentloaded")
    page.wait_for_selector(".login, .chat-tabs-list", state="visible", timeout=10000)
    if page.locator(".login").is_visible():
        page.fill('.login input[type="password"]', auth_token)
        page.keyboard.press("Enter")
    page.wait_for_function("""() => {
      const app = document.querySelector('#app')?._x_dataStack?.[0];
      return app && app.authed && app.appReady && app._sessionsInitialized;
    }""")
    page.evaluate("""async () => {
      const app = document.querySelector('#app')._x_dataStack[0];
      app.previewOpen = true;
      await app.openFile({path: 'promo-preview.html', name: 'promo-preview.html'});
    }""")
    iframe = page.locator('iframe[data-preview-html-path="promo-preview.html"]')
    expect(iframe).to_be_visible(timeout=10000)
    frame = iframe.element_handle().content_frame()
    assert frame is not None
    frame.wait_for_selector("h1")
    frame.wait_for_load_state("load")
    state = frame.evaluate("""() => ({
      background: getComputedStyle(document.body).backgroundColor,
      stylesheetCount: document.styleSheets.length,
      languageVisible: !document.getElementById('language-toggle').hidden,
      sandboxed: (() => {try {void localStorage; return false;} catch {return true;}})()
    })""")
    state["failed_asset_paths"] = failed_paths
    (capture / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    page.screenshot(path=str(capture / "muselab-preview.png"))
    assert state["background"] == "rgb(17, 16, 14)", state
    assert state["languageVisible"], state
    assert state["sandboxed"], state

    for section in frame.locator("main > section").all():
        section.scroll_into_view_if_needed()
    frame.wait_for_function("""() => Array.from(document.querySelectorAll(
      'main img')).every(img => img.complete && img.naturalWidth > 0)""")
    if frame.locator("html").get_attribute("lang") != "zh-CN":
        frame.locator("#language-toggle").click()
    frame.locator("#language-toggle").click()
    expect(frame.locator("html")).to_have_attribute("lang", "en")
    frame.locator("#language-toggle").click()
    expect(frame.locator("html")).to_have_attribute("lang", "zh-CN")
    frame.locator(".hero-actions a").first.click()
    frame.wait_for_function("location.hash === '#install'")
    frame.locator("summary").first.click()
    expect(frame.locator("details").first).to_have_attribute("open", "")

    frame.locator("[data-lightbox]").first.click()
    expect(frame.locator("#image-dialog")).to_be_visible()
    frame.locator("#image-close").click()
    expect(frame.locator("#image-dialog")).not_to_be_visible()

    frame.locator('[data-copy="install-code"]').click()
    expect(frame.locator("#copy-status")).to_contain_text(
        re.compile("已复制|手动复制"))
    # Verify both a narrow preview rail and an expanded workbench preview.
    for width in (440, 1000):
        page.evaluate("""width => {
          const app = document.querySelector('#app')._x_dataStack[0];
          app.previewWidth = width;
        }""", width)
        frame.wait_for_function(
            "document.documentElement.scrollWidth === window.innerWidth")
    frame.evaluate("scrollTo({top:0,behavior:'instant'})")
    page.screenshot(path=str(capture / "muselab-preview.png"))
    assert not [path for path in failed_paths if path.startswith("/api/files/")], failed_paths
