"""Receiving live data and navigating to it must not depend on a jump click."""

import pytest
from playwright.sync_api import expect
from tests.e2e.test_live_tail_visibility import _prepare_live_history
from tests.e2e.test_chat_render_perf import (
    _app_eval,
    _route_windowed_session,
    _capture_browser_errors,
    _assert_no_browser_errors,
)


@pytest.mark.parametrize("width", [1440, 390])
def test_scrolling_back_to_bottom_reveals_accumulated_live_messages(
    page, backend_url, auth_token, width
):
    errors = _capture_browser_errors(page)
    page.set_viewport_size({"width": width, "height": 900})
    sid = "reader-returns-without-jump"
    _prepare_live_history(page, backend_url, auth_token, sid)
    _app_eval(page, "app.input='Continue while I review';void app.send();")
    page.wait_for_function("() => window.__fakeChatStreams().length===1")
    page.evaluate("() => window.__emitSse('text',{text:'REPLY_BEFORE_READING'})")
    page.wait_for_timeout(300)
    body = page.locator(".chat-body")
    body.hover()
    page.mouse.wheel(0, -300)
    page.wait_for_timeout(450)
    assert _app_eval(page, "return app.tabState[arg].atBottom;", sid) is False
    top = body.evaluate("el=>el.scrollTop")
    page.evaluate("""() => {
      for(let i=0;i<15;i++){
        window.__emitSse('thinking',{text:'Reasoning '+i});
        window.__emitSse('text',{text:i===14?'ACCUMULATED_LATEST_REPLY':'Later reply '+i});
      }
    }""")
    page.wait_for_timeout(300)
    assert abs(body.evaluate("el=>el.scrollTop") - top) <= 2
    assert (
        _app_eval(page, "return app.tabState[arg].messages.at(-1).text;", sid)
        == "ACCUMULATED_LATEST_REPLY"
    )
    body.hover()
    page.mouse.wheel(0, 100000)
    page.wait_for_timeout(600)
    observed = _app_eval(
        page,
        """
      const s=app.tabState[arg],el=app._chatBodyElement();
      return {resident:s.messages.length,end:s.messageRange.visibleEnd,atBottom:s.atBottom,
        distance:el.scrollHeight-el.scrollTop-el.clientHeight,
        mounted:app.paneMessages(arg).some(m=>m.text==='ACCUMULATED_LATEST_REPLY')};
    """,
        sid,
    )
    if not observed["mounted"]:
        page.locator("button.jump-bottom").click()
        expect(
            page.locator(".msg.assistant", has_text="ACCUMULATED_LATEST_REPLY")
        ).to_be_in_viewport()
        observed["jump_repairs"] = True
    assert observed["mounted"] and observed["atBottom"], observed
    _assert_no_browser_errors(page, errors)


def test_pressing_bottom_scrollbar_without_moving_does_not_hide_new_reply(
    page, backend_url, auth_token
):
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "stationary-bottom-scrollbar"
    _prepare_live_history(page, backend_url, auth_token, sid)
    _app_eval(page, "app.input='Continue at bottom';void app.send();")
    page.wait_for_function("() => window.__fakeChatStreams().length===1")
    page.evaluate("() => window.__emitSse('text',{text:'BEFORE_SCROLLBAR_PRESS'})")
    page.wait_for_timeout(400)
    body = page.locator(".chat-body")
    rect = body.bounding_box()
    page.mouse.click(rect["x"] + rect["width"] - 10, rect["y"] + rect["height"] - 12)
    page.wait_for_timeout(150)
    page.evaluate("""() => {
      window.__emitSse('thinking',{text:'Next thinking block'});
      window.__emitSse('text',{text:'REPLY_AFTER_STATIONARY_PRESS'});
    }""")
    page.wait_for_timeout(400)
    observed = _app_eval(
        page,
        """
      const s=app.tabState[arg],el=app._chatBodyElement();
      return {atBottom:s.atBottom,distance:el.scrollHeight-el.scrollTop-el.clientHeight,
        mounted:app.paneMessages(arg).some(m=>m.text==='REPLY_AFTER_STATIONARY_PRESS')};
    """,
        sid,
    )
    assert observed["mounted"] and observed["atBottom"], observed
    expect(
        page.locator(".msg.assistant", has_text="REPLY_AFTER_STATIONARY_PRESS")
    ).to_be_in_viewport()


@pytest.fixture
def visible_scrollbar_page(browser_type):
    # Chromium's default headless flag hides native scrollbars entirely.
    browser = browser_type.launch(ignore_default_args=["--hide-scrollbars"])
    page = browser.new_page()
    yield page
    browser.close()


def test_real_scrollbar_drag_pauses_live_follow(visible_scrollbar_page, backend_url, auth_token):
    page = visible_scrollbar_page
    errors = _capture_browser_errors(page)
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "reader-scrollbar-drag"
    _prepare_live_history(page, backend_url, auth_token, sid)
    _app_eval(page, "app.input='Continue while I drag the scrollbar';void app.send();")
    page.wait_for_function("() => window.__fakeChatStreams().length===1")
    page.evaluate("() => window.__emitSse('text',{text:'BEFORE_REAL_SCROLLBAR_DRAG'})")
    page.wait_for_timeout(400)
    body = page.locator(".chat-body")
    geometry = body.evaluate("""el=>{
      const r=el.getBoundingClientRect();
      return {x:r.right-Math.max(6,(el.offsetWidth-el.clientWidth)/2),y:r.bottom-30,top:el.scrollTop};
    }""")
    page.mouse.move(geometry["x"], geometry["y"])
    page.mouse.down()
    page.mouse.move(geometry["x"], geometry["y"] - 120, steps=12)
    page.mouse.up()
    page.wait_for_timeout(300)
    reading_top = body.evaluate("el=>el.scrollTop")
    assert reading_top < geometry["top"] - 20, {"before": geometry["top"], "after": reading_top}
    assert _app_eval(page, "return app.tabState[arg].atBottom;", sid) is False
    page.evaluate("""()=>{
      window.__emitSse('thinking',{text:'Reasoning after scrollbar drag'});
      window.__emitSse('text',{text:'AFTER_REAL_SCROLLBAR_DRAG'});
    }""")
    page.wait_for_timeout(400)
    assert abs(body.evaluate("el=>el.scrollTop") - reading_top) <= 2
    _assert_no_browser_errors(page, errors)


@pytest.mark.parametrize("source", ["local", "history"])
def test_jump_diagnostics_distinguish_hidden_rows_from_a_history_fetch(
    page, backend_url, auth_token, source
):
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "jump-navigation-diagnostics"
    messages = _prepare_live_history(page, backend_url, auth_token, sid)
    packets = []

    def collect(route):
        payload = route.request.post_data_json
        if payload.get("phase") == "tail":
            packets.append(payload)
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    page.route("**/api/log/chat-render", collect)
    if source == "local":
        _app_eval(page, "app.input='Send before reading';void app.send();")
        page.wait_for_function("() => window.__fakeChatStreams().length===1")
        page.evaluate("() => window.__emitSse('text',{text:'INITIAL_DIAGNOSTIC_REPLY'})")
        page.wait_for_timeout(300)
    body = page.locator(".chat-body")
    body.hover()
    page.mouse.wheel(0, -300)
    page.wait_for_timeout(450)
    if source == "local":
        page.evaluate("""()=>{
          window.__emitSse('thinking',{text:'Diagnostic reasoning'});
          window.__emitSse('text',{text:'DIAGNOSTIC_LATEST_REPLY'});
        }""")
    else:
        messages.append(
            {
                "role": "assistant",
                "text": "DIAGNOSTIC_LATEST_REPLY",
                "uuid": "diagnostic-new-final",
                "block_id": "diagnostic-new-final:0:assistant",
            }
        )
        _route_windowed_session(page, sid, messages, updated_at=2)
        _app_eval(
            page, "app.sessions[0].updated_at=2;app.tabState[arg]._pendingExternalUpdate=true;", sid
        )
    page.locator("button.jump-bottom").click()
    expect(page.locator(".msg.assistant", has_text="DIAGNOSTIC_LATEST_REPLY")).to_be_in_viewport()
    for _ in range(100):
        if any(p["status"] == "ok" and p["trigger"] == "jump" for p in packets):
            break
        page.wait_for_timeout(20)
    events = [p for p in packets if p["trigger"] == "jump"]
    assert len(events) == 2, events
    before, after = events
    assert before["status"] == "start" and before["following"] == 0
    assert after["status"] == "ok" and after["following"] == 1
    assert after["visible_end"] == after["block_count"]
    assert after["history_fetch"] == int(source == "history")
    if source == "local":
        assert before["visible_end"] < before["block_count"]
    assert not any(key in after for key in ["text", "path", "url", "session_id", "token"])


@pytest.mark.parametrize("changed_direction", [False, True])
def test_delayed_scroll_events_honor_the_latest_reader_direction(
    page, backend_url, auth_token, changed_direction
):
    page.set_viewport_size({"width": 1440, "height": 900})
    sid = "delayed-native-scroll-direction"
    _prepare_live_history(page, backend_url, auth_token, sid)
    _app_eval(page, "app.input='Continue during native scrolling';void app.send();")
    page.wait_for_function("() => window.__fakeChatStreams().length===1")
    page.evaluate("() => window.__emitSse('text',{text:'BEFORE_DELAYED_SCROLL'})")
    page.wait_for_timeout(300)
    body = page.locator(".chat-body")
    body.hover()
    page.mouse.wheel(0, -600)
    page.wait_for_timeout(300)
    page.evaluate("""()=>{
      window.__emitSse('thinking',{text:'Reasoning during native scrolling'});
      window.__emitSse('text',{text:'LATEST_AFTER_DELAYED_SCROLL'});
    }""")
    page.mouse.wheel(0, 100)
    page.wait_for_timeout(200)
    if changed_direction:
        page.mouse.wheel(0, -100)
    # A native momentum/smooth-scroll event can arrive after the input event.
    # Reproduce that delivery gap without issuing a second wheel/touch intent.
    page.wait_for_timeout(550)
    body.evaluate("el=>{el.scrollTop=el.scrollHeight;}")
    page.wait_for_timeout(400)
    result = _app_eval(
        page,
        """const s=app.tabState[arg];return {atBottom:s.atBottom,
        mounted:app.paneMessages(arg).some(m=>m.text==='LATEST_AFTER_DELAYED_SCROLL')};""",
        sid,
    )
    assert result["mounted"] is (not changed_direction), result
    assert result["atBottom"] is (not changed_direction), result
