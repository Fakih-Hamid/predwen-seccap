import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                    # noqa: E402
from app.auth import new_member_token                         # noqa: E402
from app.models import Member, Team, db                       # noqa: E402
from app.state import (DEFAULT_TEAM_COUNT, TEAM_CAPACITIES,      # noqa: E402
                       TEAM_NAMES, apply_table_plan, create_session,
                       mission_rows)

NAMES = ["ai", "ren", "sora", "yuki", "kai", "mio", "haru", "nao"]

REHEARSAL_SIZES = "4,4,4,5,5,5"


def parse_sizes(spec, teams):
    """`--sizes 5,5,5,4,4,4` -> [5, 5, 5, 4, 4, 4], padded or trimmed to fit."""
    sizes = [max(0, int(part)) for part in spec.split(",") if part.strip()]
    if not sizes:
        return [0] * teams
    while len(sizes) < teams:
        sizes.append(sizes[-1])
    return sizes[:teams]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="SECCAP Incident Exercise")
    parser.add_argument("--teams", type=int, default=DEFAULT_TEAM_COUNT,
                        help="teams to create (default: %d, the class format)"
                             % DEFAULT_TEAM_COUNT)
    parser.add_argument("--code", default=None, help="session code (default: generated)")
    parser.add_argument("--rehearse", action="store_true",
                        help="also join simulated members")
    parser.add_argument("--sizes", default=REHEARSAL_SIZES,
                        help="members per team for --rehearse, comma separated "
                             f"(default: {REHEARSAL_SIZES})")
    parser.add_argument("--capacities",
                        default=",".join(str(n) for n in TEAM_CAPACITIES),
                        help="participant seats per team, comma separated "
                             "(default: %(default)s). Empty means no limit.")
    parser.add_argument("--names", default=",".join(TEAM_NAMES),
                        help="team names in order, comma separated "
                             "(default: %(default)s). Teams past the list keep "
                             "their numbered name.")
    args = parser.parse_args()

    app = create_app({"BOOTSTRAP_SCHEMA": True})
    with app.app_context():
        ev = create_session(app.config, title=args.title, code=args.code,
                            team_count=args.teams, actor="seed_demo")

        names = [n.strip() for n in args.names.split(",") if n.strip()]
        capacities = [int(n) for n in args.capacities.split(",") if n.strip()]
        apply_table_plan(ev, names=names, capacities=capacities)

        if args.rehearse:
            teams = Team.query.filter_by(session_id=ev.id).all()
            sizes = parse_sizes(args.sizes, len(teams))
            for team, size in zip(teams, sizes):
                for i in range(size):
                    member = Member(team_id=team.id,
                                    nickname=f"{team.code.lower()}-{NAMES[i % len(NAMES)]}",
                                    token=new_member_token())
                    db.session.add(member)
                    db.session.flush()
            db.session.commit()
            print(f"\n  seeded {sum(sizes)} members across {len(teams)} teams "
                  f"({', '.join(str(s) for s in sizes)})")

        print()
        print(f"  session   {ev.code}")
        print(f"  title     {ev.title}")
        print(f"  state     {ev.state}")
        print()
        print("  teams")
        for team in Team.query.filter_by(session_id=ev.id).all():
            seats = f"{team.capacity} seats" if team.capacity else "no limit"
            print(f"    {team.code:<8} {team.display_name:<16} {seats}")
        print()
        print("  missions (all locked; open them from /facilitator)")
        for row in mission_rows(ev):
            print(f"    {row.slug:<24} {row.duration_seconds // 60} min  "
                  f"content {row.content_version}")
        print()


if __name__ == "__main__":
    main()
