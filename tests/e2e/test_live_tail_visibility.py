"""Latest replies must remain visible across delayed history presentation work."""

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.e2e.test_chat_render_perf import (
    _app_eval,
    _bootstrap_session_for_real_load,
    _capture_browser_errors,
    _assert_no_browser_errors,
    _install_fake_event_source,
    _login,
    _make_mixed_messages,
    _route_windowed_session,
)


def _prepare_live_history(page, backend_url, auth_token, sid, *, load=True):
    _install_fake_event_source(page)
    page.route(
        "**/api/chat/stream/start",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"ticket":"tail-race-ticket"}'
        ),
    )
    page.route(
        f"**/api/chat/sessions/{sid}/active*",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"active":false}'
        ),
    )
    _login(page, backend_url, auth_token)
    messages = _make_mixed_messages(100, "TAIL_RACE_HISTORY")
    _route_windowed_session(page, sid, messages, updated_at=1)
    _bootstrap_session_for_real_load(page, sid, "Tail ownership")
    _app_eval(
        page,
        """
        app._pullSessionList = async () => false;
        app._ensureSessionRegistered = async () => true;
        app._confirmSessionBusy = async () => false;
        app.model = app.defaultModel = 'e2e-model';
        app.sessions[0].updated_at = 1;
    """,
        sid,
    )
    if load:
        _app_eval(page, "await app._ensureSessionLoaded(arg);", sid)
        page.wait_for_timeout(500)
    return messages


@pytest.mark.parametrize("gesture_age", [0, 450])
def test_late_quiet_highlight_cannot_hide_a_new_stream_reply(
    page, backend_url, auth_token, gesture_age
):
    errors = _capture_browser_errors(page)
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "late-quiet-highlight-tail"
    _prepare_live_history(page, backend_url, auth_token, sid)
    body = page.locator(".chat-body")
    body.hover()
    page.mouse.wheel(0, -500)
    page.wait_for_function(
        "sid => document.querySelector('#app')._x_dataStack[0].tabState[sid].atBottom === false",
        arg=sid,
    )
    page.wait_for_timeout(gesture_age)
    _app_eval(
        page,
        """
        const original = app.highlightCode.bind(app);
        let blocked = false;
        app.highlightCode = (...args) => {
            if (blocked) return original(...args);
            blocked = true;
            return new Promise(resolve => { window.__releaseQuietHighlight = resolve; });
        };
        return await app.loadSession(arg, {quiet:true, probeActive:false});
    """,
        sid,
    )
    page.wait_for_function("() => typeof window.__releaseQuietHighlight === 'function'")
    _app_eval(page, "app.input = 'Continue with a fresh reply'; void app.send();")
    page.wait_for_function("() => window.__fakeChatStreams().length === 1")
    page.wait_for_function(
        "sid => document.querySelector('#app')._x_dataStack[0].tabState[sid].atBottom === true",
        arg=sid,
    )
    page.evaluate("() => window.__releaseQuietHighlight()")
    page.wait_for_timeout(50)
    page.evaluate("() => window.__emitSse('text', {text:'LATEST_REPLY_MUST_APPEAR_WITHOUT_CLICK'})")
    page.wait_for_timeout(500)
    observed = _app_eval(
        page,
        """
        const st=app.tabState[arg];
        return {atBottom:st.atBottom, resident:st.messages.length,
            end:st.messageRange.visibleEnd,
            mounted:app.paneMessages(arg).some(m => m.text?.includes('LATEST_REPLY_MUST_APPEAR_WITHOUT_CLICK'))};
    """,
        sid,
    )
    # Record whether the reported manual workaround repairs a failing baseline.
    if not observed["mounted"]:
        page.locator("button.jump-bottom").click()
        expect(
            page.locator(".msg.assistant", has_text="LATEST_REPLY_MUST_APPEAR_WITHOUT_CLICK")
        ).to_be_visible()
        observed["manual_jump_repairs"] = True
    assert observed["mounted"] and observed["atBottom"], observed
    expect(
        page.locator(".msg.assistant", has_text="LATEST_REPLY_MUST_APPEAR_WITHOUT_CLICK")
    ).to_be_in_viewport()
    _assert_no_browser_errors(page, errors)


def test_initial_history_reveal_cannot_hide_live_messages_after_handoff(
    page, backend_url, auth_token
):
    errors = _capture_browser_errors(page)
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "history-reveal-live-handoff"
    _prepare_live_history(page, backend_url, auth_token, sid, load=False)
    _app_eval(
        page,
        """
        const reveal = app._revealMessagesChunked.bind(app);
        app._revealMessagesChunked = async (...args) => {
            window.__insideHistoryReveal=true;
            try { return await reveal(...args); }
            finally { window.__insideHistoryReveal=false; }
        };
        const original = app._yieldHistoryInstall.bind(app);
        let blocked=false;
        app._yieldHistoryInstall = () => {
            if (blocked || !window.__insideHistoryReveal) return original();
            blocked=true;
            return new Promise(resolve => {window.__releaseHistoryReveal=resolve;});
        };
        window.__initialHistoryLoad=app._ensureSessionLoaded(arg);
    """,
        sid,
    )
    page.wait_for_function("() => typeof window.__releaseHistoryReveal === 'function'")
    _app_eval(
        page,
        "void app.send({reconnect:true, sessionId:arg, turnId:'reveal-live-turn', startedAt:Date.now()/1000});",
        sid,
    )
    page.wait_for_function("() => window.__fakeChatStreams().length === 1")
    page.evaluate("""() => {
        for (let i=0;i<6;i++) {
            window.__emitSse('thinking',{text:'Intermediate reasoning '+i});
            window.__emitSse('text',{text:i===5?'LIVE_REPLY_DURING_HISTORY_MOUNT':'Intermediate reply '+i});
        }
    }""")
    page.wait_for_timeout(100)
    before = _app_eval(
        page,
        "const s=app.tabState[arg];return {rows:s.messages.length,end:s.messageRange.visibleEnd,atBottom:s.atBottom};",
        sid,
    )
    assert before["rows"] > 100 and before["rows"] == before["end"], before
    page.evaluate(
        "async () => {window.__releaseHistoryReveal();await window.__initialHistoryLoad;}"
    )
    page.wait_for_timeout(500)
    observed = _app_eval(
        page,
        """
        const s=app.tabState[arg];return {atBottom:s.atBottom,rows:s.messages.length,end:s.messageRange.visibleEnd,
            mounted:app.paneMessages(arg).some(m=>m.text?.includes('LIVE_REPLY_DURING_HISTORY_MOUNT'))};
    """,
        sid,
    )
    if not observed["mounted"]:
        page.locator("button.jump-bottom").click()
        expect(
            page.locator(".msg.assistant", has_text="LIVE_REPLY_DURING_HISTORY_MOUNT")
        ).to_be_visible()
        observed["manual_jump_repairs"] = True
    assert observed["mounted"] and observed["atBottom"], observed
    expect(
        page.locator(".msg.assistant", has_text="LIVE_REPLY_DURING_HISTORY_MOUNT")
    ).to_be_in_viewport()
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1440, "height": 900}, {"width": 390, "height": 844}],
    ids=["desktop", "mobile"],
)
def test_long_thinking_follows_output_and_preserves_manual_reading(
    page, backend_url, auth_token, viewport
):
    errors = _capture_browser_errors(page)
    page.set_viewport_size(viewport)
    sid = "thinking-inner-follow"
    _prepare_live_history(page, backend_url, auth_token, sid)
    _app_eval(page, "app.input='Think through the example';void app.send();")
    page.wait_for_function("() => window.__fakeChatStreams().length === 1")
    page.evaluate(
        "text => window.__emitSse('thinking',{text})",
        "THINKING_HEAD_MARKER\n" + "reasoning line\n" * 100 + "THINKING_LATEST_MARKER",
    )
    thinking = page.locator(".thinking pre", has_text="THINKING_HEAD_MARKER").last
    expect(thinking).to_be_visible()
    page.wait_for_timeout(300)
    distance = thinking.evaluate("(el)=>el.scrollHeight-el.clientHeight-el.scrollTop")
    assert distance <= 2, {"thinking_distance_from_latest": distance}
    thinking.hover()
    page.mouse.wheel(0, -120)
    page.wait_for_timeout(300)
    top = thinking.evaluate("(el)=>el.scrollTop")
    assert thinking.evaluate("(el)=>el.scrollHeight-el.clientHeight-el.scrollTop") > 50
    page.evaluate("text => window.__emitSse('thinking',{text})", "\n" + "more reasoning\n" * 15)
    page.wait_for_timeout(300)
    assert abs(thinking.evaluate("(el)=>el.scrollTop") - top) <= 2
    assert _app_eval(page, "return app.tabState[arg].atBottom;", sid) is True
    page.mouse.wheel(0, 10000)
    page.wait_for_timeout(300)
    page.evaluate(
        "text => window.__emitSse('thinking',{text})",
        "\nTHINKING_FINAL_MARKER\n" + "last reasoning\n" * 15,
    )
    page.wait_for_timeout(300)
    assert thinking.evaluate("(el)=>el.scrollHeight-el.clientHeight-el.scrollTop") <= 2
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("started_at_bottom", [True, False])
def test_quiet_highlight_preserves_a_later_reader_scroll(
    page, backend_url, auth_token, started_at_bottom
):
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "quiet-reader-scroll-owner"
    _prepare_live_history(page, backend_url, auth_token, sid)
    body = page.locator(".chat-body")
    body.hover()
    if not started_at_bottom:
        page.mouse.wheel(0, -300)
        page.wait_for_timeout(450)
    _app_eval(
        page,
        """
        const original=app.highlightCode.bind(app);
        let blocked=false;
        app.highlightCode=(...args)=>{
            if(blocked)return original(...args);
            blocked=true;
            return new Promise(resolve=>{window.__releaseReaderHighlight=resolve;});
        };
        await app.loadSession(arg,{quiet:true,probeActive:false});
    """,
        sid,
    )
    page.wait_for_function("() => typeof window.__releaseReaderHighlight === 'function'")
    body.hover()
    page.mouse.wheel(0, -300)
    page.wait_for_timeout(450)
    before = body.evaluate("(el)=>el.scrollTop")
    assert _app_eval(page, "return app.tabState[arg].atBottom;", sid) is False
    page.evaluate("() => window.__releaseReaderHighlight()")
    page.wait_for_timeout(300)
    after = body.evaluate("(el)=>el.scrollTop")
    assert abs(after - before) <= 2, {"before": before, "after": after}
    assert _app_eval(page, "return app.tabState[arg].atBottom;", sid) is False


@pytest.mark.parametrize("navigation", ["latest", "history"])
def test_pending_history_response_preserves_later_navigation(
    page, backend_url, auth_token, navigation
):
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "pending-history-navigation"
    messages = _prepare_live_history(page, backend_url, auth_token, sid)
    body = page.locator(".chat-body")
    if navigation == "latest":
        body.hover()
        page.mouse.wheel(0, -300)
        page.wait_for_timeout(450)
    pending = []
    pattern = f"**/api/chat/sessions/{sid}?*"
    page.route(pattern, lambda route: pending.append(route))
    _app_eval(
        page,
        "window.__pendingHistoryNavigation=app.loadSession(arg.sid,{quiet:true,probeActive:false,followTail:arg.follow});",
        {"sid": sid, "follow": navigation == "history"},
    )
    for _ in range(100):
        if pending:
            break
        page.wait_for_timeout(10)
    assert len(pending) == 1
    if navigation == "latest":
        page.locator("button.jump-bottom").click()
    else:
        body.hover()
        page.mouse.wheel(0, -300)
    page.wait_for_timeout(450)
    top = body.evaluate("(el)=>el.scrollTop")
    assert _app_eval(page, "return app.tabState[arg].atBottom;", sid) is (navigation == "latest")
    assert len(pending) == 1, "navigation should not start a redundant history request"
    messages.append(
        {
            "role": "assistant",
            "text": "NEW_REPLY_IN_PENDING_HISTORY",
            "uuid": "pending-history-final",
            "block_id": "pending-history-final:0:assistant",
        }
    )
    pending[0].fallback()
    page.evaluate("async () => await window.__pendingHistoryNavigation")
    page.wait_for_timeout(400)
    result = _app_eval(
        page,
        "const st=app.tabState[arg];return {atBottom:st.atBottom,hasLater:app.hasLaterMessages(arg)};",
        sid,
    )
    assert result["atBottom"] is (navigation == "latest"), result
    if navigation == "latest":
        expect(
            page.locator(".msg.assistant", has_text="NEW_REPLY_IN_PENDING_HISTORY")
        ).to_be_in_viewport()
    else:
        assert abs(body.evaluate("(el)=>el.scrollTop") - top) <= 2


def test_pending_jump_to_latest_does_not_override_a_new_reader_gesture(
    page, backend_url, auth_token
):
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "pending-latest-reader-gesture"
    messages = _prepare_live_history(page, backend_url, auth_token, sid)
    body = page.locator(".chat-body")
    body.hover()
    page.mouse.wheel(0, -300)
    page.wait_for_timeout(450)
    _route_windowed_session(page, sid, messages, updated_at=2)
    _app_eval(page, "app.sessions[0].updated_at=2;")
    pending = []
    page.route(f"**/api/chat/sessions/{sid}?*", lambda route: pending.append(route))
    page.locator("button.jump-bottom").click()
    for _ in range(100):
        if pending:
            break
        page.wait_for_timeout(10)
    assert len(pending) == 1
    body.hover()
    page.mouse.wheel(0, -200)
    page.wait_for_timeout(450)
    top = body.evaluate("(el)=>el.scrollTop")
    pending[0].fallback()
    page.wait_for_timeout(700)
    assert _app_eval(page, "return app.tabState[arg].atBottom;", sid) is False
    assert abs(body.evaluate("(el)=>el.scrollTop") - top) <= 2


@pytest.mark.parametrize("delivery", ["stream", "history_revision"])
def test_reading_history_keeps_receiving_new_messages_without_moving_reader(
    page, backend_url, auth_token, delivery
):
    errors = _capture_browser_errors(page)
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "reading-with-new-messages"
    messages = _prepare_live_history(page, backend_url, auth_token, sid)
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url) if f"/api/chat/sessions/{sid}?" in request.url else None
        ),
    )
    if delivery == "stream":
        _app_eval(page, "app.input='Keep working while I read';void app.send();")
        page.wait_for_function("() => window.__fakeChatStreams().length === 1")
        page.evaluate("() => window.__emitSse('text',{text:'REPLY_BEFORE_READER_SCROLL'})")
        page.wait_for_timeout(300)
    body = page.locator(".chat-body")
    body.hover()
    page.mouse.wheel(0, -300)
    page.wait_for_timeout(450)
    before = body.evaluate("el => el.scrollTop")
    assert _app_eval(page, "return app.tabState[arg].atBottom;", sid) is False
    marker = "RECEIVED_WHILE_READING_HISTORY"
    if delivery == "stream":
        page.evaluate(
            """marker => {
            for(let i=0;i<3;i++) {
                window.__emitSse('thinking',{text:'Further reasoning '+i});
                window.__emitSse('text',{text:i===2?marker:'Further reply '+i});
            }
        }""",
            marker,
        )
    else:
        messages.append(
            {
                "role": "assistant",
                "text": marker,
                "uuid": "reader-background-final",
                "block_id": "reader-background-final:0:assistant",
            }
        )
        _route_windowed_session(page, sid, messages, updated_at=2)
        _app_eval(
            page,
            """
            const meta=app.sessions.find(s=>s.id===arg);
            app._reconcileOpenSession([{...meta,updated_at:2,message_count:101,active:false}]);
        """,
            sid,
        )
    page.wait_for_function(
        """arg => document.querySelector('#app')._x_dataStack[0]
        .tabState[arg.sid].messages.some(m=>m.text===arg.marker)""",
        arg={"sid": sid, "marker": marker},
    )
    page.wait_for_timeout(400)
    assert abs(body.evaluate("el => el.scrollTop") - before) <= 2
    assert _app_eval(page, "return app.tabState[arg].atBottom;", sid) is False
    assert _app_eval(page, "return app.hasLaterMessages(arg);", sid) is True
    reads_before_jump = len(requests)
    page.locator("button.jump-bottom").click()
    expect(page.locator(".msg.assistant", has_text=marker)).to_be_in_viewport()
    assert len(requests) == reads_before_jump, (
        "latest data should already be received while reading"
    )
    _assert_no_browser_errors(page, errors)
