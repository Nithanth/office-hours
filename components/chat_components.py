"""Reusable FastHTML chat components (Phase 7).

These helpers let page modules build chat-related markup with concise, typed
wrappers instead of hand-spelling FastHTML trees everywhere.  Using central
components simplifies future refactors and keeps styling decisions in one
place.

NOTE: Existing callers are *not* migrated in this commit to avoid a
wide-ranging diff during active feature work.  Once chat-modal stabilises we
can progressively adopt these helpers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fasthtml.components import BUTTON, H2, A, Div, Form, P, Textarea

__all__ = [
    "ChatBubble",
    "ChatModal",
    "CitationCard",
]


# ---------------------------------------------------------------------------
# Leaf components
# ---------------------------------------------------------------------------


def ChatBubble(
    message: str, role: str = "assistant", oob_slot: str | None = None, **attrs: Any
) -> Div:
    """Return a formatted chat bubble div.

    Args:
        message: The text of the message (already escaped or trusted).
        role: "user" | "assistant" (CSS classes pick colours accordingly).
        **attrs: Extra attributes forwarded to the underlying Div.
    """
    if role not in {"user", "assistant"}:
        role = "assistant"
    if oob_slot:
        attrs = {**attrs, "hx_oob": oob_slot}
    return Div(P(message, style="margin:0;"), cls=f"{role}-message", **attrs)


# ---------------------------------------------------------------------------
# Citation UX
# ---------------------------------------------------------------------------


def CitationCard(
    title: str,
    authors: Iterable[str] | None = None,
    url: str | None = None,
    oob_slot: str | None = None,
    **attrs: Any,
) -> Div:
    """Small hover card for paper citations."""
    authors_str = ", ".join(authors) if authors else "Unknown authors"
    if oob_slot:
        attrs = {**attrs, "hx_oob": oob_slot}
    return Div(
        H2(title[:80] + ("…" if len(title) > 80 else ""), style="font-size:1em;margin:0 0 4px 0;"),
        P(authors_str, style="font-size:0.85em;margin:0;"),
        P(A("Open", href=url or "#", target="_blank"), style="margin:2px 0 0 0;"),
        cls="citation-card",
        style="padding:8px;max-width:260px;font-size:0.85em;line-height:1.3;box-shadow:0 2px 4px rgba(0,0,0,0.1);border-radius:4px;background:white;",
        **attrs,
    )


# ---------------------------------------------------------------------------
# Composite Chat Modal component (optional; current code builds inline)
# ---------------------------------------------------------------------------


def ChatModal(
    modal_id: str,
    title: str,
    chat_log_id: str,
    form_action: str = "/chat",
    oob_slot: str | None = None,
    **attrs: Any,
) -> Div:
    """Return a modal shell (header, log container, input form).

    This is not used yet; provided for future refactor once modal markup
    stabilises.
    """
    if oob_slot:
        attrs = {**attrs, "hx_oob": oob_slot}
    return Div(
        Div(
            # Header
            Div(
                H2(title),
                BUTTON(
                    "×",
                    cls="modal-close-btn",
                    hx_on_click="htmx.find('#modal-container').innerHTML = ''",
                ),
                cls="modal-header",
            ),
            # Body
            Div(id=chat_log_id, cls="chat-log-container"),
            Form(
                Textarea(
                    name="message",
                    placeholder="Ask about this paper…",
                    rows="3",
                    style="width: 100%; border-radius: 4px; padding: 5px;",
                ),
                BUTTON("→", type="submit", cls="send-btn", style="margin-left: 6px;"),
                method="POST",
                action=form_action,
                hx_post=form_action,
                hx_target=f"#{chat_log_id}",
                hx_swap="beforeend",
            ),
            cls="modal-content",
        ),
        cls="modal",
        **attrs,
    )
