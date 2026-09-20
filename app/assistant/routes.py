import json

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .. import missions as content
from ..auth import (
    SESSION_ASSISTANT_TEAM,
    assistant_login,
    assistant_logout,
    current_lang,
    is_facilitator,
    require_assistant,
)
from ..models import (
    MISSION_LOCKED,
    MISSION_OPEN,
    TA_FREQUENCY_QUESTIONS,
    TA_HINT_SCALE,
    TA_QUESTIONS,
    TA_SCALE,
    AssistantReport,
    EvidenceLink,
    Member,
    Observation,
    Submission,
    Team,
    audit,
    db,
)
from ..scoring import hints_taken
from ..state import (
    active_session,
    is_locked,
    mission_rows,
    visible_source_ids,
)

bp = Blueprint("assistant", __name__, url_prefix="/assistant")


def _cd():
    return current_app.config["CONTENT_DIR"]


def _disabled():
    return not current_app.config.get("ASSISTANT_PASSWORD_HASH")


@bp.get("/login")
def login():
    return render_template("assistant/login.html", disabled=_disabled())


@bp.post("/login")
def do_login():
    if assistant_login(request.form.get("password")):
        return redirect(request.args.get("next") or url_for("assistant.home"))
    return render_template("assistant/login.html", error=True,
                           disabled=_disabled()), 401


@bp.post("/logout")
@require_assistant
def logout():
    assistant_logout()
    return redirect(url_for("meta.landing"))


def _team():
    ev = active_session()
    team_id = session.get(SESSION_ASSISTANT_TEAM)
    if ev is None or not team_id:
        return ev, None
    team = db.session.get(Team, team_id)
    if team is None or team.session_id != ev.id:
        session.pop(SESSION_ASSISTANT_TEAM, None)
        return ev, None
    return ev, team


@bp.get("/")
@require_assistant
def home():
    ev, team = _team()
    if team is not None:
        return redirect(url_for("assistant.board"))
    return redirect(url_for("assistant.pick"))


@bp.get("/pick")
@require_assistant
def pick():
    ev = active_session()
    teams = (Team.query.filter_by(session_id=ev.id).order_by(Team.id).all()
             if ev else [])
    return render_template("assistant/pick.html", ev=ev, teams=[{
        "id": t.id, "name": t.display_name, "color": t.color_key,
        "online": sum(1 for m in t.members if m.online),
        "size": t.members.count(),
    } for t in teams])


@bp.post("/team")
@require_assistant
def choose_team():
    ev = active_session()
    team = db.session.get(Team, request.form.get("team_id", type=int))
    if ev is None or team is None or team.session_id != ev.id:
        return redirect(url_for("assistant.pick"))
    session[SESSION_ASSISTANT_TEAM] = team.id
    return redirect(url_for("assistant.board"))


def _display_answer(question, value):
    if value is None or value == "" or value == []:
        return None
    labels = {o["id"]: o["label"] for o in question.get("options") or []}
    items = {o["id"]: o["label"] for o in question.get("items") or []}
    if isinstance(value, list):
        return [labels.get(v, items.get(v, str(v))) for v in value]
    return labels.get(value, str(value))


def _payload(ev, team):
    """Everything the board paints, for one team, from the team's own rows."""
    lang = current_lang()
    rows = mission_rows(ev)
    open_rows = [r for r in rows if r.state == MISSION_OPEN]
    current = open_rows[0] if open_rows else next(
        (r for r in sorted(rows, key=lambda r: r.order, reverse=True)
         if r.state != MISSION_LOCKED), None)

    members = team.members.order_by(Member.joined_at).all()
    posted_in = {}
    if current is not None:
        posted_in = {o.member_id for o in Observation.query.filter_by(
            team_id=team.id, mission_slug=current.slug).all()}

    out = {
        "team": {"id": team.id, "name": team.display_name, "color": team.color_key,
                 "members": [{"nickname": m.nickname, "online": m.online,
                              "posted": m.id in posted_in} for m in members]},
        "missions": [],
        "current": None,
    }

    for row in rows:
        definition = content.get_mission(_cd(), row.slug)
        if definition is None:
            continue
        questions = definition.get("questions") or []
        answered = 0
        for s in Submission.query.filter_by(team_id=team.id, mission_slug=row.slug).all():
            value = json.loads(s.answer_json) if s.answer_json else None
            if isinstance(value, str):
                value = value.strip()
            if value not in (None, "", [], {}):
                answered += 1
        out["missions"].append({
            "slug": row.slug, "title": content.tx(definition.get("title"), lang),
            "state": row.state, "answered": answered, "questions": len(questions),
            "locked": is_locked(team, row.slug),
        })

    if current is None:
        return out

    definition = content.get_mission(_cd(), current.slug)
    rendered = content.render_mission(definition, lang, _cd())
    titles = {a["id"]: a["title"] for a in rendered["artifacts"]}

    visible = set(visible_source_ids(current, team, definition))
    sources = [{"id": entry["id"], "title": titles.get(entry["id"]),
                "arrived": entry["id"] in visible}
               for entry in definition.get("artifacts") or []]

    observations = [{
        "nickname": o.member.nickname if o.member else "—",
        "at": o.created_at.isoformat(),
        "text": o.text,
        "artifact": titles.get(o.artifact_id) if o.artifact_id else None,
    } for o in Observation.query.filter_by(team_id=team.id, mission_slug=current.slug)
        .order_by(Observation.created_at.desc()).limit(50).all()]

    subs = {s.question_key: s for s in Submission.query.filter_by(
        team_id=team.id, mission_slug=current.slug).all()}
    answers = []
    for n, q in enumerate(rendered["questions"], 1):
        s = subs.get(q["key"])
        value = json.loads(s.answer_json) if s and s.answer_json else None
        links = EvidenceLink.query.filter_by(submission_id=s.id).all() if s else []
        answers.append({
            "n": n, "key": q["key"], "prompt": q["prompt"],
            "answer": _display_answer(q, value),
            "wants_evidence": q["evidence_required"],
            "evidence": [{"artifact": titles.get(e.artifact_id, e.artifact_id),
                          "url": e.source_url, "excerpt": e.excerpt} for e in links],
            "reasoning": s.reasoning if s else None,
            "confidence": s.confidence if s else None,
            "updated_at": s.updated_at.isoformat() if s else None,
        })

    out["current"] = {
        "slug": current.slug, "title": rendered["title"], "state": current.state,
        "elapsed": current.elapsed_seconds(), "remaining": current.remaining_seconds(),
        "locked": is_locked(team, current.slug),
        "sources": sources,
        "observations": observations,
        "answers": answers,
        "hints_taken": len(hints_taken(team)),
    }
    return out


@bp.get("/board")
@require_assistant
def board():
    ev, team = _team()
    if ev is None:
        return render_template("assistant/pick.html", ev=None, teams=[])
    if team is None:
        return redirect(url_for("assistant.pick"))
    return render_template("assistant/board.html", ev=ev, team=team,
                           data=_payload(ev, team),
                           survey=_survey(ev, team),
                           questions=TA_QUESTIONS,
                           scale=TA_SCALE,
                           hint_scale=TA_HINT_SCALE,
                           frequency=TA_FREQUENCY_QUESTIONS,
                           sections=(("teamwork", ("participation", "discussion",
                                                   "split", "challenge")),
                                     ("interface", ("platform", "task")),
                                     ("hints", ("hints",))),
                           lead=is_facilitator())


def _survey(ev, team):
    row = AssistantReport.query.filter_by(team_id=team.id).one_or_none()
    if row is None:
        row = AssistantReport(session_id=ev.id, team_id=team.id)
        db.session.add(row)
        db.session.commit()
    stored = json.loads(row.answers_json) if row.answers_json else {}
    return {"answers": {k: v for k, v in stored.items() if not k.startswith("__")},
            "period": stored.get("__period", ""),
            "notes": row.notes or "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


@bp.post("/api/survey")
@require_assistant
def save_survey():
    ev, team = _team()
    if ev is None or team is None:
        return jsonify(error="no_team"), 404
    data = request.get_json(silent=True) or {}
    row = AssistantReport.query.filter_by(team_id=team.id).one_or_none()
    if row is None:
        row = AssistantReport(session_id=ev.id, team_id=team.id)
        db.session.add(row)

    answers = json.loads(row.answers_json) if row.answers_json else {}
    for key, value in (data.get("answers") or {}).items():
        allowed = TA_HINT_SCALE if key in TA_FREQUENCY_QUESTIONS else TA_SCALE
        if key in TA_QUESTIONS and str(value) in allowed:
            answers[key] = str(value)
    if "period" in data:
        answers["__period"] = (data.get("period") or "").strip()[:120]
    row.answers_json = json.dumps(answers)
    if "notes" in data:
        row.notes = (data.get("notes") or "").strip()[:4000]
    scored = [k for k in answers if not k.startswith("__")]
    audit(ev.id, "assistant", team.code, "ta_survey_saved", team.code,
          f"{len(scored)}/{len(TA_QUESTIONS)} answered")
    db.session.commit()
    return jsonify(ok=True,
                   answers={k: v for k, v in answers.items()
                            if not k.startswith("__")},
                   period=answers.get("__period", ""),
                   updated_at=row.updated_at.isoformat() if row.updated_at else None)


@bp.get("/api/board")
@require_assistant
def api_board():
    ev, team = _team()
    if ev is None or team is None:
        return jsonify(error="no_team"), 404
    return jsonify(_payload(ev, team))
