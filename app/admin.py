from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .auth import SESSION_FACILITATOR, SESSION_MEMBER, new_member_token
from .models import (
    MISSION_CLOSED,
    MISSION_LOCKED,
    MISSION_OPEN,
    Member,
    Team,
    audit,
    db,
)
from .state import (
    active_session,
    close_mission,
    create_session,
    mission_row,
    mission_rows,
    open_mission,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")

SESSION_FLAGS = ("scoreboard_visible", "provisional_visible", "collective_open",
                 "final_open", "scores_locked")

SIM_NAMES = ("ai", "ren", "sora", "yuki", "kai", "mio", "haru", "nao")

SIM_SIZES = (5, 5, 5, 4, 4, 4)

ADMIN_NICKNAME = "admin"

NEXT_ENDPOINTS = {
    "briefing": "participant.briefing",
    "team": "participant.team_home",
    "join": "participant.join",
}


@bp.before_request
def _refuse_unless_enabled():
    if not current_app.config.get("ADMIN_BYPASS"):
        abort(404)


def _ev(create=True):
    """The session this panel drives, seeding one if the database is empty."""
    ev = active_session()
    if ev is None and create:
        ev = create_session(current_app.config, title="Admin rehearsal",
                            actor="admin_bypass")
    return ev


def _sit(team):
    member = Member.query.filter_by(team_id=team.id, nickname=ADMIN_NICKNAME).first()
    if member is None:
        member = Member(team_id=team.id, nickname=ADMIN_NICKNAME,
                        token=new_member_token())
        db.session.add(member)
        db.session.flush()
        audit(team.session_id, "member", member.id, "admin_seat", team.code)
        db.session.commit()
    session[SESSION_MEMBER] = member.token
    session.permanent = True
    return member


def _relock(ev):
    for row in mission_rows(ev):
        row.state = MISSION_LOCKED
        row.opened_at = None
        row.closed_at = None
        row.paused_at = None
        row.paused_seconds = 0
        row.extension_seconds = 0
    audit(ev.id, "facilitator", "admin_bypass", "missions_relocked", ev.code)
    db.session.commit()


def _populate(ev):
    added = 0
    teams = Team.query.filter_by(session_id=ev.id).order_by(Team.id).all()
    for index, team in enumerate(teams):
        size = SIM_SIZES[index] if index < len(SIM_SIZES) else SIM_SIZES[-1]
        held = {m.nickname for m in team.members.all()}
        for name in SIM_NAMES[:size]:
            nickname = f"{team.code.lower()}-{name}"
            if nickname in held:
                continue
            db.session.add(Member(team_id=team.id, nickname=nickname,
                                  token=new_member_token()))
            db.session.flush()
            added += 1
    db.session.commit()
    return added


@bp.get("/")
def panel():
    session[SESSION_FACILITATOR] = True
    session.permanent = True

    ev = _ev()
    rows = mission_rows(ev) if ev else []
    teams = Team.query.filter_by(session_id=ev.id).all() if ev else []
    return render_template(
        "admin.html",
        ev=ev,
        rows=rows,
        teams=teams,
        flags={f: getattr(ev, f) for f in SESSION_FLAGS} if ev else {},
        member_counts={t.id: t.members.count() for t in teams},
        done=request.args.get("done"),
    )


@bp.post("/act")
def act():
    action = (request.form.get("action") or "").strip()
    ev = _ev()

    if action == "new_session":
        ev = create_session(current_app.config, title="Admin rehearsal",
                            actor="admin_bypass")
        session.pop(SESSION_MEMBER, None)      # the old seat belongs to the old session
        return _back(f"new session {ev.code}")

    if ev is None:
        return _back("no session")

    if action == "populate":
        return _back(f"{_populate(ev)} simulated members added")

    if action == "open_all":
        for row in mission_rows(ev):
            if row.state != MISSION_OPEN:
                open_mission(ev, row, actor="admin_bypass")
        return _back("all missions open")

    if action == "close_all":
        for row in mission_rows(ev):
            if row.state != MISSION_CLOSED:
                close_mission(ev, row, actor="admin_bypass")
        return _back("all missions closed and scored")

    if action == "relock":
        _relock(ev)
        return _back("timers reset, missions locked")

    if action == "mission":
        row = mission_row(ev, request.form.get("slug"))
        if row is None:
            return _back("unknown mission")
        if row.state == MISSION_OPEN:
            close_mission(ev, row, actor="admin_bypass")
            return _back(f"{row.slug} closed")
        open_mission(ev, row, actor="admin_bypass")
        return _back(f"{row.slug} open")

    if action == "flag":
        flag = request.form.get("flag")
        if flag not in SESSION_FLAGS:
            return _back("unknown flag")
        setattr(ev, flag, not getattr(ev, flag))
        if flag == "final_open" and ev.final_open:
            from . import reports
            for team in Team.query.filter_by(session_id=ev.id).all():
                reports.get_or_create(ev, team)
        audit(ev.id, "facilitator", "admin_bypass", f"set_{flag}", ev.code,
              str(getattr(ev, flag)))
        db.session.commit()
        return _back(f"{flag} = {getattr(ev, flag)}")

    if action == "sit":
        team = db.session.get(Team, request.form.get("team_id", type=int))
        if team is None or team.session_id != ev.id:
            return _back("unknown team")
        _sit(team)
        after = NEXT_ENDPOINTS.get(request.form.get("next"))
        if after:
            return redirect(url_for(after))
        return _back(f"seated in {team.display_name}")

    if action == "stand":
        session.pop(SESSION_MEMBER, None)
        return _back("seat released")

    if action == "signout":
        session.pop(SESSION_MEMBER, None)
        session.pop(SESSION_FACILITATOR, None)
        return redirect(url_for("meta.landing"))

    return _back("unknown action")


def _back(done):
    return redirect(url_for("admin.panel", done=done))
