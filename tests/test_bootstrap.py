import json
import os
import subprocess
import sys
import textwrap

import pytest
from werkzeug.security import generate_password_hash

from app import (SchemaNotInitialised, bootstrap_schema, create_app,
                 schema_is_present)
from app.models import EventSession, db

from .conftest import FACILITATOR_PASSWORD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config(db_path, **extra):
    base = {
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(db_path).replace("\\", "/"),
        "SECRET_KEY": "bootstrap-test-only",
        "FACILITATOR_PASSWORD_HASH": generate_password_hash(FACILITATOR_PASSWORD),
        "SKIP_CONTENT_VALIDATION": True,
        "DEFAULT_LANG": "en",
        "ADMIN_BYPASS": False,
        "TESTING": False,          # production semantics: workers must not build
    }
    base.update(extra)
    return base


def test_a_worker_refuses_to_boot_without_a_schema(tmp_path):
    """And says what to run, instead of racing three siblings to create one."""
    with pytest.raises(SchemaNotInitialised) as excinfo:
        create_app(_config(tmp_path / "empty.db"))
    message = str(excinfo.value)
    assert "scripts/init_db.py" in message
    assert "will not create" in message


def test_a_worker_never_issues_ddl_once_the_schema_exists(tmp_path, monkeypatch):
    """The whole point: after bootstrap, a worker only reads."""
    path = tmp_path / "ready.db"
    bootstrap_schema(create_app(_config(path, BOOTSTRAP_SCHEMA=True)))

    calls = []
    real = db.create_all
    monkeypatch.setattr(db, "create_all", lambda *a, **k: calls.append(1))
    try:
        app = create_app(_config(path))
        assert app is not None
    finally:
        monkeypatch.setattr(db, "create_all", real)
    assert calls == [], "a worker called create_all()"


def test_bootstrap_creates_the_schema_and_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    app = create_app(_config(path, BOOTSTRAP_SCHEMA=True))
    assert schema_is_present(app)

    assert bootstrap_schema(app) is True
    assert bootstrap_schema(app) is True
    assert schema_is_present(app)


def test_bootstrap_preserves_the_data_already_on_the_volume(tmp_path):
    """A restart must never cost an event its afternoon."""
    path = tmp_path / "populated.db"
    app = create_app(_config(path, BOOTSTRAP_SCHEMA=True))
    with app.app_context():
        db.session.add(EventSession(code="KEEPME", title="Existing event"))
        db.session.commit()

    for _ in range(3):                      # three more starts on the same volume
        again = create_app(_config(path, BOOTSTRAP_SCHEMA=True))
        with again.app_context():
            rows = EventSession.query.filter_by(code="KEEPME").all()
            assert len(rows) == 1
            assert rows[0].title == "Existing event"


def test_the_init_script_exits_zero_and_is_idempotent(tmp_path):
    env = dict(os.environ,
               SECCAP_DATABASE_URI="sqlite:///" + str(tmp_path / "cli.db").replace("\\", "/"),
               SECCAP_SECRET_KEY="bootstrap-test-only",
               SECCAP_ADMIN_BYPASS="")
    for run in range(2):
        done = subprocess.run([sys.executable, "scripts/init_db.py"],
                              cwd=ROOT, env=env, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        assert "schema ready" in done.stdout, (run, done.stdout, done.stderr)


def test_a_failing_bootstrap_exits_non_zero(tmp_path):
    """A database that cannot be opened must stop the deployment, loudly."""
    unwritable = tmp_path / "no-such-directory" / "seccap.db"
    env = dict(os.environ,
               SECCAP_DATABASE_URI="sqlite:///" + str(unwritable).replace("\\", "/"),
               SECCAP_SECRET_KEY="bootstrap-test-only",
               SECCAP_ADMIN_BYPASS="")
    done = subprocess.run([sys.executable, "scripts/init_db.py"],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    assert done.returncode != 0, done.stdout
    assert "FAILED" in done.stderr or "missing after bootstrap" in done.stderr


_WORKER = textwrap.dedent(
    """
    import json, os, sys
    sys.path.insert(0, sys.argv[1])
    os.environ["SECCAP_ADMIN_BYPASS"] = ""
    from app import create_app
    cfg = {
        "SQLALCHEMY_DATABASE_URI": sys.argv[2],
        "SECRET_KEY": "bootstrap-test-only",
        "FACILITATOR_PASSWORD_HASH": "x",
        "SKIP_CONTENT_VALIDATION": True,
        "TESTING": False,
        "ADMIN_BYPASS": False,
    }
    try:
        create_app(cfg)
        print(json.dumps({"ok": True, "error": None}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
    """
)


def _spawn_workers(tmp_path, uri, count=4):
    """Four real processes starting at once, the way Gunicorn forks them."""
    script = tmp_path / "worker.py"
    script.write_text(_WORKER, encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, str(script), ROOT, uri],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, cwd=ROOT)
             for _ in range(count)]
    out = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=180)
        line = [ln for ln in stdout.splitlines() if ln.startswith("{")]
        out.append(json.loads(line[-1]) if line else
                   {"ok": False, "error": "no output: " + stderr[-200:]})
    return out


def test_four_concurrent_workers_on_a_fresh_database(tmp_path):
    uri = "sqlite:///" + str(tmp_path / "race.db").replace("\\", "/")
    results = _spawn_workers(tmp_path, uri)

    assert len(results) == 4
    assert all(not r["ok"] for r in results), results
    assert {r["error"] for r in results} == {"SchemaNotInitialised"}, results
    assert not any(r["error"] == "OperationalError" for r in results), (
        "a worker still tried to create the schema: the race is back")


def test_four_concurrent_workers_after_the_bootstrap_all_start(tmp_path):
    """And with the schema already built, all four come up."""
    path = tmp_path / "race-ok.db"
    uri = "sqlite:///" + str(path).replace("\\", "/")
    done = subprocess.run(
        [sys.executable, "scripts/init_db.py"], cwd=ROOT, text=True,
        capture_output=True,
        env=dict(os.environ, SECCAP_DATABASE_URI=uri,
                 SECCAP_SECRET_KEY="bootstrap-test-only", SECCAP_ADMIN_BYPASS=""))
    assert done.returncode == 0, done.stderr

    results = _spawn_workers(tmp_path, uri)
    assert all(r["ok"] for r in results), results


def test_gunicorn_still_runs_four_workers_and_does_not_preload():
    """The fix must not have been "use one worker" or "preload the app"."""
    text = open(os.path.join(ROOT, "gunicorn.conf.py"), encoding="utf-8").read()
    assert "min(4, multiprocessing.cpu_count())" in text
    assert "preload_app = False" in text


def test_the_container_bootstraps_before_it_starts_gunicorn():
    entry = open(os.path.join(ROOT, "docker", "entrypoint.sh"), encoding="utf-8").read()
    assert entry.startswith("#!/bin/sh")
    assert "set -e" in entry, "a failed bootstrap has to stop the entrypoint"
    assert "scripts/init_db.py" in entry
    assert 'exec "$@"' in entry
    assert entry.index("init_db.py") < entry.index('exec "$@"')

    dockerfile = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    assert 'ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]' in dockerfile
    assert 'CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]' in dockerfile


def test_the_entrypoint_is_committed_with_unix_line_endings():
    """A CRLF shebang is "no such file or directory" inside the container."""
    raw = open(os.path.join(ROOT, "docker", "entrypoint.sh"), "rb").read()
    assert b"\r\n" not in raw
