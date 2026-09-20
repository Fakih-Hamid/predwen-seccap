import json
import os

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .. import external
from .. import missions as content
from .. import reports
from .. import ui
from ..auth import (
    SESSION_MEMBER,
    admin_preview,
    client_addr,
    current_event,
    current_lang,
    current_member,
    current_team,
    new_member_token,
    rate_limit,
    require_member,
    require_member_api,
    require_member_or_facilitator,
    touch_member,
)
from ..artifacts import DOWNLOAD_MAX_BYTES, artifact_brief, artifact_payload, resolve, snapshot_path
from ..models import (
    IOC_TYPES,
    MISSION_CLOSED,
    MISSION_LOCKED,
    MISSION_OPEN,
    SESSION_CLOSED,
    UNLOCK_STUDENT,
    CollectiveIOC,
    EvidenceLink,
    FinalReport,
    Member,
    Observation,
    Submission,
    Team,
    audit,
    db,
    utcnow,
)
from ..scoring import (
    charge_hint,
    collective_coverage,
    decode_answer,
    hints_taken,
    leaderboard,
    score_maxima,
    team_breakdown,
)
from ..state import (
    MAX_TEAM_NAME,
    active_session,
    can_write,
    is_locked,
    lock_submission,
    mission_row,
    mission_rows,
    hint_is_open,
    hint_opens_in,
    names_locked,
    next_locked_rung,
    open_pivot,
    source_has_arrived,
    visible_source_ids,
    unlock_hint,
    unlock_mode_of,
    rename_team,
    session_by_code,
    team_by_code,
    team_mission,
)

bp = Blueprint("participant", __name__)


def _cd():
    return current_app.config["CONTENT_DIR"]


def _clip(value, limit=None):
    limit = limit or current_app.config["MAX_TEXT"]
    return (value or "").strip()[:limit]


def _can_write(ev, team, row):
    return can_write(ev, team, row) or admin_preview()


def _blocked_by(ev, team, slug):
    if admin_preview():
        return []
    lang = current_lang()
    blocking = []
    for m in content.ordered_missions(_cd()):
        if m["slug"] == slug:
            break
        row = mission_row(ev, m["slug"])
        if row is None or not _can_write(ev, team, row):
            continue
        blocking.append({"slug": m["slug"],
                         "title": content.tx(m.get("title"), lang)})
    return blocking


def _can_write_now(ev, team, row):
    return _can_write(ev, team, row) and not _blocked_by(ev, team, row.slug)


def mission_progress(definition, team, slug):
    questions = (definition or {}).get("questions") or []
    keys = [q["key"] for q in questions]
    wanted = {q["key"] for q in questions if q.get("evidence_points")}

    answered = 0
    with_evidence = 0
    for s in Submission.query.filter_by(team_id=team.id, mission_slug=slug).all():
        if s.question_key not in keys:
            continue
        if s.question_key in wanted and EvidenceLink.query.filter_by(
                submission_id=s.id).first() is not None:
            with_evidence += 1
        value = json.loads(s.answer_json) if s.answer_json else None
        if isinstance(value, str):
            value = value.strip()
        if value in (None, "", [], {}):
            continue
        answered += 1

    return {
        "answered": answered,
        "questions": len(keys),
        "with_evidence": with_evidence,
        "evidence_wanted": len(wanted),
        "observations": Observation.query.filter_by(
            team_id=team.id, mission_slug=slug).count(),
    }


def _split_sources(definition, payload, row, team):
    visible = set(visible_source_ids(row, team, definition))
    here, coming, hidden = [], [], 0
    for card in payload.get("artifacts") or []:
        if admin_preview() or card["id"] in visible:
            here.append(card)
        else:
            hidden += 1
            if not coming:
                coming.append({"id": card["id"]})
    for c in coming:
        c["remaining"] = hidden
    return here, coming


def _download_name(entry, full_path):
    local = os.path.basename(full_path)
    raw = entry.get("external_url")
    key = external.placeholder_key(raw) if raw else None
    if key is None:
        return local

    declared = external.load(_cd()).get(key) or {}
    url = declared.get("url") or ""
    candidate = os.path.basename(url.split("?", 1)[0].rstrip("/"))
    stem, dot, extension = candidate.rpartition(".")
    if not (stem and dot and 1 <= len(extension) <= 5 and extension.isalnum()):
        return local
    return candidate.replace("\\", "").replace("/", "").replace('"', "")


def _source_url(raw):
    value = _clip(raw, 500)
    if not value:
        return None
    low = value.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return None
    return value


def _open_flags(ev):
    preview = admin_preview()
    return {
        "final_open": ev.final_open or preview,
        "collective_open": ev.collective_open or preview,
        "scoreboard_visible": ev.scoreboard_visible or preview,
    }


@bp.get("/join")
def join():
    if current_member() is not None and not admin_preview():
        return redirect(url_for("participant.team_home"))
    ev = active_session()
    return render_template("join.html", ev=ev,
                           teams=_joinable_teams(ev),
                           preview_teams=_preview_teams(ev))


def _joinable_teams(ev):
    if ev is None:
        return []
    return [{"id": t.id, "name": t.display_name, "color": t.color_key}
            for t in Team.query.filter_by(session_id=ev.id).order_by(Team.id).all()]


def _preview_teams(ev):
    if ev is None or not admin_preview():
        return []
    return Team.query.filter_by(session_id=ev.id).order_by(Team.id).all()


@bp.post("/join")
def do_join():
    if not rate_limit("join", client_addr(), current_app.config["JOIN_RPM"]):
        return render_template("join.html", ev=active_session(),
                               teams=_joinable_teams(active_session()),
                               error="rate"), 429

    typed = (request.form.get("session_code") or "").strip()
    ev = session_by_code(typed) if typed else active_session()
    if ev is None or ev.state == SESSION_CLOSED:
        return render_template("join.html", ev=active_session(),
                               teams=_joinable_teams(active_session()),
                               error="session" if typed else "no_session"), 400

    code = request.form.get("team_code")
    picked = None
    raw_id = (request.form.get("team_id") or "").strip()
    if raw_id.isdigit():
        picked = Team.query.filter_by(session_id=ev.id, id=int(raw_id)).first()
    by_code = team_by_code(ev, code)
    if picked is not None:
        team = picked if by_code is not None and by_code.id == picked.id else None
    else:
        team = by_code
    if team is None:
        return render_template("join.html", ev=ev,
                               teams=_joinable_teams(ev), error="team"), 400

    nickname = _clip(request.form.get("nickname"), 40)
    if not nickname:
        return render_template("join.html", ev=ev,
                               teams=_joinable_teams(ev), error="nickname"), 400

    existing = Member.query.filter_by(team_id=team.id).filter(
        db.func.lower(Member.nickname) == nickname.lower()).first()
    if existing is not None:
        existing.last_seen_at = utcnow()
        audit(ev.id, "member", existing.id, "rejoined", team.code)
        db.session.commit()
        member = existing
    else:
        capacity = team.capacity
        if capacity is not None and team.members.count() >= capacity:
            return render_template("join.html", ev=ev, teams=_joinable_teams(ev),
                                   error="full"), 409
        member = Member(team_id=team.id, nickname=nickname, token=new_member_token())
        db.session.add(member)
        db.session.flush()
        audit(ev.id, "member", member.id, "joined", team.code)
        db.session.commit()
        if capacity is not None:
            seats = [m.id for m in Member.query.filter_by(team_id=team.id)
                     .order_by(Member.id).all()]
            if seats.index(member.id) >= capacity:
                audit(ev.id, "member", member.id, "join_refused_full", team.code)
                db.session.delete(member)
                db.session.commit()
                return render_template("join.html", ev=ev,
                                       teams=_joinable_teams(ev),
                                       error="full"), 409

    session[SESSION_MEMBER] = member.token
    session.permanent = True
    return redirect(url_for("participant.briefing"))


@bp.post("/leave")
@require_member_api
def leave():
    session.pop(SESSION_MEMBER, None)
    return jsonify(ok=True)


@bp.post("/api/team-name")
@require_member_api
def set_team_name():
    ev, team = current_event(), current_team()
    ok, error = rename_team(ev, team, (request.get_json(silent=True) or {}).get("name"))
    if not ok:
        return jsonify(error=error), 409 if error == "locked" else 400
    return jsonify(ok=True, name=team.display_name)


@bp.get("/briefing")
@require_member
def briefing():
    return render_template("briefing.html", ev=current_event(),
                           team=current_team())


@bp.get("/lobby")
@require_member
def lobby():
    return redirect(url_for("participant.team_home"))


@bp.get("/team")
@require_member
def team_home():
    ev, team = current_event(), current_team()
    touch_member(current_member())
    lang = current_lang()
    rows = {r.slug: r for r in mission_rows(ev)}
    cards = []
    for m in content.ordered_missions(_cd()):
        row = rows.get(m["slug"])
        cards.append({
            "slug": m["slug"],
            "title": content.tx(m.get("title"), lang),
            "subtitle": content.tx(m.get("subtitle"), lang),
            "state": row.state if row else MISSION_LOCKED,
            "remaining": row.remaining_seconds() if row else 0,
            "locked_by_team": is_locked(team, m["slug"]),
            "max_points": float(m.get("max_points", 0)),
            "href": url_for("participant.mission", slug=m["slug"]),
        })

    final = content.final_definition(_cd())
    if final is not None:
        open_now = _open_flags(ev)["final_open"]
        been_there = FinalReport.query.filter_by(team_id=team.id).first() is not None
        cards.append({
            "slug": final["slug"],
            "title": content.tx(final.get("title"), lang),
            "subtitle": content.tx(final.get("subtitle"), lang),
            "state": (MISSION_OPEN if open_now else
                      MISSION_CLOSED if been_there else MISSION_LOCKED),
            "remaining": 0,
            "locked_by_team": False,
            "max_points": float(final.get("max_points", 0)),
            "href": url_for("participant.final"),
        })

    return render_template("team.html", ev=ev, team=team, cards=cards,
                           scoreboard_open=_open_flags(ev)["scoreboard_visible"],
                           names_locked=names_locked(ev),
                           max_team_name=MAX_TEAM_NAME,
                           members=team.members.order_by(Member.joined_at).all())


@bp.get("/mission/<slug>")
@require_member
def mission(slug):
    ev, team = current_event(), current_team()
    definition = content.get_mission(_cd(), slug)
    row = mission_row(ev, slug)
    if definition is None or row is None:
        abort(404)
    if row.state == MISSION_LOCKED and not admin_preview():
        return render_template("mission_locked.html", ev=ev, team=team,
                               members=team.members.order_by(Member.joined_at).all(),
                               title=content.tx(definition.get("title"), current_lang())), 200
    blocking = _blocked_by(ev, team, slug)
    if blocking:
        return render_template("mission_blocked.html", ev=ev, team=team,
                               missions=blocking, slug=slug,
                               title=content.tx(definition.get("title"),
                                                current_lang())), 200
    team_mission(ev, team, slug)
    touch_member(current_member())
    payload = content.render_mission(definition, current_lang(), _cd())
    ordered = [m["slug"] for m in content.ordered_missions(_cd())]
    payload["index"] = ordered.index(slug) + 1 if slug in ordered else 1
    payload["total"] = len(ordered)
    payload["artifacts"], payload["pending"] = _split_sources(
        definition, payload, row, team)
    next_url = next_label = None
    states = {r.slug: r.state for r in mission_rows(ev)}
    for later in ordered[payload["index"]:]:
        if states.get(later) == MISSION_OPEN:
            later_def = content.get_mission(_cd(), later)
            next_url = url_for("participant.mission", slug=later)
            next_label = content.tx(later_def.get("title"), current_lang())
            break
    if next_url is None and ev.final_open:
        next_url = url_for("participant.final")
        next_label = ui.t(_cd(), "final.title", current_lang())

    return render_template("mission.html", ev=ev, team=team, mission=payload, row=row,
                           locked=is_locked(team, slug), taken=sorted(hints_taken(team)),
                           next_url=next_url, next_label=next_label,
                           progress=mission_progress(definition, team, slug),
                           writable=_can_write_now(ev, team, row))


@bp.get("/artifacts/<artifact_id>")
@require_member
def artifact(artifact_id):
    ev = current_event()
    lang = current_lang()
    for row in mission_rows(ev):
        if row.state == MISSION_LOCKED and not admin_preview():
            continue
        definition = content.get_mission(_cd(), row.slug)
        entry = content.find_artifact(definition, artifact_id) if definition else None
        if entry is None:
            continue
        if not admin_preview() and not source_has_arrived(row, current_team(), entry):
            abort(404)
        launch = content.render_artifact_meta(entry, lang, _cd())["external"]
        runnable = content.run_it_yourself_is_live(entry, _cd())
        show_saved_copy = ((launch is None or not launch["published"])
                           and not runnable)
        payload = (artifact_payload(current_app.config["ARTIFACT_DIR"], entry,
                                    lang, _cd())
                   if show_saved_copy else artifact_brief(entry, lang, _cd()))
        team = current_team()
        open_pivot(ev, team, row, artifact_id)
        pivot_hints = []
        if entry.get("hints"):
            pivot_hints = [dict(h,
                                open=hint_is_open(ev, row, team, h),
                                opens_in=hint_opens_in(ev, row, team, h))
                           for h in content.pivot_ladder(entry, lang, _cd())]
        return render_template("artifact.html", ev=ev, team=team,
                               artifact=payload, mission_slug=row.slug,
                               mission_title=content.tx(definition.get("title"), lang),
                               launch=launch,
                               pivot_hints=pivot_hints,
                               show_saved_copy=show_saved_copy,
                               shell_unavailable=(
                                   bool(entry.get("run_it_yourself"))
                                   and not runnable))
    abort(404)


@bp.get("/artifacts/<artifact_id>/download")
@require_member
def artifact_download(artifact_id):
    ev, team, lang = current_event(), current_team(), current_lang()
    for row in mission_rows(ev):
        if row.state == MISSION_LOCKED and not admin_preview():
            continue
        definition = content.get_mission(_cd(), row.slug)
        entry = content.find_artifact(definition, artifact_id) if definition else None
        if entry is None:
            continue
        if not admin_preview() and not source_has_arrived(row, team, entry):
            abort(404)

        launch = content.render_artifact_meta(entry, lang, _cd())["external"]
        if launch is not None and launch["published"]:
            return jsonify(error="use_the_external_resource", url=launch["url"]), 409
        if content.run_it_yourself_is_live(entry, _cd()):
            return jsonify(error="run_it_yourself"), 409

        rel = snapshot_path(entry)
        full = resolve(current_app.config["ARTIFACT_DIR"], rel) if rel else None
        if full is None:
            abort(404)
        size = os.path.getsize(full)
        if size > DOWNLOAD_MAX_BYTES:
            return jsonify(error="too_large", bytes=size), 413

        with open(full, "rb") as handle:
            raw = handle.read(DOWNLOAD_MAX_BYTES)
        response = current_app.response_class(
            raw, mimetype="application/octet-stream")
        response.headers["Content-Disposition"] = (
            "attachment; filename=\"%s\"" % _download_name(entry, full))
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Length"] = str(len(raw))
        audit(ev.id, "member", current_member().id, "artifact_download", artifact_id)
        db.session.commit()
        return response
    abort(404)


@bp.post("/api/observation")
@require_member_api
def post_observation():
    """One member's note, on the team's shared board. Never scored."""
    ev, team, member = current_event(), current_team(), current_member()
    data = request.get_json(silent=True) or {}
    slug = data.get("mission_slug")
    row = mission_row(ev, slug)
    if row is None or not _can_write_now(ev, team, row):
        return jsonify(error="mission_closed"), 409
    text = _clip(data.get("text"))
    if not text:
        return jsonify(error="empty"), 400
    obs = Observation(session_id=ev.id, team_id=team.id, member_id=member.id,
                      mission_slug=slug, artifact_id=_clip(data.get("artifact_id"), 80) or None,
                      text=text)
    db.session.add(obs)
    db.session.commit()
    return jsonify(ok=True, id=obs.id)


@bp.get("/evidence")
@require_member
def evidence_board():
    return render_template("evidence.html", ev=current_event(), team=current_team())


@bp.get("/api/observations")
@require_member_api
def get_observations():
    """This team's board. Filtered on the cookie's team, never on a parameter."""
    team = current_team()
    touch_member(current_member())
    slug = request.args.get("mission_slug")
    q = Observation.query.filter_by(team_id=team.id)
    if slug:
        q = q.filter_by(mission_slug=slug)
    rows = q.order_by(Observation.created_at.desc()).limit(200).all()
    lang = current_lang()
    titles, missions = {}, {}
    for m in content.ordered_missions(_cd()):
        missions[m["slug"]] = content.tx(m.get("title"), lang)
        definition = content.get_mission(_cd(), m["slug"])
        for a in (definition or {}).get("artifacts") or []:
            titles[a["id"]] = content.tx(a.get("title"), lang)
    return jsonify(observations=[{
        "id": o.id, "text": o.text, "artifact_id": o.artifact_id,
        "artifact_title": titles.get(o.artifact_id) or o.artifact_id,
        "mission_slug": o.mission_slug,
        "mission_title": missions.get(o.mission_slug) or o.mission_slug,
        "nickname": o.member.nickname if o.member else "â€”",
        "at": o.created_at.isoformat(),
    } for o in rows])


@bp.get("/api/investigation-graph")
@require_member_api
def investigation_graph():
    ev, team = current_event(), current_team()
    lang = current_lang()
    touch_member(current_member())

    nodes, edges, seen = [], [], set()

    def add(node_id, kind, label, extra=None):
        if node_id in seen:
            return
        seen.add(node_id)
        nodes.append(dict({"id": node_id, "kind": kind, "label": label}, **(extra or {})))

    for row in mission_rows(ev):
        definition = content.get_mission(_cd(), row.slug)
        if definition is None:
            continue
        subs = {s.question_key: s for s in Submission.query.filter_by(
            team_id=team.id, mission_slug=row.slug).all()}
        for q in definition.get("questions") or []:
            sub = subs.get(q["key"])
            links = list(sub.evidence) if sub is not None else []
            if sub is None or (not links and not sub.answer_json):
                continue
            finding = f"q:{q['key']}"
            answer = (reports.readable_answer(q, decode_answer(sub), lang)
                      if sub.answer_json else None)
            add(finding, "finding", content.tx(q.get("prompt"), lang),
                {"mission": row.slug, "answered": bool(sub.answer_json),
                 "answer": answer, "confidence": sub.confidence})
            for link in links:
                entry = content.find_artifact(definition, link.artifact_id)
                source = f"a:{link.artifact_id}"
                add(source, "source",
                    content.tx(entry.get("title"), lang) if entry else link.artifact_id,
                    {"mission": row.slug,
                     "external": bool(entry and entry.get("external_url")),
                     "url": link.source_url})
                edges.append({"src": source, "dst": finding, "relation": "supports",
                              "excerpt": link.excerpt, "url": link.source_url})

    return jsonify(nodes=nodes, edges=edges,
                   sources=sum(1 for n in nodes if n["kind"] == "source"),
                   findings=sum(1 for n in nodes if n["kind"] == "finding"))


@bp.post("/api/submission")
@require_member_api
def save_submission():
    """The team's shared answer to one question. Any member may write it."""
    ev, team, member = current_event(), current_team(), current_member()
    if not rate_limit("submit", team.id, current_app.config["SUBMIT_RPM"]):
        return jsonify(error="rate", message="Slow down a moment."), 429

    data = request.get_json(silent=True) or {}
    slug, key = data.get("mission_slug"), data.get("question_key")
    row = mission_row(ev, slug)
    definition = content.get_mission(_cd(), slug)
    if row is None or definition is None:
        return jsonify(error="unknown_mission"), 404
    question = content.find_question(definition, key)
    if question is None:
        return jsonify(error="unknown_question"), 404
    if not _can_write_now(ev, team, row):
        return jsonify(error="locked"), 409

    sub = Submission.query.filter_by(team_id=team.id, question_key=key).one_or_none()
    if sub is None:
        sub = Submission(session_id=ev.id, team_id=team.id, mission_slug=slug, question_key=key)
        db.session.add(sub)
    sub.answer_json = json.dumps(data.get("answer"))[:20000]
    sub.confidence = (data.get("confidence") if data.get("confidence") in
                      ("low", "medium", "high") else sub.confidence)
    sub.reasoning = _clip(data.get("reasoning")) or sub.reasoning
    sub.updated_by = member.id
    db.session.commit()
    return jsonify(ok=True, updated_at=sub.updated_at.isoformat(),
                   by=member.nickname)


@bp.post("/api/evidence")
@require_member_api
def save_evidence():
    ev, team = current_event(), current_team()
    data = request.get_json(silent=True) or {}
    slug, key = data.get("mission_slug"), data.get("question_key")
    row = mission_row(ev, slug)
    definition = content.get_mission(_cd(), slug)
    if row is None or definition is None or content.find_question(definition, key) is None:
        return jsonify(error="unknown_question"), 404
    if not _can_write_now(ev, team, row):
        return jsonify(error="locked"), 409

    artifact_id = _clip(data.get("artifact_id"), 80)
    if not artifact_id or content.find_artifact(definition, artifact_id) is None:
        return jsonify(error="unknown_artifact"), 400

    sub = Submission.query.filter_by(team_id=team.id, question_key=key).one_or_none()
    if sub is None:
        sub = Submission(session_id=ev.id, team_id=team.id, mission_slug=slug, question_key=key)
        db.session.add(sub)
        db.session.flush()

    EvidenceLink.query.filter_by(submission_id=sub.id).delete()
    link = EvidenceLink(submission_id=sub.id, artifact_id=artifact_id,
                        source_url=_source_url(data.get("source_url")),
                        excerpt=_clip(data.get("excerpt")),
                        reasoning=_clip(data.get("reasoning")),
                        confidence=(data.get("confidence")
                                    if data.get("confidence") in ("low", "medium", "high") else None))
    db.session.add(link)
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/api/mission-state")
@require_member_api
def mission_state():
    ev, team = current_event(), current_team()
    member = current_member()
    touch_member(member)
    slug = request.args.get("mission_slug")
    row = mission_row(ev, slug)
    if row is None:
        return jsonify(error="unknown_mission"), 404
    definition = content.get_mission(_cd(), slug)

    subs = Submission.query.filter_by(team_id=team.id, mission_slug=slug).all()
    nicknames = {m.id: m.nickname for m in team.members.all()}
    answers = {}
    for s in subs:
        links = EvidenceLink.query.filter_by(submission_id=s.id).all()
        answers[s.question_key] = {
            "answer": json.loads(s.answer_json) if s.answer_json else None,
            "confidence": s.confidence,
            "reasoning": s.reasoning,
            "updated_by": nicknames.get(s.updated_by),
            "evidence": [{"artifact_id": e.artifact_id, "source_url": e.source_url,
                          "excerpt": e.excerpt, "reasoning": e.reasoning,
                          "confidence": e.confidence} for e in links],
            "updated_at": s.updated_at.isoformat(),
        }

    visible = set(visible_source_ids(row, team, definition))
    pending, hidden = None, 0
    lang = current_lang()
    sources = []
    for entry in (definition or {}).get("artifacts") or []:
        if admin_preview() or entry["id"] in visible:
            sources.append({"id": entry["id"],
                            "title": content.tx(entry.get("title"), lang),
                            "icon": entry.get("icon", "▤"),
                            "tool": entry.get("tool"),
                            "tool_name": (entry.get("tool_name")
                                          if entry.get("external_url") else None)})
        else:
            hidden += 1
            if pending is None:
                pending = entry["id"]

    posted = {o.member_id for o in Observation.query.filter_by(
        team_id=team.id, mission_slug=slug).all()}
    members = [{"id": m.id, "nickname": m.nickname,
                "online": m.online, "posted": m.id in posted, "is_me": m.id == member.id}
               for m in team.members.order_by(Member.joined_at).all()]

    return jsonify(
        mission={"slug": row.slug, "state": row.state,
                 "remaining": row.remaining_seconds(), "total": row.total_seconds,
                 "writable": _can_write_now(ev, team, row),
                 "past_suggested": row.past_suggested_time()},
        team_locked=is_locked(team, slug),
        progress=mission_progress(definition, team, slug),
        answers=answers,
        members=members,
        sources=sources,
        pending=pending,
        pending_remaining=hidden,
        hints_taken=sorted(hints_taken(team)),
        hints=[{"id": h["id"],
                "open": hint_is_open(ev, row, team, h),
                "opens_in": hint_opens_in(ev, row, team, h)}
               for h in content.all_hints(definition or {})],
        announcement=ev.announcement,
        session_state=ev.state,
        **_open_flags(ev),
    )


@bp.get("/api/session-state")
@require_member_api
def session_state():
    ev = current_event()
    touch_member(current_member())
    return jsonify(state=ev.state, scores_locked=ev.scores_locked,
                   announcement=ev.announcement,
                   announcement_at=ev.announcement_at.isoformat() if ev.announcement_at else None,
                   missions_finished=not _missions_not_finished(ev, current_team()),
                   ready=sorted(m["slug"] for m in content.ordered_missions(_cd())
                                if not _blocked_by(ev, current_team(), m["slug"])),
                   **_open_flags(ev))


@bp.post("/api/hint")
@require_member_api
def take_hint():
    """Charge once, then hand over the body. Re-reading is free."""
    ev, team = current_event(), current_team()
    data = request.get_json(silent=True) or {}
    slug, hint_id = data.get("mission_slug"), data.get("hint_id")
    definition = content.get_mission(_cd(), slug)
    row = mission_row(ev, slug)
    if definition is None or row is None:
        return jsonify(error="unknown_mission"), 404
    if not _can_write_now(ev, team, row):
        return jsonify(error="mission_closed"), 409
    lang = current_lang()
    hint = content.find_hint(definition, hint_id, lang, _cd())
    if hint is None:
        return jsonify(error="unknown_hint"), 404
    if not hint_is_open(ev, row, team, hint):
        return jsonify(error="locked", opens_in=hint_opens_in(ev, row, team, hint)), 409
    body = content.hint_body(definition, hint_id, lang, _cd())
    _, charged = charge_hint(ev, team, slug, body,
                             mode=unlock_mode_of(team, hint_id))
    return jsonify(ok=True, hint=body, charged=charged)


@bp.post("/api/hint/next")
@require_member_api
def unlock_next_hint():
    ev, team = current_event(), current_team()
    data = request.get_json(silent=True) or {}
    slug = data.get("mission_slug")
    definition = content.get_mission(_cd(), slug)
    row = mission_row(ev, slug)
    if definition is None or row is None:
        return jsonify(error="unknown_mission"), 404
    if not _can_write_now(ev, team, row):
        return jsonify(error="mission_closed"), 409

    lang = current_lang()
    artifact_id = data.get("artifact_id")
    if artifact_id:
        artifact = content.find_artifact(definition, artifact_id)
        if artifact is None or not artifact.get("external_url"):
            return jsonify(error="unknown_artifact"), 404
        open_pivot(ev, team, row, artifact_id)
        ladder = content.pivot_ladder(artifact, lang, _cd())
    else:
        ladder = list(definition.get("hints") or [])

    hint = next_locked_rung(ev, row, team, ladder)
    if hint is None:
        return jsonify(ok=True, already_open=True)
    unlock_hint(ev, team, row, hint, mode=UNLOCK_STUDENT,
                actor=str(current_member().id))
    return jsonify(ok=True, hint_id=hint["id"], level=hint["level"])


@bp.post("/api/lock")
@require_member_api
def lock():
    ev, team, member = current_event(), current_team(), current_member()
    slug = (request.get_json(silent=True) or {}).get("mission_slug")
    row = mission_row(ev, slug)
    if row is None or not (row.accepts_writes() or admin_preview()):
        return jsonify(error="mission_closed"), 409
    if _blocked_by(ev, team, slug):
        return jsonify(error="finish_the_previous_mission"), 409
    if is_locked(team, slug):
        return jsonify(ok=True, already=True)
    lock_submission(ev, team, row, member)
    return jsonify(ok=True)


@bp.get("/collective-intel")
@require_member
def collective():
    ev = current_event()
    return render_template("collective.html", ev=ev, team=current_team(),
                           ioc_types=IOC_TYPES)


@bp.get("/api/collective")
@require_member_api
def collective_state():
    ev = current_event()
    touch_member(current_member())
    rows = CollectiveIOC.query.filter_by(session_id=ev.id).order_by(
        CollectiveIOC.created_at).all()
    return jsonify(
        open=ev.collective_open or admin_preview(),
        coverage=collective_coverage(ev),
        entries=[{"type": r.ioc_type, "value": r.value, "justification": r.justification,
                  "team": r.team.display_name if r.team else "â€”",
                  "color": r.team.color_key if r.team else "teal",
                  "validated": r.validated,
                  "mine": r.team_id == current_team().id} for r in rows],
    )


@bp.post("/api/collective")
@require_member_api
def publish_ioc():
    """One verified indicator per team. Publishing again replaces it."""
    ev, team = current_event(), current_team()
    if not ev.collective_open and not admin_preview():
        return jsonify(error="closed"), 409
    data = request.get_json(silent=True) or {}
    ioc_type = data.get("type")
    value = _clip(data.get("value"), 200)
    if ioc_type not in IOC_TYPES or not value:
        return jsonify(error="bad_ioc"), 400

    row = CollectiveIOC.query.filter_by(session_id=ev.id, team_id=team.id).one_or_none()
    if row is None:
        row = CollectiveIOC(session_id=ev.id, team_id=team.id)
        db.session.add(row)
    row.ioc_type = ioc_type
    row.value = value
    row.justification = _clip(data.get("justification"), 400)
    row.validated = False          # a facilitator validates; publishing does not
    db.session.commit()
    return jsonify(ok=True)


def _missions_not_finished(ev, team):
    out = []
    lang = current_lang()
    for m in content.ordered_missions(_cd()):
        row = mission_row(ev, m["slug"])
        if row is None or not _can_write(ev, team, row):
            continue
        out.append(content.tx(m.get("title"), lang))
    return out


@bp.get("/final")
@require_member
def final():
    ev, team = current_event(), current_team()

    existing = FinalReport.query.filter_by(team_id=team.id).one_or_none()
    closed = not ev.final_open and not admin_preview()
    if closed and existing is None:
        return render_template("final_closed.html", ev=ev, team=team)
    outstanding = _missions_not_finished(ev, team)
    if outstanding and not admin_preview():
        return render_template("final_not_yet.html", ev=ev, team=team,
                               missions=outstanding)
    definition = content.final_definition(_cd())
    if definition is None:
        abort(404)
    report = existing if closed else reports.get_or_create(ev, team)
    lang = current_lang()
    payload = reports.report_payload(report, definition, lang)
    if closed:
        payload["locked"] = True
    return render_template(
        "final.html", ev=ev, team=team, closed=closed,
        dossier=reports.assemble(ev, team, _cd(), lang),
        report=payload,
        definition=content.render_mission(definition, lang, _cd()))


@bp.post("/api/final")
@require_member_api
def save_final():
    ev, team = current_event(), current_team()
    if not ev.final_open and not admin_preview():
        return jsonify(error="closed"), 409
    if _missions_not_finished(ev, team) and not admin_preview():
        return jsonify(error="missions_open"), 409
    report = reports.get_or_create(ev, team)
    if report.locked_at is not None:
        return jsonify(error="locked"), 409
    data = request.get_json(silent=True) or {}

    definition = content.final_definition(_cd()) or {}
    for field, column, key in (
            ("timeline", "timeline_json", "final_timeline"),
            ("response", "response_json", "final_response_now"),
            ("response_later", "response_future_json", "final_response_later")):
        if isinstance(data.get(field), list):
            setattr(report, column, json.dumps(reports.sanitise_order(
                data[field], reports.order_items(definition, key))))
    for field in ("verdict", "confirmed_facts", "inferences", "unknowns",
                  "scope", "limitations", "response_other"):
        if field in data:
            setattr(report, field, _clip(data[field]))
    db.session.commit()
    return jsonify(ok=True, updated_at=report.updated_at.isoformat())


@bp.post("/api/final/lock")
@require_member_api
def lock_final():
    ev, team = current_event(), current_team()
    if not ev.final_open and not admin_preview():
        return jsonify(error="closed"), 409
    if _missions_not_finished(ev, team) and not admin_preview():
        return jsonify(error="missions_open"), 409
    report = reports.get_or_create(ev, team)
    if report.locked_at is None:
        report.locked_at = utcnow()
        audit(ev.id, "member", current_member().id, "final_lock", team.code)
        db.session.commit()
    return jsonify(ok=True)


@bp.get("/presentation")
@require_member
def presentation():
    ev, team = current_event(), current_team()
    return render_template("presentation.html", ev=ev, team=team,
                           slide=reports.presentation(ev, team, _cd(), current_lang()),
                           projector=False)


def _scoreboard_session():
    return current_event() or active_session()


@bp.get("/scoreboard")
@require_member_or_facilitator
def scoreboard():
    ev = _scoreboard_session()
    return render_template("scoreboard.html", ev=ev, projector=("projector" in request.args))


@bp.get("/api/scoreboard")
@require_member_or_facilitator
def scoreboard_data():
    ev = _scoreboard_session()
    if ev is None:
        return jsonify(visible=False, rows=[], columns=[])
    if not ev.scoreboard_visible and not admin_preview():
        return jsonify(visible=False, rows=[], columns=[], locked=ev.scores_locked)

    lang = current_lang()
    columns = [{"slug": m["slug"], "label": content.tx(m.get("short_label"), lang)
                or f"M{m.get('order', 0)}"}
               for m in content.ordered_missions(_cd())]
    final = content.final_definition(_cd())
    if final is not None:
        columns.append({"slug": final["slug"],
                        "label": content.tx(final.get("short_label"), lang) or "Final"})

    return jsonify(visible=True, locked=ev.scores_locked,
                   provisional=not ev.scores_locked,
                   columns=columns,
                   rows=leaderboard(ev),
                   maxima=score_maxima(_cd()),
                   awards=[{"key": a.award_key,
                            "team": db.session.get(Team, a.team_id).display_name}
                           for a in ev_awards(ev)])


def ev_awards(ev):
    from ..models import Award
    return Award.query.filter_by(session_id=ev.id).all()


@bp.get("/api/my-score")
@require_member_api
def my_score():
    ev, team = current_event(), current_team()
    if not ev.scoreboard_visible and not admin_preview():
        return jsonify(visible=False)
    return jsonify(visible=True, provisional=not ev.scores_locked,
                   breakdown=team_breakdown(team))
