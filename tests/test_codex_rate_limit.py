import importlib.util
import json
from pathlib import Path

import pytest


def _quota_script_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "codex-quota-refresh.py"
    spec = importlib.util.spec_from_file_location("muselab_codex_quota_refresh", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_app_server_account_snapshot_normalizes_multiple_limit_buckets_and_usage():
    quota = _quota_script_module()
    result = quota._normalize_account_snapshot(
        {
            "rateLimits": {
                "limitId": "codex",
                "planType": "pro",
                "primary": {
                    "usedPercent": 46,
                    "windowDurationMins": 300,
                    "resetsAt": 1782565701,
                },
                "secondary": {
                    "usedPercent": 7,
                    "windowDurationMins": 10080,
                    "resetsAt": 1783152501,
                },
            },
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "planType": "pro",
                    "primary": {
                        "usedPercent": 46,
                        "windowDurationMins": 300,
                        "resetsAt": 1782565701,
                    },
                    "secondary": {
                        "usedPercent": 7,
                        "windowDurationMins": 10080,
                        "resetsAt": 1783152501,
                    },
                },
                "codex_spark": {
                    "limitId": "codex_spark",
                    "limitName": "Codex Spark",
                    "planType": "pro",
                    "primary": {
                        "usedPercent": 2,
                        "windowDurationMins": 10080,
                        "resetsAt": 1783152501,
                    },
                },
            },
            "rateLimitResetCredits": {"availableCount": 2},
        },
        {
            "summary": {"lifetimeTokens": 123456, "peakDailyTokens": 9000},
            "dailyUsageBuckets": [
                {"startDate": "2026-07-15", "tokens": 321},
                {"startDate": "2026-07-14", "tokens": 123},
            ],
        },
    )

    assert result["ok"] is True
    assert result["source"] == "codex-app-server"
    assert result["provider_authoritative"] is False
    assert result["account_authoritative"] is True
    assert result["windows"]["primary"]["rate_limit_type"] == "five_hour"
    assert result["windows"]["secondary"]["rate_limit_type"] == "seven_day"
    assert result["windows"]["codex_spark:primary"]["limit_name"] == "Codex Spark"
    assert result["rate_limit_reset_credits"]["available_count"] == 2
    assert result["account_usage"]["summary"]["lifetime_tokens"] == 123456
    assert result["account_usage"]["daily_usage_buckets"][0]["tokens"] == 123
    assert result["account_usage"]["daily_usage_buckets"][-1]["tokens"] == 321


def test_codex_rate_limit_reads_local_session_log(client, auth, tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions" / "2026" / "06" / "27"
    sessions.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    event = {
        "timestamp": "2026-06-27T11:08:30.585Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": None,
            "rate_limits": {
                "limit_id": "codex",
                "plan_type": "plus",
                "primary": {
                    "used_percent": 46.0,
                    "window_minutes": 300,
                    "resets_at": 1782565701,
                },
                "secondary": {
                    "used_percent": 7.0,
                    "window_minutes": 43200,
                    "resets_at": 1783152501,
                },
                "credits": None,
                "individual_limit": None,
                "rate_limit_reached_type": None,
            },
        },
    }
    (sessions / "rollout.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    r = client.get("/api/chat/codex-rate-limit", headers=auth)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["plan_type"] == "plus"
    assert d["provider_authoritative"] is False
    assert d["source_scope"] == "codex_cli_session_log"
    assert d["windows"]["primary"]["rate_limit_type"] == "five_hour"
    assert d["windows"]["primary"]["remaining_percent"] == 54.0
    assert d["windows"]["secondary"]["rate_limit_type"] == "monthly"
    assert d["windows"]["secondary"]["remaining_percent"] == 93.0


def test_codex_rate_limit_labels_weekly_window(client, auth, tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions" / "2026" / "06" / "30"
    sessions.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    event = {
        "timestamp": "2026-06-30T06:24:59.760Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "plan_type": "prolite",
                "primary": {
                    "used_percent": 1.0,
                    "window_minutes": 300,
                    "resets_at": 1782809110,
                },
                "secondary": {
                    "used_percent": 0.0,
                    "window_minutes": 10080,
                    "resets_at": 1783395910,
                },
                "rate_limit_reached_type": None,
            },
        },
    }
    (sessions / "rollout.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    r = client.get("/api/chat/codex-rate-limit", headers=auth)
    assert r.status_code == 200
    d = r.json()
    assert d["windows"]["primary"]["rate_limit_type"] == "five_hour"
    assert d["windows"]["secondary"]["rate_limit_type"] == "seven_day"


def test_codex_rate_limit_refresh_uses_app_server_bridge(client, auth, monkeypatch):
    from backend import chat as chat_mod

    payload = {
        "ok": True,
        "source": "codex-app-server",
        "source_scope": "codex_cli_exec_rate_limits",
        "provider_authoritative": False,
        "updated_at": 1782802582.322,
        "windows": {
            "primary": {
                "rate_limit_type": "five_hour",
                "used_percent": 8.0,
                "remaining_percent": 92.0,
            },
        },
        "elapsed_s": 3.8,
    }
    monkeypatch.setattr(chat_mod, "_refresh_codex_rate_limits", lambda: payload)

    r = client.get("/api/chat/codex-rate-limit?refresh=1", headers=auth)
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "codex-app-server"
    assert d["elapsed_s"] == 3.8
    assert d["windows"]["primary"]["remaining_percent"] == 92.0


def _fake_codex(tmp_path, monkeypatch, error=None, usage_error=None):
    """Exercise a real subprocess handshake without accounts or model traffic."""
    import sys

    script = tmp_path / "codex"
    requests = tmp_path / "requests.jsonl"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"error = {error!r}\nusage_error = {usage_error!r}\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        f"    with open({str(requests)!r}, 'a') as out: out.write(line)\n"
        "    method = request['method']\n"
        "    if method == 'initialized': continue\n"
        "    response = {'id': request['id']}\n"
        "    if method == 'initialize': response['result'] = {}\n"
        "    elif error: response['error'] = error\n"
        "    elif method == 'account/rateLimits/read':\n"
        "        response['result'] = {'rateLimits': {'primary': {'usedPercent': 17, 'windowDurationMins': 300}}}\n"
        "    elif method == 'account/usage/read':\n"
        "        if usage_error: response['error'] = usage_error\n"
        "        else: response['result'] = {'summary': {'lifetimeTokens': 123}}\n"
        "    else: raise AssertionError('unexpected account or model mutation')\n"
        "    print(json.dumps(response), flush=True)\n"
    )
    script.chmod(0o700)
    monkeypatch.setenv("CODEX_BIN", str(script))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "account"))
    return requests


def test_quota_bridge_reads_account_without_a_model_turn(tmp_path, monkeypatch):
    requests = _fake_codex(tmp_path, monkeypatch)
    result = _quota_script_module()._read_app_server(5)
    assert result["ok"] is True
    assert result["stale"] is False
    assert result["windows"]["primary"]["remaining_percent"] == 83
    assert [json.loads(line)["method"] for line in requests.read_text().splitlines()] == [
        "initialize", "initialized", "account/rateLimits/read", "account/usage/read",
    ]


def test_quota_script_does_not_disguise_auth_failure_as_history_success(tmp_path, monkeypatch, capsys):
    import sys

    _fake_codex(tmp_path, monkeypatch, error={
        "code": -32600, "message": "codex account authentication required to read rate limits",
    })
    quota = _quota_script_module()
    monkeypatch.setattr(quota, "_latest_rate_limits", lambda *args: pytest.fail("refresh must not mask failure"))
    monkeypatch.setattr(sys, "argv", ["codex-quota-refresh.py", "--timeout", "5"])
    assert quota.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["reason"] == "codex_auth_required"


def test_quota_bridge_keeps_limits_when_optional_usage_rpc_is_unsupported(tmp_path, monkeypatch):
    _fake_codex(tmp_path, monkeypatch, usage_error={"code": -32601, "message": "method not found"})
    result = _quota_script_module()._read_app_server(5)
    assert result["ok"] is True
    assert result["windows"]["primary"]["remaining_percent"] == 83
    assert result["account_usage_error"] == "codex_account_rpc_unsupported"


def test_failed_quota_refresh_marks_history_and_keeps_original_timestamp(client, auth, monkeypatch):
    from backend import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_refresh_codex_rate_limits", lambda: {
        "ok": False, "reason": "codex_auth_required", "stderr_tail": "private diagnostic",
    })
    monkeypatch.setattr(chat_mod, "_latest_codex_rate_limits", lambda: {
        "ok": True, "source": "codex-session-log", "updated_at": 123,
        "windows": {"primary": {"remaining_percent": 80}},
    })
    result = client.get("/api/chat/codex-rate-limit?refresh=1", headers=auth).json()
    assert result["stale"] is True
    assert result["account_authoritative"] is False
    assert result["updated_at"] == 123
    assert result["refresh"] == {"ok": False, "reason": "codex_auth_required"}
    assert "private diagnostic" not in json.dumps(result)
