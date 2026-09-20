import pathlib

from .conftest import join

SLUG = "digital-footprint"
ALL = ("digital-footprint", "fake-infrastructure", "threat-intelligence")
JS = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "seccap.js"


def open_all(facilitator):
    for slug in ALL:
        facilitator.post(f"/facilitator/mission/{slug}/open", {})


def test_the_synthesis_is_closed_while_a_mission_is_still_writable(
        app, event, facilitator):
    open_all(facilitator)
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")

    body = p.get("/final").get_data(as_text=True)
    assert "Finish the three missions first" in body
    assert "Not locked yet:" in body
    assert 'id="response"' not in body
    assert "Isolate SR-DEV-077" not in body


def test_writing_the_synthesis_is_refused_too_not_just_the_page(
        app, event, facilitator):
    open_all(facilitator)
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")

    assert p.post("/api/final", {"verdict": "early"}).status_code == 409
    assert p.post("/api/final/lock", {}).status_code == 409


def test_it_opens_once_the_team_has_locked_all_three(app, event, facilitator):
    open_all(facilitator)
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")

    for slug in ALL:
        p.post("/api/lock", {"mission_slug": slug})

    body = p.get("/final").get_data(as_text=True)
    assert "Finish the three missions first" not in body
    assert 'id="response"' in body
    assert 'id="response-later"' in body
    assert p.post("/api/final", {"verdict": "ours"}).status_code == 200


def test_a_mission_the_facilitator_never_opened_does_not_strand_a_team(
        app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/lock", {"mission_slug": SLUG})

    assert 'id="response"' in p.get("/final").get_data(as_text=True)


def test_closing_a_mission_counts_as_finished_for_a_team_that_ran_out_of_time(
        app, event, facilitator):
    open_all(facilitator)
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")
    for slug in ALL:
        facilitator.post(f"/facilitator/mission/{slug}/close", {})

    assert 'id="response"' in p.get("/final").get_data(as_text=True)


def test_the_holding_screen_watches_for_its_own_team_finishing(
        app, event, facilitator):
    open_all(facilitator)
    facilitator.post("/facilitator/session/state", {"final_open": True})
    one = join(app, event, "Team 1", "kenji")
    two = join(app, event, "Team 1", "aiko")

    assert one.get("/api/session-state").get_json()["missions_finished"] is False
    for slug in ALL:
        two.post("/api/lock", {"mission_slug": slug})
    assert one.get("/api/session-state").get_json()["missions_finished"] is True
    two_pager = join(app, event, "Team 2", "rin")
    holding = two_pager.get("/final").get_data(as_text=True)
    assert "d.missions_finished" in holding


def test_the_flag_is_per_team(app, event, facilitator):
    open_all(facilitator)
    facilitator.post("/facilitator/session/state", {"final_open": True})
    one = join(app, event, "Team 1", "kenji")
    two = join(app, event, "Team 2", "aiko")
    for slug in ALL:
        one.post("/api/lock", {"mission_slug": slug})

    assert one.get("/api/session-state").get_json()["missions_finished"] is True
    assert two.get("/api/session-state").get_json()["missions_finished"] is False


def test_the_ordering_lists_can_be_dragged_as_well_as_nudged():
    js = JS.read_text(encoding="utf-8")
    block = js.split("window.wireReorder =", 1)[1].split("function currentOrder", 1)[0]

    for handler in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
        assert handler in block, handler
    assert "setPointerCapture" in block
    assert "[data-move]" in block
    assert "if (moved && onChange)" in block


def test_a_row_that_can_be_dragged_says_so_and_does_not_scroll_the_page():
    css = (pathlib.Path(__file__).resolve().parents[1] / "app" / "static"
           / "seccap.css").read_text(encoding="utf-8")
    assert "cursor: grab" in css
    assert "touch-action: none" in css
    assert ".orderlist .orderitem.dragging" in css


def test_each_step_asks_its_own_question_on_the_way_in(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    prompts = html.split("const PROMPTS = {", 1)[1].split("const DISCUSSED", 1)[0]
    assert prompts.count("title:") == 2, "one on recording, one on answering"

    assert "Split the sources between you and post what you find" in html
    assert "Before you answer" in html
    assert "Last check before you lock" in html

    assert "Splitting the sources between you" in html
    assert "shared with the whole team" in html
    assert "shared with the team" in html
    assert "everyone has shared an observation" in html
    assert "discussed what happened" in html

    for sentence in ("We have split the starting sources",
                     "Everyone has posted, and we have discussed the findings",
                     "We checked it together and agree to submit"):
        assert sentence in html, sentence


def test_the_prompts_are_asked_one_after_the_other(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert "teamPrompt(queue[i], function () { ask(i + 1); }," in html
    assert "i === 0 ? warn : null" in html
    assert "if (i >= queue.length) { originalMove(index, focus); return; }" in html
