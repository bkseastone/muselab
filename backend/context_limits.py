"""Settings-managed effective context budgets; these do not expand a runtime."""
from __future__ import annotations

import json
import os

from . import endpoints

ENV_KEY = "MUSELAB_CONTEXT_LIMITS"
MIN_LIMIT = 1024
MAX_LIMIT = 10_000_000


def configured_limits() -> dict:
    try:
        data = json.loads(os.environ.get(ENV_KEY, "{}"))
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    result = {"providers": {}, "models": {}}
    for scope in result:
        values = data.get(scope)
        if isinstance(values, dict):
            result[scope] = {key: value for key, value in values.items()
                             if isinstance(key, str) and len(key) <= 256
                             and type(value) is int and MIN_LIMIT <= value <= MAX_LIMIT}
    return result


def provider_groups() -> list[dict]:
    return [
        {"id": "anthropic", "display": "Anthropic", "models": endpoints.anthropic_models()},
        {"id": "ducc", "display": "DUCC", "models": [
            endpoints.DUCC_PREFIX + model for model, _cli, _label in endpoints.DUCC_MODELS]},
        *[{"id": item["id"], "display": item["display"], "models": item["models"]}
          for item in endpoints.provider_meta()],
    ]


def settings_override(model: str) -> tuple[int, str]:
    limits = configured_limits()
    if model in limits["models"]:
        return limits["models"][model], "settings_model"
    if endpoints.is_ducc_model(model):
        pid = "ducc"
    else:
        provider = endpoints.lookup(model)
        if provider is None:
            pid = "anthropic" if not endpoints.is_third_party(model) else ""
        else:
            pid = provider.id
    limit = limits["providers"].get(pid, 0)
    return limit, "settings_provider" if limit else ""
