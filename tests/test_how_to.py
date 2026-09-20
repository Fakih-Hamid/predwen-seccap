import pathlib

import pytest
import yaml

from .conftest import join, open_all_sources, wind_forward

ROOT = pathlib.Path(__file__).resolve().parents[1]
MISSIONS = ROOT / "content" / "missions"

TOOL_SOURCES = [
    ("digital-footprint", "photo-exif", "exiftool"),
    ("fake-infrastructure", "update-service", "curl"),
    ("fake-infrastructure", "payload", "sha256"),
    ("fake-infrastructure", "dns", "dig"),
    ("fake-infrastructure", "rdap", "lookup"),
    ("threat-intelligence", "powershell-log", "base64"),
    ("threat-intelligence", "persistence", "spreadsheet"),
    ("threat-intelligence", "file-timeline", "spreadsheet"),
    ("threat-intelligence", "network", "spreadsheet"),
]


def os_text(label):
    if isinstance(label, dict):
        return " ".join(v for v in label.values() if isinstance(v, str))
    return label or ""


def definition(slug):
    for path in MISSIONS.glob("*.yaml"):
        tree = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if tree.get("slug") == slug:
            return tree
    raise AssertionError(slug)


def source(slug, artifact_id):
    for a in definition(slug).get("artifacts") or []:
        if a["id"] == artifact_id:
            return a
    raise AssertionError(f"{slug}/{artifact_id}")


def page(app, event, facilitator, slug, artifact_id):
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    wind_forward(app, event, slug)
    open_all_sources(app, event, slug)
    p = join(app, event, "Team 1", "kenji")
    r = p.get(f"/artifacts/{artifact_id}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


@pytest.mark.parametrize("slug,artifact_id,keyword", TOOL_SOURCES,
                         ids=[a for _, a, _ in TOOL_SOURCES])
def test_every_tool_source_explains_its_tool(slug, artifact_id, keyword):
    block = source(slug, artifact_id).get("how_to")
    assert block, f"{artifact_id} needs a tool and explains nothing"
    assert block.get("steps"), artifact_id
    for step in block["steps"]:
        words = step.get("say") if "say" in step else step
        assert words.get("ja") and words.get("en"), artifact_id


@pytest.mark.parametrize("slug,artifact_id,keyword", TOOL_SOURCES,
                         ids=[a for _, a, _ in TOOL_SOURCES])
def test_the_steps_or_commands_name_the_tool(slug, artifact_id, keyword):
    """A block that never says what it is about is a block nobody can follow."""
    block = source(slug, artifact_id)["how_to"]
    haystack = " ".join(
        [(s.get("en") or "") for s in block.get("steps") or []]
        + [c.get("cmd") or "" for c in block.get("commands") or []]
        + [os_text(block.get("tool")),
           (block.get("no_install") or {}).get("en") or ""]
    ).lower()
    assert keyword in haystack, f"{artifact_id}: nothing mentions {keyword!r}"


@pytest.mark.parametrize("slug,artifact_id,keyword", TOOL_SOURCES,
                         ids=[a for _, a, _ in TOOL_SOURCES])
def test_nobody_is_stopped_by_a_locked_down_laptop(slug, artifact_id, keyword):
    block = source(slug, artifact_id)["how_to"]
    commands = block.get("commands") or []
    labels = " ".join(os_text(c.get("os")) for c in commands).lower()
    assert "windows" in labels or block.get("no_install"), artifact_id


def test_the_panel_renders_without_being_asked_for(app, event, facilitator):
    """Open, on load. Not a <details>, not behind the Stuck? button."""
    html = page(app, event, facilitator, "digital-footprint", "photo-exif")

    assert "How to work with this" in html
    assert "exiftool -s apac-fallback-bench.jpg" in html
    assert "brew install exiftool" in html
    block = html.split("data-howto", 1)[1].split('class="stuck', 1)[0]
    assert "<details" not in block
    assert "data-rung" not in block
    assert 'class="stuck' in html


def test_it_carries_windows_where_the_command_differs(app, event, facilitator):
    html = page(app, event, facilitator, "fake-infrastructure", "payload")
    assert "Get-FileHash" in html
    assert "certutil -hashfile" in html
    assert "sha256sum" in html


def test_a_tool_that_lives_elsewhere_is_linked(app, event, facilitator):
    """"Open CyberChef" with no link is not an instruction."""
    html = page(app, event, facilitator, "threat-intelligence", "powershell-log")
    assert "https://gchq.github.io/CyberChef/" in html
    assert "Open CyberChef" in html


def test_the_tool_link_goes_through_the_manifest(app):
    from app import external

    for slug, artifact_id, _ in TOOL_SOURCES:
        link = (source(slug, artifact_id)["how_to"]).get("tool_link")
        if not link:
            continue
        assert external.placeholder_key(link), f"{artifact_id}: literal URL"

    with app.app_context():
        errors = external.validate_references(
            app.config["CONTENT_DIR"],
            [definition(s) for s in ("digital-footprint", "fake-infrastructure",
                                     "threat-intelligence")])
    assert not errors, errors


@pytest.mark.parametrize("lang,fragment", [
    ("en", "How to work with this"),
    ("ja", "この資料の調べ方"),
    ("en", "Nothing to install?"),
    ("ja", "ツールをインストールできない場合："),
])
def test_the_panel_speaks_both_languages(app, event, facilitator, lang,
                                         fragment):
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    wind_forward(app, event, "digital-footprint")
    open_all_sources(app, event, "digital-footprint")
    p = join(app, event, "Team 1", "kenji")
    with p.client.session_transaction() as sess:
        sess["lang"] = lang
    html = p.get("/artifacts/photo-exif").get_data(as_text=True)
    assert fragment in html


PLATFORM_ONLY = {"macOS / Linux", "Windows (PowerShell)", "Windows (cmd)"}


@pytest.mark.parametrize("slug,artifact_id,keyword", TOOL_SOURCES,
                         ids=[a for _, a, _ in TOOL_SOURCES])
def test_an_os_label_that_says_more_than_the_platform_is_bilingual(
        slug, artifact_id, keyword):
    prose = []
    for command in source(slug, artifact_id)["how_to"].get("commands") or []:
        label = command.get("os")
        if isinstance(label, str) and label not in PLATFORM_ONLY:
            prose.append(label)
        if isinstance(label, dict):
            assert label.get("ja") and label.get("en"), (artifact_id, label)
    assert not prose, (
        f"{artifact_id}: these `os` labels say more than the platform and are "
        f"English only: {prose}")


def test_the_panel_renders_the_label_in_japanese(app, event, facilitator):
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    wind_forward(app, event, "digital-footprint")
    open_all_sources(app, event, "digital-footprint")
    p = join(app, event, "Team 1", "kenji")
    with p.client.session_transaction() as sess:
        sess["lang"] = "ja"
    html = p.get("/artifacts/photo-exif").get_data(as_text=True)

    assert "macOS — インストール" in html
    assert "読み取り" in html
    assert "read the file" not in html


def test_the_validator_refuses_an_english_qualifier():
    from app.missions import _is_platform_only

    assert _is_platform_only("macOS / Linux")
    assert _is_platform_only("Windows (PowerShell)")
    assert _is_platform_only("Windows (cmd)")
    assert not _is_platform_only("macOS — install")
    assert not _is_platform_only("macOS / Linux (if you prefer a terminal)")
    assert not _is_platform_only("macOS (if sha256sum is missing)")


COMMANDS = ("sha256sum", "certutil", "get-filehash", "curl -i", "dig ",
            "nslookup", "exiftool -", "exiftool apac", "resolve-dnsname",
            "git show")


@pytest.mark.parametrize("slug", ["digital-footprint", "fake-infrastructure",
                                  "threat-intelligence"])
def test_the_mission_tool_rung_routes_rather_than_repeating(slug):
    rungs = [h for h in definition(slug).get("hints") or []
             if h.get("level") == "tool"]
    assert len(rungs) == 1, slug
    text = (rungs[0]["text"]["en"]).lower()

    found = [c for c in COMMANDS if c in text]
    assert not found, f"{slug}: the tool rung still spells out {found}"
    assert "page" in text, slug


def test_a_question_can_open_the_source_it_names(app, event, facilitator):
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    wind_forward(app, event, "digital-footprint")
    open_all_sources(app, event, "digital-footprint")
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/mission/digital-footprint").get_data(as_text=True)

    assert "data-evopen" in html
    assert "Open this source" in html
    assert "data-evopen hidden" in html
    assert "'/artifacts/' + encodeURIComponent(id)" in html
