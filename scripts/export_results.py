import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                       # noqa: E402
from app.models import (                                          # noqa: E402
    AuditEvent, EvidenceLink, FinalReport, Observation, ScoreEvent, Submission, Team)
from app.scoring import leaderboard, team_breakdown               # noqa: E402
from app.state import active_session, mission_rows, session_by_code  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default=None)
    parser.add_argument("--out", default="exports")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        ev = session_by_code(args.code) if args.code else active_session()
        if ev is None:
            print("no session found")
            return 1
        os.makedirs(args.out, exist_ok=True)
        names = {t.id: t.display_name for t in Team.query.filter_by(session_id=ev.id).all()}

        scores = os.path.join(args.out, f"seccap_{ev.code}_scores.csv")
        with open(scores, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["session", "team", "mission", "source", "category", "points",
                        "question", "actor", "reason", "at"])
            for e in ScoreEvent.query.filter_by(session_id=ev.id).order_by(
                    ScoreEvent.created_at).all():
                w.writerow([ev.code, names.get(e.team_id), e.mission_slug or "", e.source,
                            e.category, e.points, e.question_key or "", e.actor or "",
                            (e.reason or "").replace("\n", " "), e.created_at.isoformat()])

        subs = os.path.join(args.out, f"seccap_{ev.code}_submissions.csv")
        with open(subs, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["session", "team", "mission", "question", "answer", "confidence",
                        "reasoning", "evidence_artifact", "evidence_excerpt", "updated_at"])
            for s in Submission.query.filter_by(session_id=ev.id).order_by(Submission.id).all():
                links = EvidenceLink.query.filter_by(submission_id=s.id).all()
                link = links[0] if links else None
                w.writerow([ev.code, names.get(s.team_id), s.mission_slug, s.question_key,
                            (s.answer_json or "").replace("\n", " "), s.confidence or "",
                            (s.reasoning or "").replace("\n", " "),
                            link.artifact_id if link else "",
                            (link.excerpt or "").replace("\n", " ") if link else "",
                            s.updated_at.isoformat()])

        doc = {
            "session": {"code": ev.code, "title": ev.title, "state": ev.state,
                        "created_at": ev.created_at.isoformat()},
            "missions": [{"slug": r.slug, "state": r.state, "version": r.content_version,
                          "duration": r.duration_seconds, "extension": r.extension_seconds}
                         for r in mission_rows(ev)],
            "leaderboard": leaderboard(ev),
            "teams": [],
            "audit": [{"actor_type": a.actor_type, "actor": a.actor_id, "action": a.action,
                       "target": a.target, "detail": a.detail, "at": a.ts.isoformat()}
                      for a in AuditEvent.query.filter_by(session_id=ev.id)
                      .order_by(AuditEvent.ts).all()],
        }
        for team in Team.query.filter_by(session_id=ev.id).order_by(Team.id).all():
            report = FinalReport.query.filter_by(team_id=team.id).one_or_none()
            doc["teams"].append({
                "code": team.code, "name": team.display_name,
                "members": [{"nickname": m.nickname} for m in team.members],
                "observations": Observation.query.filter_by(team_id=team.id).count(),
                "score": team_breakdown(team),
                "final": ({"verdict": report.verdict,
                           "confirmed_facts": report.confirmed_facts,
                           "inferences": report.inferences,
                           "unknowns": report.unknowns,
                           "limitations": report.limitations,
                           "timeline": json.loads(report.timeline_json or "[]"),
                           "response": json.loads(report.response_json or "[]")}
                          if report else None),
            })
        session_path = os.path.join(args.out, f"seccap_{ev.code}_session.json")
        with open(session_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)

        for path in (scores, subs, session_path):
            print(f"  wrote {path}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
