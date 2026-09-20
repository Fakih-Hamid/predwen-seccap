import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                   # noqa: E402
from app.models import Team                                   # noqa: E402
from app.scoring import leaderboard                           # noqa: E402
from app.state import active_session, reset_session, session_by_code  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default=None)
    parser.add_argument("--out", default="backups")
    parser.add_argument("--yes", action="store_true", help="do not ask")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        ev = session_by_code(args.code) if args.code else active_session()
        if ev is None:
            print("no open session")
            return 1

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, f"seccap_{ev.code}_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"session": {"code": ev.code, "title": ev.title, "state": ev.state},
                       "leaderboard": leaderboard(ev),
                       "teams": [{"code": t.code, "name": t.display_name,
                                  "members": [m.nickname for m in t.members]}
                                 for t in Team.query.filter_by(session_id=ev.id).all()]},
                      f, ensure_ascii=False, indent=2)
        print(f"backup written to {path}")
        print("Full export with evidence and the ledger: /facilitator/export/session.json")

        if not args.yes:
            answer = input(f"close session {ev.code}? [y/N] ").strip().lower()
            if answer != "y":
                print("nothing changed")
                return 0

        reset_session(ev, actor="reset_event")
        print(f"session {ev.code} closed. Seed the next one with scripts/seed_demo.py")
        return 0


if __name__ == "__main__":
    sys.exit(main())
