import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import SchemaNotInitialised, create_app                   # noqa: E402
from app.models import EventSession, Member, Team                  # noqa: E402
from app.state import active_session, mission_rows                 # noqa: E402


def show(ev, live):
    marker = "  ← the one participants join" if live else ""
    print()
    print(f"  session   {ev.code}{marker}")
    print(f"  title     {ev.title}")
    print(f"  state     {ev.state}")
    print(f"  created   {ev.created_at:%Y-%m-%d %H:%M} UTC")

    teams = Team.query.filter_by(session_id=ev.id).order_by(Team.id).all()
    print()
    print("  team codes — write these on the board")
    for team in teams:
        seats = Member.query.filter_by(team_id=team.id).count()
        seat_note = f"{seats} joined" if seats else "empty"
        if team.capacity:
            seat_note += f" / {team.capacity} seats"
        print(f"    {team.code:<8} {team.display_name:<28} {seat_note}")

    rows = mission_rows(ev)
    if rows:
        print()
        print("  missions")
        for row in rows:
            print(f"    {row.slug:<24} {row.state}")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true",
                        help="show every session, including closed ones")
    args = parser.parse_args()

    try:
        app = create_app()
    except SchemaNotInitialised as exc:
        print(f"\n  THE DATABASE HAS NO SCHEMA, so there are no codes to show.\n\n  {exc}\n")
        return 1

    with app.app_context():
        live = active_session()

        if args.all:
            sessions = EventSession.query.order_by(EventSession.id).all()
            if not sessions:
                print("\n  no sessions in this database at all.")
                print("  seed one:  python scripts/seed_demo.py --title \"SECCAP 2026\"\n")
                return 1
            for ev in sessions:
                show(ev, live is not None and ev.id == live.id)
            return 0

        if live is None:
            print("\n  NO SESSION IS OPEN.")
            closed = EventSession.query.order_by(EventSession.id.desc()).first()
            if closed is not None:
                print(f"  The most recent one, {closed.code} ({closed.title!r}), is "
                      f"'{closed.state}'.")
                print("  A closed session cannot be joined and cannot be reopened.")
            print()
            print("  Seed a new one — the first six team codes are fixed and survive a reseed:")
            print("    python scripts/seed_demo.py --title \"SECCAP 2026\"")
            print("  See everything that is in the database:")
            print("    python scripts/show_codes.py --all")
            print()
            return 1

        show(live, True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
