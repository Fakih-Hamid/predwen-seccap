from app.models import Team
from app.state import FIXED_TEAM_CODES, add_team, create_session


def _codes(event):
    return {t.display_name: t.code for t in
            Team.query.filter_by(session_id=event.id).order_by(Team.id)}


def test_the_six_codes_are_the_fixed_ones(app):
    ev = create_session(app.config, title="A", code="AAAAAA")
    assert _codes(ev) == {"Team 1": "TCACW", "Team 2": "VX55N", "Team 3": "Z6ATK",
                          "Team 4": "P9FX6", "Team 5": "SD38L", "Team 6": "K7RMH"}


def test_reseeding_produces_the_same_codes(app):
    a = create_session(app.config, title="A", code="AAAAAA")
    b = create_session(app.config, title="B", code="BBBBBB")
    assert _codes(a) == _codes(b)
    assert set(_codes(a).values()) == set(FIXED_TEAM_CODES)


def test_a_seventh_team_gets_a_fresh_code_not_a_collision(app):
    ev = create_session(app.config, title="A", code="AAAAAA")
    add_team(ev, index=6)                       # a seventh team formed on the day
    codes = [t.code for t in
             Team.query.filter_by(session_id=ev.id).order_by(Team.id)]
    assert len(codes) == 7
    assert len(set(codes)) == 7                  # no collision
    assert codes[:6] == list(FIXED_TEAM_CODES)   # the six stay fixed
    assert codes[6] not in FIXED_TEAM_CODES      # the seventh is fresh
