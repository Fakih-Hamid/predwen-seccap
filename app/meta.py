from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from sqlalchemy import text

from .auth import set_lang
from .models import Member, Team, db
from .state import active_session

bp = Blueprint("meta", __name__)


@bp.get("/")
def landing():
    ev = active_session()
    return render_template("landing.html", ev=ev)


@bp.post("/lang")
def lang():
    set_lang((request.form.get("lang") or "").strip())
    return redirect(request.form.get("next") or url_for("meta.landing"))


@bp.get("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT 1")).scalar()
        ev = active_session()
        teams = Team.query.filter_by(session_id=ev.id).count() if ev else 0
        members = (db.session.query(Member).join(Team)
                   .filter(Team.session_id == ev.id).count()) if ev else 0
    except Exception:  # a broken volume must not read as healthy
        current_app.logger.exception("healthz: database check failed")
        return jsonify(status="error", database="unavailable"), 503
    return jsonify(status="ok",
                   database="ok",
                   session_active=ev is not None,
                   teams=teams,
                   members=members)
