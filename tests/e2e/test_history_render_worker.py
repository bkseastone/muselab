"""Real browser coverage for bounded history mounting and asynchronous rich work."""
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.e2e.test_chat_render_perf import (
    _app_eval, _bootstrap_session_for_real_load, _login, _make_mixed_messages,
    _route_windowed_session, _capture_browser_errors, _assert_no_browser_errors,
)


def test_cold_history_stays_bounded_after_settle_and_pages_complete_history(page, backend_url, auth_token):
    errors = _capture_browser_errors(page)
    page.set_viewport_size({"width": 1440, "height": 900})
    _login(page, backend_url, auth_token)
    sid = "1234abcd-history-window"
    messages = _make_mixed_messages(300, "BOUNDED")
    requests = _route_windowed_session(page, sid, messages, updated_at=100)
    _bootstrap_session_for_real_load(page, sid, "Bounded fixture")
    result = _app_eval(page, """
      app.sessions[0].updated_at = 100;
      const events = [];
      app._reportRenderPerf = fields => events.push(fields);
      const originalLatest = app.returnToLatest.bind(app);
      app.returnToLatest = async (...args) => {
        await new Promise(resolve => setTimeout(resolve, 70));
        return originalLatest(...args);
      };
      const st = app.tabState[arg];
      await app._ensureSessionLoaded(arg);
      await new Promise(resolve => app.$nextTick(resolve));
      return { resident: st.messages.length, visible: app.paneMessages(arg).length,
        phase: st.transcriptLoadPhase, events };
    """, sid)
    assert result["resident"] == 100
    assert result["visible"] == 20
    assert result["phase"] == "idle"
    event = next(row for row in result["events"] if row["phase"] == "transcript")
    assert event["settle_ms"] >= 60 and event["total_ms"] >= event["settle_ms"]
    assert event["mounted_count"] == 20
    expect(page.locator(".msg.assistant", has_text="BOUNDED_299").last).to_be_in_viewport()
    assert [request["count"] for request in requests] == [100]
    earlier = _app_eval(page, """
      const st = app.tabState[arg];
      for (let i = 0; i < 20 && st._hasMoreHistory; i++) await app.loadEarlierMessages();
      return { resident: st.messages.length, offset: st.messageRange.offset,
        first: st.messages[0].text, last: st.messages.at(-1).text };
    """, sid)
    assert earlier["offset"] == 0 and earlier["resident"] == 300
    assert "BOUNDED_000" in earlier["first"] and "BOUNDED_299" in earlier["last"]
    _assert_no_browser_errors(page, errors)


def test_large_markdown_uses_worker_sanitizes_and_discards_obsolete_results(page, backend_url, auth_token):
    errors = _capture_browser_errors(page)
    _login(page, backend_url, auth_token)
    result = _app_eval(page, r"""
      const text = '**worker formatted**\n\n' + ('text with **bold** and `code`.\n\n'.repeat(900))
        + '<img src=x onerror="window.__workerXss=1"><script>window.__workerXss=1</script>';
      const original = window.marked.parse;
      window.marked.parse = () => { throw new Error('main-thread marked called'); };
      let ticked = false;
      const message = { role: 'assistant', text };
      const pending = app._renderHistoryMessageAsync(message);
      setTimeout(() => { ticked = true; }, 0);
      const html = await pending;
      const stale = { role: 'assistant', text };
      const obsolete = app._renderHistoryMessageAsync(stale);
      stale.text = 'new version';
      const staleResult = await obsolete;
      window.marked.parse = original;
      const node = document.createElement('div'); node.innerHTML = html;
      return { ticked, strong: node.querySelectorAll('strong').length,
        unsafe: !!node.querySelector('script,[onerror]'), staleResult,
        textPreserved: node.textContent.includes('worker formatted') };
    """)
    assert result["ticked"] and result["strong"] > 800
    assert not result["unsafe"] and result["textPreserved"]
    assert result["staleResult"] is None
    _assert_no_browser_errors(page, errors)


def test_large_unlabelled_highlight_keeps_main_thread_responsive_and_full_text(page, backend_url, auth_token):
    errors = _capture_browser_errors(page)
    _login(page, backend_url, auth_token)
    result = _app_eval(page, r"""
      const pre = document.createElement('pre'), code = document.createElement('code');
      const text = 'ordinary prose value = 42 and a repeated text row\n'.repeat(2100);
      code.textContent = text; pre.append(code); document.body.append(pre);
      let ticked = false;
      const pending = app._highlightOne(code);
      setTimeout(() => { ticked = true; }, 0);
      await pending;
      const result = { ticked, preserved: code.textContent === text, done: code.dataset.hl };
      pre.remove(); return result;
    """)
    assert result == {"ticked": True, "preserved": True, "done": "1"}
    _assert_no_browser_errors(page, errors)


def test_same_length_history_replacement_and_append_update_projected_rows(page, backend_url, auth_token):
    _login(page, backend_url, auth_token)
    sid = "projection-mutations"
    _bootstrap_session_for_real_load(page, sid, "Projection fixture")
    _app_eval(page, """
      const st = app.tabState[arg];
      st.messages = [{role:'assistant', text:'INITIAL_ROW', html:'<p>INITIAL_ROW</p>', _k:'initial'}];
      st.messageRange.visibleStart = 0; st.messageRange.visibleEnd = 1;
      st._loaded = true; st.messagesReady = true;
      app._touchTranscriptPane(arg);
    """, sid)
    expect(page.locator('.msg.assistant', has_text='INITIAL_ROW')).to_be_visible()
    _app_eval(page, """
      const st = app.tabState[arg];
      st.messages.splice(0, 1, {role:'assistant', text:'REPLACED_ROW', html:'<p>REPLACED_ROW</p>', _k:'replaced'});
      st.messages.push({role:'assistant', text:'APPENDED_ROW', html:'<p>APPENDED_ROW</p>', _k:'appended'});
      st.messageRange.visibleEnd = 2;
    """, sid)
    expect(page.locator('.msg.assistant', has_text='INITIAL_ROW')).to_have_count(0)
    expect(page.locator('.msg.assistant', has_text='REPLACED_ROW')).to_be_visible()
    expect(page.locator('.msg.assistant', has_text='APPENDED_ROW')).to_be_visible()


def test_stalled_worker_is_terminated_and_next_message_can_render(page, backend_url, auth_token):
    page.add_init_script("""
      const NativeWorker = window.Worker;
      window.__fixtureWorkers = {created:0, terminated:0};
      window.Worker = function(...args) {
        const count = ++window.__fixtureWorkers.created;
        if (count === 1) return {postMessage() {}, terminate() {window.__fixtureWorkers.terminated++;}};
        return new NativeWorker(...args);
      };
    """)
    _login(page, backend_url, auth_token)
    result = _app_eval(page, r"""
      const source = '**complete fixture** ' + 'safe content '.repeat(1600);
      const first = await app._renderHistoryMessageAsync({role:'assistant',text:source});
      const second = await app._renderHistoryMessageAsync({role:'assistant',text:source});
      const node = document.createElement('div'); node.innerHTML = first;
      return {plainComplete:node.textContent === source, rich:second.includes('<strong>complete fixture</strong>'),
        ...window.__fixtureWorkers};
    """)
    assert result == {"plainComplete": True, "rich": True, "created": 2, "terminated": 1}


def test_model_catalog_refresh_updates_controls_once_without_blocking_ui(page, backend_url, auth_token):
    import json
    _login(page, backend_url, auth_token)
    calls = []
    def providers(route):
        calls.append(True)
        levels = ["auto"] if len(calls) == 1 else ["auto", "max"]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "models": [{"model": "e2e-model", "label": "Fixture", "group": "fixture",
                        "supports_effort": True, "effort_levels": levels}],
            "catalog_pending": True,
        }))
    page.route("**/api/chat/providers", providers)
    _app_eval(page, "await app._fetchModels(); return app.availableModels.length;")
    assert len(calls) == 1
    page.wait_for_function("""() => document.querySelector('#app')._x_dataStack[0]
      .availableModels[0].effort_levels.includes('max')""", timeout=10000)
    assert len(calls) == 2
    assert _app_eval(page, "return !!app._modelCatalogRefreshTimer;") is False


def test_transcript_metrics_fence_closed_and_reopened_session_owners(page, backend_url, auth_token):
    _login(page, backend_url, auth_token)
    result = _app_eval(page, """
      const sid = '1234abcd-metric-owner';
      const events = []; app._reportRenderPerf = fields => events.push(fields);
      const old = app._ensureTabState(sid);
      const token = app._beginTranscriptLoad(sid, old);
      delete app.tabState[sid];
      const fresh = app._ensureTabState(sid);
      const current = app._beginTranscriptLoad(sid, fresh);
      app._finishTranscriptPerf(token, 'error', 'failed');
      await app._settleTranscriptLoad(current, {returnToLatest:false});
      return events.map(({status,cancel_reason}) => ({status,cancel_reason}));
    """)
    assert result == [{"status": "cancelled", "cancel_reason": "superseded"},
                      {"status": "ok", "cancel_reason": "none"}]
