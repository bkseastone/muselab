"""Real-browser checks for history response consumption and cancellation."""
import pytest
from .test_chat_render_perf import _app_eval, _login


@pytest.mark.parametrize("cancel", [False, True])
def test_history_body_is_covered_by_deadline_and_upstream_cancel(page, backend_url, auth_token, cancel):
    _login(page, backend_url, auth_token)
    result = _app_eval(page, """
        const original = window.fetch;
        const upstream = new AbortController();
        let transportSignal, consumeEntered = false;
        window.fetch = async (url, options) => {
          if (url !== '/fixture') return original(url, options);
          transportSignal = options.signal;
          return new Response('{}');
        };
        try {
          const pending = app._fetchWithDeadline('/fixture', {signal: upstream.signal}, 100,
            async response => {
              consumeEntered = true;
              if (arg) setTimeout(() => upstream.abort(), 10);
              // Non-cooperative body reader still cannot hold the UI forever.
              await new Promise(() => {});
            });
          let name = '';
          try { await pending; } catch (error) { name = error.name; }
          return {name, consumeEntered, aborted: transportSignal.aborted};
        } finally { window.fetch = original; }
    """, cancel)
    assert result == {"name": "AbortError", "consumeEntered": True, "aborted": True}


def test_history_load_measures_body_wait_separately_from_json_parse(page, backend_url, auth_token):
    _login(page, backend_url, auth_token)
    result = _app_eval(page, """
        const sid = 'history-body-fixture';
        const st = app._ensureTabState(sid);
        const original = window.fetch, report = app._reportHistoryLoadPerf;
        let metrics;
        app._reportHistoryLoadPerf = value => {metrics = {...value};};
        window.fetch = async (url, options) => {
          if (!String(url).includes('/sessions/' + sid)) return original(url, options);
          return {ok: true, headers: new Headers(), text: async () => {
            await new Promise(resolve => setTimeout(resolve, 120));
            return JSON.stringify({messages: [], total: 0});
          }};
        };
        try {
          const loaded = await app.loadSession(sid);
          return {loaded, metrics, ready: st.messagesReady};
        } finally {
          window.fetch = original;
          app._reportHistoryLoadPerf = report;
          delete app.tabState[sid];
        }
    """)
    assert result["loaded"] and result["ready"]
    assert result["metrics"]["receive_ms"] >= 100
    assert result["metrics"]["parse_ms"] < result["metrics"]["receive_ms"]
    assert result["metrics"]["visibility"] == "visible"
