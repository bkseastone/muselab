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
      return a && a.appReady && a._sessionsInitialized && a._modelsLoaded
        && a.currentId && a.openTabIds.includes(a.currentId);
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
    with page.expect_event("dialog") as unload:
        page.evaluate("() => { setTimeout(() => location.reload(), 0); }")
    assert unload.value.type == "beforeunload"
    unload.value.dismiss()
    expect(field).to_have_value("https://draft.example.test/embedding")
    page.locator('.settings-draft-notice button').click()
    page.locator('.confirm-modal .modal-foot button').last.click()
    expect(field).not_to_have_value("https://draft.example.test/embedding")
    assert not page.evaluate("() => document.querySelector('#app')._x_dataStack[0].settingsDirty()")


def test_controlled_html_annotation_adds_bounded_workspace_quote(page, backend_url, auth_token):
    _login(page, backend_url, auth_token)
    page.evaluate("""async () => {
      const a = document.querySelector('#app')._x_dataStack[0];
      if (!a.currentId) await a.newSession();
      await a.openFile({path:'smooth-preview.html',name:'smooth-preview.html'});
    }""")
    frame_selector = 'iframe[data-preview-html-path="smooth-preview.html"]'
    frame = page.frame_locator(frame_selector)
    expect(frame.locator('h1')).to_be_visible()
    page.locator('.html-annotation-trigger').click()
    page.wait_for_function("() => document.querySelector('#app')._x_dataStack[0].htmlAnnotation.active")
    frame.locator('h1').click()
    expect(frame.locator('[data-muselab-annotation-overlay]')).to_be_visible()
    page.locator('.html-annotation-comment').fill('Make this heading easier to scan')
    page.screenshot(path='/tmp/muselab-html-annotation.png', full_page=True)
    page.locator('.html-annotation-form button').click()
    quote = page.evaluate("() => document.querySelector('#app')._x_dataStack[0].pendingQuotes.at(-1)")
    assert quote['path'].endswith('/smooth-preview.html')
    assert quote['workspace'] in quote['path']
    assert 'HTML element:' in quote['text']
    assert 'Smooth preview' in quote['text']
    assert 'Make this heading easier to scan' in quote['text']
    expect(frame.locator('[data-muselab-annotation-overlay]')).to_have_count(0)
    assert 'allow-same-origin' not in page.locator(frame_selector).get_attribute('sandbox')


def test_workbench_more_menu_and_memory_empty_state(page, backend_url, auth_token):
    _login(page, backend_url, auth_token)
    page.locator('.workbench-more summary').click()
    expect(page.locator('.workbench-more-actions')).to_be_visible()
    page.locator('.workbench-more-actions button[aria-label="Open skills list"]').click()
    expect(page.locator('.workbench-more')).not_to_have_attribute('open', '')
    page.evaluate("() => document.querySelector('#app')._x_dataStack[0].openMemoryCenter()")
    expect(page.locator('.memory-center-intro')).to_be_visible()
    expect(page.locator('.memory-empty-guide')).to_be_visible()
    expect(page.locator('.memory-empty-guide button')).to_be_visible()


def test_browser_metrics_record_painted_turns_without_cross_session_values(page, backend_url, auth_token):
    # Reuse the deterministic transport fixture; application send/render code is real.
    from tests.e2e.test_chat_render_perf import _install_fake_event_source, _route_windowed_session
    _install_fake_event_source(page)
    page.route("**/api/chat/stream/start", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"ticket":"metrics-fixture"}'))
    _login(page, backend_url, auth_token)
    sid = page.evaluate("() => document.querySelector('#app')._x_dataStack[0].currentId")
    _route_windowed_session(page, sid, [
        {'role':'user', 'text':'Show a deterministic browser response', 'uuid':'metrics-user'},
        {'role':'assistant', 'text':'Visible metrics response', 'uuid':'metrics-assistant', 'ts':1700000010},
    ])
    page.evaluate("""async () => {
      const a = document.querySelector('#app')._x_dataStack[0];
      await a.loadBrowserMetrics();
      a.refreshSessions = async () => {};
      a._fetchTabUsage = async () => {};
      a.availableModels = [{model:'e2e-model',label:'E2E model',group:'e2e'}];
      a.model = a.defaultModel = 'e2e-model';
      a.sessions.find(s => s.id === a.currentId).model = 'e2e-model';
      a._ensureTabState(a.currentId).atBottom = true;
      a.input = 'Show a deterministic browser response';
      a.send();
    }""")
    page.wait_for_function("() => window.__fakeChatStreams().length === 1")
    page.evaluate("() => window.__emitSse('text', {text:'Visible metrics response'})")
    page.wait_for_function("() => document.querySelector('#app')._x_dataStack[0].browserMetrics.turns[0]?.firstMs > 0")
    page.evaluate("() => window.__emitSse('done', {result:'Visible metrics response', duration_ms:200, num_turns:1})")
    page.wait_for_function("() => document.querySelector('#app')._x_dataStack[0].browserMetrics.turns[0]?.finalMs > 0")
    first = page.evaluate("() => document.querySelector('#app')._x_dataStack[0].browserMetrics.turns[0]")
    assert first['finalMs'] >= first['firstMs'] > 0
    # A pending paint from an old turn must not attach to a new turn or visible tab.
    result = page.evaluate("""async () => {
      const a = document.querySelector('#app')._x_dataStack[0];
      const m = a._browserMetrics;
      const key = a.tabState[a.currentId].messages.at(-1)._k;
      const obsolete = m.startTurn(a.currentId);
      m.paint(obsolete, 'first', key);
      m.startTurn(a.currentId);
      const hidden = m.startTurn('metrics-background');
      m.paint(hidden, 'final', key);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      return a.browserMetrics;
    }""")
    assert all(row['firstMs'] is None and row['finalMs'] is None for row in result['turns'][:3])
    assert result['readyMs'] is not None
    assert all(set(row) == {'number', 'firstMs', 'finalMs'} for row in result['turns'])
    page.evaluate("() => document.querySelector('#app')._x_dataStack[0].openSettings('service')")
    page.locator('.browser-performance summary').click()
    expect(page.locator('.browser-turn-metrics')).to_be_visible()
    page.screenshot(path='/tmp/muselab-browser-metrics.png', full_page=True)


def test_settings_save_keeps_edits_made_while_request_is_pending(page, backend_url, auth_token):
    _login(page, backend_url, auth_token)
    page.evaluate("() => document.querySelector('#app')._x_dataStack[0].openSettings('defaults')")
    pending = []
    def settings_write(route):
        if route.request.method == 'PUT':
            pending.append(route)
        else:
            route.continue_()
    page.route('**/api/settings', settings_write)
    page.evaluate("""() => {
      const a = document.querySelector('#app')._x_dataStack[0];
      a.settings.draftDefaults.permission = 'default';
      a.settings.draftKeys.DEEPSEEK_API_KEY = 'fixture-first-key';
      void a.saveSettings();
    }""")
    page.wait_for_timeout(100)
    assert pending
    page.evaluate("""() => {
      const a = document.querySelector('#app')._x_dataStack[0];
      a.settings.draftDefaults.permission = 'acceptEdits';
      a.settings.draftKeys.DEEPSEEK_API_KEY = 'fixture-second-key';
    }""")
    pending[0].fulfill(status=200, content_type='application/json', body='{"updated_count":2}')
    page.wait_for_timeout(100)
    state = page.evaluate("""() => {
      const a = document.querySelector('#app')._x_dataStack[0];
      return {dirty:a.settingsDirty(), shown:a.settings.show,
        permission:a.settings.draftDefaults.permission,
        newerKeyRetained:a.settings.draftKeys.DEEPSEEK_API_KEY === 'fixture-second-key'};
    }""")
    assert state == {'dirty':True, 'shown':True, 'permission':'acceptEdits', 'newerKeyRetained':True}
