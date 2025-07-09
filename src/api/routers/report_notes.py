"""Generate study-notes from a finished report and recent chat.

This endpoint receives the cleaned report text (sent by the browser), gathers
the per-session chat history, instructs the LLM to create concise non-redundant
bullet-style notes, and returns the result **as an attachment** (plain-text
`.txt` or `.docx`).  No SSE or on-page rendering – the browser simply downloads the file.
"""

from __future__ import annotations

import io
import re
from typing import Any, Literal, cast

from fastapi import APIRouter, Form, HTTPException, Request, Response

from chat.state import ChatState, get_chat_state
from config import config
from infra.llm_service import LLMService
from main import get_llm_service, get_session_id

try:
    from docx import Document as _DocxDocument
except ImportError:  # pragma: no cover
    _DocxDocument = None  # type: ignore[assignment]

# Public alias used throughout this module. Typed as Any so static checks don’t
# complain when python-docx is absent during type-checking.
Document = cast(Any, _DocxDocument)

# Runtime-safe optional import of python-docx classes --------------------------------
try:
    from docx.shared import Cm as _DocxCm
except Exception:  # pragma: no cover
    _DocxCm = None  # type: ignore[misc,assignment]

# Same pattern for Cm (length helper for indentation).
Cm = cast(Any, _DocxCm)

router = APIRouter(tags=["report-notes"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gather_chat_digest(cs: ChatState, char_cap: int = 5000) -> str:
    """Return a plain-text digest of the recent chat messages (all papers).

    The digest is naïvely truncated to *char_cap* to avoid huge prompts.
    """

    parts: list[str] = []
    for pid, turns in cs._histories.items():  # pylint: disable=protected-access
        if not turns:
            continue
        parts.append(f"### Discussion about {pid}")
        for t in turns:
            role = "User" if t.role == "user" else "Assistant"
            parts.append(f"• {role}: {t.content}")

    digest = "\n".join(parts)
    return digest[:char_cap]


# ---------------------------------------------------------------------------
# POST /generate-report-notes – returns downloadable .txt or .docx
# ---------------------------------------------------------------------------


@router.post("/generate-report-notes")
async def generate_report_notes(
    request: Request,
    report_text: str = Form(...),  # cleaned plain-text report provided by JS
    output_format: Literal["txt", "docx"] = Form("txt"),
) -> Response:
    if not report_text.strip():
        raise HTTPException(status_code=400, detail="Report text required")

    session_id = await get_session_id(request)
    chat_state = get_chat_state(session_id)

    chat_digest = _gather_chat_digest(chat_state)

    llm_service: LLMService | None = get_llm_service()
    if llm_service is None:
        raise HTTPException(status_code=503, detail="LLM service unavailable")

    model_key = config.DEFAULT_MODEL_KEY
    model_cfg = config.AVAILABLE_MODELS[model_key]

    provider = model_cfg["provider"]
    model_id = model_cfg["id"]

    system_prompt = (
        "You are Lux Scholar Note-Taker. Produce comprehensive study notes as plain text "
        "using bullet (•) and numbered lists where sequential steps are present. Preserve the "
        "high-level structure: 1) Topic Overview (if present) 2) Per-paper notes. Do NOT "
        "repeat content; integrate chat insights under each paper where they add new info."
    )

    user_prompt = (
        "REPORT TEXT:\n"
        + report_text.strip()[: config.PAPER_MAX_CONTENT_SNIPPET_CHARS]
        + "\n\nCHAT TRANSCRIPT (may be empty):\n"
        + chat_digest
    )

    notes_text = await llm_service.generate_completion(
        provider_name=provider,
        model_name=model_id,
        prompt=user_prompt,
        max_tokens=2048,
        system_prompt=system_prompt,
    )

    if output_format == "docx":
        if Document is None:
            raise HTTPException(
                status_code=500, detail="python-docx library not installed on server"
            )

        doc_bytes = _render_docx(notes_text)
        headers = {"Content-Disposition": 'attachment; filename="report_notes.docx"'}
        return Response(
            doc_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers=headers,
        )

    # Default plain text – prettify with hierarchical bullets / headings
    plain = _render_txt(notes_text)
    headers = {"Content-Disposition": 'attachment; filename="report_notes.txt"'}
    return Response(plain, media_type="text/plain; charset=utf-8", headers=headers)


# ---------------------------------------------------------------------------
# Helpers – simple Markdown-ish → DOCX converter (bullets, numbered, headings)
# ---------------------------------------------------------------------------


def _render_docx(md_text: str) -> bytes:
    """Very lightweight conversion of bullet/numbered markdown notes to a .docx blob."""

    if Document is None:  # pragma: no cover
        raise RuntimeError("python-docx missing")

    doc = Document()

    # Patterns that capture leading spaces so we can infer nesting level
    bullet_pat = re.compile(r"^(?P<indent>\s*)(?P<sym>[•\-*])\s+(?P<txt>.+)")
    num_pat = re.compile(r"^(?P<indent>\s*)(?P<num>\d+[.)])\s+(?P<txt>.+)")

    def _add_list_paragraph(text: str, level: int, numbered: bool = False) -> None:
        """Insert a paragraph using Word's built-in multi-level list styles."""

        if numbered:
            style = "List Number" if level == 0 else f"List Number {level + 1}"
        else:
            style = "List Bullet" if level == 0 else f"List Bullet {level + 1}"

        p = doc.add_paragraph(text, style=style)

        # Extra left-indent for nicer spacing (Word sometimes ignores style indent
        # on custom templates). 0.63 cm ≈ 0.25 inch.
        if level and Cm is not None:
            p.paragraph_format.left_indent = Cm(0.63 * level)

    for raw in md_text.splitlines():
        if not raw.strip():
            doc.add_paragraph()
            continue

        # Markdown headings ###, ##, # → Word Heading 3/2/1
        if raw.lstrip().startswith("#"):
            stripped = raw.lstrip()
            hashes, rest = stripped.split(maxsplit=1) if " " in stripped else (stripped, "")
            heading_level = min(len(hashes), 3)  # cap depth at 3 for doc clarity
            doc.add_heading(rest or stripped.lstrip("# "), level=heading_level)
            continue

        m_bullet = bullet_pat.match(raw)
        m_num = num_pat.match(raw)

        if m_bullet or m_num:
            groups = cast(re.Match[str], m_bullet or m_num)
            indent_spaces = len(groups["indent"])
            level = max(indent_spaces // 2, 0)  # 2 spaces ≈ 1 level
            content = groups["txt"]

            # Strip **bold**/_italic_ markers (Word cannot parse markdown)
            txt_clean = re.sub(r"[*_]{1,2}(.*?)?[*_]{1,2}", r"\1", content)
            _add_list_paragraph(txt_clean, level, numbered=bool(m_num))
            continue

        # Non-list line – treat as paragraph or heading
        line = raw.strip()

        # Simple heuristic for headings without #
        if line.isupper() or (line.istitle() and len(line.split()) < 8):
            doc.add_heading(line, level=2)
        else:
            doc.add_paragraph(line)

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


# ---------------------------------------------------------------------------
# Helpers – format plain-text notes with heading + bullet hierarchy
# ---------------------------------------------------------------------------


_PLAIN_BULLETS = ["•", "◦", "▪"]


def _render_txt(md_text: str) -> str:  # noqa: D401 – simple helper
    """Return plain text with nicer hierarchy (bullets & headings).

    • Level-0 bullet → •
    • Level-1 bullet → ◦
    • Level-2+ bullet → ▪

    Ordered list numbers are preserved.  Leading heading hashes are stripped.
    """

    bullet_pat = re.compile(r"^(?P<indent>\s*)([•\-*])\s+(?P<txt>.+)")
    num_pat = re.compile(r"^(?P<indent>\s*)(\d+[.)])\s+(?P<txt>.+)")

    out_lines: list[str] = []

    for raw in md_text.splitlines():
        if not raw.strip():
            out_lines.append("")
            continue

        # Headings: treat #,##,### as plain uppercase line for visibility
        if raw.lstrip().startswith("#"):
            stripped = raw.lstrip()
            hashes, rest = stripped.split(maxsplit=1) if " " in stripped else (stripped, "")
            out_lines.append(rest.upper() if rest else stripped.lstrip("# ").upper())
            continue

        m_b = bullet_pat.match(raw)
        m_n = num_pat.match(raw)

        if m_b or m_n:
            m = cast(re.Match[str], m_b or m_n)
            indent_spaces = len(m["indent"])
            level = min(indent_spaces // 2, 2)  # cap bullet symbol list
            prefix = _PLAIN_BULLETS[level] if m_b else m.group(2)
            txt_clean = re.sub(r"[*_]{1,2}(.*?)?[*_]{1,2}", r"\1", m["txt"])
            out_lines.append(f"{'  ' * level}{prefix} {txt_clean}")
            continue

        # Fallback – regular line with markdown emphasis stripped
        clean = re.sub(r"[*_]{1,2}(.*?)?[*_]{1,2}", r"\1", raw.strip())
        out_lines.append(clean)

    return "\n".join(out_lines)
