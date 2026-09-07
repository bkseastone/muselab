"""The conditional list poll must retain valid state through partial responses."""
import pytest

from .test_completion_visibility import _prepare
from .test_chat_render_perf import _app_eval, _assert_no_browser_errors


def test_session_list_body_deadline_releases_next_poll(page, backend_url, auth_token):
    errors, _, _ = _prepare(page, backend_url, auth_token)
    result = _app_eval(page, """
        const originalFetch = window.fetch;
        let calls = 0, releaseBody, aborted = false;
        const body = new Promise(resolve => {releaseBody = resolve;});
        const oldSessions = app.sessions;
        app._sessionListTimeoutMs = 100;
        app._sessionsEtag = '"old-list"';
        window.fetch = async (url, options) => {
          if (!String(url).includes('/api/chat/sessions?limit=')) return originalFetch(url, options);
          calls++;
          if (calls > 1) return {status:304, ok:false};
          options.signal.addEventListener('abort', () => {aborted = true;});
          return {status:200, ok:true, headers:new Headers({etag:'"new-list"'}), json:() => body};
        };
        try {
          const first = await app._pullSessionList(true);
          const second = await app._pullSessionList(true);
          releaseBody({sessions:[]});
          await new Promise(resolve => setTimeout(resolve, 0));
          return {first, second, calls, aborted,
            held:!!app._sessionListPullPromise, retained:app.sessions === oldSessions,
            etag:app._sessionsEtag};
        } finally {window.fetch = originalFetch;}
    """)
    assert result == {
        "first": False, "second": False, "calls": 2, "aborted": True,
        "held": False, "retained": True, "etag": '"old-list"',
    }
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("failure", ["body_error", "invalid_shape"])
def test_list_failure_keeps_validator_and_next_poll_recovers(
    page, backend_url, auth_token, failure,
):
    errors, _, _ = _prepare(page, backend_url, auth_token)
    result = _app_eval(page, """
        const originalFetch = window.fetch;
        let calls = 0;
        const etags = [];
        const saved = app.sessions;
        app._sessionsEtag = '"old-list"';
        window.fetch = async (url, options) => {
          if (!String(url).includes('/api/chat/sessions?limit=')) return originalFetch(url, options);
          calls++;
          etags.push(options.headers['If-None-Match']);
          if (calls > 1) return {status:200, ok:true,
            headers:new Headers({etag:'"new-list"'}), json:async () => ({sessions:[]})};
          return {status:200, ok:true, headers:new Headers({etag:'"new-list"'}),
            json:async () => {
              if (arg === 'body_error') throw new TypeError('synthetic interrupted body');
              return {};
            }};
        };
        try {
          const first = await app._pullSessionList(true);
          const retained = app.sessions === saved;
          const second = await app._pullSessionList(true);
          return {first, retained, second, count:app.sessions.length,
            etags, finalEtag:app._sessionsEtag};
        } finally {window.fetch = originalFetch;}
    """, failure)
    assert result == {
        "first": False, "retained": True, "second": True, "count": 0,
        "etags": ['"old-list"', '"old-list"'], "finalEtag": '"new-list"',
    }
    _assert_no_browser_errors(page, errors)
