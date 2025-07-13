"""Unit tests for infra.exa_utils.url_utils."""

from infra.exa_utils import url_utils as u


def test_extract_arxiv_id_variants() -> None:
    assert u.extract_arxiv_id("https://arxiv.org/abs/2006.09661v1") == "2006.09661"
    assert u.extract_arxiv_id("https://arxiv.org/pdf/2006.09661.pdf") == "2006.09661"
    assert u.extract_arxiv_id("arXiv:2006.09661v2") == "2006.09661"
    assert u.extract_arxiv_id("no id here") is None


def test_domain_allowed_include_exclude() -> None:
    url = "https://arxiv.org/abs/2006.09661"
    assert u.domain_allowed(url, include_domains=["arxiv.org"]) is True
    assert u.domain_allowed(url, exclude_domains=["arxiv.org"]) is False


def test_upgrade_to_pdf_url_arxiv() -> None:
    raw = "https://arxiv.org/abs/2006.09661"
    pdf = u.upgrade_to_pdf_url(raw)
    assert pdf.endswith("2006.09661.pdf")


def test_url_equivalent_arxiv_pdf_abs() -> None:
    a = "https://arxiv.org/abs/2006.09661"
    b = "https://arxiv.org/pdf/2006.09661.pdf"
    assert u.url_equivalent(a, b) is True
