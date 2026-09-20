#!/usr/bin/env python3
import os
import sqlite3
import sys


def source_path():
    uri = os.environ.get("SECCAP_DATABASE_URI", "sqlite:////data/seccap.db")
    if not uri.startswith("sqlite:"):
        raise SystemExit(f"backup_db: not a SQLite database: {uri}")
    path = uri.split("sqlite:", 1)[1].lstrip("/")
    return "/" + path if uri.startswith("sqlite:////") else path


def table_count(conn):
    return conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]


def main(destination):
    src_path = source_path()
    if not os.path.exists(src_path):
        raise SystemExit(f"backup_db: no database at {src_path}")

    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    if os.path.exists(destination):
        raise SystemExit(f"backup_db: refusing to overwrite {destination}")

    src = sqlite3.connect(src_path)
    try:
        expected = table_count(src)
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)                       # the SQLite backup API
        finally:
            dst.close()
    finally:
        src.close()

    check = sqlite3.connect(destination)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        got = table_count(check)
    finally:
        check.close()

    if integrity != "ok":
        raise SystemExit(f"backup_db: integrity_check said {integrity!r}")
    if got != expected:
        raise SystemExit(
            f"backup_db: copy has {got} tables, source has {expected}")

    size = os.path.getsize(destination)
    print(f"backup_db: {destination} ({size} bytes, {got} tables, integrity ok)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: backup_db.py <destination.db>")
    try:
        sys.exit(main(sys.argv[1]))
    except Exception as exc:                       # noqa: BLE001 — report and fail
        print(f"backup_db: FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
