"""Workspace index subtree-ignore list (exact + prefix, env-extensible) tests."""

from __future__ import annotations


def test_is_ignored_name_matches_exact_and_prefix(app_module, monkeypatch):
    from backend import workspace_store as ws

    monkeypatch.setattr(ws, "_IGNORED_SUBTREE_PREFIXES", frozenset({"snapshot."}))

    # Exact matches from the built-in list still prune.
    assert ws._is_ignored_name("node_modules")
    assert ws._is_ignored_name(".git")
    # Prefix matches prune timestamped snapshot families.
    assert ws._is_ignored_name("snapshot.20260720")
    assert ws._is_ignored_name("snapshot.2026")
    # A prefix must not swallow the bare name or unrelated siblings.
    assert not ws._is_ignored_name("snapshot")
    assert not ws._is_ignored_name("snapshots")
    assert not ws._is_ignored_name("src")


def test_ignored_descendant_prunes_prefix_dirs(app_module, monkeypatch):
    from backend import workspace_store as ws

    monkeypatch.setattr(ws, "_IGNORED_SUBTREE_PREFIXES", frozenset({"backup."}))

    assert ws.is_ignored_descendant("backup.20260101/file.txt")
    assert ws.is_ignored_descendant("a/backup.20260101/file.txt")
    assert not ws.is_ignored_descendant("a/backup/file.txt")


def test_env_subtree_names_parses_csv(app_module, monkeypatch):
    from backend import workspace_store as ws

    monkeypatch.setenv("MUSELAB_IGNORED_SUBTREES", " bigdata , caches ,, snap ")
    assert ws._env_subtree_names("MUSELAB_IGNORED_SUBTREES") == frozenset(
        {"bigdata", "caches", "snap"}
    )
    monkeypatch.delenv("MUSELAB_IGNORED_SUBTREES")
    assert ws._env_subtree_names("MUSELAB_IGNORED_SUBTREES") == frozenset()


def test_env_extends_ignored_subtrees(app_module, monkeypatch):
    import importlib

    from backend import workspace_store as ws

    monkeypatch.setenv("MUSELAB_IGNORED_SUBTREES", "bigdata")
    monkeypatch.setenv("MUSELAB_IGNORED_SUBTREE_PREFIXES", "snap.")
    importlib.reload(ws)
    try:
        assert "bigdata" in ws._IGNORED_SUBTREES
        assert ws._is_ignored_name("snap.20260720")
    finally:
        monkeypatch.delenv("MUSELAB_IGNORED_SUBTREES", raising=False)
        monkeypatch.delenv("MUSELAB_IGNORED_SUBTREE_PREFIXES", raising=False)
        importlib.reload(ws)
