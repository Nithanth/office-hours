"""Unit tests for NotesGenerator internal _render_txt helper."""

from __future__ import annotations

from services.notes.notes_generator import _render_txt


def test_render_txt_bullets_and_headings() -> None:
    md = """\
# Title
* Item one
  * Subitem a
1. First
2. Second
"""

    out = _render_txt(md)
    # Headings uppercase
    assert "TITLE" in out
    # Bullet char used
    assert "• Item one" in out
    # Indented sub-bullet with ◦
    assert "◦ Subitem a" in out
    # Numbered list preserved
    assert "1." in out and "2." in out
