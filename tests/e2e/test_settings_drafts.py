"""Settings edits survive in-page navigation; narrow multilingual forms fit."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


def _login(page: Page, base: str, token: str):
    page.goto(base, wait_until="domcontentloaded")
    page.wait_for_selector(".login, .chat-tabs-list", state="visible")
    if page.locator(".login").is_visible():
        page.fill('.login input[type="password"]', token)
        page.keyboard.press("Enter")
    page.wait_for_function("""() => {
      const a = document.querySelector('#app')?._x_dataStack?.[0];
      return a && a.appReady && a._sessionsInitialized;
    }""")


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_settings_controls_fit_narrow_bilingual_viewports(page, backend_url, auth_token, width, lang):
    page.set_viewport_size({"width": width, "height": 844})
    _login(page, backend_url, auth_token)
    page.evaluate("""async lang => {
      const a = document.querySelector('#app')._x_dataStack[0];
      a.lang = lang; await a.openSettings('defaults');
    }""", lang)
    for section in ["defaults", "provider", "memory_engine", "service", "general", "extensions"]:
        page.evaluate("p => document.querySelector('#app')._x_dataStack[0].selectSettingsPage(p)", section)
        page.wait_for_timeout(150)
        clipped = page.evaluate("""() => {
          const modal = document.querySelector('.settings-modal');
          const bounds = modal.getBoundingClientRect();
          return [...modal.querySelectorAll('input:not([type=checkbox]):not([type=radio]), select, textarea')]
            .filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
            .map(el => ({model: el.getAttribute('x-model'), rect: el.getBoundingClientRect()}))
            .filter(({rect}) => rect.left < bounds.left - 1 || rect.right > bounds.right + 1 || rect.width < 60)
            .map(({model, rect}) => ({model, left: rect.left, right: rect.right, width: rect.width}));
        }""")
        assert clipped == [], (width, lang, section, clipped)
    page.screenshot(path=f"/tmp/muselab-settings-{width}-{lang}.png", full_page=True)


def test_settings_drafts_survive_close_escape_and_memory_navigation(page, backend_url, auth_token):
    _login(page, backend_url, auth_token)
    page.evaluate("() => document.querySelector('#app')._x_dataStack[0].openSettings('memory_engine')")
    page.wait_for_function("() => document.querySelector('#app')._x_dataStack[0].settings.memory.configLoaded")
    field = page.locator('[x-model="settings.memory.config.embedding.base_url"]')
    field.fill("https://draft.example.test/embedding")
    expect(page.locator('.settings-draft-notice')).to_be_visible()
    page.locator('.settings-modal .modal-close').click()
    expect(page.locator('.confirm-modal')).to_be_visible()
    page.locator('.confirm-modal .modal-foot button').first.click()
    expect(field).to_have_value("https://draft.example.test/embedding")
    page.keyboard.press("Escape")
    expect(page.locator('.confirm-modal')).to_be_visible()
    page.locator('.confirm-modal .modal-foot button').last.click()
    expect(page.locator('.settings-modal')).to_be_hidden()
    page.evaluate("() => document.querySelector('#app')._x_dataStack[0].openMemoryCenter()")
    expect(page.locator('.memory-center-section')).to_be_visible()
    page.locator('.memory-center-head button').first.click()
    expect(field).to_have_value("https://draft.example.test/embedding")
    guarded = page.evaluate("""() => {
      const e = new Event('beforeunload', {cancelable:true});
      window.dispatchEvent(e); return e.defaultPrevented;
    }""")
    assert guarded
    page.locator('.settings-draft-notice button').click()
    page.locator('.confirm-modal .modal-foot button').last.click()
    expect(field).not_to_have_value("https://draft.example.test/embedding")
    assert not page.evaluate("() => document.querySelector('#app')._x_dataStack[0].settingsDirty()")
