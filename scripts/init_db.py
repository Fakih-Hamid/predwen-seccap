#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import bootstrap_schema, create_app, schema_is_present  # noqa: E402
from app.models import db  # noqa: E402


def main():
    app = create_app({"BOOTSTRAP_SCHEMA": True, "SKIP_CONTENT_VALIDATION": True})

    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if not schema_is_present(app):
        print(f"init_db: schema still missing after bootstrap at {uri}",
              file=sys.stderr)
        return 1

    with app.app_context():
        from sqlalchemy import inspect
        tables = sorted(inspect(db.engine).get_table_names())
    print(f"init_db: schema ready at {uri} ({len(tables)} tables)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                       # noqa: BLE001 — report and fail
        print(f"init_db: FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
