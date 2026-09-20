from .conftest import join


def test_a_participant_cannot_reach_the_scoring_page(app, event):
    p = join(app, event, "Team 1", "kenji")
    res = p.get("/facilitator/scoring")
    assert res.status_code in (302, 303)
    assert "/facilitator/login" in res.headers["Location"]


def test_the_facilitator_sees_the_real_maximum(facilitator):
    html = facilitator.get("/facilitator/scoring").get_data(as_text=True)
    assert "How a team" in html and "score is built" in html
    assert "465" in html
    assert "450" in html
    assert "+15" in html


def test_it_names_the_five_ioc_categories_of_the_bonus(facilitator):
    html = facilitator.get("/facilitator/scoring").get_data(as_text=True)
    for category in ("domain", "hash", "process", "persistence", "file path"):
        assert category in html, category
