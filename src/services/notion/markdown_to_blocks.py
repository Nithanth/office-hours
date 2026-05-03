"""Convert a Markdown string into Notion block JSON.

Deliberately minimal: handles the subset of Markdown that report/blog output
actually produces — headings (#/##/###), paragraphs, bullet & numbered lists,
fenced code blocks, blockquotes, and inline **bold**, *italic*, `code`, and
[text](url) links. Anything richer is emitted as plain paragraph text.
"""

from __future__ import annotations

import re
from typing import Any

# Notion hard limits.
_MAX_RICH_TEXT_CHARS = 2000
_MAX_BLOCKS_PER_REQUEST = 100

_INLINE_PATTERN = re.compile(
    r"(\*\*(?P<bold>[^*]+)\*\*"
    r"|\*(?P<italic>[^*]+)\*"
    r"|`(?P<code>[^`]+)`"
    r"|\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)]+)\))"
)


def _chunk_text(text: str, limit: int = _MAX_RICH_TEXT_CHARS) -> list[str]:
    """Split *text* into chunks within Notion's rich_text length limit."""
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _rich_text_segment(
    content: str,
    *,
    bold: bool = False,
    italic: bool = False,
    code: bool = False,
    link: str | None = None,
) -> list[dict[str, Any]]:
    """Build one or more rich_text entries for *content* (chunked if long)."""
    segments: list[dict[str, Any]] = []
    for chunk in _chunk_text(content):
        text_obj: dict[str, Any] = {"content": chunk}
        if link:
            text_obj["link"] = {"url": link}
        segments.append(
            {
                "type": "text",
                "text": text_obj,
                "annotations": {
                    "bold": bold,
                    "italic": italic,
                    "code": code,
                    "strikethrough": False,
                    "underline": False,
                    "color": "default",
                },
            }
        )
    return segments


def _parse_inline(text: str) -> list[dict[str, Any]]:
    """Parse inline Markdown into a list of Notion rich_text objects."""
    if not text:
        return []

    out: list[dict[str, Any]] = []
    cursor = 0
    for match in _INLINE_PATTERN.finditer(text):
        if match.start() > cursor:
            out.extend(_rich_text_segment(text[cursor : match.start()]))
        groups = match.groupdict()
        if groups["bold"]:
            out.extend(_rich_text_segment(groups["bold"], bold=True))
        elif groups["italic"]:
            out.extend(_rich_text_segment(groups["italic"], italic=True))
        elif groups["code"]:
            out.extend(_rich_text_segment(groups["code"], code=True))
        elif groups["link_text"]:
            out.extend(_rich_text_segment(groups["link_text"], link=groups["link_url"]))
        cursor = match.end()
    if cursor < len(text):
        out.extend(_rich_text_segment(text[cursor:]))
    return out


def _block(block_type: str, rich_text: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"rich_text": rich_text, **extra}
    return {"object": "block", "type": block_type, block_type: body}


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """Convert *markdown* to a list of Notion block dicts."""
    lines = markdown.splitlines()
    blocks: list[dict[str, Any]] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Fenced code block
        if stripped.startswith("```"):
            lang = stripped[3:].strip() or "plain text"
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # consume closing fence
            code_text = "\n".join(code_lines)
            blocks.append(
                _block(
                    "code",
                    _rich_text_segment(code_text),
                    language=lang.lower() if lang.lower() in _NOTION_CODE_LANGS else "plain text",
                )
            )
            continue

        # Headings
        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            blocks.append(_block(f"heading_{level}", _parse_inline(heading_match.group(2).strip())))
            i += 1
            continue

        # Blockquote
        if stripped.startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(_block("quote", _parse_inline(" ".join(quote_lines))))
            continue

        # Bulleted list
        if re.match(r"^[-*+]\s+", stripped):
            item_text = re.sub(r"^[-*+]\s+", "", stripped)
            blocks.append(_block("bulleted_list_item", _parse_inline(item_text)))
            i += 1
            continue

        # Numbered list
        if re.match(r"^\d+\.\s+", stripped):
            item_text = re.sub(r"^\d+\.\s+", "", stripped)
            blocks.append(_block("numbered_list_item", _parse_inline(item_text)))
            i += 1
            continue

        # Paragraph — accumulate consecutive non-empty lines
        para_lines = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not _is_block_boundary(lines[i]):
            para_lines.append(lines[i].strip())
            i += 1
        blocks.append(_block("paragraph", _parse_inline(" ".join(para_lines))))

    return blocks


def _is_block_boundary(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped.startswith("#")
        or stripped.startswith("```")
        or stripped.startswith(">")
        or bool(re.match(r"^[-*+]\s+", stripped))
        or bool(re.match(r"^\d+\.\s+", stripped))
    )


def chunk_blocks(
    blocks: list[dict[str, Any]], size: int = _MAX_BLOCKS_PER_REQUEST
) -> list[list[dict[str, Any]]]:
    """Split blocks into Notion-API-sized batches."""
    return [blocks[i : i + size] for i in range(0, len(blocks), size)]


# Notion code block language whitelist (subset that covers most outputs).
_NOTION_CODE_LANGS: set[str] = {
    "bash",
    "c",
    "c#",
    "c++",
    "clojure",
    "coffeescript",
    "css",
    "dart",
    "diff",
    "docker",
    "elixir",
    "elm",
    "erlang",
    "f#",
    "flow",
    "fortran",
    "go",
    "graphql",
    "groovy",
    "haskell",
    "html",
    "java",
    "javascript",
    "json",
    "julia",
    "kotlin",
    "latex",
    "less",
    "lisp",
    "livescript",
    "lua",
    "makefile",
    "markdown",
    "matlab",
    "mermaid",
    "nix",
    "objective-c",
    "ocaml",
    "pascal",
    "perl",
    "php",
    "plain text",
    "powershell",
    "prolog",
    "protobuf",
    "python",
    "r",
    "reason",
    "ruby",
    "rust",
    "sass",
    "scala",
    "scheme",
    "scss",
    "shell",
    "sql",
    "swift",
    "toml",
    "typescript",
    "vb.net",
    "verilog",
    "vhdl",
    "visual basic",
    "webassembly",
    "xml",
    "yaml",
}
