"""Service-layer generation of study notes from a finished report + chat log."""

from __future__ import annotations

import io
import re
from typing import Any, Literal, cast

from loguru import logger

from config import config
from prompts.prompt_builder import _load_template as _load_prompt
from services.core.llm_service import LLMService
from utils.dedupe import deduplicate_chat

# Optional dependency on python-docx ------------------------------------------------
DocxDocument: Any  # runtime alias for python-docx Document class
DocxCm: Any  # runtime alias for python-docx length helper

try:
    from docx import Document as _DocxDocument
    from docx.shared import Cm as _DocxCm

    DocxDocument = _DocxDocument
    DocxCm = _DocxCm
except ImportError:  # pragma: no cover – server may run without docx export
    DocxDocument = None
    DocxCm = None

logger = logger.bind(component="NotesGenerator")

# ---------------------------------------------------------------------------
# Markdown → plain-text prettifier (bullet hierarchy, headings)
# ---------------------------------------------------------------------------

_PLAIN_BULLETS = ["•", "◦", "▪"]


def _render_txt(md_text: str) -> str:
    """Convert simple markdown-ish bullets/headings to hierarchical plain text."""

    bullet_pat = re.compile(r"^(?P<indent>\s*)([•\-*])\s+(?P<txt>.+)")
    num_pat = re.compile(r"^(?P<indent>\s*)(\d+[.)])\s+(?P<txt>.+)")

    out_lines: list[str] = []
    for raw in md_text.splitlines():
        if not raw.strip():
            out_lines.append("")
            continue

        if raw.lstrip().startswith("#"):
            stripped = raw.lstrip()
            # Strip leading # symbols and uppercase for visibility
            rest = stripped.lstrip("# ")
            out_lines.append(rest.upper())
            continue

        m_b = bullet_pat.match(raw)
        m_n = num_pat.match(raw)
        if m_b or m_n:
            m = cast(re.Match[str], m_b or m_n)
            indent_spaces = len(m["indent"])
            level = min(indent_spaces // 2, 2)
            prefix = _PLAIN_BULLETS[level] if m_b else m.group(2)
            txt_clean = re.sub(r"[*_]{1,2}(.*?)?[*_]{1,2}", r"\1", m["txt"])
            out_lines.append(f"{'  ' * level}{prefix} {txt_clean}")
            continue

        clean = re.sub(r"[*_]{1,2}(.*?)?[*_]{1,2}", r"\1", raw.strip())
        out_lines.append(clean)

    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Markdown → DOCX converter (optional)
# ---------------------------------------------------------------------------


def _render_docx(md_text: str) -> bytes:
    """Return a minimal Word document with list indentation preserved."""

    if DocxDocument is None:  # pragma: no cover – dependency missing
        raise RuntimeError("python-docx library not installed on server")

    doc = DocxDocument()

    bullet_pat = re.compile(r"^(?P<indent>\s*)([•\-*])\s+(?P<txt>.+)")
    num_pat = re.compile(r"^(?P<indent>\s*)(\d+[.)])\s+(?P<txt>.+)")

    def _add_list_paragraph(text: str, level: int, numbered: bool = False) -> None:
        style = ("List Number" if numbered else "List Bullet") + (f" {level + 1}" if level else "")
        p = doc.add_paragraph(text, style=style)
        if level and DocxCm is not None:
            p.paragraph_format.left_indent = DocxCm(0.63 * level)

    for raw in md_text.splitlines():
        if not raw.strip():
            doc.add_paragraph()
            continue

        # Simple markdown headings
        if raw.lstrip().startswith("#"):
            stripped = raw.lstrip()
            hashes, rest = stripped.split(maxsplit=1) if " " in stripped else (stripped, "")
            heading_level = min(len(hashes), 3)
            doc.add_heading(rest or stripped.lstrip("# "), level=heading_level)
            continue

        m_b = bullet_pat.match(raw)
        m_n = num_pat.match(raw)
        if m_b or m_n:
            m = cast(re.Match[str], m_b or m_n)
            indent_spaces = len(m["indent"])
            level = max(indent_spaces // 2, 0)
            content = m["txt"]
            txt_clean = re.sub(r"[*_]{1,2}(.*?)?[*_]{1,2}", r"\1", content)
            _add_list_paragraph(txt_clean, level, numbered=bool(m_n))
            continue

        line = raw.strip()
        if line.isupper() or (line.istitle() and len(line.split()) < 8):
            doc.add_heading(line, level=2)
        else:
            doc.add_paragraph(line)

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class NotesGenerator:  # pylint: disable=too-few-public-methods
    """Generate study notes and return bytes / text + HTTP meta."""

    # Loaded from ``prompts/templates/notes/system.md`` so wording can be tuned
    # without touching application code.
    _SYSTEM_PROMPT: str = _load_prompt("notes/system.md")

    @staticmethod
    async def generate(
        *,
        llm_service: LLMService,
        report_text: str,
        chat_digest: str = "",
        provider: str,
        model_id: str,
        output_format: Literal["txt", "docx"] = "txt",
    ) -> tuple[bytes | str, str, str]:
        """Return *(content, mime_type, filename)* for the generated notes."""

        # Remove chat sentences that overlap strongly with the report to save tokens.
        chat_digest_clean = deduplicate_chat(chat_digest, report_text) if chat_digest else ""

        logger.debug(
            "generating_notes",
            provider=provider,
            model=model_id,
            fmt=output_format,
            report_chars=len(report_text),
            chat_chars=len(chat_digest_clean),
        )

        user_prompt = (
            "REPORT TEXT:\n"
            + report_text.strip()[: config.PAPER_MAX_CONTENT_SNIPPET_CHARS]
            + "\n\nCHAT TRANSCRIPT (may be empty):\n"
            + chat_digest_clean
        )

        notes_text = await llm_service.generate_completion(
            provider_name=provider,
            model_name=model_id,
            prompt=user_prompt,
            max_tokens=2048,
            system_prompt=NotesGenerator._SYSTEM_PROMPT,
        )

        if output_format == "docx":
            doc_bytes = _render_docx(notes_text)
            return (
                doc_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "report_notes.docx",
            )

        plain = _render_txt(notes_text)
        return plain, "text/plain; charset=utf-8", "report_notes.txt"
