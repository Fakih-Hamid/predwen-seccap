import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from .conftest import join

ROOT = Path(__file__).resolve().parents[1]
MISSIONS = ("digital-footprint", "fake-infrastructure", "threat-intelligence")

PROVIDED = {
    "SECCAP", "CSS", "Node", "Object", "Array", "JSON", "Math", "Date",
    "String", "Number", "Boolean", "Promise", "Set", "Map", "RegExp", "Error",
    "URL", "URLSearchParams", "FormData", "Intl", "NaN", "Infinity",
}


def scripts_of(html):
    return [body for body in
            re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
            if body.strip()]


COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)
COMMENT_LINE = re.compile(r"(?m)//.*$")
SINGLE_QUOTED = re.compile(r"'(?:\\.|[^'\\\n])*'")
DOUBLE_QUOTED = re.compile(r'"(?:\\.|[^"\\\n])*"')
BACKTICKED = re.compile(r"`(?:\\.|[^`\\])*`", re.S)


def code_only(source):
    source = COMMENT_BLOCK.sub(" ", source)
    source = COMMENT_LINE.sub(" ", source)
    source = SINGLE_QUOTED.sub("''", source)
    source = DOUBLE_QUOTED.sub('""', source)
    source = BACKTICKED.sub("``", source)
    return source


def undeclared_shouty_names(source):
    source = code_only(source)
    used = set(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", source))
    declared = set(re.findall(
        r"\b(?:const|let|var|function|class)\s+([A-Z][A-Z0-9_]{2,})\b", source))
    for name in list(used):
        if re.search(r"\.\s*%s\b" % name, source):
            used.discard(name)
        elif re.search(r"\b%s\s*:" % name, source):
            used.discard(name)
    return sorted(used - declared - PROVIDED)


def pages(app, event, facilitator):
    """One of every participant and facilitator screen that carries a script."""
    for slug in MISSIONS:
        facilitator.post(f"/facilitator/mission/{slug}/open", {})
    facilitator.post("/facilitator/session/state",
                     {"final_open": True, "collective_open": True})
    p = join(app, event, "Team 1", "kenji")

    out = {
        "team": p.get("/team"),
        "briefing": p.get("/briefing"),
        "mission": p.get(f"/mission/{MISSIONS[0]}"),
        "evidence": p.get("/evidence"),
        "collective-intel": p.get("/collective-intel"),
        "scoreboard": p.get("/scoreboard"),
        "console": facilitator.get("/facilitator"),
        "scoring": facilitator.get("/facilitator/scoring"),
    }
    for slug in MISSIONS:
        p.post("/api/lock", {"mission_slug": slug})
    out["final"] = p.get("/final")
    out["presentation"] = p.get("/presentation")

    from app.models import Team
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    out["grade"] = facilitator.get(f"/facilitator/grade/{team.id}")

    return {name: r.get_data(as_text=True) for name, r in out.items()}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_inline_script_parses(app, event, facilitator):
    broken = []
    for name, html in pages(app, event, facilitator).items():
        for i, body in enumerate(scripts_of(html)):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                             encoding="utf-8") as handle:
                handle.write(body)
                path = handle.name
            result = subprocess.run(["node", "--check", path],
                                    capture_output=True, text=True)
            Path(path).unlink(missing_ok=True)
            if result.returncode:
                broken.append(f"{name} script {i}: {result.stderr.strip()[:400]}")
    assert not broken, "\n".join(broken)


def test_no_page_reads_a_constant_it_never_declared(app, event, facilitator):
    missing = []
    for name, html in pages(app, event, facilitator).items():
        free = undeclared_shouty_names("\n;\n".join(scripts_of(html)))
        if free:
            missing.append(f"{name}: {free}")
    assert not missing, "\n".join(missing)


def test_the_static_bundle_parses():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "static" / "seccap.js")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
