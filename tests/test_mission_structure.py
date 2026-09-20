import pathlib
import re

import pytest

from .conftest import join

SLUG = "digital-footprint"


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def open_mission(facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})


def page(p):
    return p.get(f"/mission/{SLUG}").get_data(as_text=True)


def progress_of(p):
    return p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()["progress"]


def answer(p, key, value):
    r = p.post("/api/submission", {"mission_slug": SLUG, "question_key": key,
                                   "answer": value})
    assert r.get_json().get("ok"), r.get_json()


def test_the_server_counts_the_work_and_the_page_starts_from_it(app, event,
                                                                facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    assert "0 of 10 answered" in page(p)

    answer(p, "m1_key_id", "SR-REL-2019")
    assert "1 of 10 answered" in page(p)


def test_the_poll_and_the_page_agree(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    answer(p, "m1_key_id", "SR-REL-2019")

    counts = progress_of(p)
    assert counts["answered"] == 1
    assert f"{counts['answered']} of {counts['questions']} answered" in page(p)


def test_an_empty_answer_is_not_an_answer(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    answer(p, "m1_key_id", "   ")
    assert progress_of(p)["answered"] == 0


def test_evidence_is_counted_separately_from_answers(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    answer(p, "m1_key_id", "SR-REL-2019")
    assert progress_of(p)["with_evidence"] == 0

    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "github-account",
                             "excerpt": "read it here"})
    assert progress_of(p)["with_evidence"] == 1


def test_the_counts_are_the_team_s_not_the_member_s(app, event, facilitator):
    """Five laptops, one number."""
    open_mission(facilitator)
    kenji = join(app, event, "Team 1", "kenji")
    aoi = join(app, event, "Team 1", "aoi")
    answer(kenji, "m1_key_id", "SR-REL-2019")

    assert progress_of(aoi)["answered"] == 1
    assert "1 of 10 answered" in page(aoi)


def test_one_team_s_work_is_not_another_s(app, event, facilitator):
    open_mission(facilitator)
    kenji = join(app, event, "Team 1", "kenji")
    rin = join(app, event, "Team 2", "rin")
    answer(kenji, "m1_key_id", "SR-REL-2019")

    assert progress_of(rin)["answered"] == 0


def test_observations_are_counted_too(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    assert progress_of(p)["observations"] == 0

    p.post("/api/observation", {"mission_slug": SLUG,
                                "text": "the commit touches the updater"})
    assert progress_of(p)["observations"] == 1


def test_progress_is_work_and_never_time(app, event, facilitator):
    """The one rule this line must never break."""
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    counts = progress_of(p)

    for clock in ("remaining", "elapsed", "seconds", "minutes", "past_suggested"):
        assert clock not in counts, clock

    html = page(p)
    line = html.split('class="progress"', 1)[1].split("</p>", 1)[0]
    for clock in ("remaining", "elapsed", "countdown", "min", "残り"):
        assert clock not in line, (clock, line)


def test_the_line_is_announced_but_only_when_it_changes(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    head = html.split('class="progress"', 1)[1].split(">", 1)[0]
    assert 'role="status"' in head and 'aria-live="polite"' in head
    assert "if (el.textContent !== text) el.textContent = text;" in html


@pytest.mark.parametrize("lang,fragment", [
    ("en", "of 10 answered"),
    ("ja", "問に回答"),
])
def test_the_progress_line_speaks_both_languages(app, event, facilitator, lang,
                                                 fragment):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    assert fragment in page(p)


def test_the_page_has_a_heading_structure(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    levels = [int(m) for m in re.findall(r"<h([123])\b", html)]
    assert levels.count(1) == 1, "exactly one H1"
    assert levels.count(2) == 4, f"one H2 per step: {levels}"
    assert levels.count(3) == 12, f"10 questions + 2 panels in step 1: {levels}"
    seen = 0
    for level in levels:
        assert level <= seen + 1, f"jumped from h{seen} to h{level}"
        seen = max(seen, level)


def test_each_step_is_a_landmark_with_its_own_heading(app, event, facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))

    for name in ("explore", "record", "answer", "review"):
        marker = f'<section class="step" id="step-{name}"'
        assert marker in html, name
        head = html.split(marker, 1)[1].split(">", 1)[0]
        assert f'aria-labelledby="h-{name}"' in head, name
        assert 'tabindex="-1"' in head, f"{name} cannot take focus when shown"
        assert f'id="h-{name}"' in html, name


def test_every_step_has_somewhere_to_go(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    nav = html.split('class="steps"', 1)[1].split("</nav>", 1)[0]
    targets = re.findall(r'href="#([^"]+)"', nav)
    assert len(targets) == 4, targets
    for target in targets:
        assert f'id="{target}"' in html, f"#{target} goes nowhere"


def test_the_four_steps_are_static_and_never_derived_from_the_clock(
        app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    nav = html.split('class="steps"', 1)[1].split("</nav>", 1)[0]
    assert "<script" not in nav
    for carried in re.findall(r'data-([a-z-]+)=', nav):
        assert carried == "goto", f"a step carries state: data-{carried}"
    for clock in ("remaining", "elapsed", "opens_in", "past_suggested", "minutes"):
        assert clock not in nav, clock
    answer(p, "m1_key_id", "SR-REL-2019")
    assert nav == page(p).split('class="steps"', 1)[1].split("</nav>", 1)[0]


def test_no_step_gates_another(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)
    nav = html.split('class="steps"', 1)[1].split("</nav>", 1)[0]

    for gate in ("disabled", "aria-disabled", "hidden", "display:none"):
        assert gate not in nav, gate
    assert len(re.findall(r'data-goto="', nav)) == 4

    assert "Math.max(0, Math.min(STEPS.length - 1, index))" in html
    for gate in ("progress.answered &&", "if (!answered) return"):
        assert gate not in html, gate


def test_the_sections_are_visible_without_the_script(app, event, facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))
    css = (pathlib.Path(__file__).resolve().parents[1]
           / "app" / "static" / "seccap.css").read_text(encoding="utf-8")

    assert ".page.js .step[hidden] { display: none; }" in css
    assert ".step[hidden] { display: none; }" not in css.replace(
        ".page.js .step[hidden] { display: none; }", "")
    assert "document.querySelector('.page').classList.add('js');" in html
    assert '<div class="stepnav" id="stepnav" hidden>' in html


def test_the_step_nav_puts_the_primary_action_bottom_right(app, event,
                                                           facilitator):
    """Back on the left, Next on the right, and Next is the primary."""
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))
    nav = html.split('class="stepnav"', 1)[1].split("</div>", 1)[0]

    assert nav.index('id="stepback"') < nav.index('id="stepnext"')
    assert 'class="btn primary" id="stepnext"' in nav
    assert 'class="btn ghost" id="stepback"' in nav
    css = (pathlib.Path(__file__).resolve().parents[1]
           / "app" / "static" / "seccap.css").read_text(encoding="utf-8")
    block = css.split(".stepnav {", 1)[1].split("}", 1)[0]
    assert "justify-content: flex-end" in block


@pytest.mark.parametrize("lang,fragment", [
    ("en", "Explore the sources"),
    ("ja", "資料を調べる"),
    ("en", "Review and lock"),
    ("ja", "見直してロックする"),
    ("en", "Click any number above to return to that step"),
    ("ja", "上の番号をクリックすると、どのステップにも戻れます。"),
    ("en", "Step {n} of {total}"),
    ("ja", "ステップ {n} / {total}"),
])
def test_the_steps_speak_both_languages(app, event, facilitator, lang, fragment):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    assert fragment in page(p)


def test_the_autosave_still_reaches_every_question(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert html.count("document.getElementById('questionlist')") == 2
    assert '<div id="questionlist">' in html
    answer(p, "m1_key_id", "SR-REL-2019")
    assert progress_of(p)["answered"] == 1
