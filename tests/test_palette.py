import itertools
import pathlib
import re

import pytest

CSS = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "seccap.css"

AA_NORMAL = 4.5
AA_LARGE = 3.0
TEAM_SEPARATION = 45.0


def tokens():
    """The `:root` custom properties, resolved one level of var()."""
    text = CSS.read_text(encoding="utf-8")
    root = text.split(":root {", 1)[1].split("\n}", 1)[0]
    raw = dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", root))
    out = {}
    for name, value in raw.items():
        value = value.strip()
        alias = re.fullmatch(r"var\(--([a-z0-9-]+)\)", value)
        if alias:
            value = raw[alias.group(1)].strip()
        if value.startswith("#"):
            out[name] = value
    return out


def srgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lum(rgb):
    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    x, y = lum(srgb(a)), lum(srgb(b))
    hi, lo = max(x, y), min(x, y)
    return (hi + 0.05) / (lo + 0.05)


def _encode(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def grey(rgb):
    c = _encode(lum(rgb))
    return (c, c, c)


def _to_lms(rgb):
    r, g, b = (_lin(c) for c in rgb)
    return (17.8824 * r + 43.5161 * g + 4.11935 * b,
            3.45565 * r + 27.1554 * g + 3.86714 * b,
            0.0299566 * r + 0.184309 * g + 1.46709 * b)


def _from_lms(lms):
    l, m, s = lms
    return tuple(_encode(v) for v in (
        0.0809444479 * l + -0.130504409 * m + 0.116721066 * s,
        -0.0102485335 * l + 0.0540193266 * m + -0.113614708 * s,
        -0.000365296938 * l + -0.00412161469 * m + 0.693511405 * s))


def deuteranopia(rgb):
    l, m, s = _to_lms(rgb)
    return _from_lms((l, 0.494207 * l + 1.24827 * s, s))


def protanopia(rgb):
    l, m, s = _to_lms(rgb)
    return _from_lms((2.02344 * m + -2.52581 * s, m, s))


def separation(a, b):
    ar, ag, ab = (c * 255 for c in a)
    br, bg, bb = (c * 255 for c in b)
    rm = (ar + br) / 2
    return (((2 + rm / 256) * (ar - br) ** 2 + 4 * (ag - bg) ** 2
             + (2 + (255 - rm) / 256) * (ab - bb) ** 2) ** 0.5)


REAL_COMBINATIONS = [
    ("body text on the page", "txt", "bg"),
    ("body text on a panel", "txt", "panel"),
    ("secondary text on a panel", "txt2", "panel"),
    ("muted text on a panel", "mut", "panel"),
    ("mut2 on a panel", "mut2", "panel"),
    ("question numbers on a panel", "faint", "panel"),
    ("faint on the page", "faint", "bg"),
    ("faint in the review dialog", "faint", "panel2"),
    ("typed answer in a field", "txt", "input"),
    ("placeholder in a field", "mut2", "input"),
    ("eyebrow on a panel", "cyan", "panel"),
    ("link on the page", "cyan", "bg"),
    ("active tab on the page", "cyan", "panel2"),
    ("saved line on a panel", "green", "panel"),
    ("saved line in a dialog", "green", "panel2"),
    ("saved line on the active question", "green", "active"),
    ("warning on a panel", "amber", "panel"),
    ("warning in a dialog", "amber", "panel2"),
    ("warning on the active question", "amber", "active"),
    ("danger on a panel", "red", "panel"),
    ("danger in a dialog", "red", "panel2"),
    ("role chip on the page", "violet", "bg"),
    ("nav tab on the page", "mut", "bg"),
    ("menu text on a raised surface", "mut2", "panel2"),
    ("mut2 on a button", "mut2", "panel3"),
    ("button label on a button", "txt", "panel3"),
    ("muted on a button", "mut", "panel3"),
    ("text on the active question", "txt", "active"),
    ("muted on the active question", "mut", "active"),
]

TEAM_TOKENS = ["team-green", "team-violet", "team-rose", "team-sky",
               "team-amber", "team-teal"]


@pytest.mark.parametrize("label,fg,bg", REAL_COMBINATIONS)
def test_every_real_combination_clears_aa(label, fg, bg):
    pal = tokens()
    r = contrast(pal[fg], pal[bg])
    assert r >= AA_NORMAL, f"{label}: {pal[fg]} on {pal[bg]} is {r:.2f}:1"


def test_the_semantic_names_exist_and_point_somewhere():
    text = CSS.read_text(encoding="utf-8")
    root = text.split(":root {", 1)[1].split("\n}", 1)[0]
    for name, target in (("--info", "--cyan"), ("--ok", "--green"),
                         ("--attn", "--amber"), ("--danger", "--red")):
        assert f"{name}: var({target})" in root, name


def test_no_token_is_defined_twice():
    pal = tokens()
    text = CSS.read_text(encoding="utf-8")
    root = text.split(":root {", 1)[1].split("\n}", 1)[0]
    names = re.findall(r"--([a-z0-9-]+):", root)
    assert len(names) == len(set(names)), [n for n in names if names.count(n) > 1]
    assert pal["bg"] != pal["panel"], "the page and its panels are one surface"


@pytest.mark.parametrize("a,b", [
    ("bg", "panel"), ("panel", "input"), ("panel", "active"),
    ("panel", "panel2"), ("panel2", "panel3"),
])
def test_the_surfaces_are_a_ladder_not_a_pile(a, b):
    pal = tokens()
    assert pal[a] != pal[b]
    assert contrast(pal[a], pal[b]) >= 1.05, f"{a} and {b} are the same surface"


def test_fields_are_outlined_harder_than_the_panels_around_them():
    text = CSS.read_text(encoding="utf-8")
    assert "--fieldline:" in text
    rule = re.search(r"input\[type=text\][^{]*\{([^}]*)\}", text)
    assert rule and "var(--fieldline)" in rule.group(1)


def test_focus_is_visible_everywhere():
    text = CSS.read_text(encoding="utf-8")
    rule = re.search(r":focus-visible\s*\{([^}]*)\}", text)
    assert rule, "nothing styles :focus-visible"
    body = rule.group(1)
    assert "outline:" in body and "none" not in body
    assert "box-shadow:" in body


def test_reduced_motion_is_still_respected():
    assert "@media (prefers-reduced-motion: reduce)" in CSS.read_text(encoding="utf-8")


def test_all_six_team_colours_are_readable_on_the_board():
    pal = tokens()
    for name in TEAM_TOKENS:
        r = contrast(pal[name], pal["bg"])
        assert r >= AA_NORMAL, f"{name} is {r:.2f}:1 on the board"


@pytest.mark.parametrize("mode,fn", [
    ("normal", lambda c: c),
    ("greyscale", grey),
    ("deuteranopia", deuteranopia),
    ("protanopia", protanopia),
])
def test_no_two_teams_share_a_colour_in_any_mode(mode, fn):
    """The old six scored 1 in greyscale: violet and rose were the same team."""
    pal = tokens()
    seen = {n: fn(srgb(pal[n])) for n in TEAM_TOKENS}
    worst = min((separation(seen[a], seen[b]), a, b)
                for a, b in itertools.combinations(TEAM_TOKENS, 2))
    d, a, b = worst
    assert d >= TEAM_SEPARATION, f"{mode}: {a} and {b} are {d:.0f} apart"


def test_the_team_colour_is_never_the_only_carrier():
    css = CSS.read_text(encoding="utf-8")
    stripes = re.findall(r"\.c-([a-z]+)\s*\{[^}]*\}", css)
    assert stripes, "no team stripe rules"
    for rule in re.findall(r"\.c-[a-z]+[^{]*\{([^}]*)\}", css):
        assert "border-left-color" in rule, rule
        assert "content:" not in rule, "a stripe is inventing text"
