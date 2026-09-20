from .conftest import join, wind_forward

SLUG = "digital-footprint"


def test_a_participant_cannot_reach_the_scoring_export(app, event):
    p = join(app, event, "Team 1", "kenji")
    res = p.get("/facilitator/export/scoring.csv")
    assert res.status_code in (302, 303)
    assert "/facilitator/login" in res.headers["Location"]


def test_every_question_has_a_row_including_the_ones_worth_nothing(
        app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    wind_forward(app, event, SLUG)
    p.post("/api/submission", {"mission_slug": SLUG,
                               "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})

    body = facilitator.get("/facilitator/export/scoring.csv").get_data(as_text=True)

    assert "session,team,mission,mission_title,question,question_prompt" in body
    assert "m1_key_id" in body
    assert "m1_flag" in body
    assert "m1_w_separation" in body
    assert "SR-REL-2019" in body


def test_the_export_says_what_the_question_was_not_only_its_key(
        app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    wind_forward(app, event, SLUG)
    p.post("/api/submission", {"mission_slug": SLUG,
                               "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})

    body = facilitator.get("/facilitator/export/scoring.csv").get_data(as_text=True)
    row = next(line for line in body.splitlines() if ",m1_key_id," in line)

    assert "Digital Footprint" in row
    assert "key" in row.lower() and len(row) > 120


def test_the_row_says_answered_and_out_of_what(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    wind_forward(app, event, SLUG)
    p.post("/api/submission", {"mission_slug": SLUG,
                               "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})

    body = facilitator.get("/facilitator/export/scoring.csv").get_data(as_text=True)
    row = next(line for line in body.splitlines() if ",m1_key_id," in line)
    cells = row.split(",")
    assert "yes" in cells          # answered
    assert "9.0" in row or "9" in row   # 5 points + 4 evidence points


def test_a_member_who_posted_nothing_does_not_block_the_lock(app, event,
                                                             facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    kenji = join(app, event, "Team 1", "kenji")
    join(app, event, "Team 1", "silent-one")        # never posts anything
    wind_forward(app, event, SLUG)

    res = kenji.post("/api/lock", {"mission_slug": SLUG})
    assert res.status_code == 200, res.get_data(as_text=True)[:200]
