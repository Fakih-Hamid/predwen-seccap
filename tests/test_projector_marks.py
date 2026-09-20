import pathlib
import re

import pytest

from .conftest import join

CSS = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "seccap.css"
STATIC = pathlib.Path(__file__).resolve().parents[1] / "app" / "static"


def board(facilitator, projector=True, lang="en"):
    with facilitator.client.session_transaction() as sess:
        sess["lang"] = lang
    return facilitator.get(
        "/scoreboard?projector" if projector else "/scoreboard").get_data(as_text=True)


def test_the_marks_are_in_the_projector_frame(facilitator, event):
    html = board(facilitator, projector=True)
    strip = html.split('class="projmarks"', 1)
    assert len(strip) == 2, "no institutional strip on the projector"
    block = strip[1].split("</div>", 1)[0]
    assert "naist.png" in block
    assert "iplab.png" in block


def test_the_ordinary_scoreboard_does_not_grow_a_second_copy(facilitator, event):
    html = board(facilitator, projector=False)
    assert 'class="projmarks"' not in html
    assert "foot-marks" in html


def test_the_files_are_the_ones_that_were_always_used(facilitator, event):
    assert (STATIC / "naist.png").exists()
    assert (STATIC / "iplab.png").exists()


def test_the_alt_text_is_unchanged(facilitator, event):
    html = board(facilitator, projector=True)
    block = html.split('class="projmarks"', 1)[1].split("</div>", 1)[0]
    assert 'alt="NAIST"' in block
    assert 'alt="IPLab, Laboratory for Cyber Resilience"' in block
    footer = board(facilitator, projector=False)
    assert 'alt="NAIST"' in footer
    assert 'alt="IPLab, Laboratory for Cyber Resilience"' in footer


def test_a_missing_file_removes_itself_rather_than_showing_a_broken_glyph(
        facilitator, event):
    block = board(facilitator, projector=True).split(
        'class="projmarks"', 1)[1].split("</div>", 1)[0]
    assert block.count('onerror="this.remove()"') == 2


def test_the_strip_is_taken_out_of_the_flow(facilitator, event):
    css = CSS.read_text(encoding="utf-8")
    rule = css.split(".projector .projmarks {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in rule
    assert "top:" in rule and "right:" in rule
    assert ".projector .page { position: relative; }" in css


def test_it_is_hidden_outside_projector_mode_by_the_stylesheet_too(facilitator):
    css = CSS.read_text(encoding="utf-8")
    assert ".projmarks { display: none; }" in css


def test_the_max_line_is_compact_on_the_projector(facilitator):
    css = CSS.read_text(encoding="utf-8")
    assert ".projector .maxline {" in css
    rule = css.split(".projector .maxline {", 1)[1].split("}", 1)[0]
    assert "font-size" in rule and "margin" in rule


@pytest.mark.parametrize("lang,columns", [
    ("en", ["Rank", "Team", "M1", "M2", "M3", "Final", "Total"]),
    ("ja", ["順位", "チーム", "M1", "M2", "M3", "統合", "合計"]),
])
def test_every_column_label_is_still_defined(app, facilitator, event, lang,
                                             columns):
    from app import ui

    for key, expected in zip(["rank", "team", "total"],
                             [columns[0], columns[1], columns[-1]]):
        source = "common.team" if key == "team" else f"scoreboard.{key}"
        got = ui.t(app.config["CONTENT_DIR"], source, lang)
        assert got == expected, (source, got)
        assert not got.startswith("⟦"), source


def test_no_ui_key_renders_as_a_placeholder(app, facilitator, event):
    """The whole file, not just the scoreboard."""
    from app import ui
    errors = ui.validate(app.config["CONTENT_DIR"])
    assert errors == [], errors


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_the_projected_page_carries_the_board_and_the_marks_together(
        facilitator, event, lang):
    html = board(facilitator, projector=True, lang=lang)
    assert 'class="projmarks"' in html
    assert 'id="maxline"' in html
    assert 'id="prov"' in html
    assert "table" in html
