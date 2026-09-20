from .conftest import join, open_all_sources, wind_forward

SLUG = "digital-footprint"


def opened(app, event, facilitator, slug=SLUG):
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    p = join(app, event, "Team 1", "kenji")
    wind_forward(app, event, slug)
    open_all_sources(app, event, slug)
    return p


def test_a_choice_question_links_the_source_it_is_answered_from(app, event,
                                                                facilitator):
    p = opened(app, event, facilitator)
    html = p.get("/mission/" + SLUG).get_data(as_text=True)

    assert 'class="lookin"' in html
    assert "Sources for this question:" in html
    assert "photo-exif" in html
    assert "username-collision" in html


def test_the_pointer_carries_no_answer_key(app, event, facilitator):
    """It says where to read. It must not say what the reading concludes."""
    import pathlib

    import yaml

    p = opened(app, event, facilitator)
    html = p.get("/mission/" + SLUG).get_data(as_text=True)

    tree = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parents[1] / "content" / "missions"
         / "m1-digital-footprint.yaml").read_text(encoding="utf-8"))
    for q in tree["questions"]:
        for value in (q.get("validator") or {}).get("accept") or []:
            assert str(value) not in html, value
    assert '"correct"' not in html


def test_every_option_carries_its_own_way_back_to_the_source(app, event,
                                                              facilitator):
    p = opened(app, event, facilitator)
    html = p.get("/mission/" + SLUG).get_data(as_text=True)

    block = html.split('id="q-m1_exif"', 1)[1].split('id="q-', 1)[0]
    assert block.count('class="optref"') >= 3, block[:400]
    assert "photo-exif" in block


def test_a_multi_select_says_how_to_rule_an_option_OUT(app, event, facilitator):
    p = opened(app, event, facilitator)
    html = p.get("/mission/" + SLUG).get_data(as_text=True)

    block = html.split('id="q-m1_exif"', 1)[1].split('id="q-', 1)[0]
    assert "not on that list is not evidence" in block, block[:400]

    import pathlib

    import yaml

    tree = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parents[1] / "content" / "missions"
         / "m1-digital-footprint.yaml").read_text(encoding="utf-8"))
    exif = next(q for q in tree["questions"] if q["key"] == "m1_exif")
    for correct in ("artist", "datetime", "model"):
        assert correct not in exif["help"]["en"].lower(), correct


def test_a_source_that_has_not_arrived_yet_is_not_linked(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")          # no wind_forward: minute 0
    html = p.get("/mission/" + SLUG).get_data(as_text=True)

    block = html.split('id="q-m1_collision"', 1)[1].split("</div>", 1)[0]
    assert "username-collision" not in block
