"""A web page as the text a person would read on it.

The standard library's HTML parser, not a dependency: what the model needs is the
words, in order, without scripts, styles, navigation or markup. Block elements end a
line, so headings and paragraphs don't run into each other.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIPPED = frozenset({"script", "style", "noscript", "template", "svg", "head", "nav", "footer"})
_BLOCKS = frozenset(
    "p div br li tr h1 h2 h3 h4 h5 h6 section article header blockquote pre table ul ol "
    "dt dd".split()
)
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self._skipping = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in _SKIPPED:
            self._skipping += 1
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _SKIPPED:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
        elif not self._skipping:
            self.parts.append(data)


def readable(html: str) -> tuple[str, str]:
    """(title, text) of an HTML page, whitespace folded."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    lines = (_SPACES.sub(" ", line).strip() for line in "".join(reader.parts).split("\n"))
    text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    title = _SPACES.sub(" ", " ".join(reader.title)).strip()
    return title, text
