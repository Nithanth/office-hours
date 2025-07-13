"""Pure URL helpers for arXiv IDs, domain filters, DOI extraction, etc."""
import re
from typing import Any, Optional
from urllib.parse import urlparse
from loguru import logger

# arXiv helpers ------------------------------------------------------------
def extract_arxiv_id(url_or_text: str | None) -> Optional[str]:
    """Return the bare arXiv ID (e.g. ``2006.09661``) if present in *url_or_text*."""
    if not url_or_text:
        return None

    txt = str(url_or_text).strip().lower().split("?")[0].split("#")[0]

    # New-style IDs (YYMM.NNNNN)
    m = re.search(r"(?P<id>\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?", txt)
    if m:
        return m.group("id")

    # Old category-based IDs (e.g. cs/0307015)
    m = re.search(r"(?P<id>[a-z\-]+\/[0-9]{7})(?:v\d+)?", txt, re.IGNORECASE)
    if m:
        return m.group("id")

    # Prefixed form "arXiv:2006.09661v2"
    m = re.search(r"arxiv:(?P<id>\d{4}\.\d{4,5})(?:v\d+)?", txt)
    if m:
        return m.group("id")
    return None

    # fall-through

def canonical_arxiv_abs_url(arxiv_id: str) -> str:  # noqa: D401 – helper
    """Canonical https://arxiv.org/abs/<id> URL."""
    return f"https://arxiv.org/abs/{arxiv_id}"

# Domain include/exclude filter -------------------------------------------
def _expand_domain_synonyms(domain: str) -> list[str]:
    synonyms: dict[str, list[str]] = {
        "arxiv.org": ["ar5iv.org"],
    }
    domain_lc = domain.lower()
    return [domain_lc] + synonyms.get(domain_lc, [])

def domain_allowed(
    url: Any | None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> bool:
    """Return **True** when *url* passes include/exclude domain filters."""
    if url is None:
        return False

    url_str = str(url)
    try:
        host = urlparse(url_str).netloc.lower()
    except Exception:  # pragma: no cover
        host = url_str.lower()

    # Include filter -----------------------------------------------------
    if include_domains:
        allowed = False
        for d in include_domains:
            for pattern in _expand_domain_synonyms(d):
                if host.endswith(pattern):
                    allowed = True
                    break
            if allowed:
                break
        if not allowed:
            return False

    # Exclude filter -----------------------------------------------------
    if exclude_domains:
        for d in exclude_domains:
            for pattern in _expand_domain_synonyms(d):
                if host.endswith(pattern):
                    return False
    return True

    # No explicit logging to avoid noise in hot path

# PDF URL upgrade helpers --------------------------------------------------
def upgrade_to_pdf_url(url: str | None) -> str | None:
    """Return a more reliable *PDF* URL when possible."""
    if not url:
        return url
    url_str = str(url)

    if url_str.lower().endswith(".pdf"):
        return url_str

    if "arxiv.org" in url_str or "ar5iv.org" in url_str:
        arxiv_id = extract_arxiv_id(url_str)
        if arxiv_id:
            upgraded = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            logger.debug("upgrade_to_pdf_url", original=url_str, upgraded=upgraded)
            return upgraded

    # bioRxiv / medRxiv – convert /content/<id> to .full.pdf
    if "biorxiv.org" in url_str or "medrxiv.org" in url_str:
        parsed = urlparse(url_str)
        if parsed.path.startswith("/content/") and not parsed.path.endswith(".pdf"):
            base_path = parsed.path.rstrip("/")
            upgraded = f"https://{parsed.netloc}{base_path}.full.pdf"
            logger.debug("upgrade_to_pdf_url", original=url_str, upgraded=upgraded)
            return upgraded

    # IEEE Xplore
    if "ieeexplore.ieee.org" in url_str:
        parsed = urlparse(url_str)
        m = re.search(r"/document/(\d+)", parsed.path)
        if m:
            doc_id = m.group(1)
            upgraded = f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={doc_id}"
            logger.debug("upgrade_to_pdf_url", original=url_str, upgraded=upgraded)
            return upgraded

    # ACM DL
    if "dl.acm.org" in url_str:
        parsed = urlparse(url_str)
        if parsed.path.startswith("/doi/") and not parsed.path.endswith("/pdf"):
            upgraded = f"https://{parsed.netloc}{parsed.path}/pdf"
            logger.debug("upgrade_to_pdf_url", original=url_str, upgraded=upgraded)
            return upgraded

    # OpenReview
    if "openreview.net" in url_str:
        parsed = urlparse(url_str)
        paper_id = None
        if parsed.query:
            for kv in parsed.query.split("&"):
                if kv.startswith("id="):
                    paper_id = kv.split("=", 1)[1]
                    break
        if not paper_id and parsed.path.startswith("/forum"):
            parts = parsed.path.rstrip("/").split("/")
            if len(parts) >= 3:
                paper_id = parts[-1]
        if paper_id:
            upgraded = f"https://openreview.net/pdf?id={paper_id}"
            logger.debug("upgrade_to_pdf_url", original=url_str, upgraded=upgraded)
            return upgraded

    # ACL Anthology
    if "aclanthology.org" in url_str or "aclweb.org" in url_str:
        parsed = urlparse(url_str)
        if parsed.path.lower().endswith(".pdf"):
            return url_str
        clean_path = parsed.path.rstrip("/")
        if clean_path:
            upgraded = f"https://{parsed.netloc}{clean_path}.pdf"
            logger.debug("upgrade_to_pdf_url", original=url_str, upgraded=upgraded)
            return upgraded

    return url_str

# Misc utilities -----------------------------------------------------------
_DOI_REGEX = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)

def extract_doi(text: str | None) -> Optional[str]:
    """Lightweight DOI extractor (lower-cases result)."""
    if not text:
        return None
    m = _DOI_REGEX.search(text)
    return m.group(0).lower() if m else None

def url_equivalent(a: str | None, b: str | None) -> bool:  # noqa: D401
    """Return **True** when *a* and *b* clearly refer to the same document."""
    if not a or not b:
        return False

    def _norm(u: str) -> str:
        pu = urlparse(u)
        host = pu.netloc.lower().removeprefix("www.")
        path = pu.path.rstrip("/")
        # arXiv: /abs/ and /pdf/ map to same id
        if "arxiv.org" in host:
            m = re.search(r"(?:abs|pdf)/(\d{4}\.\d{4,5})", path)
            if m:
                return f"arxiv:{m.group(1)}"
        return f"{host}{path}"

    return _norm(a) == _norm(b)

# URL classification -------------------------------------------------------
_PAPER_URL_RE = re.compile(
    r"^(https?://)"  # scheme
    r"("  # start group of domains
    r"([\w.-]+\.)*(arxiv\.org|biorxiv\.org|medrxiv\.org|doi\.org|acm\.org|ieee\.org|springer\.com|elsevier\.com|wiley\.com|nature\.com|sciencemag\.org|pubmed\.ncbi\.nlm\.nih\.gov|ssrn\.com|semanticscholar\.org|researchgate\.net)"
    r"(/\S*)?"  # optional path
    r"|"  # or generic domain with at least one path segment
    r"([\w-]+\.)+[A-Za-z]{2,6}(/([\w.-]+)){1,}/?"  # generic domain
    r")$",
    re.IGNORECASE,
)

def is_paper_url(text: str) -> bool:  # noqa: D401 simple classifier
    """Return **True** if *text* looks like an academic paper URL."""
    result = bool(_PAPER_URL_RE.match(text.strip()))
    logger.debug("is_paper_url", url=text, is_paper=result)
    return result
