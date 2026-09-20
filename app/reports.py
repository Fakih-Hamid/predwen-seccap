import json

from .models import (
    EvidenceLink,
    FinalReport,
    Submission,
    TeamMission,
    db,
    utcnow,
)
from . import missions as content
from .scoring import decode_answer, team_breakdown

TOP_ACTIONS = 5
TOP_EVIDENCE = 3


def order_item_defs(definition, key):
    """The authored items of one ordering question, as they were written."""
    for question in definition.get("questions") or []:
        if question["key"] == key:
            return list(question.get("items") or [])
    return []


def order_items(definition, key):
    """The ids a team is allowed to rank for one ordering question."""
    return [item["id"] for item in order_item_defs(definition, key)]


def sanitise_order(submitted, allowed):
    permitted, seen, clean = set(allowed), set(), []
    for raw in submitted:
        item = str(raw)[:60]
        if item in permitted and item not in seen:
            seen.add(item)
            clean.append(item)
    return clean


def _loads(raw, default):
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return value if isinstance(value, type(default)) else default


def assemble(ev, team, content_dir, lang):
    """The team's own evidence and answers from Missions 1–3, read-only."""
    dossier = []
    for m in content.ordered_missions(content_dir):
        subs = Submission.query.filter_by(team_id=team.id, mission_slug=m["slug"]).all()
        by_key = {s.question_key: s for s in subs}
        items = []
        for q in m.get("questions") or []:
            sub = by_key.get(q["key"])
            if sub is None:
                continue
            answer = decode_answer(sub)
            links = [
                {"artifact_id": link.artifact_id,
                 "artifact_title": _artifact_title(m, link.artifact_id, lang),
                 "excerpt": link.excerpt,
                 "reasoning": link.reasoning,
                 "confidence": link.confidence}
                for link in EvidenceLink.query.filter_by(submission_id=sub.id).all()
            ]
            items.append({
                "question_key": q["key"],
                "prompt": content.tx(q.get("prompt"), lang),
                "answer": answer,
                "answer_text": readable_answer(q, answer, lang),
                "confidence": sub.confidence,
                "reasoning": sub.reasoning,
                "evidence": links,
            })
        tm = TeamMission.query.filter_by(team_id=team.id, mission_slug=m["slug"]).one_or_none()
        dossier.append({
            "slug": m["slug"],
            "title": content.tx(m.get("title"), lang),
            "locked": bool(tm and tm.submitted_at),
            "items": items,
            "evidence_count": sum(len(i["evidence"]) for i in items),
        })
    return dossier


def _artifact_title(mission, artifact_id, lang):
    a = content.find_artifact(mission, artifact_id)
    return content.tx(a.get("title"), lang) if a else artifact_id


def readable_answer(question, answer, lang):
    if answer is None:
        return ""
    qtype = question.get("type")
    if qtype in ("choice", "multi_choice"):
        labels = {o["id"]: content.tx(o.get("label"), lang) for o in question.get("options") or []}
        ids = answer if isinstance(answer, list) else [answer]
        return " / ".join(labels.get(i, str(i)) for i in ids)
    if qtype == "order":
        labels = {o["id"]: content.tx(o.get("label"), lang) for o in question.get("items") or []}
        return " → ".join(labels.get(i, str(i)) for i in (answer or []))
    if qtype == "graph":
        return " · ".join(str(e) for e in (answer or []))
    if isinstance(answer, list):
        return ", ".join(str(a) for a in answer)
    return str(answer)


def get_or_create(ev, team):
    report = FinalReport.query.filter_by(team_id=team.id).one_or_none()
    if report is None:
        report = FinalReport(session_id=ev.id, team_id=team.id, assembled_at=utcnow())
        db.session.add(report)
        db.session.commit()
    return report


def report_payload(report, definition, lang):
    """The team's saved synthesis, decoded, with the authored candidate lists."""
    return {
        "timeline": _loads(report.timeline_json, []),
        "verdict": report.verdict or "",
        "confirmed_facts": report.confirmed_facts or "",
        "inferences": report.inferences or "",
        "unknowns": report.unknowns or "",
        "scope": report.scope or "",
        "response": _loads(report.response_json, []),
        "response_later": _loads(report.response_future_json, []),
        "response_other": report.response_other or "",
        "limitations": report.limitations or "",
        "locked": report.locked_at is not None,
        "timeline_events": [{"id": e["id"], "label": content.tx(e.get("label"), lang)}
                            for e in (definition.get("timeline_events") or [])],
        "response_items": [{"id": i["id"], "label": content.tx(i.get("label"), lang)}
                           for i in order_item_defs(definition, "final_response_now")],
        "response_later_items": [{"id": i["id"], "label": content.tx(i.get("label"), lang)}
                                 for i in order_item_defs(definition, "final_response_later")],
        "top_actions": TOP_ACTIONS,
    }


def score_final(ev, team, definition, actor="server"):
    from .models import SRC_AUTO, ScoreEvent
    from .scoring import check_answer

    report = FinalReport.query.filter_by(team_id=team.id).one_or_none()
    if report is None:
        return []

    already = ScoreEvent.query.filter_by(team_id=team.id, mission_slug=definition["slug"],
                                         source=SRC_AUTO).first()
    if already is not None:
        return []

    events = []
    for q in definition.get("questions") or []:
        if q["key"] == "final_timeline":
            answer = _loads(report.timeline_json, [])
        elif q["key"] == "final_response_later":
            answer = _loads(report.response_future_json, [])[:TOP_ACTIONS]
        elif q["key"] == "final_response_now":
            answer = _loads(report.response_json, [])[:TOP_ACTIONS]
        else:
            continue
        _, ratio = check_answer(q, answer)
        earned = round(float(q.get("points", 0)) * ratio, 2)
        if earned:
            events.append(ScoreEvent(
                session_id=ev.id, team_id=team.id, mission_slug=definition["slug"],
                source=SRC_AUTO, category=q.get("category", "correctness"), points=earned,
                reason=f"{q['key']}: {int(ratio * 100)}% of the authored ordering",
                question_key=q["key"], actor=actor))
    db.session.add_all(events)
    db.session.commit()
    return events


def presentation(ev, team, content_dir, lang):
    definition = content.final_definition(content_dir)
    report = FinalReport.query.filter_by(team_id=team.id).one_or_none()
    dossier = assemble(ev, team, content_dir, lang)

    timeline_labels = {e["id"]: content.tx(e.get("label"), lang)
                       for e in (definition.get("timeline_events") or [])} if definition else {}
    action_labels = {}
    if definition:
        for key in ("final_response_now", "final_response_later"):
            for item in order_item_defs(definition, key):
                action_labels[item["id"]] = content.tx(item.get("label"), lang)
        for a in definition.get("response_actions") or []:
            action_labels.setdefault(a["id"], content.tx(a.get("label"), lang))

    evidence = []
    for mission in dossier:
        for item in mission["items"]:
            for link in item["evidence"]:
                if link["excerpt"]:
                    evidence.append({"mission": mission["title"],
                                     "artifact": link["artifact_title"],
                                     "excerpt": link["excerpt"],
                                     "reasoning": link["reasoning"]})
    strongest = evidence[:TOP_EVIDENCE]

    return {
        "team": {"name": team.display_name, "color": team.color_key, "code": team.code},
        "verdict": (report.verdict if report else "") or "",
        "timeline": [timeline_labels.get(i, i) for i in _loads(report.timeline_json if report else "", [])],
        "evidence": strongest,
        "actions": [action_labels.get(i, i)
                    for i in _loads(report.response_json if report else "", [])[:TOP_ACTIONS]],
        "actions_later": [action_labels.get(i, i)
                          for i in _loads(report.response_future_json if report else "",
                                          [])[:TOP_ACTIONS]],
        "response_other": (report.response_other if report else "") or "",
        "confirmed_facts": (report.confirmed_facts if report else "") or "",
        "inferences": (report.inferences if report else "") or "",
        "unknowns": (report.unknowns if report else "") or "",
        "scope": (report.scope if report else "") or "",
        "limitations": (report.limitations if report else "") or "",
        "score": team_breakdown(team),
        "evidence_count": sum(m["evidence_count"] for m in dossier),
    }
