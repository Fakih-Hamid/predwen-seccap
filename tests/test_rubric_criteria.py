from app import missions as content

from .conftest import join

MISSIONS = ("digital-footprint", "fake-infrastructure", "threat-intelligence")


def cd(app):
    return app.config["CONTENT_DIR"]


def _grade_page(app, event, facilitator, team_name="Team 1", lang="en"):
    from app.models import Team

    p = join(app, event, team_name, "kenji")
    p.client.post("/lang", data={"lang": lang, "csrf_token": p.csrf})
    facilitator.client.post("/lang", data={"lang": lang, "csrf_token": facilitator.csrf})
    team = Team.query.filter_by(session_id=event.id, display_name=team_name).one()
    return facilitator.get(f"/facilitator/grade/{team.id}").data.decode()


def test_a_single_pair_is_one_criterion_not_its_keys():
    assert content.tx_list({"ja": "日本語", "en": "English"}, "en") == ["English"]
    assert content.tx_list({"ja": "日本語", "en": "English"}, "ja") == ["日本語"]


def test_a_list_of_pairs_still_behaves_exactly_as_before():
    items = [{"ja": "一つ目", "en": "first"}, {"ja": "二つ目", "en": "second"}]
    assert content.tx_list(items, "en") == ["first", "second"]
    assert content.tx_list(items, "ja") == ["一つ目", "二つ目"]


def test_none_and_a_bare_string_are_handled():
    assert content.tx_list(None, "en") == []
    assert content.tx_list([], "ja") == []
    assert content.tx_list("one criterion", "en") == ["one criterion"]


def test_the_keys_are_never_the_output(app):
    """The exact symptom: bullets reading 'ja' and 'en'."""
    for slug in MISSIONS:
        m = content.load_missions(cd(app))[slug]
        for item in m.get("rubric") or []:
            for lang in ("ja", "en"):
                rendered = content.tx_list(item.get("criteria"), lang)
                assert rendered, f"{slug}/{item['key']} ({lang}) has no criteria"
                assert set(rendered) != {"ja", "en"}, f"{slug}/{item['key']} ({lang})"
                for line in rendered:
                    assert line not in ("ja", "en"), f"{slug}/{item['key']} ({lang})"


def test_every_rubric_in_every_mission_renders_real_text(app):
    """Whatever shape it was authored in, in both languages."""
    for m in content.load_missions(cd(app)).values():
        for item in m.get("rubric") or []:
            for lang in ("ja", "en"):
                for line in content.tx_list(item.get("criteria"), lang):
                    assert isinstance(line, str) and len(line) > 3, (
                        f"{m['slug']}/{item['key']} ({lang}): {line!r}")


def test_the_grading_page_shows_criteria_in_english(app, event, facilitator):
    body = _grade_page(app, event, facilitator, lang="en")
    assert "<li>ja</li>" not in body and "<li>en</li>" not in body
    assert "Full marks for confirmed / downloaded-only / contacted-only" in body
    assert "Does not call the file safe or malicious" in body


def test_the_grading_page_shows_criteria_in_japanese(app, event, facilitator):
    body = _grade_page(app, event, facilitator, lang="ja")
    assert "<li>ja</li>" not in body and "<li>en</li>" not in body
    assert "SR-DEV-077 を「侵害確認済み」" in body
    assert "VirusTotal の結果だけで、ファイルが安全か、悪意のあるものかを断定していない" in body


def test_both_authored_shapes_reach_the_page(app, event, facilitator):
    """`final-incident` uses the list shape, the missions use one pair."""
    body = _grade_page(app, event, facilitator, lang="en")
    assert "The three boxes contain three different kinds of claim" in body
    assert "Full marks for a concrete value in all five types" in body
