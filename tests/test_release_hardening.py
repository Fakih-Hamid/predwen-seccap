import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.auth import reset_rate_limits
from app.models import Team, db
from app.state import create_session

from .conftest import FACILITATOR_PASSWORD


def _app(**overrides):
    """A second application, so the proxy setting can differ per test."""
    base = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-only",
        "FACILITATOR_PASSWORD_HASH": generate_password_hash(FACILITATOR_PASSWORD),
        "SKIP_CONTENT_VALIDATION": True,
        "DEFAULT_LANG": "en",
        "ADMIN_BYPASS": False,
    }
    base.update(overrides)
    application = create_app(base)
    with application.app_context():
        db.create_all()
        reset_rate_limits()
    return application


def test_healthz_publishes_no_session_code(app, event):
    body = app.test_client().get("/healthz").get_json()
    assert event.code not in str(body)
    for leaked in ("session", "session_state", "code"):
        assert leaked not in body, f"/healthz still carries {leaked!r}"


def test_healthz_publishes_no_team_code(app, event):
    body = str(app.test_client().get("/healthz").get_json())
    for team in Team.query.filter_by(session_id=event.id).all():
        assert team.code not in body, f"team code {team.code} is in /healthz"


def test_healthz_publishes_no_secret_or_configuration(app, event):
    body = str(app.test_client().get("/healthz").get_json())
    for secret in (app.config["SECRET_KEY"], app.config["FACILITATOR_PASSWORD_HASH"],
                   app.config["SQLALCHEMY_DATABASE_URI"]):
        assert secret and secret not in body


def test_healthz_says_only_safe_things(app, event):
    body = app.test_client().get("/healthz").get_json()
    assert set(body) == {"status", "database", "session_active", "teams", "members"}
    assert body["session_active"] is True
    assert isinstance(body["teams"], int) and isinstance(body["members"], int)


def test_healthz_really_opens_the_database(app):
    """A probe that never touches the disk reports healthy through a dead volume."""
    calls = []
    original = db.session.execute

    def counted(statement, *a, **kw):
        calls.append(str(statement))
        return original(statement, *a, **kw)

    db.session.execute = counted
    try:
        assert app.test_client().get("/healthz").status_code == 200
    finally:
        db.session.execute = original
    assert any("SELECT 1" in c for c in calls), (
        "healthz answered without querying the database")


def test_healthz_reports_unhealthy_when_the_database_is_gone(app, monkeypatch):
    import app.meta as meta

    def broken(*_a, **_kw):
        raise RuntimeError("unable to open database file /data/seccap.db")

    monkeypatch.setattr(meta.db.session, "execute", broken)
    res = app.test_client().get("/healthz")
    assert res.status_code == 503
    body = res.get_json()
    assert body["status"] == "error" and body["database"] == "unavailable"
    assert "seccap.db" not in str(body)
    assert "detail" not in body


def test_healthz_needs_no_cookie_and_is_still_safe(app, event):
    """It is exempt from CSRF and from auth, so it has to be safe unauthenticated."""
    fresh = app.test_client()
    body = fresh.get("/healthz").get_json()
    assert body["status"] == "ok"
    assert event.code not in str(body)


def _join_attempt(client, forwarded=None):
    client.get("/join")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return client.post("/join", data={"csrf_token": token, "session_code": "NOPE00",
                                      "team_code": "NOPE0", "nickname": "x"},
                       headers=headers)


def _address_seen(application, forwarded, peer="10.0.0.1"):
    seen = {}

    @application.route("/__client_addr_probe")
    def _probe():
        from app.auth import client_addr
        seen["addr"] = client_addr()
        return ""

    application.test_client().get(
        "/__client_addr_probe",
        headers={"X-Forwarded-For": forwarded},
        environ_base={"REMOTE_ADDR": peer})
    return seen["addr"]


def test_by_default_a_forged_forwarded_header_is_ignored():
    """The classroom laptop has no proxy, so the header is not evidence."""
    application = _app()
    assert application.config["TRUSTED_PROXY_COUNT"] == 0
    assert _address_seen(application, "203.0.113.9", peer="192.0.2.1") == "192.0.2.1", (
        "X-Forwarded-For was believed with no proxy declared: anyone could then "
        "mint a fresh rate-limit bucket per request")


def test_with_one_proxy_declared_the_client_address_is_extracted():
    application = _app(TRUSTED_PROXY_COUNT=1)
    assert _address_seen(application, "203.0.113.9") == "203.0.113.9"


def test_only_the_declared_number_of_hops_is_trusted():
    """One proxy means the LAST entry. Anything the client prepended is theirs."""
    application = _app(TRUSTED_PROXY_COUNT=1)
    assert _address_seen(application, "1.2.3.4, 203.0.113.9") == "203.0.113.9"


def test_the_middleware_is_absent_entirely_when_nothing_is_declared():
    """Not a flag checked at request time — the layer is never installed."""
    from werkzeug.middleware.proxy_fix import ProxyFix

    assert not isinstance(_app().wsgi_app, ProxyFix)
    assert isinstance(_app(TRUSTED_PROXY_COUNT=2).wsgi_app, ProxyFix)


def test_twenty_five_students_behind_a_proxy_do_not_share_one_quota():
    """The bug this setting exists for: JOIN_RPM is 20 and a class is 25."""
    application = _app(TRUSTED_PROXY_COUNT=1, JOIN_RPM=20)
    with application.app_context():
        reset_rate_limits()
        create_session(application.config, title="Rate limit", code="RATEAA",
                       team_count=5)

    refused = 0
    for i in range(25):
        client = application.test_client()
        res = _join_attempt(client, forwarded=f"198.51.100.{i + 1}")
        if res.status_code == 429:
            refused += 1
    assert refused == 0, (
        f"{refused} of 25 distinct students were rate-limited as though they "
        f"were one client")


def test_one_client_is_still_limited_when_it_goes_over():
    """The limiter must not have been traded away for the fix above."""
    application = _app(TRUSTED_PROXY_COUNT=1, JOIN_RPM=5)
    with application.app_context():
        reset_rate_limits()
        create_session(application.config, title="Rate limit", code="RATEBB",
                       team_count=5)

    statuses = []
    for _ in range(8):
        client = application.test_client()
        statuses.append(_join_attempt(client, forwarded="198.51.100.77").status_code)
    assert 429 in statuses, "one address was never limited"
    assert statuses.index(429) >= 5, f"limited too early: {statuses}"


def test_a_forged_header_cannot_escape_the_limiter_when_no_proxy_is_declared():
    """With no proxy, every forged address still lands in the same bucket."""
    application = _app(JOIN_RPM=5)
    with application.app_context():
        reset_rate_limits()
        create_session(application.config, title="Rate limit", code="RATECC",
                       team_count=5)

    statuses = []
    for i in range(8):
        client = application.test_client()
        statuses.append(_join_attempt(client, forwarded=f"198.51.100.{i + 1}").status_code)
    assert 429 in statuses, (
        "a caller changed X-Forwarded-For each request and was never limited")


def test_every_dependency_is_pinned_to_an_exact_version():
    """`>=` means the image built in October is not the one rehearsed in August."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = line.split(";")[0].strip()
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][0-9A-Za-z.\-]*", name), (
            f"requirements.txt is not pinned: {line!r}")


def test_the_docker_base_image_is_pinned_to_a_digest():
    """A floating tag moves under you between the rehearsal and the event."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^FROM\s+(\S+)", dockerfile, re.M)
    assert match, "no FROM line"
    assert re.fullmatch(r"python:3\.\d+\.\d+-slim@sha256:[0-9a-f]{64}", match.group(1)), (
        f"base image {match.group(1)!r} is not pinned to a patch tag AND a digest")


@pytest.mark.parametrize("forbidden", ["psycopg", "alembic", "htmx", "flask-migrate"])
def test_the_architecture_was_not_widened(forbidden):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    assert forbidden not in text
