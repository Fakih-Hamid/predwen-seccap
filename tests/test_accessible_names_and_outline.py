import re

import pytest

from app import missions as content

from .conftest import join, open_mission

SLUGS = ("digital-footprint", "fake-infrastructure", "threat-intelligence")
HEADING = re.compile(r"<h([1-6])\b", re.I)
OPEN_LINK = re.compile(r"<a\b[^>]*\bdata-evopen\b[^>]*>", re.I)
ARIA = re.compile(r'aria-label="([^"]*)"', re.I)


@pytest.fixture()
def reader(app, event, facilitator):
    for slug in SLUGS:
        open_mission(facilitator, slug)
    return join(app, event, "Team 1", "kenji")


def main_of(html):
    return html.split("<main", 1)[-1].split("</main>", 1)[0]


def artifact_ids(app, slug):
    with app.app_context():
        mission = content.get_mission(app.config["CONTENT_DIR"], slug)
    return [a["id"] for a in mission["artifacts"]]


@pytest.mark.parametrize("slug", SLUGS)
def test_every_open_source_link_says_which_question_it_belongs_to(reader, slug):
    html = main_of(reader.get(f"/mission/{slug}").get_data(as_text=True))
    links = OPEN_LINK.findall(html)
    assert links, f"{slug} renders no open-source links at all"

    labels = []
    for tag in links:
        found = ARIA.search(tag)
        assert found, f"{slug}: an open-source link has no aria-label: {tag}"
        labels.append(found.group(1))

    assert len(set(labels)) == len(labels), (
        f"{slug}: {len(links)} links share {len(set(labels))} name(s) — a "
        f"screen reader cannot tell them apart: {labels}")


@pytest.mark.parametrize("slug", SLUGS)
def test_the_visible_text_stays_short(reader, slug):
    html = main_of(reader.get(f"/mission/{slug}").get_data(as_text=True))
    visible = re.findall(r"<a\b[^>]*\bdata-evopen\b[^>]*>(.*?)</a>", html, re.S)
    assert visible, slug
    for text in visible:
        assert "question" not in text.lower(), text
        assert "設問" not in text, text
        assert len(" ".join(text.split())) < 30, text


@pytest.mark.parametrize("slug", SLUGS)
def test_the_link_is_out_of_the_tab_order_until_it_is_offered(reader, slug):
    html = main_of(reader.get(f"/mission/{slug}").get_data(as_text=True))
    for tag in OPEN_LINK.findall(html):
        assert re.search(r"\bhidden\b", tag), (
            f"{slug}: an open-source link renders without `hidden`, so it is "
            f"in the tab order before there is anything to open: {tag}")
    assert "link.hidden = !id;" in reader.get(
        f"/mission/{slug}").get_data(as_text=True)


def levels(html):
    return [int(n) for n in HEADING.findall(main_of(html))]


@pytest.mark.parametrize("slug", SLUGS)
def test_the_mission_page_outline_never_skips_a_level(reader, slug):
    found = levels(reader.get(f"/mission/{slug}").get_data(as_text=True))
    assert found and found[0] == 1, found
    for previous, current in zip(found, found[1:]):
        assert current <= previous + 1, (
            f"{slug}: heading jumps from h{previous} to h{current}: {found}")


def test_every_source_page_outline_never_skips_a_level(app, reader):
    checked = 0
    for slug in SLUGS:
        for artifact_id in artifact_ids(app, slug):
            html = reader.get(f"/artifacts/{artifact_id}").get_data(as_text=True)
            found = levels(html)
            assert found and found[0] == 1, (artifact_id, found)
            for previous, current in zip(found, found[1:]):
                assert current <= previous + 1, (
                    f"{artifact_id}: h{previous} then h{current}: {found}")
            checked += 1
    assert checked >= 25, f"only {checked} source pages seen"


def test_the_source_page_asks_for_the_level_it_needs(reader):
    source = reader.get("/artifacts/commit").get_data(as_text=True)
    assert re.search(r'<h2 id="stuck-h-commit"', source), (
        "the source page's Stuck? heading is not an h2")
    assert "<h3" not in main_of(source)


def test_the_per_question_hints_introduce_no_heading(reader):
    html = main_of(reader.get("/mission/digital-footprint").get_data(as_text=True))
    block = html.split('data-scope="q:', 1)[1][:500]
    assert "<summary" in block
    assert not HEADING.search(block), block[:120]


def test_the_guard_would_catch_a_regression():
    assert levels("<main><h1>a</h1><h3>b</h3></main>") == [1, 3]
    found = levels("<main><h1>a</h1><h3>b</h3></main>")
    assert any(c > p + 1 for p, c in zip(found, found[1:]))
    ok = levels("<main><h1>a</h1><h2>b</h2><h3>c</h3></main>")
    assert not any(c > p + 1 for p, c in zip(ok, ok[1:]))
