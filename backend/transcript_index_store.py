"""Rebuildable SQLite descriptor cache; canonical conversation bytes stay in JSONL.

Only new descriptors and a small source checkpoint are written on append. No
connection outlives an operation, so session deletion has no open database/WAL
owner to join. SQLite's rollback journal makes descriptors and checkpoint one
transaction; a failed append can always be retried from the old checkpoint.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from .private_storage import ensure_private_regular_file


def load(path: Path, schema: int) -> dict[str, Any] | None:
    if not ensure_private_regular_file(path):
        return None
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT value FROM metadata WHERE id=1').fetchone()
            if row is None:
                return None
            index = json.loads(row[0])
            if not isinstance(index, dict) or index.get('schema') != schema:
                return None
            if not isinstance(index.get('source'), dict):
                return None
            index['records'] = [json.loads(row[0]) for row in db.execute(
                'SELECT descriptor FROM records ORDER BY position')]
        return index
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
        return None


def _write(path: Path, index: dict[str, Any], first_record: int, create: bool) -> None:
    # The parent session directory and all SQLite files are private. DELETE
    # journals are removed on close and inherit the database's 0600 mode.
    db = sqlite3.connect(path)
    try:
        with db:
            if create:
                db.execute('CREATE TABLE metadata (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('CREATE TABLE records (position INTEGER PRIMARY KEY, descriptor TEXT NOT NULL)')
            db.executemany('INSERT INTO records VALUES (?, ?)', (
                (position, json.dumps(index['records'][position], ensure_ascii=False,
                                      separators=(',', ':')))
                for position in range(first_record, len(index['records']))
            ))
            metadata = {name: index[name] for name in (
                'schema', 'history_generation', 'source')}
            db.execute('INSERT OR REPLACE INTO metadata VALUES (1, ?)', (
                json.dumps(metadata, ensure_ascii=False, separators=(',', ':')),))
    finally:
        db.close()


def persist(path: Path, index: dict[str, Any], first_record: int, *, rebuild: bool) -> None:
    path = path.absolute()
    exists = ensure_private_regular_file(path)
    if exists and not rebuild:
        _write(path, index, first_record, False)
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    os.close(fd)
    temp_path = Path(temporary)
    try:
        _write(temp_path, index, 0, True)
        os.replace(temp_path, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        temp_path.unlink(missing_ok=True)
