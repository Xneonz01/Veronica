"""A web page as the text a person would read on it, in time linear in its size.

What the model needs is the words, in order, without scripts, styles, navigation or
markup; block elements end a line, so headings and paragraphs don't run into each other.

Not the standard library's `html.parser`: on crafted input it slows far faster than the
page grows (40 KB of `<a ` took 31 s, and a page may be 2 MB), and it ran on the event
loop the calls share. This reader can't: every `<` either completes a tag, a comment or
a declaration, which is then consumed, or, with nothing left that closes it, makes the
rest of the page text and ends the read. No position is read twice, so the cost is the
page's length. A `<` not followed by a letter, `/`, `!` or `?` is text, as a browser
reads it ("a < b").
"""

from __future__ import annotations

import html as entities
import re

from app.core.text_safety import clean_untrusted

# Elements whose content is never read: the markup around the page's words.
_SKIPPED = frozenset({"head", "nav", "footer", "template", "svg", "math"})
# Elements whose content is raw text to the end tag: scripts and styles are code, the
# rest are read by no person; a title is kept apart.
_RAW = frozenset(
    {"script", "style", "noscript", "textarea", "xmp", "iframe", "noembed", "noframes", "title"}
)
_BLOCKS = frozenset(
    "p div br li tr h1 h2 h3 h4 h5 h6 section article header blockquote pre table ul ol "
    "dt dd".split()
)
# The end of a tag: its `>`, or the opening quote of an attribute value, whose closing
# quote is then found with `str.find`, so a `>` inside a value doesn't end the tag.
_TAG_SCAN = re.compile(r">|=\s*([\"'])")
# A tag's name, which opens it only straight after `<` (or `</`): "a < b" is text.
_NAME = re.compile(r"/?([A-Za-z][A-Za-z0-9:-]*)")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
# The most of a page that is parsed: the model reads at most 8,000 characters of it.
MAX_HTML_CHARS = 512 * 1024


def readable(html: str) -> tuple[str, str]:
    """(title, text) of an HTML page, whitespace folded and invisible characters removed."""
    reader = _Reader(html[:MAX_HTML_CHARS])
    reader.read()
    lines = (_SPACES.sub(" ", line).strip() for line in "".join(reader.parts).split("\n"))
    text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    title = _SPACES.sub(" ", " ".join(reader.title)).strip()
    return clean_untrusted(title), clean_untrusted(text)


class _Reader:
    def __init__(self, html: str) -> None:
        self.html = html
        self.parts: list[str] = []
        self.title: list[str] = []
        self._skipping = 0

    def read(self) -> None:
        html, at = self.html, 0
        while at < len(html):
            lt = html.find("<", at)
            if lt == -1:
                self._text(html[at:])
                return
            self._text(html[at:lt])
            after = lt + 1
            if html.startswith("!--", after):
                end = html.find("-->", after + 3)
                if end == -1:
                    return  # the rest is a comment
                at = end + 3
            elif html.startswith(("!", "?"), after):
                end = html.find(">", after)
                if end == -1:
                    return
                at = end + 1
            elif not _NAME.match(html, after):
                self._text("<")
                at = after
            else:
                end = _tag_end(html, after)
                if end == -1:
                    self._text(html[lt:])
                    return
                at = self._tag(html[after:end], end + 1)
                if at == -1:
                    return

    def _tag(self, inside: str, at: int) -> int:
        """Act on one tag; return where reading continues, or -1 at the end."""
        found = _NAME.match(inside)
        if found is None:
            return at
        name, closing = found.group(1).lower(), inside.startswith("/")
        if not closing and name in _RAW:
            close = re.compile(rf"</{name}\b", re.IGNORECASE).search(self.html, at)
            if close is None:
                return -1  # unclosed raw text runs to the end
            if name == "title":
                self.title.append(entities.unescape(self.html[at : close.start()]))
            end = self.html.find(">", close.end())
            return -1 if end == -1 else end + 1
        if name in _SKIPPED and not inside.rstrip().endswith("/"):
            self._skipping = max(0, self._skipping + (-1 if closing else 1))
        elif name in _BLOCKS:
            self.parts.append("\n")
        return at

    def _text(self, chunk: str) -> None:
        if chunk and not self._skipping:
            self.parts.append(entities.unescape(chunk))


def _tag_end(html: str, start: int) -> int:
    """The index of the `>` that ends the tag starting at `start`, or -1 if none does."""
    at = start
    while True:
        found = _TAG_SCAN.search(html, at)
        if found is None:
            return -1
        if found.group(0) == ">":
            return found.start()
        close = html.find(found.group(1), found.end())
        if close == -1:
            return -1
        at = close + 1
