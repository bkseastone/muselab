#!/usr/bin/env python3
"""Copy legacy state from one stopped Docker container without replacing it.

Linux/WSL only: renameat2(RENAME_NOREPLACE) protects whole-directory publication.
The original container and private source tar archives are retained on success
and failure. No Docker lifecycle command is issued by this tool.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

try:
    import fcntl
except ImportError:  # Unsupported hosts still receive a clear preflight error.
    fcntl = None

SOURCES = {
    "env": ("/app/.env", "config/.env"),
    "mcp": ("/app/mcp.json", "config/mcp.json"),
    "providers": ("/app/provider_overrides.json", "config/provider_overrides.json"),
    "vendor": ("/home/muse/.local/state/muselab/vendor-cli", "state/muselab/vendor-cli"),
}


def private_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def no_replace(source: Path, target: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise RuntimeError("Linux renameat2 is required; run this tool inside Linux or WSL")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise RuntimeError(f"target already exists; nothing will overwrite it: {target}")
        raise OSError(code, os.strerror(code), str(target))


def inspect_stopped(docker: str, container: str) -> str:
    result = subprocess.run([docker, "inspect", "--type", "container", container],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"docker inspect failed (exit {result.returncode}); resolve Docker access first")
    try:
        rows = json.loads(result.stdout)
        row = rows[0] if isinstance(rows, list) and len(rows) == 1 else {}
        state = row["State"]
        container_id = row["Id"]
        stopped = (state.get("Status") in {"exited", "created"}
                   and state.get("Running") is False
                   and not state.get("Restarting") and not state.get("Paused")
                   and state.get("Pid", 0) == 0)
        if not isinstance(container_id, str) or not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise ValueError
    except (ValueError, KeyError, TypeError, IndexError):
        raise RuntimeError("docker inspect returned an unexpected response") from None
    if not stopped:
        raise RuntimeError("stop the selected old container before migration; this tool does not stop it")
    return container_id


def copy_source(docker: str, container_id: str, source: str, archive: Path) -> bool:
    with archive.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        result = subprocess.run([docker, "cp", f"{container_id}:{source}", "-"],
                                stdout=stream, stderr=subprocess.PIPE)
        stream.flush()
        os.fsync(stream.fileno())
    if result.returncode == 0:
        return True
    error = result.stderr.decode("utf-8", "replace").strip()
    missing = re.fullmatch(
        rf"(?:Error response from daemon: )?Could not find the file {re.escape(source)} "
        rf"in container {re.escape(container_id)}\.?", error,
    )
    if missing and archive.stat().st_size == 0:
        archive.unlink()
        return False
    raise RuntimeError(f"docker cp failed for {source} (exit {result.returncode}); source was not classified as absent")


def extract_source(archive: Path, source: str, target: Path) -> None:
    """Accept ordinary files/directories only; preserve unknown data in the tar."""
    base = PurePosixPath(source).name
    with tarfile.open(archive, "r:") as stream:
        members = stream.getmembers()
        if not members:
            raise RuntimeError(f"empty archive for {source}")
        for member in members:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or ".." in name.parts or not name.parts
                    or name.parts[0] != base or not (member.isdir() or member.isfile())):
                raise RuntimeError(f"unsupported or unsafe archive entry for {source}; retained raw backup for manual recovery")
        for member in members:
            suffix = PurePosixPath(member.name).parts[1:]
            destination = target.joinpath(*suffix)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if member.isdir():
                destination.mkdir(exist_ok=True, mode=0o700)
            else:
                incoming = stream.extractfile(member)
                if incoming is None:
                    raise RuntimeError(f"unreadable archive member for {source}")
                with incoming, destination.open("xb") as outgoing:
                    os.fchmod(outgoing.fileno(), 0o600)
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
    for directory in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True) if target.is_dir() else []:
        sync_directory(directory)
    sync_directory(target if target.is_dir() else target.parent)


def parent_directories(target: Path, sessions: Path) -> None:
    chain = list(target.parent.relative_to(sessions).parts)
    current = sessions
    for name in chain:
        current /= name
        if os.path.lexists(current):
            if current.is_symlink() or not current.is_dir():
                raise RuntimeError(f"destination parent is not an ordinary directory: {current}")
        else:
            current.mkdir(mode=0o700)
            sync_directory(current.parent)


def migrate(docker: str, container: str, sessions: Path) -> dict:
    if sys.platform != "linux" or fcntl is None:
        raise RuntimeError("migration requires Linux or WSL; this host is unsupported and no state was changed")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
        raise RuntimeError("pass one explicit container name or ID")
    if not sessions.is_dir() or sessions.is_symlink():
        raise RuntimeError("--sessions-dir must be an existing ordinary host sessions directory")
    if sessions.stat().st_uid != os.geteuid():
        raise RuntimeError("run as the sessions directory owner so private files keep the correct bind-mount ownership")
    if not hasattr(ctypes.CDLL(None), "renameat2"):
        raise RuntimeError("Linux renameat2 is required; use Linux or WSL")
    container_id = inspect_stopped(docker, container)
    lock_fd = os.open(sessions / ".muselab-migration.lock",
                      os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup = Path(tempfile.mkdtemp(prefix=".muselab-docker-migration-", dir=sessions))
        os.chmod(backup, 0o700)
        stage = backup / "stage"
        stage.mkdir(mode=0o700)
        manifest = {"container": container, "container_id": container_id,
                    "sessions_dir": str(sessions), "backup_dir": str(backup),
                    "uid": os.geteuid(), "gid": os.getegid(), "phase": "backing_up",
                    "sources": {}, "published": []}
        receipt = backup / "manifest.json"
        private_json(receipt, manifest)
        published: list[tuple[Path, Path, tuple[int, int]]] = []
        try:
            # Complete every raw backup before extracting or publishing state.
            for name, (source, relative) in SOURCES.items():
                archive = backup / f"{name}.tar"
                exists = copy_source(docker, container_id, source, archive)
                info = {"source": source, "target": relative, "present": exists}
                if exists:
                    with archive.open("rb") as stream:
                        info["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                    info["archive"] = archive.name
                manifest["sources"][name] = info
                private_json(receipt, manifest)
            for name, (source, relative) in SOURCES.items():
                if manifest["sources"][name]["present"]:
                    extract_source(backup / f"{name}.tar", source, stage / relative)
            manifest["phase"] = "staged"
            private_json(receipt, manifest)
            targets = [(stage / name, sessions / name)
                       for name in ("config", "state/muselab/vendor-cli") if (stage / name).exists()]
            # Check every destination before the first publication. renameat2
            # also fences a target created between this check and the rename.
            for _source, target in targets:
                parent_directories(target, sessions)
                if os.path.lexists(target):
                    raise RuntimeError(f"target already exists; not overwritten: {target}")
            if inspect_stopped(docker, container_id) != container_id:
                raise RuntimeError("container identity changed during migration")
            manifest["phase"] = "publishing"
            private_json(receipt, manifest)
            for source, target in targets:
                identity = (source.stat().st_dev, source.stat().st_ino)
                no_replace(source, target)
                published.append((source, target, identity))
                sync_directory(source.parent)
                sync_directory(target.parent)
                manifest["published"].append(str(target.relative_to(sessions)))
                private_json(receipt, manifest)
            manifest["phase"] = "complete"
            private_json(receipt, manifest)
            return manifest
        except BaseException:
            # Best-effort rollback only of directories this invocation moved.
            # If another writer replaced a target, retain it and the archives.
            for source, target, identity in reversed(published):
                try:
                    current = target.lstat()
                    if (current.st_dev, current.st_ino) == identity:
                        no_replace(target, source)
                        sync_directory(target.parent)
                        sync_directory(source.parent)
                except (OSError, RuntimeError):
                    pass
            manifest["phase"] = "failed"
            try:
                private_json(receipt, manifest)
            except OSError:
                pass
            print(f"Migration stopped; original container retained. Private recovery archive: {backup}", file=sys.stderr)
            raise
    finally:
        os.close(lock_fd)


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True, help="one stopped legacy container name or ID")
    parser.add_argument("--sessions-dir", required=True, type=Path, help="existing host ./sessions bind-mount directory")
    parser.add_argument("--docker", default="docker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = migrate(args.docker, args.container, args.sessions_dir.absolute())
    except (OSError, RuntimeError, tarfile.TarError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "backup_dir": result["backup_dir"],
                      "published": result["published"], "phase": result["phase"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
