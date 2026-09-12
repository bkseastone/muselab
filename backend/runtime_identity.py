"""Bounded, read-only identity probes for a session-owned workspace."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import selectors
import subprocess
import threading
import time
from typing import Any

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()
_GIT_TIMEOUT = 2.0
_MAX_OUTPUT = 256_000


def git_read(cwd: Path, *args: str) -> tuple[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    process = None
    try:
        process = subprocess.Popen(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(cwd), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        body = bytearray()
        deadline = time.monotonic() + _GIT_TIMEOUT
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    return "", "unavailable"
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > _MAX_OUTPUT:
                    return "", "too_large"
        if process.wait(timeout=max(0.01, deadline - time.monotonic())):
            return "", "not_git"
        return body.decode("utf-8", errors="replace").rstrip("\n"), "ok"
    except (OSError, subprocess.TimeoutExpired):
        return "", "unavailable"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()


def directory_identity(cwd: Path) -> dict[str, Any]:
    resolved = cwd.resolve(strict=True)
    st = resolved.stat()
    if not resolved.is_dir() or st.st_uid != os.geteuid():
        raise ValueError("workspace is unavailable or belongs to another user")
    return {"cwd": str(resolved), "device": st.st_dev, "inode": st.st_ino}


def inspect_workspace(cwd: Path, *, refresh: bool = False) -> dict[str, Any]:
    identity = directory_identity(cwd)
    key = identity["cwd"]
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if (
            not refresh
            and cached
            and now - cached[0] < 5
            and cached[1].get("directory") == identity
        ):
            return dict(cached[1])
    root, state = git_read(cwd, "rev-parse", "--show-toplevel")
    result: dict[str, Any] = {
        "directory": identity,
        "workspace": key,
        "host": socket.gethostname(),
        "backend": "MuseLab / Claude Agent SDK",
        "git_state": state,
        "repository": None,
        "branch": None,
        "head": None,
        "is_worktree": False,
        "dirty": None,
        "changed_paths": [],
        "observed_at": time.time(),
    }
    if state == "ok":
        # A registered subdirectory must not expose other repository paths.
        head, head_state = git_read(cwd, "rev-parse", "HEAD")
        branch, _ = git_read(cwd, "symbolic-ref", "--short", "-q", "HEAD")
        git_dir, _ = git_read(cwd, "rev-parse", "--absolute-git-dir")
        common, _ = git_read(cwd, "rev-parse", "--git-common-dir")
        common_path = (cwd / common).resolve() if common else None
        status, status_state = git_read(
            cwd, "status", "--porcelain=v1", "-z", "--untracked-files=normal", "--", "."
        )
        result.update(
            repository=root if Path(root).resolve() == cwd.resolve() else None,
            branch=branch or ("detached" if head_state == "ok" else "unborn"),
            head=head or None,
            is_worktree=bool(common_path and git_dir and common_path != Path(git_dir)),
            dirty=bool(status) if status_state == "ok" else None,
            changed_paths=[entry for entry in status.split("\0") if entry][:200],
            status_complete=status_state == "ok",
        )
    with _LOCK:
        if len(_CACHE) >= 128:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (now, result)
    return dict(result)


def task_diff(cwd: Path, baseline: dict[str, Any]) -> dict[str, Any]:
    if not baseline or baseline.get("directory") != directory_identity(cwd):
        return {"available": False, "reason": "workspace_changed_or_missing_baseline"}
    if baseline.get("git_state") != "ok" or not baseline.get("head"):
        return {"available": False, "reason": "no_git_baseline"}
    patch, state = git_read(
        cwd, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", baseline["head"], "--", "."
    )
    return {
        "available": state == "ok",
        "reason": state,
        "base": baseline["head"],
        "baseline_dirty": baseline.get("dirty"),
        "scope": "workspace_since_base"
        if baseline.get("dirty") is False
        else "includes_preexisting_changes",
        "baseline_paths": baseline.get("changed_paths", []),
        "patch": patch if state == "ok" else "",
        "untracked_included": False,
        "attribution": "workspace_changes_not_exclusive_agent_authorship",
    }
