import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
CONTENT = ROOT / "content"

SPLIT_IN_TEMPLATES = [
    ("briefing.story", "briefing.html"),
    ("briefing.rules", "briefing.html"),
]


def ui():
    return yaml.safe_load((CONTENT / "ui.yaml").read_text(encoding="utf-8"))


def missions():
    for path in sorted((CONTENT / "missions").glob("*.yaml")):
        tree = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if tree.get("narrative"):
            yield tree["slug"], tree["narrative"]


MISSIONS = list(missions())


@pytest.mark.parametrize("dotted,template", SPLIT_IN_TEMPLATES)
def test_the_template_really_does_split_it(dotted, template):
    """If the template stops splitting, this file is testing a fiction."""
    body = (TEMPLATES / template).read_text(encoding="utf-8")
    key = dotted.split(".", 1)[1]
    assert re.search(rf"t\('{re.escape(dotted)}'\)\.split\('..n..n'\)"
                     .replace("..n..n", r"\\n\\n"), body), \
        f"{template} no longer splits {key} into paragraphs"


@pytest.mark.parametrize("dotted,_", SPLIT_IN_TEMPLATES,
                         ids=[d for d, _ in SPLIT_IN_TEMPLATES])
@pytest.mark.parametrize("lang", ["ja", "en"])
def test_briefing_paragraphs_survive_yaml(dotted, _, lang):
    section, key = dotted.split(".")
    text = ui()[section][key][lang]
    assert "\n\n" in text, (
        f"{dotted} [{lang}] has no paragraph break the template can find — "
        f"it will render as one block. A folded scalar needs TWO blank lines; "
        f"a literal block needs one.")


@pytest.mark.parametrize("slug,narrative", MISSIONS, ids=[s for s, _ in MISSIONS])
@pytest.mark.parametrize("lang", ["ja", "en"])
def test_narrative_paragraphs_survive_yaml(slug, narrative, lang):
    text = narrative[lang]
    assert "\n\n" in text, (
        f"{slug} narrative [{lang}] renders as one block")


@pytest.mark.parametrize("slug,narrative", MISSIONS, ids=[s for s, _ in MISSIONS])
@pytest.mark.parametrize("lang", ["ja", "en"])
def test_no_stray_single_newlines(slug, narrative, lang):
    text = narrative[lang]
    strays = text.count("\n") - 2 * text.count("\n\n")
    assert strays == 0, (
        f"{slug} narrative [{lang}] has {strays} single newline(s) inside a "
        f"paragraph")


def test_the_mission_page_splits_the_narrative():
    body = (TEMPLATES / "mission.html").read_text(encoding="utf-8")
    assert "mission.narrative.split('\\n\\n')" in body


@pytest.mark.parametrize("lang,first,second", [
    ("en", "Sakura Robotics detected a suspicious login",
     "Your team is the incident-response unit"),
    ("ja", "開発者アカウントへの不審なログイン",
     "あなたのチームはインシデント対応チームです"),
])
def test_the_briefing_asks_in_a_paragraph_of_its_own(lang, first, second):
    """What happened, and what you are being asked to do, are two things."""
    paragraphs = ui()["briefing"]["story"][lang].split("\n\n")
    assert len(paragraphs) == 2, paragraphs
    assert first in paragraphs[0]
    assert second in paragraphs[1]
