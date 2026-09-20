import re
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import create_app, missions as content
from app.auth import reset_rate_limits
from app.models import IOC_TYPES, REQUIRED_IOC_TYPES, CollectiveIOC, Team, db
from app.scoring import collective_coverage, maybe_award_collective
from app.state import DEFAULT_TEAM_COUNT, create_session

from .conftest import FACILITATOR_PASSWORD

ROOT = Path(__file__).resolve().parents[1]

TEAM_SIZE = 6
CLASS_SIZE = DEFAULT_TEAM_COUNT * TEAM_SIZE      # 36: well past the room


def cd(app):
    return app.config["CONTENT_DIR"]


def _fresh_app(**overrides):
    base = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-only",
        "FACILITATOR_PASSWORD_HASH": generate_password_hash(FACILITATOR_PASSWORD),
        "SKIP_CONTENT_VALIDATION": True,
        "DEFAULT_LANG": "en",
        "ADMIN_BYPASS": False,
    }
    base.update(overrides)
    application = create_app(base)
    with application.app_context():
        db.create_all()
        reset_rate_limits()
    return application


def test_the_default_join_limit_is_above_the_size_of_a_class():
    """Twenty was below the class. The class is thirty."""
    from app.config import Config
    assert Config.JOIN_RPM >= CLASS_SIZE, (Config.JOIN_RPM, CLASS_SIZE)


def test_a_whole_class_joins_from_one_address_within_a_minute():
    application = _fresh_app()
    with application.app_context():
        reset_rate_limits()
        event = create_session(application.config, title="Rate limit",
                               code="CLASSA", team_count=DEFAULT_TEAM_COUNT)
        codes = [t.code for t in Team.query.filter_by(session_id=event.id).all()]
    assert len(codes) == DEFAULT_TEAM_COUNT == 6
    assert len(set(codes)) == 6, "two teams share a code"

    refused, joined = [], 0
    for i in range(CLASS_SIZE):
        client = application.test_client()      # every one is 127.0.0.1
        client.get("/join")
        with client.session_transaction() as sess:
            token = sess["csrf_token"]
        res = client.post("/join", data={
            "csrf_token": token, "session_code": event.code,
            "team_code": codes[i % DEFAULT_TEAM_COUNT],
            "nickname": "student%d" % i})
        if res.status_code == 429:
            refused.append(i + 1)
        else:
            joined += 1
    assert not refused, (
        "%d of %d students were refused (first at #%s). SECCAP_JOIN_RPM is the "
        "setting; do not remove the limiter."
        % (len(refused), CLASS_SIZE, refused[:1]))
    assert joined == CLASS_SIZE

    with application.app_context():
        teams = Team.query.filter_by(session_id=event.id).all()
        assert len(teams) == 6
        for team in teams:
            nicknames = {m.nickname for m in team.members.all()}
            assert len(nicknames) == TEAM_SIZE, (team.display_name, nicknames)


def test_the_limiter_is_still_there():
    """Raising the ceiling must not be the same as removing it."""
    application = _fresh_app(JOIN_RPM=5)
    with application.app_context():
        reset_rate_limits()
        event = create_session(application.config, title="Still limited",
                               code="LIMITA", team_count=DEFAULT_TEAM_COUNT)

    seen = []
    for _ in range(9):
        client = application.test_client()
        client.get("/join")
        with client.session_transaction() as sess:
            token = sess["csrf_token"]
        seen.append(client.post("/join", data={
            "csrf_token": token, "session_code": "WRONG0",
            "team_code": "XXXXX", "nickname": "grinder"}).status_code)
    assert 429 in seen, seen


def _css():
    return (ROOT / "app" / "static" / "seccap.css").read_text(encoding="utf-8")


def test_the_projector_scoreboard_has_a_bounded_vertical_budget():
    css = _css()
    block = css[css.index(".projector { overflow: hidden; }"):]
    assert re.search(r"\.projector\s*\{[^}]*overflow:\s*hidden", block)
    assert re.search(r"\.projector\s+\.page\s*\{[^}]*height:\s*100vh", block)
    assert re.search(r"\.projector\s+header\.top\s+nav\.tabs\s*\{[^}]*display:\s*none", block)

    pad = re.search(r"\.projector\s+table\.board\s+td\s*\{[^}]*padding:\s*([\d.]+)vh", block)
    assert pad, "the projector row padding is no longer expressed in vh"
    assert float(pad.group(1)) <= 0.7, (
        "%svh row padding does not fit six rows on a 1080px screen" % pad.group(1))


def test_the_projector_type_is_still_large_enough_to_read_from_the_back():
    """Six rows were bought with type size, so there is a floor on it."""
    css = _css()
    block = css[css.index(".projector { overflow: hidden; }"):]

    def size(selector):
        found = re.search(re.escape(selector) + r"\s*\{[^}]*font-size:\s*([\d.]+)rem", block)
        assert found, selector
        return float(found.group(1))

    assert size(".projector table.board td.nm") >= 1.5      # team name
    assert size(".projector table.board td.total") >= 2.0   # the number that matters
    assert size(".projector table.board td.num") >= 1.375   # M1 / M2 / M3
    assert size(".projector .page > h1") >= 2.0


def test_the_ordinary_scoreboard_is_not_pinned_to_the_viewport():
    """The fix is scoped to projector mode; the normal board still scrolls."""
    css = _css()
    normal = re.search(r"^\.page \{([^}]*)\}", css, re.M)
    assert normal and "100vh" not in normal.group(1)


def test_the_reveal_animation_is_untouched():
    css = _css()
    assert "@keyframes lbReveal" in css
    assert re.search(r"table\.board tr\.reveal \{ animation: lbReveal", css)


def test_the_projector_flag_still_reaches_the_template(app, event, facilitator):
    from .conftest import join
    p = join(app, event, "Team 1", "kenji")
    assert 'class="projector"' in p.get("/scoreboard?projector=1").data.decode()
    assert 'class="projector"' not in p.get("/scoreboard").data.decode()


def test_file_path_is_a_category_the_class_board_accepts():
    assert "file_path" in IOC_TYPES


def test_the_five_required_categories_are_the_five_mission_three_asks_for():
    assert set(REQUIRED_IOC_TYPES) == {"hash", "domain", "process",
                                       "persistence", "file_path"}


def test_a_shared_proxy_address_is_not_required_for_the_bonus():
    assert "ip" not in REQUIRED_IOC_TYPES
    assert "ip" in IOC_TYPES, "existing rows must stay readable"


def test_the_bonus_unlocks_on_the_five_required_categories(app, event):
    with app.app_context():
        teams = Team.query.filter_by(session_id=event.id).all()
        for i, kind in enumerate(REQUIRED_IOC_TYPES):
            db.session.add(CollectiveIOC(
                session_id=event.id, team_id=teams[i % len(teams)].id,
                ioc_type=kind, value="indicator-%s" % kind,
                justification="rehearsal", validated=True))
        db.session.commit()

        coverage = collective_coverage(event)
        assert set(coverage) == set(REQUIRED_IOC_TYPES), sorted(coverage)
        assert all(coverage.values()), coverage
        awarded = maybe_award_collective(event)
        assert len(awarded) == len(teams), "every team shares the bonus"


def test_the_bonus_does_not_wait_for_an_address(app, event):
    """The five required kinds are enough even with no `ip` row at all."""
    with app.app_context():
        team = Team.query.filter_by(session_id=event.id).first()
        for kind in REQUIRED_IOC_TYPES:
            db.session.add(CollectiveIOC(
                session_id=event.id, team_id=team.id, ioc_type=kind,
                value="v-%s" % kind, justification="r", validated=True))
        db.session.commit()
        assert "ip" not in collective_coverage(event)
        assert maybe_award_collective(event), "the bonus fired without an ip row"


def test_mission_three_asks_for_the_categories_the_board_accepts(app):
    """The rubric and the board have to name the same kinds, in both languages."""
    m3 = content.load_missions(cd(app))["threat-intelligence"]
    item = next(r for r in m3["rubric"] if r["key"] == "m3_r_coverage")
    en = content.tx(item["prompt"], "en").lower()
    ja = content.tx(item["prompt"], "ja")
    for word in ("hash", "domain", "process", "persistence", "file path"):
        assert word in en, "%r is missing from the English rubric prompt" % word
    for word in ("ハッシュ", "ドメイン", "プロセス", "永続化", "ファイルパス"):
        assert word in ja, "%r is missing from the Japanese rubric prompt" % word


def test_the_class_board_names_the_same_five_kinds(app):
    from app import ui
    for lang, words in (("en", ("hash", "domain", "process", "persistence", "file path")),
                        ("ja", ("ハッシュ", "ドメイン", "プロセス", "永続化", "ファイルパス"))):
        lead = ui.t(cd(app), "collective.lead", lang)
        for w in words:
            assert w.lower() in lead.lower(), "%s: %r missing from collective.lead" % (lang, w)
