"""The README's contents list has to keep pointing at real headings.

Hand-written anchors die silently when a heading is renamed, so this test is
the thing that notices. It implements GitHub's slug rules, which were checked
against a rendered page when the list was added.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, strip punctuation, spaces to hyphens."""
    text = heading.replace("`", "").lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text.strip())


def headings(markdown: str, level: int = 2) -> list[str]:
    """Headings at `level`, ignoring any `#` line inside a fenced code block."""
    found: list[str] = []
    fence = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            fence = not fence
        elif not fence and re.match(rf"^{'#' * level} (.+)$", line):
            found.append(line[level + 1 :].strip())
    return found


def toc(markdown: str) -> list[tuple[str, str]]:
    return re.findall(r"^- \[([^\]]+)\]\(#([^)]+)\)$", markdown, re.M)


def test_every_row_points_at_a_real_heading():
    text = README.read_text()
    targets = {slug(h) for h in headings(text)}
    broken = [(label, anchor) for label, anchor in toc(text) if anchor not in targets]
    assert not broken, f"contents rows with no heading: {broken}"


def test_every_section_is_listed_in_order():
    text = README.read_text()
    sections = [h for h in headings(text) if h != "Contents"]
    assert [label for label, _ in toc(text)] == sections


def test_no_two_headings_share_an_anchor():
    # GitHub appends -1 to the second of a duplicate pair, silently breaking a
    # link that used to resolve.
    duplicates = [s for s, n in Counter(slug(h) for h in headings(README.read_text())).items() if n > 1]
    assert not duplicates, f"duplicate heading anchors: {duplicates}"


def test_contents_is_the_first_section():
    assert headings(README.read_text())[0] == "Contents"
