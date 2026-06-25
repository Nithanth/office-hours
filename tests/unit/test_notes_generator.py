"""Unit tests for notes generation (json_to_md, render formats)."""

from __future__ import annotations

import pytest

from services.notes.notes_generator import _json_to_md, _render_txt


class TestJsonToMd:
    """Tests for _json_to_md conversion."""

    def test_valid_notes_doc_dict(self):
        """Well-formed NotesDoc dict converts to markdown."""
        doc = {
            "sections": [
                {
                    "heading": "Introduction to Transformers",
                    "bullets": [
                        {
                            "text": "Self-attention replaces recurrence",
                            "sub": [
                                {"text": "O(1) sequential operations", "sub": []},
                                {"text": "Enables parallelization", "sub": []},
                            ],
                        },
                        {
                            "text": "Multi-head attention allows diverse representations",
                            "sub": [],
                        },
                    ],
                },
                {
                    "heading": "Training Details",
                    "bullets": [
                        {"text": "Trained on WMT datasets", "sub": []},
                    ],
                },
            ]
        }
        result = _json_to_md(doc)
        assert "INTRODUCTION TO TRANSFORMERS" in result
        assert "Self-attention replaces recurrence" in result
        assert "O(1) sequential operations" in result
        assert "TRAINING DETAILS" in result

    def test_nested_bullets_indented(self):
        doc = {
            "sections": [
                {
                    "heading": "Methods",
                    "bullets": [
                        {
                            "text": "Top level",
                            "sub": [
                                {
                                    "text": "Level 1",
                                    "sub": [
                                        {"text": "Level 2", "sub": []},
                                    ],
                                },
                            ],
                        },
                    ],
                }
            ]
        }
        result = _json_to_md(doc)
        lines = result.split("\n")
        # Check indentation increases
        method_lines = [l for l in lines if "Level" in l or "Top level" in l]
        assert any("  " in l for l in method_lines)  # some indentation exists

    def test_empty_sections(self):
        doc = {"sections": []}
        result = _json_to_md(doc)
        assert result == ""

    def test_invalid_input_returns_string(self):
        """Non-dict input should not crash."""
        result = _json_to_md("just a string")
        assert isinstance(result, str)


class TestRenderTxt:
    """Tests for _render_txt markdown-to-plain conversion."""

    def test_headings_uppercased(self):
        md = "# Introduction\n## Methods\nSome text"
        result = _render_txt(md)
        assert "INTRODUCTION" in result
        assert "METHODS" in result

    def test_bullets_preserved(self):
        md = "• First point\n  ◦ Sub point\n    ▪ Deep point"
        result = _render_txt(md)
        assert "First point" in result
        assert "Sub point" in result
        assert "Deep point" in result

    def test_bold_stripped(self):
        md = "• **Bold text** and *italic*"
        result = _render_txt(md)
        assert "**" not in result
        assert "*" not in result or "Bold text" in result

    def test_empty_input(self):
        result = _render_txt("")
        assert result == ""

    def test_numbered_lists(self):
        md = "1. First item\n2. Second item"
        result = _render_txt(md)
        assert "First item" in result
        assert "Second item" in result
