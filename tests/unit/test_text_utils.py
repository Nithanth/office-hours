"""Unit tests for infra.exa_utils.text_utils."""

from infra.exa_utils import text_utils as tu


def test_clean_text_dedent_and_collapse() -> None:
    raw = """
        Line 1


            Line 2 indented

    """
    cleaned = tu.clean_text(raw)
    assert "\n\n" in cleaned  # collapsed excess blank lines to max 1
    assert cleaned.startswith("Line 1")
    # Leading indentation common to all lines is removed but internal logical
    # indent (for nested paragraphs) is preserved.
    assert cleaned.count("    ") <= 4
