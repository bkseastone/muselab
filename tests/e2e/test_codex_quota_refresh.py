"""Quota failure/recovery UX, using synthetic account responses only."""

from playwright.sync_api import expect

from .test_chat_render_perf import _app_eval, _login, _capture_browser_errors, _assert_no_browser_errors


def test_quota_refresh_waits_reports_auth_history_and_recovers(page, backend_url, auth_token, tmp_path):
    errors = _capture_browser_errors(page)
    pending = []
    page.route("**/api/chat/codex-rate-limit*", lambda route: pending.append(route))
    _login(page, backend_url, auth_token)
    _app_eval(page, "app.lang='zh'; await app.openSettings();")
    page.get_by_role("button", name="用量看板", exact=True).click()
    page.wait_for_function("() => document.querySelector('#app')._x_dataStack[0].codexLimitLoading")
    section = page.locator('.settings-section[data-page="cost"]')
    refresh = section.locator('.settings-section-title button')
    status = section.locator('.codex-quota-status')
    expect(refresh).to_be_disabled()
    expect(status).to_contain_text("正在刷新")
    page.wait_for_timeout(100)
    _app_eval(page, "app.loadCostDashboard(true);")
    assert len(pending) == 1
    pending.pop(0).fulfill(json={
        "ok": True, "source": "codex-session-log", "updated_at": 100,
        "stale": True, "refresh": {"ok": False, "reason": "codex_auth_required"},
        "windows": {"primary": {"rate_limit_type": "five_hour", "remaining_percent": 80, "used_percent": 20}},
    })
    expect(status).to_contain_text("codex login")
    expect(section.locator('.codex-quota-stale')).to_be_visible()
    expect(section.locator('.codex-quota-source-row')).to_contain_text("Codex 会话历史")
    expect(section.locator('.codex-quota-metric-label')).to_have_text("当时剩余")
    expect(refresh).to_be_enabled()
    refresh.click()
    expect(refresh).to_be_disabled()
    expect(status).to_contain_text("正在刷新")
    page.wait_for_timeout(100)
    assert len(pending) == 1
    pending.pop(0).fulfill(json={
        "ok": True, "source": "codex-app-server", "updated_at": 200, "stale": False,
        "windows": {"primary": {"rate_limit_type": "five_hour", "remaining_percent": 60, "used_percent": 40}},
    })
    expect(refresh).to_be_enabled()
    expect(status).not_to_be_visible()
    expect(section.locator('.codex-quota-stale')).not_to_be_visible()
    expect(section.locator('.codex-quota-value')).to_have_text("60%")
    expect(section.locator('.codex-quota-metric-label')).to_have_text("剩余")
    refresh.click()
    expect(status).to_contain_text("正在刷新")
    page.wait_for_timeout(100)
    pending.pop(0).fulfill(json={
        "ok": True, "source": "codex-session-log", "updated_at": 100,
        "stale": True, "refresh": {"ok": False, "reason": "codex_auth_required"},
        "windows": {"primary": {"rate_limit_type": "five_hour", "remaining_percent": 80, "used_percent": 20}},
    })
    expect(refresh).to_be_enabled()
    expect(status).to_contain_text("codex login")
    expect(section.locator('.codex-quota-value')).to_have_text("60%")
    assert _app_eval(page, "return app.codexLimit.updated_at;") == 200
    refresh.click()
    expect(status).to_contain_text("正在刷新")
    page.wait_for_timeout(100)
    pending.pop(0).fulfill(status=503, body="unavailable")
    expect(refresh).to_be_enabled()
    expect(status).to_contain_text("刷新失败")
    expect(section.locator('.codex-quota-stale')).to_be_visible()
    expect(section.locator('.codex-quota-value')).to_have_text("60%")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(status).to_be_visible()
    assert section.evaluate("el => el.scrollWidth <= el.clientWidth")
    page.screenshot(path=str(tmp_path / 'quota-mobile.png'), full_page=True)
    _assert_no_browser_errors(page, errors)
