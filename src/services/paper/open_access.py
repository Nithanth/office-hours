"""Bounded, cached discovery of public copies of a paper."""

import asyncio
import json
import re
import time
import urllib.parse
import urllib.request
from contextlib import suppress
from functools import lru_cache
from html.parser import HTMLParser

from config import config
from services.core.exa_utils.url_utils import extract_arxiv_id, extract_doi
from services.paper.metadata.semantic_scholar import _get_headers
from services.paper.metadata.unpaywall import resolve_open_access_url


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def is_paywall(text: str, *, max_chars: int = 3000, is_html: bool = False) -> bool:
    challenge_markup = False
    if is_html:
        challenge_markup = bool(
            re.search(
                r"cf-turnstile|g-recaptcha|h-captcha|/recaptcha/|challenges\.cloudflare\.com/|"
                r"(?:class|id)=[\"'][^\"']*(?:captcha|turnstile)|"
                r"<title[^>]*>\s*just a moment",
                text,
                re.I,
            )
        )
        parser = _VisibleText()
        parser.feed(text)
        text = " ".join(" ".join(parser.parts).split())
    if len(text) >= max_chars:
        return False
    return challenge_markup or bool(
        re.search(
            r"access denied|purchase (?:this |the )?article|sign in to (?:access|read)|subscribe to (?:access|read)|403 forbidden|"
            r"just a moment|verifying (?:you are human|your browser)|complete the check below|"
            r"enable javascript and cookies to continue|(?:complete|solve) (?:the )?captcha",
            text,
            re.I,
        )
    )


@lru_cache(maxsize=512)
def _candidates(url: str, title: str, epoch: int) -> tuple[str, ...]:
    doi = extract_doi(url)
    arxiv = extract_arxiv_id(url) if "arxiv" in url.lower() else None
    meta = {}
    fields = "title,externalIds,openAccessPdf"
    base = "https://api.semanticscholar.org/graph/v1/paper/"
    if doi:
        endpoint = (
            base + urllib.parse.quote("DOI:" + doi, safe="") + "?fields=" + fields
        )
    elif title:
        endpoint = (
            base
            + "search/match?"
            + urllib.parse.urlencode({"query": title, "fields": fields})
        )
    else:
        endpoint = (
            base + urllib.parse.quote("URL:" + url, safe="") + "?fields=" + fields
        )
    try:
        req = urllib.request.Request(endpoint, headers=_get_headers())
        with urllib.request.urlopen(req, timeout=3) as response:
            payload = json.load(response)
        meta = (payload.get("data") or [{}])[0] if "data" in payload else payload
    except (OSError, ValueError, IndexError):
        meta = {}
    ext = meta.get("externalIds") or {}
    arxiv = arxiv or ext.get("ArXiv")
    doi = doi or ext.get("DOI")
    candidates: list[str | None] = []
    if arxiv:
        candidates.append(f"https://arxiv.org/pdf/{arxiv}.pdf")
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname in {"openreview.net", "www.openreview.net"}:
        paper_id = urllib.parse.parse_qs(parsed.query).get("id", [None])[0]
        if paper_id:
            candidates.append(
                "https://openreview.net/pdf?" + urllib.parse.urlencode({"id": paper_id})
            )
    if doi and config.OPENALEX_MAILTO:
        with suppress(OSError, ValueError):
            candidates.append(resolve_open_access_url(doi, timeout=3))
    candidates.append((meta.get("openAccessPdf") or {}).get("url"))
    return tuple(
        dict.fromkeys(
            c for c in candidates if c and c.startswith(("https://", "http://"))
        )
    )


async def open_copy_candidates(
    url: str, title: str = "", failed: tuple[str, ...] = ()
) -> list[str]:
    try:
        candidates = await asyncio.wait_for(
            asyncio.to_thread(_candidates, url, title, int(time.time() // 900)),
            timeout=8,
        )
    except TimeoutError:
        return []
    return [candidate for candidate in candidates if candidate not in {url, *failed}]
