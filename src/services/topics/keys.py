"""Topic identity: kind detection + canonical keys.

The query string is only the *seed* of a topic — never its identity. Two
searches phrased differently ("INR" vs "implicit neural representations")
should converge on one subject topic; a bare arXiv link is a paper topic; a
"papers by X" query is an author topic.
"""

from __future__ import annotations

import re

# Leading filler phrases stripped before canonicalizing a subject query.
_FILLER_PREFIXES = (
    "papers on ",
    "papers about ",
    "papers by ",
    "paper on ",
    "research on ",
    "research about ",
    "works by ",
    "works on ",
    "latest on ",
    "recent advances in ",
    "advances in ",
    "survey of ",
    "a survey of ",
)

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s\-]", re.UNICODE)


def normalize_topic_key(query: str) -> str:
    """Canonicalize a query into a subject identity key.

    Lowercases, strips filler prefixes, drops punctuation, collapses
    whitespace. Deterministic and cheap — smarter normalization (acronym
    expansion via LLM) can layer on later without changing the contract.
    """
    key = (query or "").strip().lower()
    key = _PUNCT_RE.sub(" ", key)
    key = _WS_RE.sub(" ", key).strip()
    for prefix in _FILLER_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix) :].strip()
            break
    return key


_URLISH_RE = re.compile(r"^(?:https?://|www\.|arxiv\.org/|\d{4}\.\d{4,5})", re.I)


def detect_topic_kind(query: str, *, is_url_input: bool = False) -> str:
    """Classify a seed query into a topic kind: subject | paper | author."""
    if is_url_input or _URLISH_RE.match((query or "").strip()):
        return "paper"
    from api.routes.search import _detect_author_query

    # Only explicit "papers by X" phrasing auto-classifies as an author topic.
    # Bare-name guesses ("Vincent Sitzmann") only reach here when the search
    # already fell through to the topic pipeline — i.e. no author matched —
    # so labeling them author would point the feed at a nonexistent person.
    detected = _detect_author_query(query)
    if detected and detected[1]:
        return "author"
    return "subject"


def canonical_key_for(query: str, *, kind: str) -> str:
    """Canonical identity for *kind*: normalized subject / url / author name."""
    if kind == "paper":
        return (query or "").strip().lower()
    if kind == "author":
        from api.routes.search import _detect_author_query

        detected = _detect_author_query(query)
        author = detected[0] if detected else query
        return normalize_topic_key(author)
    return normalize_topic_key(query)
