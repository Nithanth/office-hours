"""Study-notes, blog-post, and Notion export generators.

POST `/generate-report-notes` – downloadable notes (`.txt`, `.md`, or `.docx`).
POST `/generate-blog` – queues a blog generation, returns a view URL immediately.
GET  `/blog-status` – polled by the blog page to fetch markdown when ready.
GET  `/blog-view` – standalone rendered blog page.
POST `/export-notion` – push report to a Notion page/database and return its URL.
"""

import asyncio
import html as _html
import re as _re
import time as _time
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from api.deps import get_llm_service, get_session_id
from config import config
from prompts.prompt_builder import _load_template
from services.chat.chat_state import ChatState, get_chat_state
from services.core import model_prefs
from services.core.llm_service import LLMService
from services.notes.notes_generator import NotesGenerator
from utils.dedupe import deduplicate_chat
from utils.rate_limit import limiter

router = APIRouter(tags=["report-notes"])


def _gather_chat_digest(cs: ChatState, char_cap: int = 5000) -> str:
    """Return a plain-text digest of the recent chat messages (all papers).

    The digest is truncated to *char_cap* to keep prompt size reasonable.
    """

    parts: list[str] = []
    for pid, turns in cs._histories.items():
        if not turns:
            continue
        parts.append(f"### Discussion about {pid}")
        for t in turns:
            role = "User" if t.role == "user" else "Assistant"
            parts.append(f"• {role}: {t.content}")

    digest = "\n".join(parts)
    return digest[:char_cap]


# paper_id optional – helps fetch RAG snippets for extra key takeaways
@limiter.limit("20/5minute")
@router.post("/generate-report-notes")
async def generate_report_notes(
    request: Request,
    report_text: str = Form(...),
    output_format: Literal["txt", "md", "docx"] = Form("txt"),
    paper_id: str | None = Form(None),  # deprecated, kept for backward compat
) -> Response:
    """Generate study notes from *report_text* and recent chat history.

    The notes are returned as a downloadable `.txt` or `.docx` attachment.
    """

    logger.info("report_notes: request received", output_format=output_format)

    if not report_text.strip():
        logger.warning("report_notes: empty report text provided")
        raise HTTPException(status_code=400, detail="Report text required")

    session_id = await get_session_id(request)
    chat_state = get_chat_state(session_id)

    chat_digest = _gather_chat_digest(chat_state)

    # Build list of paper IDs to include RAG evidence.
    paper_ids: list[str] | None

    if paper_id:
        paper_ids = [paper_id]
    else:
        paper_ids = (
            list(chat_state._histories.keys()) if chat_state._histories else None
        )

    llm_service: LLMService | None = get_llm_service()
    if llm_service is None:
        logger.error("LLM service unavailable – cannot generate notes")
        raise HTTPException(status_code=503, detail="LLM service unavailable")

    model_key = config.DEFAULT_MODEL_KEY
    model_cfg = config.AVAILABLE_MODELS[model_key]

    provider = model_cfg["provider"]
    model_id = model_prefs.resolve_model_id(model_key)

    try:
        content, media_type, filename = await NotesGenerator.generate(
            llm_service=llm_service,
            report_text=report_text,
            chat_digest=chat_digest,
            provider=provider,
            model_id=model_id,
            output_format=output_format,
            paper_ids=paper_ids,
        )
    except RuntimeError as err:
        logger.error("notes_generation_error", error=str(err))
        raise HTTPException(status_code=500, detail=str(err)) from err

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Blog post generator
# ---------------------------------------------------------------------------

_BLOG_SYSTEM_PROMPT: str = _load_template("blog/system.md")
_NOTION_OUTLINE_PROMPT: str = _load_template("notion/outline.md")


# ---------------------------------------------------------------------------
# User-tunable blog options (audience / length / tone / free-text extras)
# ---------------------------------------------------------------------------
# These knobs live alongside the static system prompt and are appended as a
# trailing "User overrides" section so the base prompt remains the source of
# truth and individual values can be added / changed without touching it.

_BLOG_AUDIENCE_OPTIONS: dict[str, str] = {
    "technical": (
        "Write for a **technical** reader — assume familiarity with calculus, "
        "linear algebra, and the relevant subfield's vocabulary. You may use "
        "domain-specific terms without defining the basics, but still ground "
        "abstract concepts in concrete examples."
    ),
    "mixed": (
        "Write for a **mixed** audience — technically curious readers from "
        "adjacent fields. Define jargon the first time you use it, but don't "
        "talk down. Aim for the level of a well-written *Quanta* article."
    ),
    "layperson": (
        "Write for a **layperson** — assume no math beyond high school and no "
        "domain expertise. Lean heavily on analogies and concrete scenarios. "
        "Replace formulas with prose. Skip jargon, or define it inline."
    ),
}

_BLOG_LENGTH_OPTIONS: dict[str, str] = {
    "short": (
        "Target a **short** post: roughly 500–800 words. Aim for one tight "
        "narrative thread. Use 2–3 H2 sections at most. One diagram if it helps."
    ),
    "medium": (
        "Target a **medium-length** post: roughly 1200–1800 words. 3–5 H2 "
        "sections. 1–2 diagrams where they materially help."
    ),
    "long": (
        "Target a **long** post: roughly 2500–3500 words. 5–7 H2 sections, "
        "each with substance. Use 2–3 diagrams. Include more detail per claim."
    ),
}

_BLOG_TONE_OPTIONS: dict[str, str] = {
    "distill": (
        "**Distill.pub** style: a precise, careful, slightly playful technical "
        "essay. Confident but never glib. Inline citations in prose. Use the "
        "second person sparingly."
    ),
    "casual": (
        "**Casual blog** style: conversational, first-person allowed, short "
        "paragraphs, an occasional aside. Think a smart friend explaining the "
        "field over coffee. Still rigorous; just less starched."
    ),
    "lecture": (
        "**Lecture-notes** style: structured and didactic. Use enumerated "
        "claims, brief definitions, and worked examples. Light on rhetoric; "
        'heavy on signposting ("first…", "now consider…", "note that…").'
    ),
}

_DEFAULT_BLOG_OPTS = {
    "audience": "technical",
    "length": "medium",
    "tone": "distill",
}

# Hard cap on the user-supplied free-text instructions, to avoid prompt-budget
# blowups and discourage adversarial inputs.
_BLOG_EXTRA_MAX_CHARS = 500


def _build_blog_system_prompt(
    *,
    audience: str | None,
    length: str | None,
    tone: str | None,
    extra: str | None,
) -> str:
    """Render the blog system prompt with user overrides appended.

    Each knob is keyed to a paragraph; unknown values fall back to the default.
    The base prompt is unchanged — overrides are simply appended in a marked
    "User customizations" section so they cannot accidentally weaken the
    structural rules at the top of the template.
    """

    aud_key = (audience or _DEFAULT_BLOG_OPTS["audience"]).strip().lower()
    len_key = (length or _DEFAULT_BLOG_OPTS["length"]).strip().lower()
    tone_key = (tone or _DEFAULT_BLOG_OPTS["tone"]).strip().lower()

    aud_text = _BLOG_AUDIENCE_OPTIONS.get(
        aud_key, _BLOG_AUDIENCE_OPTIONS[_DEFAULT_BLOG_OPTS["audience"]]
    )
    len_text = _BLOG_LENGTH_OPTIONS.get(
        len_key, _BLOG_LENGTH_OPTIONS[_DEFAULT_BLOG_OPTS["length"]]
    )
    tone_text = _BLOG_TONE_OPTIONS.get(
        tone_key, _BLOG_TONE_OPTIONS[_DEFAULT_BLOG_OPTS["tone"]]
    )

    extra_clean = (extra or "").strip()
    if len(extra_clean) > _BLOG_EXTRA_MAX_CHARS:
        extra_clean = extra_clean[:_BLOG_EXTRA_MAX_CHARS].rstrip()

    diagram_directive = _BLOG_DIAGRAM_BUDGETS.get(
        len_key, _BLOG_DIAGRAM_BUDGETS[_DEFAULT_BLOG_OPTS["length"]]
    )

    sections = [
        _BLOG_SYSTEM_PROMPT.rstrip(),
        "",
        "## User customizations (these override defaults above)",
        "",
        "**Audience.** " + aud_text,
        "",
        "**Length.** " + len_text,
        "",
        "**Tone.** " + tone_text,
        "",
        "**Diagram requirement (non-negotiable).** " + diagram_directive,
    ]
    if extra_clean:
        sections.extend(
            [
                "",
                "**Additional instructions from the user (treat as guidance, not as overriding the structural rules):**",
                "",
                "> " + extra_clean.replace("\n", "\n> "),
            ]
        )
    return "\n".join(sections) + "\n"


# Per-length diagram requirement injected into the prompt. The base template
# already explains the diagram budget; this duplicates it at the bottom of the
# prompt so it's the *last* thing the model reads before generating, which we
# found empirically encourages compliance better than just the upstream rule.
_BLOG_DIAGRAM_BUDGETS: dict[str, str] = {
    "short": (
        "This is a SHORT post — emit at least one diagram (Mermaid for a "
        "concept map / pipeline, OR Vega-Lite for a parameter-tunable plot). "
        "Pick whichever genuinely helps; do not skip diagrams entirely."
    ),
    "medium": (
        "This is a MEDIUM post — emit at least 1 and ideally 2 diagrams. "
        "Mix Mermaid (concept maps / methodology / comparison) with Vega-Lite "
        "(parameter-sensitive quantities). At least one diagram is required."
    ),
    "long": (
        "This is a LONG post — emit at least 2 and ideally 3 diagrams. "
        "Long posts have room to develop ideas visually: use Mermaid for "
        "structural relationships and Vega-Lite for any quantity where a "
        "slider or dropdown helps the reader build intuition. Skipping "
        "diagrams in a long post wastes the format."
    ),
}


# Default ~1 hour TTL on cached blog entries; tunable via env (config.BLOG_TTL_SECONDS).
# Cap entries to prevent unbounded growth even before TTL kicks in (config.BLOG_CACHE_MAX_ENTRIES).


@dataclass
class _BlogEntry:
    """Cached state for an in-flight or completed blog generation."""

    state: Literal["pending", "ready", "error"] = "pending"
    markdown: str = ""
    error: str = ""
    created_at: float = field(default_factory=_time.time)
    # The inputs that produced this blog. Stored so /regenerate-blog can
    # recover the source material + previous customization without requiring
    # the client to round-trip them. Truncated to the same snippet length the
    # original generation used so cache memory stays bounded.
    report_text: str = ""
    audience: str = ""
    length: str = ""
    tone: str = ""
    theme: str = ""
    extra: str = ""
    # When set, the finished markdown is persisted on the report row
    # (reports.blog_markdown) so the blog survives cache eviction + restarts.
    report_id: str = ""
    user_id: str = ""

    @property
    def expires_at(self) -> float:
        return self.created_at + int(config.BLOG_TTL_SECONDS)


# In-memory cache keyed by short-lived token.
_BLOG_CACHE: dict[str, _BlogEntry] = {}

# Latest in-flight token per report so /reports/{id}/blog can report
# "writing" state (and the pane can poll the same token).
_BLOG_BY_REPORT: dict[str, str] = {}

# Patterns that frequently leak from chat-tuned LLMs as preamble before the
# actual article. Stripped server-side as a defence-in-depth measure even
# though the prompt forbids them.
_BLOG_PREAMBLE_PATTERNS: tuple[_re.Pattern[str], ...] = (
    _re.compile(
        r"^\s*here\s+is\s+(?:a|the|an)\s+[^.\n]{0,80}[:\.!]?\s*$", _re.IGNORECASE
    ),
    _re.compile(r"^\s*below\s+is\s+[^.\n]{0,80}[:\.!]?\s*$", _re.IGNORECASE),
    _re.compile(
        r"^\s*sure[,!.]\s+(?:here|i'?d|i'?ll|let)[^.\n]{0,80}[:\.!]?\s*$",
        _re.IGNORECASE,
    ),
    _re.compile(
        r"^\s*i'?ve\s+(?:written|crafted|prepared|put\s+together)[^.\n]{0,120}[:\.!]?\s*$",
        _re.IGNORECASE,
    ),
    _re.compile(
        r"^\s*(?:certainly|absolutely|of\s+course)[,!.]?\s*[^.\n]{0,80}[:\.!]?\s*$",
        _re.IGNORECASE,
    ),
)

# Patterns that commonly trail the article as meta-commentary ("I hope this helps…").
_BLOG_POSTAMBLE_PATTERNS: tuple[_re.Pattern[str], ...] = (
    _re.compile(r"^\s*i\s+hope\s+(?:this|that)[^.\n]{0,200}[\.!]?\s*$", _re.IGNORECASE),
    _re.compile(r"^\s*let\s+me\s+know[^.\n]{0,200}[\.!?]?\s*$", _re.IGNORECASE),
    _re.compile(r"^\s*feel\s+free\s+to[^.\n]{0,200}[\.!?]?\s*$", _re.IGNORECASE),
    _re.compile(r"^\s*if\s+you'?d\s+like[^.\n]{0,200}[\.!?]?\s*$", _re.IGNORECASE),
)


def _clean_blog_markdown(md: str) -> str:
    """Strip chat-style preamble/postamble and unwrap accidental code fencing.

    Some LLMs ignore the "no preamble" instruction and start with sentences
    like "Here is a compelling blog post:" which then get rendered as the
    H1. We post-process the model output to:

      1. Drop a leading triple-backtick wrapper (```markdown ... ```) if the
         model wrapped the whole response in a code fence.
      2. Drop preamble lines until we find the first H1 (`# `) heading or a
         line that is clearly substantive content.
      3. Drop trailing meta-commentary lines after the article ends.
      4. Collapse excessive leading/trailing whitespace.
    """

    if not md:
        return md

    text = md.strip()

    # 1. Unwrap a leading-and-trailing code fence around the whole response.
    fence_match = _re.match(
        r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$",
        text,
        _re.DOTALL | _re.IGNORECASE,
    )
    if fence_match:
        text = fence_match.group(1).strip()

    lines = text.split("\n")

    # 2. Drop preamble. If the response contains an H1 anywhere in the first
    #    ~10 non-empty lines, drop everything before it. Otherwise, drop only
    #    leading lines that match a known preamble pattern.
    first_h1 = -1
    nonempty_seen = 0
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        nonempty_seen += 1
        if line.lstrip().startswith("# "):
            first_h1 = idx
            break
        if nonempty_seen >= 10:
            break

    if first_h1 > 0:
        lines = lines[first_h1:]
    else:
        # No H1 in window — strip any opening preamble-pattern lines.
        while lines and (
            not lines[0].strip()
            or any(pat.match(lines[0]) for pat in _BLOG_PREAMBLE_PATTERNS)
        ):
            lines.pop(0)

    # 3. Drop trailing meta-commentary lines that match postamble patterns.
    while lines:
        tail = lines[-1]
        if not tail.strip():
            lines.pop()
            continue
        if any(pat.match(tail) for pat in _BLOG_POSTAMBLE_PATTERNS):
            lines.pop()
            continue
        break

    # 4. Normalise the first H1: strip wrapping emphasis (**, __, *, _, ``)
    #    and escaped asterisks. The H1 is rendered as bold by the page
    #    already; the model habitually adds redundant bold, which then either
    #    double-bolds or leaves literal asterisks visible.
    h1_found = False
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("# "):
            prefix = line[: len(line) - len(stripped)] + "# "
            title = stripped[2:].strip()
            lines[idx] = prefix + _normalise_blog_title(title)
            h1_found = True
            break

    # 4b. If no H1 was emitted, look for a leading bare-emphasis line
    #     (e.g. `**Title**`) and promote it to an H1. Frontend `extractTitle`
    #     would otherwise pick up that line with the literal asterisks.
    if not h1_found:
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            promoted = _promote_bare_bold_title(line)
            if promoted is not None:
                lines[idx] = promoted
            break

    cleaned = "\n".join(lines).strip()
    return cleaned or md.strip()


# Matches a line that is *only* an emphasis-wrapped string with no leading `#`
# heading marker. Used to detect model output like `**My Title**\n\n…` so we
# can promote it to a proper H1 before downstream renderers see it.
_BARE_EMPHASIS_TITLE_RE = _re.compile(
    r"""^\s*                # optional leading whitespace
        (?:\\?\*{1,3}|_{1,3}|`)   # opening run of *, **, ***, _, __, ___ or `, optionally escaped
        \s*
        (?P<inner>.+?)             # the actual title text (non-greedy)
        \s*
        (?:\\?\*{1,3}|_{1,3}|`)   # closing run (we don't require it to match exactly)
        \s*$
    """,
    _re.VERBOSE,
)


def _promote_bare_bold_title(line: str) -> str | None:
    """If *line* is just an emphasis-wrapped title, return `# <clean title>`.

    Returns None if the line doesn't look like a bare emphasis title.
    """
    m = _BARE_EMPHASIS_TITLE_RE.match(line)
    if not m:
        return None
    inner = m.group("inner").strip()
    # Run through the full normaliser so nested escapes (`\*Title\*`) get
    # stripped too.
    cleaned = _normalise_blog_title(inner)
    if not cleaned:
        return None
    # Bail out on lines that look like body content masquerading as emphasis.
    # Heuristic: a real title is rarely more than ~120 chars and rarely ends
    # with a sentence-final period — body sentences in bold usually do.
    if len(cleaned) > 120:
        return None
    return "# " + cleaned


# Wrappers we strip from the start/end of the H1 line. Order matters — strip
# longer / escaped variants first.
_TITLE_WRAPPER_PAIRS: tuple[tuple[str, str], ...] = (
    (r"\*\*", r"\*\*"),
    (r"\*", r"\*"),
    ("**", "**"),
    ("__", "__"),
    ("*", "*"),
    ("_", "_"),
    ("`", "`"),
    ('"', '"'),
    ("'", "'"),
)


def _normalise_blog_title(title: str) -> str:
    """Strip wrapping emphasis / escapes from an H1 title, repeatedly."""
    s = title.strip()
    # Drop a leading "# " if the model produced "# # Real Title"
    while s.startswith("# "):
        s = s[2:].lstrip()
    # Iteratively peel off symmetric wrappers until none match.
    changed = True
    while changed and s:
        changed = False
        for left, right in _TITLE_WRAPPER_PAIRS:
            if (
                len(s) > len(left) + len(right)
                and s.startswith(left)
                and s.endswith(right)
            ):
                s = s[len(left) : -len(right)].strip()
                changed = True
                break
    # Collapse internal whitespace runs.
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def _purge_expired_blogs() -> None:
    """Drop entries past their TTL and trim oldest if cache is over capacity."""

    now = _time.time()
    for tok in list(_BLOG_CACHE.keys()):
        if _BLOG_CACHE[tok].expires_at < now:
            _BLOG_CACHE.pop(tok, None)

    cap = int(config.BLOG_CACHE_MAX_ENTRIES)
    if len(_BLOG_CACHE) > cap:
        # Drop oldest first.
        oldest = sorted(_BLOG_CACHE.items(), key=lambda kv: kv[1].created_at)
        for tok, _ in oldest[: len(_BLOG_CACHE) - cap]:
            _BLOG_CACHE.pop(tok, None)


async def _run_blog_generation(
    token: str,
    *,
    llm_service: LLMService,
    provider: str,
    model_id: str,
    report_text: str,
    chat_digest: str,
    system_prompt: str = _BLOG_SYSTEM_PROMPT,
    user_prompt: str | None = None,
) -> None:
    """Background task that runs the LLM and stores the result in `_BLOG_CACHE`.

    If `user_prompt` is provided, it is used verbatim (used by /regenerate-blog
    which constructs a revision-flavoured prompt). Otherwise the default
    "Transform the following research report..." prompt is built from
    `report_text` + `chat_digest`.

    Any exception is captured into the cache entry as `state="error"`; we
    intentionally swallow it here so the asyncio task does not bubble up an
    unhandled exception into the event loop.
    """

    try:
        snippet_chars = config.PAPER_MAX_CONTENT_SNIPPET_CHARS
        if user_prompt is None:
            user_prompt = (
                "Transform the following research report into a compelling blog post.\n\n"
                "REPORT:\n" + report_text.strip()[:snippet_chars]
            )
            if chat_digest:
                user_prompt += "\n\nADDITIONAL INSIGHTS FROM CHAT:\n" + chat_digest

        max_tokens = int(config.BLOG_MAX_TOKENS)

        blog_md_raw = await llm_service.generate_completion(
            provider_name=provider,
            model_name=model_id,
            prompt=user_prompt,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )
        blog_md = _clean_blog_markdown(blog_md_raw)

        entry = _BLOG_CACHE.get(token)
        if entry is None:
            # Entry expired or was evicted before generation finished; nothing to record.
            logger.warning("blog_generation_orphan", token=token, chars=len(blog_md))
            return
        entry.state = "ready"
        entry.markdown = blog_md
        if entry.report_id and entry.user_id:
            try:
                from services.persistence.database import (
                    database_available,
                    session_scope,
                )
                from services.persistence.repositories import set_report_blog

                if database_available():
                    async for session in session_scope():
                        await set_report_blog(
                            session,
                            user_id=entry.user_id,
                            report_id=entry.report_id,
                            markdown=blog_md,
                        )
            except (
                Exception
            ) as persist_err:  # noqa: BLE001 – persistence is best-effort
                logger.warning(
                    "blog_persist_failed",
                    report_id=entry.report_id,
                    error=str(persist_err),
                )
        logger.info(
            "blog_generation_ready",
            token=token,
            chars_raw=len(blog_md_raw),
            chars_clean=len(blog_md),
            stripped=len(blog_md_raw) - len(blog_md),
        )
    except (
        Exception
    ) as err:  # noqa: BLE001 – surface any LLM/network error to the polling client
        logger.error("blog_generation_error", token=token, error=str(err))
        entry = _BLOG_CACHE.get(token)
        if entry is not None:
            entry.state = "error"
            entry.error = str(err) or "Blog generation failed"


@limiter.limit("10/5minute")
@router.post("/generate-blog")
async def generate_blog(
    request: Request,
    report_text: str = Form(...),
    audience: str = Form(""),
    length: str = Form(""),
    tone: str = Form(""),
    theme: str = Form(""),
    extra: str = Form(""),
    report_id: str = Form(""),
) -> JSONResponse:
    """Queue a blog generation and return the view URL immediately.

    The actual LLM call runs as a background task so the popup window opened by
    the click handler can be navigated within the same user gesture (avoids
    popup-blocker / auto-close issues with long-running synchronous LLM calls).

    Optional `audience`, `length`, `tone`, and `extra` form fields tailor the
    system prompt — see `_build_blog_system_prompt`. All four default to a
    Distill.pub-style ~1500-word technical post if omitted.
    """

    logger.info("blog_generate: request received")

    if not report_text.strip():
        raise HTTPException(status_code=400, detail="Report text required")

    session_id = await get_session_id(request)
    chat_state = get_chat_state(session_id)
    chat_digest_raw = _gather_chat_digest(chat_state, char_cap=8000)
    chat_digest = (
        deduplicate_chat(chat_digest_raw, report_text) if chat_digest_raw else ""
    )

    llm_service: LLMService | None = get_llm_service()
    if llm_service is None:
        raise HTTPException(status_code=503, detail="LLM service unavailable")

    model_key = config.DEFAULT_MODEL_KEY
    model_cfg = config.AVAILABLE_MODELS[model_key]

    system_prompt = _build_blog_system_prompt(
        audience=audience,
        length=length,
        tone=tone,
        extra=extra,
    )

    from services.auth.session import get_auth_user_id

    user_id = get_auth_user_id(request) or ""
    report_id_clean = (report_id or "").strip()

    _purge_expired_blogs()
    token = str(_uuid.uuid4())
    snippet_chars = config.PAPER_MAX_CONTENT_SNIPPET_CHARS
    _BLOG_CACHE[token] = _BlogEntry(
        state="pending",
        report_text=report_text.strip()[:snippet_chars],
        audience=(audience or "").strip().lower(),
        length=(length or "").strip().lower(),
        tone=(tone or "").strip().lower(),
        theme=(theme or "").strip().lower() or "indigo",
        extra=(extra or "").strip()[:_BLOG_EXTRA_MAX_CHARS],
        report_id=report_id_clean if user_id else "",
        user_id=user_id,
    )
    if report_id_clean and user_id:
        _BLOG_BY_REPORT[report_id_clean] = token

    asyncio.create_task(
        _run_blog_generation(
            token,
            llm_service=llm_service,
            provider=model_cfg["provider"],
            model_id=model_prefs.resolve_model_id(model_key),
            report_text=report_text,
            chat_digest=chat_digest,
            system_prompt=system_prompt,
        )
    )

    logger.info(
        "blog_queued",
        token=token,
        audience=(audience or _DEFAULT_BLOG_OPTS["audience"]).lower(),
        length=(length or _DEFAULT_BLOG_OPTS["length"]).lower(),
        tone=(tone or _DEFAULT_BLOG_OPTS["tone"]).lower(),
        has_extra=bool((extra or "").strip()),
    )
    return JSONResponse({"url": f"/blog-view?token={token}", "token": token})


@limiter.limit("10/5minute")
@router.post("/regenerate-blog")
async def regenerate_blog(
    request: Request,
    token: str = Form(""),
    audience: str = Form(""),
    length: str = Form(""),
    tone: str = Form(""),
    theme: str = Form(""),
    extra: str = Form(""),
    report_id: str = Form(""),
) -> JSONResponse:
    """Revise an existing blog using natural-language feedback.

    Two sources for the previous draft, checked in order:

    1. `report_id` — the persisted blog on the report row (`blog_markdown`
       against `report_text`). This is the path used by the in-page blog
       pane, and works even after the ephemeral cache entry expired.
    2. `token` — the original cache entry (standalone /blog-view flow).

    Queues a new background generation and returns its token. The original
    cache entry is preserved (read-only) so the user can flip back if needed.
    """

    logger.info("blog_regenerate: request received")

    _purge_expired_blogs()

    from services.auth.session import get_auth_user_id

    user_id = get_auth_user_id(request) or ""
    report_id_clean = (report_id or "").strip()

    prev: _BlogEntry | None = None
    if report_id_clean and user_id:
        from services.persistence.database import database_available, session_scope
        from services.persistence.repositories import get_report

        report = None
        if database_available():
            async for session in session_scope():
                report = await get_report(
                    session, user_id=user_id, report_id=report_id_clean
                )
        if report is None or not (report.blog_markdown or "").strip():
            raise HTTPException(
                status_code=404,
                detail="No saved blog for this report. Generate a fresh one.",
            )
        prev = _BlogEntry(
            state="ready",
            markdown=report.blog_markdown or "",
            report_text=report.report_text or "",
        )
    else:
        prev = _BLOG_CACHE.get(token)
        if prev is None:
            raise HTTPException(
                status_code=404,
                detail="Previous blog not found or expired. Generate a fresh one.",
            )
        if prev.state != "ready":
            raise HTTPException(
                status_code=409,
                detail="Previous blog is still being written. Wait until it finishes.",
            )
        if not prev.report_text.strip():
            # Pre-PR cache entries may not have report_text populated.
            raise HTTPException(
                status_code=410,
                detail="Original report no longer cached. Regenerate from the report page.",
            )

    feedback_clean = (extra or "").strip()
    if not feedback_clean:
        raise HTTPException(
            status_code=400,
            detail="Please describe what you'd like changed about the blog.",
        )
    # Allow a longer feedback than the initial 500 cap — revisions tend to be
    # more detailed than initial customizations.
    if len(feedback_clean) > 1000:
        feedback_clean = feedback_clean[:1000].rstrip()

    llm_service: LLMService | None = get_llm_service()
    if llm_service is None:
        raise HTTPException(status_code=503, detail="LLM service unavailable")

    model_key = config.DEFAULT_MODEL_KEY
    model_cfg = config.AVAILABLE_MODELS[model_key]

    # Use new audience/length/tone if provided, otherwise inherit previous.
    eff_audience = (audience or prev.audience or "").strip().lower()
    eff_length = (length or prev.length or "").strip().lower()
    eff_tone = (tone or prev.tone or "").strip().lower()

    system_prompt = _build_blog_system_prompt(
        audience=eff_audience,
        length=eff_length,
        tone=eff_tone,
        extra="",  # feedback goes in the user prompt below for emphasis
    )

    snippet_chars = config.PAPER_MAX_CONTENT_SNIPPET_CHARS
    user_prompt = (
        "You previously wrote the blog post below. The reader has asked for "
        "revisions. Rewrite the post to address their feedback while keeping "
        "what's strong (structure, factual grounding, diagrams that still "
        "make sense). Apply the revised audience / length / tone if changed. "
        "Treat the original research report as the source of truth — do not "
        "hallucinate findings not present in it.\n\n"
        "USER FEEDBACK (apply this):\n"
        + feedback_clean
        + "\n\nPREVIOUS DRAFT:\n"
        + prev.markdown.strip()[: max(snippet_chars - 4000, 4000)]
        + "\n\nORIGINAL REPORT (for grounding):\n"
        + prev.report_text.strip()[:snippet_chars]
    )

    eff_theme = (theme or prev.theme or "indigo").strip().lower()

    new_token = str(_uuid.uuid4())
    _BLOG_CACHE[new_token] = _BlogEntry(
        state="pending",
        report_text=prev.report_text,
        audience=eff_audience,
        length=eff_length,
        tone=eff_tone,
        theme=eff_theme,
        extra=feedback_clean,
        report_id=report_id_clean if user_id else "",
        user_id=user_id,
    )
    if report_id_clean and user_id:
        _BLOG_BY_REPORT[report_id_clean] = new_token

    asyncio.create_task(
        _run_blog_generation(
            new_token,
            llm_service=llm_service,
            provider=model_cfg["provider"],
            model_id=model_prefs.resolve_model_id(model_key),
            report_text=prev.report_text,
            chat_digest="",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    )

    logger.info(
        "blog_regenerate_queued",
        prev_token=token,
        new_token=new_token,
        audience=eff_audience or _DEFAULT_BLOG_OPTS["audience"],
        length=eff_length or _DEFAULT_BLOG_OPTS["length"],
        tone=eff_tone or _DEFAULT_BLOG_OPTS["tone"],
        feedback_chars=len(feedback_clean),
    )
    return JSONResponse({"url": f"/blog-view?token={new_token}", "token": new_token})


@router.get("/blog-status")
async def blog_status(token: str = Query(...)) -> JSONResponse:
    """Return the current state of a queued blog generation.

    Polled by `static/js/blog-view.js` until `state == "ready"` or `"error"`.
    """

    _purge_expired_blogs()
    entry = _BLOG_CACHE.get(token)
    if entry is None:
        return JSONResponse(
            status_code=404,
            content={"state": "expired", "error": "Blog not found or expired."},
        )

    payload: dict[str, str] = {"state": entry.state}
    if entry.state == "ready":
        payload["markdown"] = entry.markdown
        payload["theme"] = entry.theme or "indigo"
    elif entry.state == "error":
        payload["error"] = entry.error or "Blog generation failed"
    return JSONResponse(payload)


@router.get("/reports/{report_id}/blog")
async def report_blog(request: Request, report_id: str) -> JSONResponse:
    """Return the persisted blog for a report, or its in-flight state.

    The blog pane on the report page polls this on load: `ready` returns the
    stored markdown, `pending`/`error` proxy the latest generation attempt,
    and `empty` means no blog has been generated yet.
    """

    from services.auth.session import require_auth_user_id
    from services.persistence.database import database_available, session_scope
    from services.persistence.repositories import get_report

    user_id = require_auth_user_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Report history is not configured.")

    async for session in session_scope():
        report = await get_report(session, user_id=user_id, report_id=report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    markdown = (report.blog_markdown or "").strip()
    if markdown:
        return JSONResponse({"state": "ready", "markdown": markdown})

    _purge_expired_blogs()
    token = _BLOG_BY_REPORT.get(report_id)
    entry = _BLOG_CACHE.get(token) if token else None
    if entry is None:
        return JSONResponse({"state": "empty"})
    if entry.state == "error":
        return JSONResponse(
            {"state": "error", "error": entry.error or "Blog generation failed"}
        )
    return JSONResponse({"state": "pending", "token": token})


@router.get("/blog-view")
async def blog_view(
    token: str = Query(...),
) -> Response:
    """Render the blog page shell. The markdown is fetched client-side via /blog-status."""

    _purge_expired_blogs()
    entry = _BLOG_CACHE.get(token)
    if entry is None:
        raise HTTPException(
            status_code=404, detail="Blog not found or expired. Generate a new one."
        )

    safe_token = _html.escape(token, quote=True)
    safe_theme = _html.escape(entry.theme or "indigo", quote=True)

    page = f"""<!DOCTYPE html>
<html lang="en" data-accent="{safe_theme}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Blog — Office Hours</title>
  <!-- Apply the app's saved theme BEFORE first paint to avoid a light/dark flash. -->
  <script>
    (function () {{
      try {{
        var t = localStorage.getItem("theme");
        if (t === "dark") document.documentElement.dataset.theme = "dark";
      }} catch (e) {{}}
    }})();
  </script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/css/blog.css">
  <!-- modal.css supplies styles for the customize modal triggered by Revise. -->
  <link rel="stylesheet" href="/static/css/modal.css">
</head>
<body class="blog-is-loading blog-scope">

  <!-- Reading progress bar -->
  <div class="blog-progress-bar"></div>

  <!-- Nav -->
  <nav class="blog-nav">
    <a href="/" class="blog-nav-brand">Office Hours</a>
    <div class="blog-nav-actions">
      <!-- "Writing blog…" pill shown while body.blog-is-loading is set. Hidden when initBlog() finishes. -->
      <span class="blog-writing-pill" aria-live="polite">
        <span class="oh-spinner small"></span>
        <span>Writing blog…</span>
      </span>
      <button id="blog-theme-toggle" class="blog-nav-btn" title="Toggle theme" aria-label="Toggle theme">🌙</button>
      <button id="blog-revise-btn" class="blog-nav-btn" title="Revise this blog (audience / length / tone / feedback)" disabled>Revise</button>
      <button id="blog-copy-btn" class="blog-nav-btn" title="Copy markdown (available once blog finishes)" disabled>Copy MD</button>
      <button id="blog-download-btn" class="blog-nav-btn" title="Download .md (available once blog finishes)" disabled>Download MD</button>
      <button id="blog-save-html-btn" class="blog-nav-btn" title="Save as self-contained HTML (available once blog finishes)" disabled>Save HTML</button>
      <button id="blog-save-pdf-btn" class="blog-nav-btn" title="Save as PDF (available once blog finishes)" disabled>Save PDF</button>
    </div>
  </nav>

  <!-- Hero -->
  <header class="blog-hero">
    <div class="blog-meta">
      <span class="blog-tag">Research</span>
      <span class="blog-meta-divider"></span>
      <span id="blog-date"></span>
      <span class="blog-meta-divider"></span>
      <span id="blog-read-time"></span>
    </div>
    <h1 id="blog-hero-title">Composing your blog</h1>
  </header>

  <!-- Article (rendered by JS once /blog-status returns markdown) -->
  <article class="blog-article">
    <div class="blog-loading" role="status" aria-live="polite">
      <div class="blog-loading-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 4h11l5 5v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z" />
          <path d="M14 4v5h5" />
          <path d="M8 13h8" />
          <path d="M8 17h5" />
        </svg>
      </div>
      <h2 class="blog-loading-headline">Writing your blog<span class="dots"></span></h2>
      <p class="blog-loading-substep" id="blog-loading-substep">Reading the report…</p>
    </div>
  </article>

  <!-- Footer -->
  <footer class="blog-footer">
    Generated by <a href="/">Office Hours</a>
  </footer>

  <!-- Token + accent theme consumed by blog-view.js -->
  <script>window.__BLOG_TOKEN__ = "{safe_token}"; window.__BLOG_ACCENT__ = "{safe_theme}";</script>
  <!-- Shared customization modal (used by the Revise button). Loaded before
       blog-view.js so window.ohBlog.openCustomizeModal is available. -->
  <script src="/static/js/blog-modal.js"></script>
  <script src="/static/js/blog-view.js"></script>
</body>
</html>"""

    return HTMLResponse(content=page)


# ---------------------------------------------------------------------------
# Notion export
# ---------------------------------------------------------------------------


def _parse_tags(llm_output: str) -> tuple[str, list[str]]:
    """Split the TAGS: line from the outline and return (outline, tags)."""
    lines = llm_output.rstrip().split("\n")
    tags: list[str] = []
    outline = llm_output

    # Walk backwards to find the TAGS line (should be last non-empty line)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if stripped.upper().startswith("TAGS:"):
            raw = stripped[len("TAGS:") :].strip()
            tags = [t.strip() for t in raw.split(",") if t.strip()]
            outline = "\n".join(lines[:i]).rstrip()
            break
        # Stop searching if we hit a non-TAGS content line
        break

    return outline, tags


def _notion_json_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "detail": message},
    )


@limiter.limit("10/5minute")
@router.post("/export-notion")
async def export_notion(
    request: Request,
    report_text: str = Form(...),
    title: str = Form("Office Hours Report"),
    source_url: str = Form(""),
    audience: str = Form(""),
    model: str = Form(""),
    report_id: str | None = Form(None),
) -> Response:
    """Generate a structured outline, then push it to Notion."""

    logger.info("notion_export: request received")

    if not report_text.strip():
        return _notion_json_error("Report text required", 400)

    from services.auth.session import get_auth_user_id
    from services.persistence.database import database_available, session_scope

    user_id = get_auth_user_id(request)
    notion_api_key = config.NOTION_API_KEY
    notion_parent_page_id = config.NOTION_PARENT_PAGE_ID
    notion_parent_database_id = config.NOTION_PARENT_DATABASE_ID
    destination = "env"

    if user_id and database_available():
        try:
            from services.persistence.repositories import (
                get_notion_integration,
                notion_access_token,
            )

            async for db_session in session_scope():
                integration = await get_notion_integration(db_session, user_id=user_id)
            if integration is not None:
                notion_api_key = notion_access_token(integration)
                notion_parent_page_id = integration.parent_page_id
                notion_parent_database_id = integration.parent_database_id
                destination = integration.workspace_name or "notion"
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "notion_user_config_load_failed", user_id=user_id, error=str(err)
            )

    if not notion_api_key:
        return _notion_json_error(
            "Notion is not connected. Connect Notion in Settings first.",
            400,
        )
    if not (notion_parent_page_id or notion_parent_database_id):
        return _notion_json_error(
            "Choose a Notion destination in Settings before exporting.",
            400,
        )

    # --- Generate structured outline via LLM ---
    llm_service: LLMService | None = get_llm_service()
    outline_md = report_text.strip()  # fallback: raw report
    tags: list[str] = []

    if llm_service:
        model_key = config.DEFAULT_MODEL_KEY
        model_cfg = config.AVAILABLE_MODELS[model_key]
        try:
            user_prompt = (
                "Distill the following research report into a structured outline.\n\n"
                "REPORT:\n"
                + report_text.strip()[: config.PAPER_MAX_CONTENT_SNIPPET_CHARS]
            )
            raw_outline = await llm_service.generate_completion(
                provider_name=model_cfg["provider"],
                model_name=model_prefs.resolve_model_id(model_key),
                prompt=user_prompt,
                max_tokens=1024,
                system_prompt=_NOTION_OUTLINE_PROMPT,
            )
            outline_md, tags = _parse_tags(raw_outline)
            logger.info("notion_outline_generated", chars=len(outline_md), tags=tags)
        except Exception as err:
            logger.warning("notion_outline_fallback", error=str(err))
            # Fall through — outline_md stays as raw report text
    else:
        logger.warning("notion_export: LLM unavailable, exporting raw report")

    from services.notion.notion_exporter import NotionExporter, NotionExportError

    exporter = NotionExporter(api_key=notion_api_key)
    try:
        url = await exporter.export_report(
            title=title,
            markdown=outline_md,
            parent_page_id=notion_parent_page_id,
            parent_database_id=notion_parent_database_id,
            metadata={
                "source_url": source_url or None,
                "audience": audience or None,
                "model": model or None,
                "type": "Report",
                "tags": tags,
            },
        )
    except NotionExportError as err:
        logger.error("notion_export_failed", error=str(err))
        if user_id and database_available():
            try:
                from services.persistence.repositories import record_export

                async for db_session in session_scope():
                    await record_export(
                        db_session,
                        user_id=user_id,
                        report_id=report_id,
                        export_type="notion",
                        destination=destination,
                        url=None,
                        status="failed",
                        error=str(err),
                    )
            except Exception as persist_err:  # noqa: BLE001
                logger.warning("notion_export_record_failed", error=str(persist_err))
        return _notion_json_error(str(err), 502)
    finally:
        await exporter.close()

    if user_id and database_available():
        try:
            from services.persistence.repositories import record_export

            async for db_session in session_scope():
                await record_export(
                    db_session,
                    user_id=user_id,
                    report_id=report_id,
                    export_type="notion",
                    destination=destination,
                    url=url,
                    status="success",
                )
        except Exception as persist_err:  # noqa: BLE001
            logger.warning("notion_export_record_failed", error=str(persist_err))

    logger.info("notion_export_success", url=url)
    return JSONResponse(content={"ok": True, "url": url})
