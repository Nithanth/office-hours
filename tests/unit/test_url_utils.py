"""Unit tests for services.core.exa_utils.url_utils."""

from services.core.exa_utils import url_utils as u


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


def test_url_equivalent_arxiv_ar5iv_mirror() -> None:
    """ar5iv.org mirrors should be treated as the same paper as arxiv.org."""
    assert (
        u.url_equivalent(
            "https://arxiv.org/abs/2401.12345",
            "https://ar5iv.org/abs/2401.12345",
        )
        is True
    )


def test_url_equivalent_arxiv_version_suffix() -> None:
    """v1 vs v2 of the same paper should compare equal."""
    assert (
        u.url_equivalent(
            "https://arxiv.org/abs/2006.09661v1",
            "https://arxiv.org/abs/2006.09661v3",
        )
        is True
    )


def test_url_equivalent_arxiv_old_style_id() -> None:
    """Old-style category/ID arXiv URLs should compare equal across abs/pdf."""
    assert (
        u.url_equivalent(
            "https://arxiv.org/abs/cs/0307015",
            "https://arxiv.org/pdf/cs/0307015",
        )
        is True
    )


def test_url_equivalent_doi() -> None:
    """Two URLs carrying the same DOI should compare equal."""
    assert (
        u.url_equivalent(
            "https://doi.org/10.1038/s41586-020-03051-4",
            "https://dx.doi.org/10.1038/s41586-020-03051-4",
        )
        is True
    )


def test_url_equivalent_distinct_papers() -> None:
    """Different arXiv IDs must not compare equal."""
    assert (
        u.url_equivalent(
            "https://arxiv.org/abs/2401.12345",
            "https://arxiv.org/abs/2401.12346",
        )
        is False
    )


def test_url_equivalent_handles_none() -> None:
    assert u.url_equivalent(None, "https://arxiv.org/abs/2006.09661") is False
    assert u.url_equivalent("https://arxiv.org/abs/2006.09661", None) is False
    assert u.url_equivalent(None, None) is False
