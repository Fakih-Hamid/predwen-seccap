import functools
import secrets
import time
from collections import defaultdict, deque

from flask import (
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from .models import EventSession, Member, Team, aware, db, utcnow

SESSION_MEMBER = "member_token"
SESSION_FACILITATOR = "is_facilitator"
SESSION_ASSISTANT = "is_assistant"
SESSION_ASSISTANT_TEAM = "assistant_team_id"
SESSION_CSRF = "csrf_token"
SESSION_LANG = "lang"


def csrf_token():
    token = session.get(SESSION_CSRF)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_CSRF] = token
    return token


def _csrf_ok():
    sent = (request.headers.get("X-CSRF-Token")
            or (request.form.get("csrf_token") if request.form else None)
            or ((request.get_json(silent=True) or {}).get("csrf_token")
                if request.is_json else None))
    expected = session.get(SESSION_CSRF)
    return bool(expected) and bool(sent) and secrets.compare_digest(str(sent), str(expected))


def install_csrf(app):
    """Guard every unsafe method. Exemptions are named, never inferred."""
    exempt = {"static", "meta.healthz"}

    @app.before_request
    def _guard():
        if request.method in ("GET", "HEAD", "OPTIONS"):
            csrf_token()          # mint it on the way in so the first POST has one
            return None
        if request.endpoint in exempt:
            return None
        if _csrf_ok():
            return None
        if request.accept_mimetypes.accept_html and not request.is_json:
            return render_template("error.html", code=400,
                                   body_key="error.csrf_body"), 400
        return jsonify(error="csrf", message="Reload the page and try again."), 400


_BUCKETS = defaultdict(deque)


def rate_limit(bucket, key, per_minute):
    """True when the call is allowed. Sliding one-minute window, in process."""
    now = time.monotonic()
    q = _BUCKETS[(bucket, key)]
    while q and now - q[0] > 60.0:
        q.popleft()
    if len(q) >= per_minute:
        return False
    q.append(now)
    return True


def reset_rate_limits():
    _BUCKETS.clear()


def client_addr():
    return request.remote_addr or "-"


def new_member_token():
    return secrets.token_urlsafe(32)


def current_member():
    if "member" in g:
        return g.member
    token = session.get(SESSION_MEMBER)
    g.member = Member.query.filter_by(token=token).one_or_none() if token else None
    return g.member


def current_team():
    member = current_member()
    return db.session.get(Team, member.team_id) if member else None


def current_event():
    team = current_team()
    return db.session.get(EventSession, team.session_id) if team else None


def touch_member(member):
    if member is None:
        return False
    threshold = current_app.config.get("PRESENCE_SECONDS", 15)
    last = aware(member.last_seen_at)
    if last is not None and (utcnow() - last).total_seconds() < threshold:
        return False
    member.last_seen_at = utcnow()
    db.session.commit()
    return True


def require_member(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        member = current_member()
        if member is None:
            if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
                return jsonify(error="no_session"), 401
            return redirect(url_for("participant.join"))
        return view(*a, **kw)
    return wrapped


def require_member_api(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if current_member() is None:
            return jsonify(error="no_session"), 401
        return view(*a, **kw)
    return wrapped


def facilitator_login(password):
    digest = current_app.config.get("FACILITATOR_PASSWORD_HASH") or ""
    if not digest:
        return False
    if not check_password_hash(digest, password or ""):
        return False
    session.pop(SESSION_MEMBER, None)
    session.pop(SESSION_ASSISTANT, None)
    session.pop(SESSION_ASSISTANT_TEAM, None)
    session[SESSION_FACILITATOR] = True
    session.permanent = True
    return True


def facilitator_logout():
    session.pop(SESSION_FACILITATOR, None)


def is_facilitator():
    return bool(session.get(SESSION_FACILITATOR))


def assistant_login(password):
    digest = current_app.config.get("ASSISTANT_PASSWORD_HASH") or ""
    if not digest:
        return False
    if not check_password_hash(digest, password or ""):
        return False
    session.pop(SESSION_MEMBER, None)
    session.pop(SESSION_FACILITATOR, None)
    session[SESSION_ASSISTANT] = True
    session.permanent = True
    return True


def assistant_logout():
    session.pop(SESSION_ASSISTANT, None)
    session.pop(SESSION_ASSISTANT_TEAM, None)


def is_assistant():
    return bool(session.get(SESSION_ASSISTANT))


def require_assistant(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not (is_assistant() or is_facilitator()):
            if request.method != "GET":
                return jsonify(error="forbidden"), 403
            return redirect(url_for("assistant.login", next=request.path))
        return view(*a, **kw)
    return wrapped


def admin_preview():
    return bool(current_app.config.get("ADMIN_BYPASS")) and is_facilitator()


def require_member_or_facilitator(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if current_member() is not None or is_facilitator():
            return view(*a, **kw)
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify(error="no_session"), 401
        return redirect(url_for("participant.join"))
    return wrapped


def require_facilitator(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not is_facilitator():
            if request.method != "GET":
                return jsonify(error="forbidden"), 403
            return redirect(url_for("facilitator.login", next=request.path))
        return view(*a, **kw)
    return wrapped


def current_lang():
    lang = session.get(SESSION_LANG)
    if lang in ("ja", "en"):
        return lang
    return current_app.config.get("DEFAULT_LANG", "ja")


def set_lang(lang):
    if lang in ("ja", "en"):
        session[SESSION_LANG] = lang
        return True
    return False
