import json
import re
import unicodedata

from .models import (
    REQUIRED_IOC_TYPES,
    SRC_AUTO,
    SRC_COLLECTIVE,
    SRC_OVERRIDE,
    SRC_RUBRIC,
    SRC_SPEED,
    UNLOCK_AUTOMATIC,
    CollectiveIOC,
    HintUsage,
    IOC_TYPES,
    ScoreEvent,
    Submission,
    Team,
    TeamMission,
    aware,
    db,
    utcnow,
)
from . import missions as content

SPEED_THRESHOLD = 0.70

COLLECTIVE_BONUS = 15.0


def normalise(value):
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = s.strip().strip("\"'`").strip()
    s = re.sub(r"\s+", " ", s)
    return s.casefold()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def decode_answer(submission):
    if submission is None or not submission.answer_json:
        return None
    try:
        return json.loads(submission.answer_json)
    except (ValueError, TypeError):
        return None


def _distractor_count(question, want):
    options = question.get("options") or []
    if not options:
        return 0
    return sum(1 for o in options if normalise(o.get("id")) not in want)


def check_answer(question, answer):
    qtype = question.get("type")
    validator = question.get("validator") or {}
    kind = validator.get("kind")
    accept = validator.get("accept")

    if accept is None and qtype in ("choice", "multi_choice"):
        accept = [o["id"] for o in question.get("options") or [] if o.get("correct")]
        kind = kind or "set_equal"

    if qtype == "free_text":
        return None, 0.0                      # a human decides; never auto-scored

    if answer is None or answer == "" or answer == []:
        return False, 0.0

    if qtype in ("short_text", "choice", "confidence"):
        got = normalise(answer if not isinstance(answer, list) else (answer or [None])[0])
        if kind == "regex":
            ok = bool(re.fullmatch(str(accept), got, re.IGNORECASE))
            return ok, 1.0 if ok else 0.0
        if kind == "contains":
            ok = any(normalise(a) in got for a in _as_list(accept))
            return ok, 1.0 if ok else 0.0
        ok = got in {normalise(a) for a in _as_list(accept)}
        return ok, 1.0 if ok else 0.0

    if qtype in ("multi_choice", "graph"):
        want = {normalise(a) for a in _as_list(accept)}
        got = {normalise(a) for a in _as_list(answer)}
        if not want:
            return False, 0.0
        if kind == "subset":
            if got - want:
                penalty = len(got - want) / max(len(want), 1)
                ratio = max(0.0, len(got & want) / len(want) - penalty)
                return False, round(ratio, 4)
            ratio = len(got & want) / len(want)
            return ratio >= 1.0, round(ratio, 4)
        if got == want:
            return True, 1.0
        total_wrong = _distractor_count(question, want)
        earned = len(got & want) / len(want)
        penalty = (len(got - want) / total_wrong) if total_wrong else 0.0
        return False, round(max(0.0, min(1.0, earned - penalty)), 4)

    if qtype == "order" and kind == "constraints":
        return check_order_constraints(validator, answer)

    if qtype == "order":
        want = [normalise(a) for a in _as_list(accept)]
        got = [normalise(a) for a in _as_list(answer)]
        if got == want:
            return True, 1.0
        if len(want) < 2:
            return False, 0.0
        index = {v: i for i, v in enumerate(got)}
        pairs = correct = 0
        for i in range(len(want)):
            for j in range(i + 1, len(want)):
                a, b = want[i], want[j]
                if a in index and b in index:
                    pairs += 1
                    if index[a] < index[b]:
                        correct += 1
        return False, round(correct / pairs, 4) if pairs else 0.0

    return False, 0.0


def check_order_constraints(validator, answer):
    picked = [normalise(a) for a in _as_list(answer)]
    if not picked:
        return False, 0.0

    rules = [len(set(picked)) == len(picked)]

    exact_count = validator.get("exact_count")
    if exact_count:
        rules.append(len(picked) == exact_count)

    first = validator.get("first")
    if first:
        rules.append(picked[0] == normalise(first))

    for item in validator.get("must_include") or []:
        rules.append(normalise(item) in picked)

    for pair in validator.get("before") or []:
        earlier, later = normalise(pair[0]), normalise(pair[1])
        if earlier in picked and later in picked:
            rules.append(picked.index(earlier) < picked.index(later))
        else:
            rules.append(True)

    for item in validator.get("must_not") or []:
        rules.append(normalise(item) not in picked)

    if not rules:
        return False, 0.0
    satisfied = sum(1 for ok in rules if ok)
    ratio = satisfied / len(rules)
    return satisfied == len(rules), round(ratio, 4)


def check_evidence(question, submission):
    accepted = {a for a in question.get("accepted_evidence") or []}
    if not accepted:
        return False
    links = list(submission.evidence) if submission is not None else []
    if not links:
        return False
    return any(link.artifact_id in accepted for link in links)


def submitted_remaining_seconds(mission_row, tm):
    if tm.submitted_at is None or mission_row.opened_at is None:
        return 0
    elapsed = (aware(tm.submitted_at) - aware(mission_row.opened_at)).total_seconds()
    elapsed = max(0.0, elapsed - mission_row.paused_seconds)
    return int(max(0, min(mission_row.total_seconds,
                          mission_row.total_seconds - elapsed)))


def score_mission(session, team, mission_row, definition, actor="server"):
    tm = _team_mission(session, team, mission_row.slug)
    if tm.scored_at is not None:
        return []

    subs = {s.question_key: s for s in Submission.query.filter_by(
        team_id=team.id, mission_slug=mission_row.slug).all()}

    events = []
    correctness_earned = correctness_total = 0.0

    for q in definition.get("questions") or []:
        key = q["key"]
        cat = q.get("category", "correctness")
        points = float(q.get("points", 0))
        ev_points = float(q.get("evidence_points", 0))
        sub = subs.get(key)
        answer = decode_answer(sub)

        if q["type"] in content.AUTO_TYPES:
            correct, ratio = check_answer(q, answer)
            earned = round(points * ratio, 2)
            if cat == "correctness":
                correctness_earned += earned
                correctness_total += points
            if earned:
                events.append(_event(session, team, mission_row.slug, SRC_AUTO, cat,
                                     earned, f"{key}: {'correct' if correct else 'partial'}",
                                     key, actor))

        if ev_points:
            if check_evidence(q, sub):
                events.append(_event(session, team, mission_row.slug, SRC_AUTO, "evidence",
                                     ev_points, f"{key}: evidence linked to an accepted artifact",
                                     key, actor))

    speed_budget = float((definition.get("budget") or {}).get("speed", 0))
    if speed_budget and correctness_total:
        ratio = correctness_earned / correctness_total
        if ratio >= SPEED_THRESHOLD and tm.submitted_at is not None:
            left = submitted_remaining_seconds(mission_row, tm)
            frac = left / max(1, mission_row.total_seconds)
            bonus = round(speed_budget * frac, 2)
            if bonus:
                events.append(_event(session, team, mission_row.slug, SRC_SPEED, "speed",
                                     bonus,
                                     f"{int(ratio * 100)}% correctness, {left}s remaining",
                                     None, actor))

    tm.scored_at = utcnow()
    db.session.add_all(events)
    db.session.commit()
    return events


def undo_auto_scoring(session, mission_row, actor="server"):
    events, touched = [], 0
    closed_at = aware(mission_row.closed_at) if mission_row.closed_at else None

    for team in Team.query.filter_by(session_id=session.id).all():
        tm = TeamMission.query.filter_by(
            team_id=team.id, mission_slug=mission_row.slug).one_or_none()
        if tm is None or tm.scored_at is None:
            continue

        auto = ScoreEvent.query.filter(
            ScoreEvent.session_id == session.id,
            ScoreEvent.team_id == team.id,
            ScoreEvent.mission_slug == mission_row.slug,
            ScoreEvent.source.in_((SRC_AUTO, SRC_SPEED))).all()
        for e in auto:
            if not e.points:
                continue
            events.append(_event(session, team, mission_row.slug, SRC_OVERRIDE,
                                 e.category, -e.points,
                                 f"reopened: reverses {e.reason}",
                                 e.question_key, actor))

        if (closed_at is not None and tm.submitted_at is not None
                and aware(tm.submitted_at) == closed_at):
            tm.submitted_at = None
            tm.locked_by = None

        tm.scored_at = None
        touched += 1

    if events:
        db.session.add_all(events)
    if touched:
        db.session.commit()
    return touched


def _team_mission(session, team, slug):
    tm = TeamMission.query.filter_by(team_id=team.id, mission_slug=slug).one_or_none()
    if tm is None:
        tm = TeamMission(session_id=session.id, team_id=team.id, mission_slug=slug)
        db.session.add(tm)
        db.session.flush()
    return tm


def _event(session, team, slug, source, category, points, reason, question_key, actor):
    return ScoreEvent(session_id=session.id, team_id=team.id, mission_slug=slug,
                      source=source, category=category, points=points, reason=reason,
                      question_key=question_key, actor=actor)


def charge_hint(session, team, mission_slug, hint, mode=UNLOCK_AUTOMATIC):
    existing = HintUsage.query.filter_by(team_id=team.id, hint_id=hint["id"]).one_or_none()
    if existing is not None:
        return existing, False
    usage = HintUsage(session_id=session.id, team_id=team.id, mission_slug=mission_slug,
                      hint_id=hint["id"], cost=0,
                      level=hint.get("level"), unlock_mode=mode)
    db.session.add(usage)
    db.session.commit()
    return usage, True


def hints_taken(team):
    return {h.hint_id for h in HintUsage.query.filter_by(team_id=team.id).all()}


def grade_rubric(session, team, mission_slug, rubric_item, points, actor, reason=None):
    cat = rubric_item.get("category", "reasoning")
    key = rubric_item["key"]
    cap = float(rubric_item.get("points", 0))
    points = max(0.0, min(cap, float(points)))

    graded_before = ScoreEvent.query.filter_by(
        team_id=team.id, question_key=key).filter(
        ScoreEvent.source.in_((SRC_RUBRIC, SRC_OVERRIDE))).all()
    prior = sum(e.points for e in graded_before)
    delta = round(points - prior, 2)
    if abs(delta) < 0.001 and graded_before:
        return None
    source = SRC_RUBRIC if not graded_before else SRC_OVERRIDE
    why = reason or (f"rubric {key} = {points}/{cap}" if source == SRC_RUBRIC
                     else f"rubric {key} re-graded {prior} → {points}")
    event = _event(session, team, mission_slug, source, cat, delta, why, key, actor)
    db.session.add(event)
    db.session.commit()
    return event


def override(session, team, mission_slug, category, points, reason, actor):
    """A free-hand correction. A reason is mandatory — the caller enforces it."""
    event = _event(session, team, mission_slug, SRC_OVERRIDE, category,
                   float(points), reason, None, actor)
    db.session.add(event)
    db.session.commit()
    return event


def collective_coverage(session):
    rows = CollectiveIOC.query.filter_by(session_id=session.id, validated=True).all()
    covered = {r.ioc_type for r in rows}
    return {t: (t in covered) for t in REQUIRED_IOC_TYPES}


def maybe_award_collective(session):
    """Award the shared bonus once, when every category is covered."""
    coverage = collective_coverage(session)
    if not all(coverage.values()):
        return []
    already = ScoreEvent.query.filter_by(session_id=session.id, source=SRC_COLLECTIVE).first()
    if already is not None:
        return []
    events = []
    for team in Team.query.filter_by(session_id=session.id).all():
        events.append(_event(session, team, None, SRC_COLLECTIVE, "collective",
                             COLLECTIVE_BONUS,
                             "class covered every IOC category", None, "server"))
    db.session.add_all(events)
    db.session.commit()
    return events


def score_maxima(content_dir):
    base = float(sum(float(m.get("max_points", 0))
                     for m in content.ordered_missions(content_dir)))
    final = content.final_definition(content_dir)
    if final is not None:
        base += float(final.get("max_points", 0))
    return {"base": base,
            "collective_bonus": COLLECTIVE_BONUS,
            "total": base + COLLECTIVE_BONUS}


def required_rubric_items(content_dir):
    out = []
    definitions = list(content.ordered_missions(content_dir))
    final = content.final_definition(content_dir)
    if final is not None:
        definitions.append(final)
    for m in definitions:
        for item in m.get("rubric") or []:
            out.append({"mission_slug": m["slug"], "key": item["key"],
                        "points": float(item.get("points", 0))})
    return out


def grading_progress(session, content_dir):
    required = required_rubric_items(content_dir)
    teams = Team.query.filter_by(session_id=session.id).order_by(Team.id).all()
    graded = {(e.team_id, e.question_key) for e in ScoreEvent.query.filter_by(
        session_id=session.id).filter(
        ScoreEvent.source.in_((SRC_RUBRIC, SRC_OVERRIDE))).all()
        if e.question_key}

    per_team = []
    done = 0
    for team in teams:
        missing = [item for item in required
                   if (team.id, item["key"]) not in graded]
        done += len(required) - len(missing)
        per_team.append({"team_id": team.id, "name": team.display_name,
                         "done": len(required) - len(missing),
                         "required": len(required),
                         "missing": [{"key": i["key"],
                                      "mission_slug": i["mission_slug"],
                                      "points": i["points"]} for i in missing]})
    total = len(required) * len(teams)
    return {"required_per_team": len(required), "teams": len(teams),
            "done": done, "total": total, "remaining": total - done,
            "complete": done == total and total > 0,
            "per_team": per_team,
            "incomplete_teams": [t["name"] for t in per_team if t["missing"]]}


def team_score_parts(team):
    """This team's points split into what it earned and what the class did."""
    rows = ScoreEvent.query.filter_by(team_id=team.id).all()
    collective = round(sum(r.points for r in rows if r.source == SRC_COLLECTIVE), 2)
    total = round(sum(r.points for r in rows), 2)
    return {"base": round(total - collective, 2),
            "collective": collective,
            "total": total}


def team_total(team):
    rows = ScoreEvent.query.filter_by(team_id=team.id).all()
    return round(sum(r.points for r in rows), 2)


def team_breakdown(team):
    """Points by mission and by category, plus the running total."""
    rows = ScoreEvent.query.filter_by(team_id=team.id).all()
    by_mission, by_category = {}, {}
    for r in rows:
        by_mission[r.mission_slug or "session"] = round(
            by_mission.get(r.mission_slug or "session", 0) + r.points, 2)
        by_category[r.category] = round(by_category.get(r.category, 0) + r.points, 2)
    return {"total": round(sum(r.points for r in rows), 2),
            "by_mission": by_mission, "by_category": by_category,
            "events": len(rows)}


def team_question_breakdown(team, content_dir):
    rows = []
    definitions = list(content.ordered_missions(content_dir))
    final = content.final_definition(content_dir)
    if final is not None:
        definitions.append(final)

    events = ScoreEvent.query.filter_by(team_id=team.id).all()
    for definition in definitions:
        slug = definition.get("slug")
        subs = {s.question_key: s for s in Submission.query.filter_by(
            team_id=team.id, mission_slug=slug).all()}
        for q in definition.get("questions") or []:
            key = q["key"]
            sub = subs.get(key)
            answer = decode_answer(sub) if sub is not None else None
            answered = answer not in (None, "", [], {})
            correct, ratio = (False, 0.0)
            if answered:
                try:
                    correct, ratio = check_answer(q, answer)
                except Exception:            # a prose answer has no validator
                    correct, ratio = (False, 0.0)
            rows.append({
                "mission": slug,
                "question": key,
                "mission_title": content.tx(definition.get("title"), "en"),
                "prompt": content.tx(q.get("prompt"), "en"),
                "category": q.get("category", "correctness"),
                "type": q.get("type"),
                "answered": answered,
                "correct": bool(correct),
                "ratio": round(float(ratio or 0), 3),
                "awarded": round(sum(e.points for e in events
                                     if e.question_key == key), 2),
                "max_points": round(float(q.get("points", 0))
                                    + float(q.get("evidence_points", 0)), 2),
                "answer": answer,
            })
    return rows


def leaderboard(session):
    out = []
    for team in Team.query.filter_by(session_id=session.id).order_by(Team.id).all():
        breakdown = team_breakdown(team)
        parts = team_score_parts(team)
        out.append({
            "team_id": team.id,
            "name": team.display_name,
            "color": team.color_key,
            "total": breakdown["total"],
            "base": parts["base"],
            "collective": parts["collective"],
            "evidence": breakdown["by_category"].get("evidence", 0),
            "by_mission": breakdown["by_mission"],
            "by_category": breakdown["by_category"],
        })
    out.sort(key=lambda r: (-r["total"], -r["evidence"], r["name"]))
    for i, row in enumerate(out, 1):
        row["rank"] = i
    return out
