import re

import pytest

from app import missions as content

from .conftest import join, open_all_sources, wind_forward

SENTENCE = re.compile(
    r"(?:\b[A-Za-z][A-Za-z'’\-]*\b[ ,;:()/]+){4,}\b[A-Za-z]{2,}")
JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
MARKUP = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S)
ENTITY = re.compile(r"&[a-z]+;|&#\d+;")

SLUGS = ("digital-footprint", "fake-infrastructure", "threat-intelligence")


COMMAND = re.compile(r"<pre\b[^>]*>.*?</pre>", re.S)


def visible_text(html):
    body = html.split("<main", 1)[-1].split("</main>", 1)[0]
    return ENTITY.sub(" ", MARKUP.sub(" ", COMMAND.sub(" ", body)))


@pytest.fixture()
def reader(app, event, facilitator):
    """A joined participant whose session language is Japanese."""
    for slug in SLUGS:
        facilitator.post(f"/facilitator/mission/{slug}/open", {})
        wind_forward(app, event, slug)
        open_all_sources(app, event, slug)
    p = join(app, event, "Team 1", "kenji")
    with p.client.session_transaction() as sess:
        sess["lang"] = "ja"
    return p


def screens(app):
    urls = ["/team", "/briefing", "/final", "/evidence"]
    urls += [f"/mission/{slug}" for slug in SLUGS]
    with app.app_context():
        for slug in SLUGS:
            mission = content.get_mission(app.config["CONTENT_DIR"], slug)
            urls += [f"/artifacts/{a['id']}" for a in mission["artifacts"]]
    return urls


def test_no_english_sentence_on_any_japanese_screen(app, reader):
    urls = screens(app)
    assert len(urls) >= 30, f"only {len(urls)} screens — did the content shrink?"

    offences, rendered_japanese, unreachable = [], 0, []
    for url in urls:
        response = reader.get(url)
        if response.status_code != 200:
            unreachable.append((url, response.status_code))
            continue
        text = visible_text(response.get_data(as_text=True))
        if JAPANESE.search(text):
            rendered_japanese += 1
        for hit in SENTENCE.findall(text):
            offences.append((url, " ".join(hit.split())[:90]))

    assert not unreachable, f"screens that did not render: {unreachable}"
    assert rendered_japanese == len(urls), (
        f"only {rendered_japanese}/{len(urls)} screens rendered any Japanese")
    assert not offences, (
        "English prose on a Japanese page:\n"
        + "\n".join(f"  {url}\n      {hit}" for url, hit in offences))


def test_the_regression_this_was_written_for(app, reader):
    html = reader.get("/artifacts/photo-exif").get_data(as_text=True)
    assert "macOS — インストール" in html
    assert "read the file" not in html
    assert "if you prefer a terminal" not in \
        reader.get("/artifacts/powershell-log").get_data(as_text=True)


def test_the_sweep_would_catch_a_regression():
    assert SENTENCE.search("macOS / Linux (if you prefer a terminal)")
    assert SENTENCE.search("Download the original photograph and read it.")
    assert SENTENCE.search("Open a terminal and run the command below")

    for ok in ("dig +short <hostname> A",
               "Get-FileHash -Algorithm SHA256",
               "certutil -hashfile SakuraVPNUpdate_4.2.1.bin SHA256",
               "T1059.001 PowerShell",
               "archive.sakura-vpn-update.com",
               "Windows (PowerShell)",
               "SR-DEV-077 SR-FIN-014 SR-LAB-021",
               "timestamp_utc host event path sha256 source",
               "CyberChef Decode text UTF-16LE",
               "ALLOW_UNSIGNED_RECOVERY",
               "SR-REL-2019",
               "Predwen SECCAP",
               "端末で dig +short を実行します",
               "macOS / Linux"):
        assert not SENTENCE.search(ok), ok


def test_the_short_labels_are_the_validators_job_not_this_one():
    from app.missions import _is_platform_only

    assert not SENTENCE.search("macOS — read the file")
    assert not _is_platform_only("macOS — read the file")
    assert _is_platform_only("Windows (PowerShell)")
