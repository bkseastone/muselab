"""Authenticated context budget edits survive reload and respect routing."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from dotenv import dotenv_values


@pytest.fixture
def isolated(client, monkeypatch, tmp_path):
    from backend import api_settings, endpoints
    monkeypatch.setattr(api_settings, "ENV_PATH", tmp_path / "settings.env")
    monkeypatch.setattr(endpoints, "OVERRIDES_PATH", tmp_path / "providers.json")
    monkeypatch.setenv("MUSELAB_CONTEXT_LIMITS", "{}")
    monkeypatch.delenv("MUSELAB_THIRD_PARTY_CONTEXT_LIMIT", raising=False)
    monkeypatch.delenv("MUSELAB_CONTEXT_LIMIT_DUCC_GLM_5", raising=False)
    return api_settings


def test_provider_model_precedence_and_durable_auto_reset(client, auth, isolated, monkeypatch):
    from backend.chat import _context_limit_details
    from backend.context_limits import configured_limits

    def put(scope, key, tokens):
        r = client.put("/api/settings/context-limits", headers=auth,
                       json={"scope": scope, "key": key, "tokens": tokens})
        assert r.status_code == 200, r.text

    model = "ducc:glm-5"
    assert _context_limit_details(model)["context_limit"] == 128000
    assert _context_limit_details(model, sdk_max=200000)["context_limit"] == 200000
    put("providers", "ducc", 500000)
    assert _context_limit_details(model, sdk_max=200000)["context_limit"] == 500000
    assert _context_limit_details(model)["context_limit_source"] == "settings_provider"
    put("models", model, 300000)
    assert _context_limit_details(model)["context_limit"] == 300000
    assert _context_limit_details("ducc:glm-5-1")["context_limit"] == 500000
    assert _context_limit_details("glm-5")["context_limit"] == 200000
    monkeypatch.setenv("MUSELAB_CONTEXT_LIMIT_DUCC_GLM_5", "150000")
    assert _context_limit_details(model)["context_limit"] == 150000
    monkeypatch.delenv("MUSELAB_CONTEXT_LIMIT_DUCC_GLM_5")
    saved = dotenv_values(isolated.ENV_PATH)["MUSELAB_CONTEXT_LIMITS"]
    monkeypatch.setenv("MUSELAB_CONTEXT_LIMITS", saved)
    assert configured_limits()["models"][model] == 300000
    put("models", model, None)
    assert _context_limit_details(model)["context_limit"] == 500000
    put("providers", "ducc", None)
    assert _context_limit_details(model, sdk_max=200000)["context_limit"] == 200000


@pytest.mark.parametrize("tokens", [0, -1, 1023, 10000001, 200000.5, True, "200000"])
def test_invalid_values_never_persist(client, auth, isolated, tokens):
    r = client.put("/api/settings/context-limits", headers=auth,
                   json={"scope": "providers", "key": "ducc", "tokens": tokens})
    assert r.status_code == 422
    assert not isolated.ENV_PATH.exists()


def test_auth_and_unknown_route(client, auth, isolated):
    body = {"scope": "models", "key": "unknown:model", "tokens": 200000}
    assert client.put("/api/settings/context-limits", json=body).status_code == 401
    assert client.put("/api/settings/context-limits", json=body, headers=auth).status_code == 422


def test_concurrent_partial_edits_preserve_each_other(client, auth, isolated):
    from backend.context_limits import configured_limits
    keys = ["ducc", "anthropic", "b:deepseek-"]
    def edit(key):
        return client.put("/api/settings/context-limits", headers=auth,
                          json={"scope": "providers", "key": key, "tokens": 300000})
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(edit, keys))
    assert all(r.status_code == 200 for r in responses)
    assert configured_limits()["providers"] == dict.fromkeys(keys, 300000)
    assert json.loads(dotenv_values(isolated.ENV_PATH)["MUSELAB_CONTEXT_LIMITS"])["providers"] == dict.fromkeys(keys, 300000)


def test_context_key_cannot_interpolate_environment_on_restart(client, auth, isolated):
    from backend import endpoints
    unsafe = "claude-${SYNTHETIC_SECRET}"
    endpoints.set_anthropic_models([unsafe])
    r = client.put("/api/settings/context-limits", headers=auth,
                   json={"scope": "models", "key": unsafe, "tokens": 200000})
    assert r.status_code == 422
    assert not isolated.ENV_PATH.exists()


@pytest.mark.parametrize("sdk_window,expected", [(0, 128000), (200000, 200000)])
def test_reset_auto_does_not_reuse_cached_settings_budget(client, auth, isolated, sdk_window, expected):
    from backend import chat
    sid = "context-settings-reset-fixture"
    chat._session_usage[sid] = {
        "context_limit": 350000, "context_limit_source": "settings_provider",
        "context_used": 10000, "sdk_context_max_tokens": sdk_window,
    }
    response = client.get(f"/api/chat/usage/{sid}?model=ducc:glm-5", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["context_limit"] == expected
    assert response.json()["context_limit_source"] != "settings_provider"
