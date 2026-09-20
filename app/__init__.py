import os

from flask import Flask, g, jsonify, request, url_for
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .config import INSECURE_SECRET, Config
from .models import ROLES, db

BLUEPRINTS = (
    "app.participant.routes",
    "app.facilitator.routes",
    "app.assistant.routes",
    "app.meta",
)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
    except Exception:  # not SQLite; nothing to set
        pass


_ADDED_COLUMNS = (
    ("event_sessions", "fallback_snapshots", "BOOLEAN NOT NULL DEFAULT 0"),
    ("evidence_links", "source_url", "VARCHAR(500)"),
    ("hint_usage", "level", "VARCHAR(16)"),
    ("hint_usage", "unlock_mode", "VARCHAR(20) NOT NULL DEFAULT 'automatic'"),
    ("hint_unlocks", "level", "VARCHAR(16)"),
    ("hint_unlocks", "mode", "VARCHAR(20) NOT NULL DEFAULT 'facilitator'"),
    ("final_reports", "response_future_json", "TEXT"),
    ("final_reports", "response_other", "TEXT"),
    ("final_reports", "scope", "TEXT"),
    ("teams", "capacity", "INTEGER"),
)


def _add_missing_columns():
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    for table, column, spec in _ADDED_COLUMNS:
        if table not in tables:
            continue                       # create_all just built it, with the column
        have = {c["name"] for c in inspector.get_columns(table)}
        if column in have:
            continue
        db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {spec}"))
        db.session.commit()


_SENTINEL_TABLE = "event_sessions"


def schema_is_present(app):
    """True when the database already carries the schema. Read-only."""
    from sqlalchemy import inspect

    with app.app_context():
        return _SENTINEL_TABLE in set(inspect(db.engine).get_table_names())


def bootstrap_schema(app):
    with app.app_context():
        db.create_all()
        _add_missing_columns()
    return True


class SchemaNotInitialised(RuntimeError):
    """Raised in a worker that finds no schema. Never creates one."""


def create_app(overrides=None):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    proxies = int(app.config.get("TRUSTED_PROXY_COUNT") or 0)
    if proxies > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies,
                                x_host=0, x_port=0, x_prefix=0)

    db.init_app(app)

    from importlib import import_module
    for module in BLUEPRINTS:
        app.register_blueprint(import_module(module).bp)

    if app.config.get("ADMIN_BYPASS"):
        app.register_blueprint(import_module("app.admin").bp)
        app.logger.warning(
            "SECCAP_ADMIN_BYPASS is set: /admin grants the facilitator console "
            "without a password. Never leave this on for a session with "
            "participants, or on a host reachable from outside the room.")

    if app.config.get("BOOTSTRAP_SCHEMA", app.config.get("TESTING", False)):
        bootstrap_schema(app)
    elif not schema_is_present(app):
        raise SchemaNotInitialised(
            "The database at %s has no schema, and a web worker will not create "
            "one — four of them racing to do that is the bug this check exists "
            "for. Run the bootstrap first:\n"
            "    python scripts/init_db.py\n"
            "The container entrypoint (docker/entrypoint.sh) already runs this "
            "before starting Gunicorn; if you are seeing this inside the "
            "container, the entrypoint was bypassed."
            % app.config.get("SQLALCHEMY_DATABASE_URI"))

    if not app.config.get("TESTING") and app.config["SECRET_KEY"] == INSECURE_SECRET:
        raise RuntimeError(
            "SECCAP_SECRET_KEY is unset, so the session cookie would be signed with the "
            "placeholder key from app/config.py. Anyone could then forge a facilitator "
            "session and close every mission. Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
            "and put it in .env as SECCAP_SECRET_KEY."
        )

    from .auth import csrf_token, current_lang, install_csrf
    install_csrf(app)

    from . import missions as content, ui

    if not app.config.get("SKIP_CONTENT_VALIDATION"):
        errors = (content.validate_all(app.config["CONTENT_DIR"], app.config["ARTIFACT_DIR"])
                  + ui.validate(app.config["CONTENT_DIR"]))
        if errors:
            raise RuntimeError("content validation failed:\n  - " + "\n  - ".join(errors))

    def _nav(member):
        from flask import request

        from .auth import admin_preview
        from .models import MISSION_OPEN
        from .state import active_session, mission_rows

        if member is None:
            return None
        preview = admin_preview()
        ev = active_session()
        current = None
        if ev is not None:
            open_rows = [r for r in mission_rows(ev) if r.state == MISSION_OPEN]
            viewing = (request.view_args or {}).get("slug")
            row = next((r for r in open_rows if r.slug == viewing), None)
            if row is None and open_rows:
                row = open_rows[-1]
            if row is not None:
                definition = content.get_mission(app.config["CONTENT_DIR"],
                                                 row.slug)
                current = {
                    "slug": row.slug,
                    "title": content.tx((definition or {}).get("title"),
                                        current_lang()) or row.slug,
                }
        return {
            "mission": current,
            "collective_open": bool(ev and (ev.collective_open or preview)),
            "final_open": bool(ev and (ev.final_open or preview)),
        }

    asset_stamps = {}

    def asset(filename):
        stamp = asset_stamps.get(filename)
        if stamp is None:
            try:
                info = os.stat(os.path.join(app.static_folder, filename))
                stamp = "%x%x" % (int(info.st_mtime), info.st_size)
            except OSError:
                stamp = "0"
            asset_stamps[filename] = stamp
        return url_for("static", filename=filename, v=stamp)

    @app.context_processor
    def _assets():
        return {"asset": asset}

    @app.context_processor
    def _inject():
        from .auth import (admin_preview, current_member, current_team,
                           is_assistant, is_facilitator)
        lang = current_lang()
        member = current_member()
        return {
            "nav": _nav(member),
            "admin_preview": admin_preview(),
            "t": lambda key, **fmt: ui.t(app.config["CONTENT_DIR"], key, lang, **fmt),
            "tl": lambda key: ui.t_list(app.config["CONTENT_DIR"], key, lang),
            "lang": lang,
            "html_lang": "ja" if lang == "ja" else "en",
            "csrf_token": csrf_token,
            "is_facilitator": is_facilitator(),
            "is_assistant": is_assistant(),
            "me": member,
            "my_team": current_team(),
            "roles": ROLES,
            "poll_seconds": app.config["POLL_SECONDS"],
        }

    @app.before_request
    def _drop_member_cache():
        g.pop("member", None)

    @app.errorhandler(404)
    def _404(_e):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify(error="not_found"), 404
        from flask import render_template
        return render_template("error.html", code=404), 404

    @app.errorhandler(500)
    def _500(_e):
        db.session.rollback()
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify(error="server_error"), 500
        from flask import render_template
        return render_template("error.html", code=500), 500

    @app.after_request
    def _headers(response):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if response.mimetype == "text/html":
            response.headers.setdefault(
                "Cache-Control", "no-store, no-cache, must-revalidate, private")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    return app


def main():  # pragma: no cover - the local runner
    app = create_app({"BOOTSTRAP_SCHEMA": True})
    debug = os.environ.get("SECCAP_DEBUG", "").lower() not in ("", "0", "false", "no")
    here = os.path.dirname(os.path.abspath(__file__))
    ignore = [os.path.join(os.path.dirname(here), d, "*")
              for d in (".venv", "venv", "instance", "__pycache__", ".pytest_cache", ".git")]
    app.run(host=os.environ.get("SECCAP_HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
            debug=debug, use_reloader=debug, exclude_patterns=ignore)
