import pathlib
import re

import pytest

CSS = (pathlib.Path(__file__).resolve().parents[1]
       / "app" / "static" / "seccap.css").read_text(encoding="utf-8")

PALETTE = dict(re.findall(r"--([a-z0-9\-]+):\s*(#[0-9a-fA-F]{6});", CSS))

SURFACES = ("bg", "panel", "panel2", "panel3", "input", "active")
TEXT = ("txt", "txt2", "mut", "mut2", "faint",
        "cyan", "amber", "green", "red", "violet", "rose",
        "gold")

AA = 4.5

KNOWN_UNUSED = {("faint", "panel3")}


def relative_luminance(hex_colour):
    channels = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def linear(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    x, y = relative_luminance(a), relative_luminance(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def test_the_palette_is_still_the_one_this_file_measures():
    """If a colour is added or removed, this file has to see it."""
    for name in SURFACES + TEXT:
        assert name in PALETTE, f"--{name} is gone from seccap.css"


@pytest.mark.parametrize("text", TEXT)
@pytest.mark.parametrize("surface", SURFACES)
def test_text_on_surface_meets_aa(text, surface):
    ratio = contrast(PALETTE[text], PALETTE[surface])
    if (text, surface) in KNOWN_UNUSED:
        assert ratio < AA, (
            f"--{text} on --{surface} is now {ratio:.2f} and passes AA — "
            f"remove it from KNOWN_UNUSED")
        return
    assert ratio >= AA, (
        f"--{text} on --{surface} is {ratio:.2f}, below AA {AA}")


def test_the_two_colours_the_comments_say_were_fixed_are_still_fixed():
    """Named, so a revert to the old hex is caught by name."""
    assert PALETTE["mut2"] == "#8496a9", "the pre-fix #6f8092 is back"
    assert PALETTE["faint"] == "#7c8fa3", "the pre-fix #47576a is back"
    assert contrast(PALETTE["mut2"], PALETTE["panel2"]) >= AA
    assert contrast(PALETTE["faint"], PALETTE["panel"]) >= AA


def test_the_how_to_panel_is_readable():
    for text in ("txt", "txt2", "mut"):
        assert contrast(PALETTE[text], PALETTE["panel2"]) >= AA, text
    assert contrast(PALETTE["txt2"], PALETTE["input"]) >= AA


def test_the_guard_would_catch_a_regression():
    """A guard nobody has seen fail is a guard nobody should trust."""
    assert contrast("#000000", "#ffffff") > 20
    assert contrast("#777777", "#808080") < AA
    assert contrast("#6f8092", PALETTE["panel2"]) < AA
