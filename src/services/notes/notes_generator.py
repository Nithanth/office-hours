"""Service-layer generation of study notes from a finished report + chat log."""

from __future__ import annotations

import io
import re
import json
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
# JSON → markdown bullet converter (handles NotesDoc schema)
# ---------------------------------------------------------------------------

def _json_to_md(parsed: NotesDoc | dict | list) -> str:
    """Convert a parsed NotesDoc (or compatible dict/list) to markdown bullets."""

    # Allow dict/list directly; wrap in NotesDoc if possible.
    if not isinstance(parsed, NotesDoc):
        try:
            parsed = NotesDoc.model_validate(parsed)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 – fallback trivial stringify
            return str(parsed)

    lines: list[str] = []

    def _walk_bullet(b, level: int) -> None:  # noqa: ANN001
        indent = "  " * level
        prefix = f"{_PLAIN_BULLETS[min(level, 2)]} "
        if b.ordered:
            prefix = "1. "
        lines.append(f"{indent}{prefix}{b.text.strip()}")
        if b.sub:
            for sub_b in b.sub:
                _walk_bullet(sub_b, level + 1)

    for sec in parsed.sections:
        lines.append(sec.heading.upper())
        for bullet in sec.bullets:
            _walk_bullet(bullet, 0)
        lines.append("")  # blank line between sections

    return "\n".join(lines).strip()


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
        paper_ids: list[str] | None = None,
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
            paper_ids=paper_ids,
        )

        # ----------------------------------------------------------
        # Build the final user prompt given to the LLM
        # ----------------------------------------------------------
        # We prepend explicit instructions so the model never defaults to JSON.
        instr_block = (
            "You are preparing STUDY NOTES for the user. "
            "Follow the OUTPUT FORMAT rules given in the system prompt. "
            "Summarise each major section of the REPORT and then each PAPER discussed, "
            "weaving in *non-redundant* CHAT insights."
        )

        user_prompt = (
            instr_block
            + "\n\nREPORT TEXT:\n"  # keep exact marker for easy future parsing
            + report_text.strip()[: config.PAPER_MAX_CONTENT_SNIPPET_CHARS]
            + "\n\nCHAT TRANSCRIPT (may be empty):\n"
            + (chat_digest_clean or "(no chat messages)")
        )

        # ------------------------------------------------------------------
        # Optional: inject top-k evidence chunks so the LLM can add missing
        # key takeaways if the report overlooked something.
        # ------------------------------------------------------------------

        if paper_ids:
            rag_blocks: list[str] = []
            from services.paper.vector_store import index_exists, search as vs_search
            from services.paper.embedding import embed_query as _embed_q

            q_vec = _embed_q("What are the key takeaways of this paper?")

            for _pid in paper_ids:
                try:
                    if index_exists(_pid):
                        matches = vs_search(_pid, q_vec, top_k=config.RAG_TOP_K)
                        if matches:
                            rag_blocks.append(
                                f"## Paper {_pid}\n" + "\n\n".join(m["text"] for m in matches)
                            )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("notes_rag_failed", paper_id=_pid, error=str(exc))

            if rag_blocks:
                user_prompt += "\n\nTOP-K RAG CHUNKS (by paper):\n" + "\n\n".join(rag_blocks)
                logger.debug("notes_rag_added", papers=len(rag_blocks))

        # --------------------------------------------------
        # LLM call and structured-output handling
        # --------------------------------------------------

        notes_raw = await llm_service.generate_completion(
            provider_name=provider,
            model_name=model_id,
            prompt=user_prompt,
            max_tokens=config.NOTES_MAX_TOKENS,
            system_prompt=NotesGenerator._SYSTEM_PROMPT,
        )

        # Try to parse JSON first; fall back to raw text.
        try:
            parsed_obj = json.loads(notes_raw)
            notes_doc = NotesDoc.model_validate(parsed_obj)
            logger.debug("notes_json_validated", sections=len(notes_doc.sections))
            notes_text = _json_to_md(notes_doc)
        except Exception:
            notes_text = str(notes_raw)

        if output_format == "docx":
            doc_bytes = _render_docx(notes_text)
            return (
                doc_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "report_notes.docx",
            )

        plain = _render_txt(notes_text)
        return plain, "text/plain; charset=utf-8", "report_notes.txt"
