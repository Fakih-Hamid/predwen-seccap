import pathlib

from .conftest import join

M1 = "digital-footprint"
M2 = "fake-infrastructure"
M3 = "threat-intelligence"
CSS = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "seccap.css"


def open_mission(facilitator, slug):
    facilitator.post(f"/facilitator/mission/{slug}/open", {})


def test_an_open_mission_is_not_enough_while_the_last_one_is_unlocked(
        app, event, facilitator):
    open_mission(facilitator, M1)
    open_mission(facilitator, M2)
    p = join(app, event, "Team 1", "kenji")

    body = p.get(f"/mission/{M2}").get_data(as_text=True)
    assert "Finish the mission before this one" in body
    assert "Mission 1" in body, "it names what is in the way"
    assert "data-answer" not in body
    assert 'id="lock"' not in body


def test_locking_the_previous_one_opens_it(app, event, facilitator):
    open_mission(facilitator, M1)
    open_mission(facilitator, M2)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/lock", {"mission_slug": M1})

    body = p.get(f"/mission/{M2}").get_data(as_text=True)
    assert "Finish the mission before this one" not in body
    assert "data-answer" in body


def test_the_writes_are_refused_too_not_only_the_page(app, event, facilitator):
    open_mission(facilitator, M1)
    open_mission(facilitator, M2)
    p = join(app, event, "Team 1", "kenji")

    assert p.post("/api/submission", {"mission_slug": M2,
                                      "question_key": "m2_manifest_host",
                                      "answer": "early"}).status_code == 409
    assert p.post("/api/observation", {"mission_slug": M2,
                                       "text": "early"}).status_code == 409
    assert p.post("/api/lock", {"mission_slug": M2}).status_code == 409

    assert p.post("/api/submission", {"mission_slug": M1,
                                      "question_key": "m1_flag",
                                      "answer": "ALLOW_UNSIGNED_RECOVERY"}
                  ).status_code == 200


def test_the_poll_says_the_mission_is_not_writable(app, event, facilitator):
    open_mission(facilitator, M1)
    open_mission(facilitator, M2)
    p = join(app, event, "Team 1", "kenji")

    state = p.get(f"/api/mission-state?mission_slug={M2}").get_json()
    assert state["mission"]["writable"] is False
    assert state["mission"]["state"] == "open", "the mission itself is open"


def test_a_mission_the_facilitator_closed_stops_blocking(app, event, facilitator):
    open_mission(facilitator, M1)
    open_mission(facilitator, M2)
    p = join(app, event, "Team 1", "kenji")
    facilitator.post(f"/facilitator/mission/{M1}/close", {})

    assert "data-answer" in p.get(f"/mission/{M2}").get_data(as_text=True)


def test_a_mission_never_opened_blocks_nothing(app, event, facilitator):
    open_mission(facilitator, M1)
    open_mission(facilitator, M3)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/lock", {"mission_slug": M1})

    assert "data-answer" in p.get(f"/mission/{M3}").get_data(as_text=True)


def test_the_gate_is_per_team(app, event, facilitator):
    open_mission(facilitator, M1)
    open_mission(facilitator, M2)
    one = join(app, event, "Team 1", "kenji")
    two = join(app, event, "Team 2", "aiko")
    one.post("/api/lock", {"mission_slug": M1})

    assert "data-answer" in one.get(f"/mission/{M2}").get_data(as_text=True)
    assert "Finish the mission before this one" in \
        two.get(f"/mission/{M2}").get_data(as_text=True)


def test_the_holding_screen_reopens_itself_when_a_teammate_locks(
        app, event, facilitator):
    """The lock is pressed on somebody else's laptop."""
    open_mission(facilitator, M1)
    open_mission(facilitator, M2)
    one = join(app, event, "Team 1", "kenji")
    two = join(app, event, "Team 1", "aiko")

    assert M2 not in one.get("/api/session-state").get_json()["ready"]
    two.post("/api/lock", {"mission_slug": M1})
    assert M2 in one.get("/api/session-state").get_json()["ready"]

    blocked = join(app, event, "Team 2", "rin").get(f"/mission/{M2}") \
        .get_data(as_text=True)
    assert "d.ready" in blocked, "the holding screen polls for it"


def test_the_synthesis_still_waits_for_a_blocked_mission(app, event, facilitator):
    open_mission(facilitator, M1)
    open_mission(facilitator, M2)
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")

    assert p.get("/api/session-state").get_json()["missions_finished"] is False
    assert "Finish the three missions first" in p.get("/final").get_data(as_text=True)


def test_a_dialog_taller_than_the_screen_can_be_scrolled():
    css = CSS.read_text(encoding="utf-8")
    start = css.index(".modalback {")
    block = css[start:css.index("body.modal-open", start)]

    assert "align-items: flex-start" in block, "centring clips the top"
    assert "overflow-y: auto" in block, "the overlay is what scrolls"
    assert "margin: auto" in block, "still centred while it fits"
    assert "align-items: center" not in block


def test_the_review_list_leaves_room_for_the_buttons():
    css = CSS.read_text(encoding="utf-8")
    block = css[css.index(".reviewlist {"):css.index(".reviewlist li")]
    import re
    share = re.search(r"max-height:\s*(\d+)vh", block)
    assert share, block
    assert int(share.group(1)) <= 40, "the dialog also has to fit its buttons"
    assert "overflow-y: auto" in block
