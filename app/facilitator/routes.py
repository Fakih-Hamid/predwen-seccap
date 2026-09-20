import csv
import io
import json

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from .. import external
from .. import missions as content
from .. import reports
from .. import ui
from ..auth import current_lang, facilitator_login, facilitator_logout, require_facilitator
from ..models import (
    AWARDS,
    TA_QUESTIONS,
    AssistantReport,
    aware,
    IOC_TYPES,
    MISSION_LOCKED,
    MISSION_OPEN,
    SESSION_BRIEFING,
    UNLOCK_FACILITATOR,
    SESSION_DEBRIEF,
    SESSION_FINAL,
    Award,
    AuditEvent,
    CollectiveIOC,
    EvidenceLink,
    FinalReport,
    Member,
    Observation,
    ScoreEvent,
    Submission,
    Team,
    TeamMission,
    audit,
    db,
    utcnow,
)
from ..scoring import (
    collective_coverage,
    decode_answer,
    grade_rubric,
    grading_progress,
    hints_taken,
    leaderboard,
    maybe_award_collective,
    override,
    score_maxima,
    team_breakdown,
    team_question_breakdown,
    team_score_parts,
)
from ..state import (
    DEFAULT_TEAM_COUNT,
    active_session,
    add_team,
    close_mission,
    apply_table_plan,
    create_session,
    extend_mission,
    hint_is_open,
    hint_opens_in,
    mission_row,
    mission_rows,
    next_locked_mission_rung,
    open_mission,
    pause_mission,
    reset_session,
    unlock_hint,
    unlock_submission,
)

bp = Blueprint("facilitator", __name__, url_prefix="/facilitator")

FIELD_LABELS = {
    "confirmed_facts": {"ja": "【確認できた事実】", "en": "[Confirmed facts]"},
    "inferences":      {"ja": "【妥当な推論】", "en": "[Inferences]"},
    "unknowns":        {"ja": "【まだ分からないこと】", "en": "[Unknowns]"},
}


def _cd():
    return current_app.config["CONTENT_DIR"]


def expected_answer(q, lang, mission=None):
    if q.get("type") == "free_text":
        for item in (mission or {}).get("rubric") or []:
            if item.get("response_key") != q["key"]:
                continue
            lines = content.tx_list(item.get("criteria"), lang)
            note = content.tx(item.get("facilitator_note"), lang)
            if note:
                lines = list(lines) + [note]
            if lines:
                return "   ·   ".join(lines)
        return content.tx(q.get("help"), lang) or None

    validator = q.get("validator") or {}
    accept = validator.get("accept")
    options = q.get("options") or []
    labels = {o["id"]: content.tx(o.get("label"), lang) for o in options}
    for item in q.get("items") or []:
        labels[item["id"]] = content.tx(item.get("label"), lang)

    if validator.get("kind") == "constraints":
        def named(ids):
            return "、".join(str(labels.get(i, i)) for i in ids) if lang == "ja"                    else ", ".join(str(labels.get(i, i)) for i in ids)
        parts = []
        if validator.get("exact_count"):
            parts.append(("ちょうど%d件" if lang == "ja" else "exactly %d")
                         % validator["exact_count"])
        if validator.get("first"):
            parts.append(("1番目：%s" if lang == "ja" else "first: %s")
                         % labels.get(validator["first"], validator["first"]))
        if validator.get("must_include"):
            parts.append(("必ず含む：%s" if lang == "ja" else "must include: %s")
                         % named(validator["must_include"]))
        for earlier, later in validator.get("before") or []:
            parts.append(("%s は %s より前" if lang == "ja" else "%s before %s")
                         % (labels.get(earlier, earlier), labels.get(later, later)))
        if validator.get("must_not"):
            parts.append(("入れてはいけない：%s" if lang == "ja" else "never: %s")
                         % named(validator["must_not"]))
        return "   ·   ".join(parts) or None

    if accept is None:
        accept = [o["id"] for o in options if o.get("correct")]
    if not accept:
        return None

    values = accept if isinstance(accept, (list, tuple)) else [accept]
    joiner = "  →  " if q.get("type") == "order" else "   ·   "
    return joiner.join(str(labels.get(v, v)) for v in values)


def _actor():
    return "facilitator"


def _ev_or_404():
    ev = active_session()
    if ev is None:
        return None
    return ev


@bp.get("/login")
def login():
    return render_template("facilitator/login.html",
                           disabled=not current_app.config.get("FACILITATOR_PASSWORD_HASH"))


@bp.post("/login")
def do_login():
    if facilitator_login(request.form.get("password")):
        return redirect(request.args.get("next") or url_for("facilitator.console"))
    return render_template("facilitator/login.html", error=True,
                           disabled=not current_app.config.get("FACILITATOR_PASSWORD_HASH")), 401


@bp.post("/logout")
@require_facilitator
def logout():
    facilitator_logout()
    return redirect(url_for("meta.landing"))


@bp.get("/")
@require_facilitator
def console():
    ev = active_session()
    return render_template("facilitator/console.html", ev=ev,
                           missions=(mission_rows(ev) if ev else []),
                           defs={m["slug"]: m for m in content.ordered_missions(_cd())},
                           maxima=score_maxima(_cd()),
                           lang=current_lang(), awards=AWARDS)


@bp.get("/scoring")
@require_facilitator
def scoring():
    cd = _cd()
    lang = current_lang()
    rows = []
    for m in content.ordered_missions(cd):
        rows.append({"name": content.tx(m.get("title"), lang),
                     "points": int(m.get("max_points", 0)),
                     "budget": m.get("budget") or {}, "final": False})
    final = content.final_definition(cd)
    if final is not None:
        rows.append({"name": content.tx(final.get("title"), lang),
                     "points": int(final.get("max_points", 0)),
                     "budget": final.get("budget") or {}, "final": True})
    return render_template("facilitator/scoring.html",
                           maxima=score_maxima(cd), rows=rows)


@bp.post("/session")
@require_facilitator
def new_session():
    try:
        teams = int(request.form.get("teams") or DEFAULT_TEAM_COUNT)
    except (TypeError, ValueError):
        teams = DEFAULT_TEAM_COUNT
    ev = create_session(current_app.config,
                        title=(request.form.get("title") or "").strip() or None,
                        team_count=max(1, min(10, teams)),
                        actor=_actor())
    apply_table_plan(ev)
    return redirect(url_for("facilitator.console", created=ev.code))


@bp.post("/session/team")
@require_facilitator
def add_one_team():
    ev = _ev_or_404()
    if ev is None:
        return jsonify(error="no_session"), 404

    team = add_team(ev, actor=_actor())
    return jsonify(ok=True, team={"id": team.id, "name": team.display_name,
                                  "code": team.code,
                                  "color": team.color_key or None})


@bp.post("/session/team/<int:team_id>/capacity")
@require_facilitator
def set_team_capacity(team_id):
    from ..models import audit

    ev = _ev_or_404()
    team = db.session.get(Team, team_id)
    if ev is None or team is None or team.session_id != ev.id:
        return jsonify(error="no_team"), 404

    raw = (request.get_json(silent=True) or {}).get("capacity")
    if raw is None or str(raw).strip() == "":
        capacity = None
    else:
        try:
            capacity = int(str(raw).strip())
        except (TypeError, ValueError):
            return jsonify(error="bad_capacity"), 400
        if not 1 <= capacity <= 50:
            return jsonify(error="bad_capacity"), 400

    team.capacity = capacity
    audit(ev.id, "facilitator", _actor(), "team_capacity", team.code,
          "no limit" if capacity is None else str(capacity))
    db.session.commit()
    return jsonify(ok=True, capacity=capacity)


@bp.post("/session/state")
@require_facilitator
def session_state():
    ev = _ev_or_404()
    if ev is None:
        return jsonify(error="no_session"), 404
    data = request.get_json(silent=True) or {}
    flags = ("scoreboard_visible", "provisional_visible", "collective_open",
             "final_open", "scores_locked")

    if data.get("scores_locked") and not ev.scores_locked:
        progress = grading_progress(ev, _cd())
        if not progress["complete"]:
            return jsonify(
                error="grading_incomplete",
                message=ui.t(_cd(), "facilitator.lock_blocked", current_lang(),
                             remaining=progress["remaining"],
                             teams=", ".join(progress["incomplete_teams"])),
                grading=progress), 409

    for flag in flags:
        if flag in data:
            setattr(ev, flag, bool(data[flag]))
            audit(ev.id, "facilitator", _actor(), f"set_{flag}", ev.code, str(bool(data[flag])))
    if data.get("state") in (SESSION_BRIEFING, SESSION_FINAL, SESSION_DEBRIEF):
        ev.state = data["state"]
        audit(ev.id, "facilitator", _actor(), "session_state", ev.code, data["state"])
    if "announcement" in data:
        text = (data["announcement"] or "").strip()[:400]
        ev.announcement = text or None
        ev.announcement_at = utcnow() if text else None
        audit(ev.id, "facilitator", _actor(), "announce", ev.code, text[:120])
    if data.get("final_open"):
        for team in Team.query.filter_by(session_id=ev.id).all():
            reports.get_or_create(ev, team)
    db.session.commit()
    return jsonify(ok=True, **{f: getattr(ev, f) for f in flags}, state=ev.state)


@bp.post("/session/reset")
@require_facilitator
def do_reset():
    """Closes the session. Nothing is deleted — export first."""
    ev = _ev_or_404()
    if ev is None:
        return jsonify(error="no_session"), 404
    reset_session(ev, actor=_actor())
    return jsonify(ok=True)


@bp.post("/mission/<slug>/<action>")
@require_facilitator
def mission_control(slug, action):
    ev = _ev_or_404()
    row = mission_row(ev, slug) if ev else None
    if row is None:
        return jsonify(error="unknown_mission"), 404
    if action == "open":
        open_mission(ev, row, _actor())
    elif action == "pause":
        pause_mission(ev, row, _actor())
    elif action == "close":
        close_mission(ev, row, _actor())
    elif action == "extend":
        seconds = int((request.get_json(silent=True) or {}).get("seconds", 300))
        extend_mission(ev, row, seconds, _actor())
    else:
        return jsonify(error="unknown_action"), 400
    return jsonify(ok=True, state=row.state, remaining=row.remaining_seconds(),
                   total=row.total_seconds)


@bp.post("/mission/<slug>/unlock/<int:team_id>")
@require_facilitator
def unlock(slug, team_id):
    ev = _ev_or_404()
    team = db.session.get(Team, team_id)
    row = mission_row(ev, slug) if ev else None
    if ev is None or team is None or team.session_id != ev.id or row is None:
        return jsonify(error="not_found"), 404
    unlock_submission(ev, team, row, _actor())
    return jsonify(ok=True)


def _next_locked_rung(ev, team, defs):
    return next_locked_mission_rung(ev, team, _cd())


@bp.post("/api/hint/unlock")
@require_facilitator
def unlock_hint_for_team():
    ev = _ev_or_404()
    if ev is None:
        return jsonify(error="no_session"), 404
    data = request.get_json(silent=True) or {}
    team = db.session.get(Team, data.get("team_id"))
    if team is None or team.session_id != ev.id:
        return jsonify(error="unknown_team"), 404

    slug = data.get("mission_slug")
    definition = content.get_mission(_cd(), slug)
    row = mission_row(ev, slug)
    if definition is None or row is None:
        return jsonify(error="unknown_mission"), 404
    hint = content.find_hint(definition, data.get("hint_id"), current_lang(), _cd())
    if hint is None:
        return jsonify(error="unknown_hint"), 404

    unlock_hint(ev, team, row, hint, mode=UNLOCK_FACILITATOR, actor=_actor())
    return jsonify(ok=True, team=team.display_name, hint_id=hint["id"],
                   level=hint["level"])


def _idle_for(iso):
    if not iso:
        return 0
    from datetime import datetime
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return 0
    return max(0, (utcnow() - aware(then)).total_seconds())


def _last_activity(team):
    stamps = []
    latest = (Submission.query.filter_by(team_id=team.id)
              .order_by(Submission.updated_at.desc()).first())
    if latest is not None:
        stamps.append(latest.updated_at)
    note = (Observation.query.filter_by(team_id=team.id)
            .order_by(Observation.created_at.desc()).first())
    if note is not None:
        stamps.append(note.created_at)
    stamps = [s for s in stamps if s is not None]
    return max(stamps).isoformat() if stamps else None


def _mission_cards(ev, defs, lang):
    cards = [{"slug": r.slug,
              "title": content.tx(defs[r.slug].get("title"), lang)
                       if r.slug in defs else r.slug,
              "state": r.state, "remaining": r.remaining_seconds(),
              "total": r.total_seconds, "order": r.order, "switch": None}
             for r in mission_rows(ev)]

    final = content.final_definition(_cd())
    if final is not None:
        cards.append({"slug": final["slug"],
                      "title": content.tx(final.get("title"), lang),
                      "state": MISSION_OPEN if ev.final_open else MISSION_LOCKED,
                      "remaining": None, "total": None,
                      "order": int(final.get("order", 99)),
                      "switch": "final_open"})
    return cards


@bp.get("/api/progress")
@require_facilitator
def progress():
    """One payload for the whole console: teams, presence, timers, submissions."""
    ev = _ev_or_404()
    if ev is None:
        return jsonify(session=None)
    lang = current_lang()
    defs = {m["slug"]: m for m in content.ordered_missions(_cd())}

    rows = []
    for team in Team.query.filter_by(session_id=ev.id).order_by(Team.id).all():
        members = team.members.order_by(Member.joined_at).all()
        per_mission = {}
        for slug, definition in defs.items():
            questions = definition.get("questions") or []
            total_q = len(questions)
            answered = Submission.query.filter_by(team_id=team.id, mission_slug=slug).filter(
                Submission.answer_json.isnot(None)).count()
            evidence = (db.session.query(EvidenceLink).join(Submission)
                        .filter(Submission.team_id == team.id,
                                Submission.mission_slug == slug).count())
            wants = {q["key"] for q in questions if q.get("evidence_points")}
            have = {s.question_key for s in
                    Submission.query.filter_by(team_id=team.id,
                                               mission_slug=slug).all()
                    if s.question_key in wants
                    and EvidenceLink.query.filter_by(submission_id=s.id).first()}
            tm = TeamMission.query.filter_by(team_id=team.id, mission_slug=slug).one_or_none()
            per_mission[slug] = {
                "answered": answered, "questions": total_q, "evidence": evidence,
                "evidence_wanted": len(wants),
                "evidence_missing": sorted(wants - have),
                "locked": bool(tm and tm.submitted_at),
                "scored": bool(tm and tm.scored_at),
            }
        report = FinalReport.query.filter_by(team_id=team.id).one_or_none()
        rows.append({
            "team_id": team.id, "code": team.code, "name": team.display_name,
            "color": team.color_key,
            "capacity": team.capacity,
            "members": [{"id": m.id, "nickname": m.nickname,
                         "online": m.online, "last_seen": (m.last_seen_at.isoformat()
                                                           if m.last_seen_at else None)}
                        for m in members],
            "online": sum(1 for m in members if m.online),
            "observations": Observation.query.filter_by(team_id=team.id).count(),
            "missions": per_mission,
            "final_locked": bool(report and report.locked_at),
            "score": team_breakdown(team),
            "hints_taken": sorted(hints_taken(team)),
            "next_locked": _next_locked_rung(ev, team, defs),
            "last_activity": _last_activity(team),
            "size": len(members),
        })

    open_rows = [r for r in mission_rows(ev) if r.state == MISSION_OPEN]
    active = open_rows[0] if open_rows else None
    active_slug = active.slug if active else None

    locked_now = sum(1 for r in rows
                     if active_slug and (r["missions"].get(active_slug) or {}).get("locked"))

    attention = []
    for r in rows:
        pm = (r["missions"].get(active_slug) or {}) if active_slug else {}
        if active_slug and not pm.get("locked"):
            if r["online"] == 0:
                attention.append({"name": r["name"], "why": "offline"})
            elif active and active.elapsed_seconds() > 480 and pm.get("answered", 0) == 0:
                attention.append({"name": r["name"], "why": "no_answers"})
            elif _idle_for(r["last_activity"]) > 600:
                attention.append({"name": r["name"], "why": "idle"})

    last_export = (AuditEvent.query.filter_by(session_id=ev.id, action="export")
                   .order_by(AuditEvent.ts.desc()).first())

    return jsonify(
        status={
            "active_mission": (content.tx(defs[active_slug].get("title"), lang)
                               if active_slug in defs else active_slug),
            "active_slug": active_slug,
            "elapsed": active.elapsed_seconds() if active else None,
            "remaining": active.remaining_seconds() if active else None,
            "teams_total": len(rows),
            "teams_locked": locked_now,
            "attention": attention,
            "last_export": ({"kind": last_export.target,
                             "at": last_export.ts.isoformat()} if last_export else None),
            "grading": grading_progress(ev, _cd()),
        },
        session={"code": ev.code, "title": ev.title, "state": ev.state,
                 "scoreboard_visible": ev.scoreboard_visible,
                 "provisional_visible": ev.provisional_visible,
                 "collective_open": ev.collective_open,
                 "final_open": ev.final_open,
                 "scores_locked": ev.scores_locked,
                 "announcement": ev.announcement},
        missions=_mission_cards(ev, defs, lang),
        teams=rows,
        collective={"coverage": collective_coverage(ev),
                    "entries": CollectiveIOC.query.filter_by(session_id=ev.id).count()},
        leaderboard=leaderboard(ev),
    )


@bp.get("/grade/<int:team_id>")
@require_facilitator
def grade(team_id):
    """Everything one team wrote, beside what it was authored to be worth."""
    ev = _ev_or_404()
    team = db.session.get(Team, team_id)
    if ev is None or team is None or team.session_id != ev.id:
        return render_template("error.html", code=404), 404
    lang = current_lang()

    report = FinalReport.query.filter_by(team_id=team.id).one_or_none()

    def final_answer(q):
        if report is None:
            return None
        if q["key"] == "final_response_other":
            return report.response_other or None
        column = {"final_timeline": report.timeline_json,
                  "final_response_now": report.response_json,
                  "final_response_later": report.response_future_json}.get(q["key"])
        if not column:
            return None
        try:
            ids = json.loads(column)
        except (TypeError, ValueError):
            return None
        if q["key"] != "final_timeline":
            ids = ids[:reports.TOP_ACTIONS]
        labels = {i["id"]: content.tx(i.get("label"), lang)
                  for i in q.get("items") or []}
        return "  →  ".join(str(labels.get(i, i)) for i in ids) or None

    final_slug = (content.final_definition(_cd()) or {}).get("slug")

    blocks = []
    for m in content.ordered_missions(_cd()) + \
            ([content.final_definition(_cd())] if content.final_definition(_cd()) else []):
        subs = {s.question_key: s for s in Submission.query.filter_by(
            team_id=team.id, mission_slug=m["slug"]).all()}
        titles = {a["id"]: content.tx(a.get("title"), lang)
                  for a in m.get("artifacts") or []}
        questions = []
        for q in m.get("questions") or []:
            sub = subs.get(q["key"])
            links = EvidenceLink.query.filter_by(submission_id=sub.id).all() if sub else []
            questions.append({
                "key": q["key"], "type": q["type"], "points": q.get("points", 0),
                "prompt": content.tx(q.get("prompt"), lang),
                "expected": expected_answer(q, lang, m),
                "answer": (final_answer(q) if m["slug"] == final_slug
                           else reports.readable_answer(q, decode_answer(sub), lang)),
                "reasoning": sub.reasoning if sub else None,
                "confidence": sub.confidence if sub else None,
                "evidence": [{"artifact_id": e.artifact_id,
                              "artifact_title": titles.get(e.artifact_id,
                                                           e.artifact_id),
                              "source_url": e.source_url,
                              "excerpt": e.excerpt,
                              "reasoning": e.reasoning} for e in links],
            })
        by_key = {q["key"]: q for q in questions}

        def written(r):
            if r.get("response_key"):
                return by_key.get(r["response_key"], {}).get("answer")
            fields = r.get("report_field")
            if not fields or report is None:
                return None
            if isinstance(fields, str):
                fields = [fields]
            parts = []
            for name in fields:
                value = (getattr(report, name, None) or "").strip()
                if value:
                    label = FIELD_LABELS.get(name, {}).get(lang)
                    parts.append("%s%s" % (label + "\n" if label else "", value))
            return "\n\n".join(parts) or None

        rubric = [{
            "key": r["key"], "points": float(r.get("points", 0)),
            "category": r.get("category", "reasoning"),
            "prompt": content.tx(r.get("prompt"), lang),
            "criteria": content.tx_list(r.get("criteria"), lang),
            "response_key": (r.get("response_key")
                             or (", ".join(r["report_field"])
                                 if isinstance(r.get("report_field"), list)
                                 else r.get("report_field"))),
            "response": written(r),
            "awarded": sum(e.points for e in ScoreEvent.query.filter_by(
                team_id=team.id, question_key=r["key"]).filter(
                ScoreEvent.source.in_(("rubric", "override"))).all()),
            "notes": content.tx(r.get("facilitator_note"), lang),
        } for r in m.get("rubric") or []]
        blocks.append({"slug": m["slug"], "title": content.tx(m.get("title"), lang),
                       "questions": questions, "rubric": rubric})

    events = ScoreEvent.query.filter_by(team_id=team.id).order_by(ScoreEvent.created_at).all()

    authored = {}
    for m in content.ordered_missions(_cd()) + \
            ([content.final_definition(_cd())] if content.final_definition(_cd()) else []):
        for q in m.get("questions") or []:
            authored[(m["slug"], q["key"])] = q

    autoscore = []
    for r in team_question_breakdown(team, _cd()):
        q = authored.get((r["mission"], r["question"]))
        r = dict(r, answer=(reports.readable_answer(q, r["answer"], lang)
                            if q is not None else r["answer"]))
        if not r["answered"]:
            why = "not answered"
        elif r["correct"]:
            why = "correct"
        elif r["ratio"] > 0:
            why = "partly correct (%d%%)" % round(r["ratio"] * 100)
        elif r["type"] == "free_text":
            why = "written answer — marked on the rubric above, not here"
        else:
            why = "wrong"
        if r["awarded"] and r["max_points"] and r["awarded"] > r["ratio"] * r["max_points"]:
            why += " · includes evidence points"
        r["why"] = why
        autoscore.append(r)

    return render_template("facilitator/grade.html", ev=ev, team=team, blocks=blocks,
                           report=report, events=events, autoscore=autoscore,
                           breakdown=team_breakdown(team),
                           categories=("correctness", "evidence", "reasoning",
                                       "completeness", "speed", "collective",
                                       "other"))


@bp.post("/api/grade")
@require_facilitator
def apply_grade():
    ev = _ev_or_404()
    data = request.get_json(silent=True) or {}
    team = db.session.get(Team, int(data.get("team_id") or 0))
    if ev is None or team is None or team.session_id != ev.id:
        return jsonify(error="not_found"), 404
    if ev.scores_locked:
        return jsonify(error="scores_locked"), 409

    slug, key = data.get("mission_slug"), data.get("rubric_key")
    definition = content.get_mission(_cd(), slug)
    item = content.find_rubric(definition, key) if definition else None
    if item is None:
        return jsonify(error="unknown_rubric"), 404
    event = grade_rubric(ev, team, slug, item, data.get("points", 0), _actor(),
                         reason=(data.get("reason") or "").strip()[:400] or None)
    return jsonify(ok=True, delta=(event.points if event else 0),
                   breakdown=team_breakdown(team))


@bp.post("/api/override")
@require_facilitator
def apply_override():
    ev = _ev_or_404()
    data = request.get_json(silent=True) or {}
    team = db.session.get(Team, int(data.get("team_id") or 0))
    reason = (data.get("reason") or "").strip()
    if ev is None or team is None or team.session_id != ev.id:
        return jsonify(error="not_found"), 404
    if ev.scores_locked:
        return jsonify(error="scores_locked"), 409
    if not reason:
        return jsonify(error="reason_required"), 400
    try:
        points = float(data.get("points"))
    except (TypeError, ValueError):
        return jsonify(error="bad_points"), 400
    override(ev, team, data.get("mission_slug"), data.get("category") or "other",
             points, reason[:400], _actor())
    return jsonify(ok=True, breakdown=team_breakdown(team))


@bp.post("/api/collective/validate")
@require_facilitator
def validate_ioc():
    ev = _ev_or_404()
    data = request.get_json(silent=True) or {}
    row = db.session.get(CollectiveIOC, int(data.get("ioc_id") or 0))
    if ev is None or row is None or row.session_id != ev.id:
        return jsonify(error="not_found"), 404
    row.validated = bool(data.get("validated", True))
    audit(ev.id, "facilitator", _actor(), "ioc_validate", f"{row.ioc_type}:{row.value}",
          str(row.validated))
    db.session.commit()
    awarded = maybe_award_collective(ev)
    return jsonify(ok=True, coverage=collective_coverage(ev), bonus_awarded=len(awarded) > 0)


@bp.get("/api/collective")
@require_facilitator
def collective_list():
    ev = _ev_or_404()
    if ev is None:
        return jsonify(entries=[])
    rows = CollectiveIOC.query.filter_by(session_id=ev.id).order_by(CollectiveIOC.created_at).all()
    return jsonify(coverage=collective_coverage(ev), types=list(IOC_TYPES),
                   entries=[{"id": r.id, "type": r.ioc_type, "value": r.value,
                             "justification": r.justification, "validated": r.validated,
                             "team": r.team.display_name if r.team else "—"} for r in rows])


@bp.post("/api/award")
@require_facilitator
def set_award():
    ev = _ev_or_404()
    data = request.get_json(silent=True) or {}
    key, team_id = data.get("award_key"), int(data.get("team_id") or 0)
    team = db.session.get(Team, team_id)
    if ev is None or key not in AWARDS or team is None or team.session_id != ev.id:
        return jsonify(error="bad_award"), 400
    row = Award.query.filter_by(session_id=ev.id, award_key=key).one_or_none()
    if row is None:
        row = Award(session_id=ev.id, award_key=key)
        db.session.add(row)
    row.team_id = team.id
    audit(ev.id, "facilitator", _actor(), "award", key, team.code)
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/present/<int:team_id>")
@require_facilitator
def present(team_id):
    ev = _ev_or_404()
    team = db.session.get(Team, team_id)
    if ev is None or team is None or team.session_id != ev.id:
        return render_template("error.html", code=404), 404
    return render_template("presentation.html", ev=ev, team=team,
                           slide=reports.presentation(ev, team, _cd(), current_lang()),
                           projector=True)


@bp.post("/api/final/score")
@require_facilitator
def score_finals():
    """Auto-score the two ordered parts of every team's synthesis."""
    ev = _ev_or_404()
    definition = content.final_definition(_cd())
    if ev is None or definition is None:
        return jsonify(error="not_found"), 404
    n = 0
    for team in Team.query.filter_by(session_id=ev.id).all():
        n += len(reports.score_final(ev, team, definition))
    audit(ev.id, "facilitator", _actor(), "final_scored", ev.code, f"{n} events")
    db.session.commit()
    return jsonify(ok=True, events=n)


def _record_export(ev, kind):
    audit(ev.id, "facilitator", _actor(), "export", kind, "")
    db.session.commit()


@bp.get("/export/results.csv")
@require_facilitator
def export_results():
    ev = _ev_or_404()
    if ev is None:
        return jsonify(error="no_session"), 404
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["session", "team", "mission", "source", "category", "points",
                "question", "actor", "reason", "at"])
    names = {t.id: t.display_name for t in Team.query.filter_by(session_id=ev.id).all()}
    for e in ScoreEvent.query.filter_by(session_id=ev.id).order_by(ScoreEvent.created_at).all():
        w.writerow([ev.code, names.get(e.team_id, e.team_id), e.mission_slug or "",
                    e.source, e.category, e.points, e.question_key or "",
                    e.actor or "", (e.reason or "").replace("\n", " "),
                    e.created_at.isoformat()])

    maxima = score_maxima(_cd())
    w.writerow([])
    w.writerow(["session", "team", "base_score", "collective_bonus", "total",
                "max_base", "max_collective_bonus", "max_total"])
    for team in Team.query.filter_by(session_id=ev.id).order_by(Team.id).all():
        parts = team_score_parts(team)
        w.writerow([ev.code, team.display_name, parts["base"],
                    parts["collective"], parts["total"],
                    maxima["base"], maxima["collective_bonus"], maxima["total"]])
    _record_export(ev, "scores")
    return _csv(buf, f"seccap_{ev.code}_scores.csv")


@bp.get("/export/scoring.csv")
@require_facilitator
def export_scoring():
    ev = _ev_or_404()
    if ev is None:
        return jsonify(error="no_session"), 404

    lang = current_lang()
    definitions = {m["slug"]: m for m in content.ordered_missions(_cd())}
    final = content.final_definition(_cd())
    if final is not None:
        definitions[final["slug"]] = final

    def flat(text):
        """One CSV cell is one line, as in submissions.csv."""
        return (text or "").replace(chr(10), " ")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["session", "team", "mission", "mission_title",
                "question", "question_prompt", "category", "type",
                "answered", "correct", "ratio", "awarded", "max_points",
                "answer", "answer_text"])
    for team in Team.query.filter_by(session_id=ev.id).order_by(Team.id).all():
        for r in team_question_breakdown(team, _cd()):
            answer = r["answer"]
            mission = definitions.get(r["mission"]) or {}
            q = content.find_question(mission, r["question"]) or {}
            readable = reports.readable_answer(q, answer, lang) if q else ""
            if isinstance(answer, (list, tuple)):
                answer = " | ".join(str(a) for a in answer)
            w.writerow([ev.code, team.display_name, r["mission"],
                        content.tx(mission.get("title"), lang),
                        r["question"], flat(content.tx(q.get("prompt"), lang)),
                        r["category"], r["type"],
                        "yes" if r["answered"] else "no",
                        "yes" if r["correct"] else "no",
                        r["ratio"], r["awarded"], r["max_points"],
                        "" if answer is None else str(answer),
                        flat(readable)])
    _record_export(ev, "scoring")
    return _csv(buf, f"seccap_{ev.code}_scoring.csv")


@bp.get("/export/submissions.csv")
@require_facilitator
def export_submissions():
    ev = _ev_or_404()
    if ev is None:
        return jsonify(error="no_session"), 404
    buf = io.StringIO()
    w = csv.writer(buf)
    lang = current_lang()
    w.writerow(["session", "team", "mission", "question", "question_prompt",
                "answer", "answer_text", "expected", "confidence",
                "reasoning", "evidence_artifact", "evidence_excerpt",
                "updated_at"])
    names = {t.id: t.display_name for t in Team.query.filter_by(session_id=ev.id).all()}
    definitions = {m["slug"]: m for m in content.ordered_missions(_cd())}
    final = content.final_definition(_cd())
    if final is not None:
        definitions[final["slug"]] = final

    def flat(text):
        """One CSV cell is one line. A pasted excerpt is often several."""
        return (text or "").replace(chr(10), " ")

    for s in Submission.query.filter_by(session_id=ev.id).order_by(Submission.id).all():
        links = EvidenceLink.query.filter_by(submission_id=s.id).all()
        link = links[0] if links else None
        mission = definitions.get(s.mission_slug) or {}
        q = content.find_question(mission, s.question_key) or {}
        titles = {a["id"]: content.tx(a.get("title"), lang)
                  for a in mission.get("artifacts") or []}
        w.writerow([ev.code, names.get(s.team_id, s.team_id), s.mission_slug,
                    s.question_key,
                    flat(content.tx(q.get("prompt"), lang)),
                    flat(s.answer_json),
                    flat(reports.readable_answer(q, decode_answer(s), lang)) if q else "",
                    flat(expected_answer(q, lang, mission)) if q else "",
                    s.confidence or "",
                    flat(s.reasoning),
                    titles.get(link.artifact_id, link.artifact_id) if link else "",
                    flat(link.excerpt) if link else "",
                    s.updated_at.isoformat()])

    _record_export(ev, "submissions")
    return _csv(buf, f"seccap_{ev.code}_submissions.csv")


@bp.get("/export/ta_reports.csv")
@require_facilitator
def export_ta_reports():
    ev = _ev_or_404()
    if ev is None:
        return jsonify(error="no_session"), 404
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["session", "team", "members", "period"] + list(TA_QUESTIONS)
               + ["notes", "updated_at"])
    for team in Team.query.filter_by(session_id=ev.id).order_by(Team.id).all():
        row = AssistantReport.query.filter_by(team_id=team.id).one_or_none()
        answers = json.loads(row.answers_json) if row and row.answers_json else {}
        w.writerow([ev.code, team.display_name,
                    Member.query.filter_by(team_id=team.id).count(),
                    answers.get("__period", "")]
                   + [answers.get(q, "") for q in TA_QUESTIONS]
                   + [(row.notes or "").replace(chr(10), " ") if row else "",
                      row.updated_at.isoformat() if row and row.updated_at else ""])
    return _csv(buf, f"seccap_{ev.code}_ta_reports.csv")


@bp.get("/export/session.json")
@require_facilitator
def export_session():
    ev = _ev_or_404()
    if ev is None:
        return jsonify(error="no_session"), 404
    teams = Team.query.filter_by(session_id=ev.id).order_by(Team.id).all()
    doc = {
        "session": {"code": ev.code, "title": ev.title, "state": ev.state,
                    "created_at": ev.created_at.isoformat(),
                    "scoreboard_visible": ev.scoreboard_visible,
                    "provisional_visible": ev.provisional_visible,
                    "collective_open": ev.collective_open,
                    "final_open": ev.final_open,
                    "scores_locked": ev.scores_locked,
                       "external": external.summary(_cd()),
                    "maxima": score_maxima(_cd())},
        "missions": [{"slug": r.slug, "state": r.state, "duration": r.duration_seconds,
                      "extension": r.extension_seconds, "version": r.content_version,
                      "opened_at": r.opened_at.isoformat() if r.opened_at else None}
                     for r in mission_rows(ev)],
        "teams": [],
        "audit": [{"actor_type": a.actor_type, "actor": a.actor_id, "action": a.action,
                   "target": a.target, "detail": a.detail, "at": a.ts.isoformat()}
                  for a in AuditEvent.query.filter_by(session_id=ev.id)
                  .order_by(AuditEvent.ts).all()],
    }
    for team in teams:
        report = FinalReport.query.filter_by(team_id=team.id).one_or_none()
        doc["teams"].append({
            "code": team.code, "name": team.display_name,
            "members": [{"nickname": m.nickname} for m in team.members],
            "observations": [{"mission": o.mission_slug, "artifact": o.artifact_id,
                              "text": o.text, "by": o.member.nickname if o.member else None,
                              "at": o.created_at.isoformat()}
                             for o in Observation.query.filter_by(team_id=team.id).all()],
            "submissions": [{"mission": s.mission_slug, "question": s.question_key,
                             "answer": json.loads(s.answer_json) if s.answer_json else None,
                             "confidence": s.confidence, "reasoning": s.reasoning,
                             "evidence": [{"artifact": e.artifact_id, "excerpt": e.excerpt,
                                           "reasoning": e.reasoning}
                                          for e in EvidenceLink.query.filter_by(
                                              submission_id=s.id).all()]}
                            for s in Submission.query.filter_by(team_id=team.id).all()],
            "final": ({"verdict": report.verdict,
                       "timeline": json.loads(report.timeline_json or "[]"),
                       "response": json.loads(report.response_json or "[]"),
                       "confirmed_facts": report.confirmed_facts,
                       "inferences": report.inferences, "unknowns": report.unknowns,
                       "limitations": report.limitations,
                       "locked_at": report.locked_at.isoformat() if report.locked_at else None}
                      if report else None),
            "score": team_breakdown(team),
        })
    _record_export(ev, "session")
    return Response(json.dumps(doc, ensure_ascii=False, indent=2),
                    mimetype="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="seccap_{ev.code}_session.json"'})


def _csv(buf, filename):
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
