"""Final-history recovery must converge without a browser reload.

When the live UUID is missing, stable completed-turn identity can repair
incomplete text. A known UUID still requires its own canonical boundary.
Detached background work must not gate the foreground history. Keep these
rules covered alongside successor and quiet-rendering protections.
"""
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.e2e.test_chat_render_perf import (
    _app_eval,
    _assert_no_browser_errors,
    _capture_browser_errors,
    _login,
)


SID = "completion-visibility-fixture"
FINAL = "CANONICAL_FINAL_COMPLETE"


def _prepare(page, backend_url, auth_token, *, state=None, activity=None, history_failures=0):
    errors = _capture_browser_errors(page)
    _login(page, backend_url, auth_token)
    history = {
        "id": SID, "name": "Completion fixture", "model": "e2e-model",
        "permission": "bypassPermissions", "thinking": True,
        "messages": [
            {"role": "user", "text": "FIXTURE_PROMPT", "uuid": "fixture-user",
             "_turnRoot": True},
            {"role": "assistant", "text": FINAL, "uuid": "fixture-final"},
        ],
        "offset": 0, "total": 2, "message_count": 2, "pre_total": 0,
        "history_order": "normal", "history_generation": "fixture-generation",
        "has_more": False, "has_later": False, "updated_at": 2,
        "completion_state": {
            "stable": True, "active": False, "turn_id": "",
            "completed_turn_id": "fixture-turn", **(state or {}),
        },
    }
    reads = []

    def respond(route):
        nonlocal history_failures
        reads.append(route.request.url)
        if "?tail=" in route.request.url and history_failures > 0:
            history_failures -= 1
            route.fulfill(status=503, json={"detail": "fixture retry"})
            return
        route.fulfill(json=activity if route.request.url.endswith("/active") else history)

    page.route(f"**/api/chat/sessions/{SID}?*", respond)
    page.route(f"**/api/chat/sessions/{SID}/active", respond)
    _app_eval(page, """
        const sid = arg;
        app.refreshSessions = async () => {};
        app._syncSessionListQuiet = async () => {};
        app._fetchTabUsage = async () => {};
        app._scheduleIdlePreload = () => {};
        app._checkActiveTurn = () => {};
        app._syncQueueFromServer = async () => {};
        app._drainPendingQueue = async () => {};
        app.sessions = [{id: sid, name: 'Completion fixture', message_count: 2}];
        app.openTabIds = [sid];
        app.tabState = {};
        app.currentId = sid;
        app.mobileTab = 'chat';
        const st = app._ensureTabState(sid);
        st._loaded = true;
        st.atBottom = true;
        st.messages.push(
          {role: 'user', text: 'FIXTURE_PROMPT', uuid: 'fixture-user',
           _turnRoot: true, _k: `${sid}:uuid:fixture-user`},
          {role: 'assistant', text: 'LIVE_PARTIAL', _k: `${sid}:live:partial`},
        );
        Object.assign(st.messageRange, {
          visibleStart: 0, visibleEnd: 2, offset: 0, total: 2,
          preTotal: 0, order: 'normal', generation: 'fixture-live',
        });
        app._activateTabState(sid);
        window.visibilityRequests = [];
        window.realVisibilityRequest = app._requestSessionSync.bind(app);
        app._requestSessionSync = (id, reason, options) => {
          window.visibilityRequests.push({id, reason, options});
          return Promise.resolve(false);
        };
        window.visibilityScrolls = [];
        const originalLoad = app.loadSession.bind(app);
        app.loadSession = async (id, options) => {
          window.visibilityScrolls.push(options.followTail === true);
          return originalLoad(id, options);
        };
    """, SID)
    return errors, history, reads


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("expected_uuid,expected_text", [
    ("", "LIVE_PARTIAL"), ("", ""),
])
def test_completed_identity_recovers_incomplete_live_reply(
    page, backend_url, auth_token, width, expected_uuid, expected_text,
):
    page.set_viewport_size({"width": width, "height": 900})
    errors, _, reads = _prepare(page, backend_url, auth_token)
    result = _app_eval(page, """
        const st = app.tabState[arg.sid];
        st._userScrollAt = 12;
        const options = {
          expectedText: arg.text, expectedAssistantUuid: arg.uuid,
          completedTurnId: 'fixture-turn', followTail: true,
          followTailUserScrollAt: 11,
        };
        st._pendingCompletedTurnSync = options;
        const loaded = await app._runCompletedTurnSync(arg.sid, st, options);
        await new Promise(resolve => app.$nextTick(resolve));
        return {loaded, pending: st._pendingCompletedTurnSync,
          text: st.messages.at(-1).text, ready: st.messagesReady,
          loading: st.messagesLoading, requests: window.visibilityRequests.length,
          followTail: window.visibilityScrolls};
    """, {"sid": SID, "uuid": expected_uuid, "text": expected_text})
    assert result == {
        "loaded": True, "pending": None, "text": FINAL, "ready": True,
        "loading": False, "requests": 0, "followTail": [False],
    }
    assert len(reads) == 1
    expect(page.locator(f'.msg-pane[data-tid="{SID}"]')).to_contain_text(FINAL)
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("state,expected_uuid", [
    ({"stable": False}, ""),
    ({"active": True, "turn_id": "fixture-turn"}, ""),
    ({"active": True, "turn_id": "successor-turn"}, ""),
    ({"completed_turn_id": ""}, ""),
    ({}, "missing-live-uuid"),
])
def test_incomplete_live_reply_still_requires_stable_idle_commit(
    page, backend_url, auth_token, state, expected_uuid,
):
    errors, _, _ = _prepare(page, backend_url, auth_token, state=state)
    result = _app_eval(page, """
        const st = app.tabState[arg.sid];
        const loaded = await app._runCompletedTurnSync(arg.sid, st, {
          expectedText: 'LIVE_PARTIAL', completedTurnId: 'fixture-turn',
          expectedAssistantUuid: arg.uuid,
        });
        return {loaded, text: st.messages.at(-1).text,
          reasons: window.visibilityRequests.map(r => r.reason)};
    """, {"sid": SID, "uuid": expected_uuid})
    assert result == {"loaded": False, "text": "LIVE_PARTIAL", "reasons": ["completed_turn"]}
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("width", [1440, 390])
def test_replay_gap_loads_final_while_detached_background_is_active(
    page, backend_url, auth_token, width,
):
    page.set_viewport_size({"width": width, "height": 900})
    errors, _, reads = _prepare(page, backend_url, auth_token, activity={
        "active": True, "background": True, "attachable": False,
        "turn_id": "fixture-turn", "background_tasks_pending": 1,
    })
    result = _app_eval(page, """
        const st = app.tabState[arg];
        st._canonicalResyncPending = true;
        st.sessionSync.canonicalStartedAt = Date.now() - 5000;
        st.activeTurnId = 'fixture-turn';
        st.streaming = true;
        let closed = 0;
        st.es = {close: () => {closed += 1;}};
        const loaded = await app._runCanonicalReplaySync(arg, st);
        await new Promise(resolve => app.$nextTick(resolve));
        return {loaded, pending: st._canonicalResyncPending,
          text: st.messages.at(-1).text, streaming: st.streaming, closed,
          ready: st.messagesReady, loading: st.messagesLoading,
          background: st.backgroundActive,
          retries: window.visibilityRequests.filter(r => r.reason === 'canonical_replay').length};
    """, SID)
    assert result == {
        "loaded": True, "pending": False, "text": FINAL, "streaming": False,
        "closed": 1, "ready": True, "loading": False, "background": True,
        "retries": 0,
    }
    assert any("?tail=" in url for url in reads)
    expect(page.locator(f'.msg-pane[data-tid="{SID}"]')).to_contain_text(FINAL)
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("case", [
    "foreground", "attachable", "successor", "admission",
    "successor_during_probe", "stream_during_probe",
])
def test_replay_recovery_preserves_foreground_owner(
    page, backend_url, auth_token, case,
):
    errors, _, reads = _prepare(page, backend_url, auth_token, activity={
        "active": True, "background": case != "foreground",
        "attachable": case == "attachable", "turn_id": "fixture-turn",
    })
    result = _app_eval(page, """
        const st = app.tabState[arg.sid];
        st._canonicalResyncPending = true;
        st.sessionSync.canonicalStartedAt = Date.now() - 5000;
        st.activeTurnId = arg.testCase === 'successor' ? 'successor-turn' : 'fixture-turn';
        st._composerSubmitToken = arg.testCase === 'admission' ? 'new-submit' : null;
        st.streaming = true;
        let closed = 0;
        st.es = {close: () => {closed += 1;}};
        const originalFetch = app._fetchWithDeadline.bind(app);
        app._fetchWithDeadline = async (...args) => {
          const response = await originalFetch(...args);
          if (arg.testCase === 'successor_during_probe') {
            st.activeTurnId = 'successor-turn';
          }
          if (arg.testCase === 'stream_during_probe') {
            st.es = {close: () => {closed += 1;}};
          }
          return response;
        };
        await app._runCanonicalReplaySync(arg.sid, st);
        return {text: st.messages.at(-1).text, streaming: st.streaming, closed,
          pending: st._canonicalResyncPending,
          retries: window.visibilityRequests.filter(r => r.reason === 'canonical_replay').length};
    """, {"sid": SID, "testCase": case})
    assert result == {
        "text": "LIVE_PARTIAL", "streaming": True, "closed": 0,
        "pending": True, "retries": 1,
    }
    assert len(reads) == 1 and reads[0].endswith("/active")
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("mode", ["completed", "replay", "replay_retry"])
def test_final_visibility_converges_through_real_session_scheduler(
    page, backend_url, auth_token, mode,
):
    errors, _, reads = _prepare(page, backend_url, auth_token, activity={
        "active": True, "background": True, "attachable": False,
        "turn_id": "fixture-turn", "background_tasks_pending": 1,
    }, history_failures=int(mode == "replay_retry"))
    _app_eval(page, """
        app._requestSessionSync = window.realVisibilityRequest;
        const st = app.tabState[arg.sid];
        if (arg.mode === 'completed') {
          app._reconcileCompletedTurn(
            arg.sid, st, 'LIVE_PARTIAL', 0, '', 'fixture-turn');
        } else {
          st.activeTurnId = 'fixture-turn';
          st.streaming = true;
          st.es = {close: () => {}};
          app._scheduleCanonicalStreamReload(arg.sid, st);
        }
    """, {"sid": SID, "mode": mode})
    pane = page.locator(f'.msg-pane[data-tid="{SID}"]')
    expect(pane).to_contain_text(FINAL, timeout=10000)
    expect(pane).to_contain_text("FIXTURE_PROMPT")
    result = _app_eval(page, """
        const st = app.tabState[arg];
        app._stopBgContPoller(arg);
        return {streaming: st.streaming, pending: st._canonicalResyncPending,
          completedPending: st._pendingCompletedTurnSync || null,
          ready: st.messagesReady, loading: st.messagesLoading};
    """, SID)
    assert result == {
        "streaming": False, "pending": False, "completedPending": None,
        "ready": True, "loading": False,
    }
    assert sum("?tail=" in url for url in reads) == (2 if mode == "replay_retry" else 1)
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("known_uuid", [True, False])
def test_completed_partial_viewport_adopts_final_without_refresh(
    page, backend_url, auth_token, width, known_uuid,
):
    """A verified completion keeps the scrolled live node as it acquires a UUID."""
    page.set_viewport_size({"width": width, "height": 900})
    errors, _, _ = _prepare(page, backend_url, auth_token)
    result = _app_eval(page, """
        const st = app.tabState[arg.sid];
        st.atBottom = false;
        st.messageRange.visibleStart = 1;
        st.messageRange.visibleEnd = 2;
        st._userScrollAt = 10;
        const live = st.messages.at(-1);
        const loaded = await app._runCompletedTurnSync(arg.sid, st, {
          expectedText: 'LIVE_PARTIAL',
          expectedAssistantUuid: arg.known ? 'fixture-final' : '',
          completedTurnId: 'fixture-turn', followTail: false,
        });
        return {loaded, text: st.messages.at(-1).text,
          sameNodeOwner: st.messages.at(-1) === live,
          key: st.messages.at(-1)._k, atBottom: st.atBottom,
          pending: st._pendingCompletedTurnSync,
          followTail: window.visibilityScrolls};
    """, {"sid": SID, "known": known_uuid})
    assert result == {
        "loaded": True, "text": FINAL, "sameNodeOwner": True,
        "key": f"{SID}:live:partial", "atBottom": False,
        "pending": None, "followTail": [False],
    }
    expect(page.locator(f'.msg-pane[data-tid="{SID}"]')).to_contain_text(FINAL)
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("same_turn", [True, False])
def test_lost_done_viewport_uses_only_retired_turn_commit(
    page, backend_url, auth_token, same_turn,
):
    """List recovery gets the same mapping only for its exact retired stream."""
    errors, _, _ = _prepare(page, backend_url, auth_token)
    result = _app_eval(page, """
        const st = app.tabState[arg.sid];
        st.atBottom = false;
        st.messageRange.visibleStart = 1;
        st.messageRange.visibleEnd = 2;
        st.activeTurnId = arg.same ? 'fixture-turn' : 'another-turn';
        st.streaming = true;
        const live = st.messages.at(-1);
        app._retireStaleSessionStream(arg.sid, st);
        const loaded = await app._runHistoryRevisionSync(arg.sid, st);
        return {loaded, text:st.messages.at(-1).text,
          sameOwner:st.messages.at(-1) === live, atBottom:st.atBottom};
    """, {"sid": SID, "same": same_turn})
    assert result == {
        "loaded": same_turn, "text": FINAL if same_turn else "LIVE_PARTIAL",
        "sameOwner": True, "atBottom": False,
    }
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("anchor_kind", ["interior", "transient_only"])
@pytest.mark.parametrize("background", [False, True])
def test_completion_recovers_removed_range_edges(
    page, backend_url, auth_token, anchor_kind, background, width,
):
    page.set_viewport_size({"width": width, "height": 900})
    errors, _, reads = _prepare(page, backend_url, auth_token)
    result = _app_eval(page, """
        const st = app.tabState[arg.sid];
        st.atBottom = false;
        const prompt = st.messages[0];
        const partial = st.messages[1];
        const ephemeral = n => ({role:'assistant', text:'transient ' + n,
            _k:arg.sid + ':live:transient-' + n});
        st.messages = arg.kind === 'interior'
          ? [ephemeral(1), prompt, ephemeral(2), partial]
          : [prompt, ephemeral(1), partial];
        st.messageRange.visibleStart = arg.kind === 'interior' ? 0 : 1;
        st.messageRange.visibleEnd = arg.kind === 'interior' ? 3 : 2;
        st.messageRange.total = st.messages.length;
        st._userScrollAt = 10;
        if (arg.background) app.currentId = 'other-tab';
        const loaded = await app._runCompletedTurnSync(arg.sid, st, {
          expectedText:'LIVE_PARTIAL', expectedAssistantUuid:'fixture-final',
          completedTurnId:'fixture-turn', followTail:false,
        });
        const first = {loaded, final:st.messages.at(-1).text,
          atBottom:st.atBottom, pending:!!st._pendingCompletedTurnSync};
        if (arg.background) {app.currentId = arg.sid; app._activateTabState(arg.sid);}
        return first;
    """, {"sid": SID, "kind": anchor_kind, "background": background})
    assert result == {"loaded": True, "final": FINAL,
                      "atBottom": False, "pending": False}
    expect(page.locator(f'.msg-pane[data-tid="{SID}"]')).to_contain_text(FINAL)
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("boundary", ["partial", "full_order", "unrelated", "active"])
def test_missing_reader_anchor_does_not_authorize_unrelated_replacement(
    page, backend_url, auth_token, boundary,
):
    errors, history, _ = _prepare(page, backend_url, auth_token)
    if boundary == "partial":
        history.update(offset=20, total=22, has_more=True)
    elif boundary == "active":
        history["completion_state"]["active"] = True
    elif boundary == "unrelated":
        history["completion_state"]["completed_turn_id"] = "another-turn"
    result = _app_eval(page, """
        const st = app.tabState[arg.sid];
        st.activeTurnId = 'fixture-turn';
        st.atBottom = false;
        st.messages.splice(1, 0, {role:'assistant', text:'reader anchor',
          _k:arg.sid + ':live:reader'});
        st.messageRange.visibleStart = 1;
        st.messageRange.visibleEnd = 2;
        st.messageRange.total = 3;
        if (arg.boundary === 'full_order') st.messageRange.order = 'full';
        const anchor = st.messages[1];
        const loaded = await app.loadSession(arg.sid, {quiet:true, probeActive:false});
        return {loaded, preserved:st.messages[1] === anchor, atBottom:st.atBottom};
    """, {"sid": SID, "boundary": boundary})
    assert result == {"loaded": False, "preserved": True, "atBottom": False}
    _assert_no_browser_errors(page, errors)


def test_removed_anchor_recovery_settles_real_scheduler(page, backend_url, auth_token):
    errors, _, reads = _prepare(page, backend_url, auth_token)
    _app_eval(page, """
        const st = app.tabState[arg];
        st.atBottom = false;
        st.messages.splice(1, 0, {role:'assistant', text:'transient reader block',
          _k:arg + ':live:reader'});
        Object.assign(st.messageRange, {visibleStart:1, visibleEnd:2, total:3});
        app._requestSessionSync = window.realVisibilityRequest;
        app._reconcileCompletedTurn(arg, st, 'LIVE_PARTIAL', 0,
          'fixture-final', 'fixture-turn', {followTail:false});
    """, SID)
    page.wait_for_function("""sid => {
        const st = document.querySelector('#app')._x_dataStack[0].tabState[sid];
        return !st._pendingCompletedTurnSync && st._installedCanonicalCount === 2;
    }""", arg=SID)
    result = _app_eval(page, """
        const st = app.tabState[arg];
        return {atBottom:st.atBottom, text:st.messages.at(-1).text};
    """, SID)
    assert result == {"atBottom": False, "text": FINAL}
    expect(page.locator(f'.msg-pane[data-tid="{SID}"]')).to_contain_text(FINAL)
    assert sum('?tail=' in url for url in reads) == 1
    _assert_no_browser_errors(page, errors)
