"""Typed FastHTML components for chat bubbles, citation cards, and chat modal."""

from collections.abc import Iterable
from typing import Any

from fasthtml.components import (
    H2,
    A,
    Div,
    Form,
    Label,
    P,
    Span,
)

from pages.controls import Button as BUTTON
from pages.controls import IconButton, Textarea
from pages.controls import Input as INPUT


# Leaf components
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


# Citation card
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
        H2(
            title[:80] + ("…" if len(title) > 80 else ""),
            style="font-size:1em;margin:0 0 4px 0;",
        ),
        P(authors_str, style="font-size:0.85em;margin:0;"),
        P(A("Open", href=url or "#", target="_blank"), style="margin:2px 0 0 0;"),
        cls="citation-card",
        style="padding:8px;max-width:260px;font-size:0.85em;line-height:1.3;box-shadow:0 2px 4px rgba(0,0,0,0.1);border-radius:4px;background:white;",
        **attrs,
    )


# Chat modal
def ChatStarters(paper_id: str, form_id: str) -> Div:
    if paper_id == "global":
        questions = (
            "How can I turn a topic into a research question?",
            "How do I tell whether a study is reliable?",
            "How should I compare papers with different methods?",
        )
    elif paper_id == "topic" or paper_id.startswith("topic:"):
        questions = (
            "What are the main findings across these papers?",
            "Where do these studies disagree, and why?",
            "What questions about this topic are still open?",
        )
    else:
        questions = (
            "What is the main contribution of this paper?",
            "How did the authors test their approach?",
            "What are the limitations of this study?",
        )

    return Div(
        P("Suggested questions", cls="chat-starters-label"),
        *(
            BUTTON(
                question,
                type="button",
                form=form_id,
                cls="chat-starter",
                variant="surface",
            )
            for question in questions
        ),
        cls="chat-starters",
        role="group",
        aria_label="Suggested questions",
    )


def ChatModal(
    modal_id: str,
    title: str,
    chat_log_id: str,
    paper_id: str,
    chat_key: str,
    history: Iterable[Div],
    form_action: str = "/chat",
    oob_slot: str | None = None,
    **attrs: Any,
) -> Div:
    """Return a modal shell (header, log container, input form)."""
    if oob_slot:
        attrs = {**attrs, "hx_oob": oob_slot}

    is_global = paper_id == "global"
    form_id = f"{chat_log_id}-form"

    # Sources toggle only for global assistant
    sources_toggle = (
        Label(
            INPUT(type="checkbox", name="with_sources", value="1"),
            Span("Use sources", style="margin-left:4px;"),
            cls="sources-toggle",
        )
        if is_global
        else ()
    )

    return Div(
        Div(
            # Header
            Div(
                H2(title, cls="chat-modal-title"),
                IconButton(
                    "Close modal",
                    "×",
                    cls="modal-close-btn",
                    onclick="htmx.find('#modal-container').innerHTML = ''",
                ),
                cls="modal-header",
            ),
            # Chat log
            Div(
                *history,
                id=chat_log_id,
                cls="chat-log-container modal-body",
                data_chat_pid=paper_id,
                data_chat_key=chat_key,
                style="visibility:hidden",
            ),
            ChatStarters(paper_id, form_id),
            # Input area
            Form(
                Div(
                    Textarea(
                        name="message",
                        placeholder=(
                            "Ask anything…"
                            if is_global
                            else (
                                "Ask about this topic…"
                                if paper_id == "topic" or paper_id.startswith("topic:")
                                else "Ask about this paper…"
                            )
                        ),
                        rows="2",
                        cls="chat-textarea",
                        aria_label="Message",
                    ),
                    Div(
                        sources_toggle,
                        IconButton(
                            "Send message",
                            "→",
                            type="submit",
                            cls="send-btn",
                            variant="primary",
                        ),
                        cls="chat-form-controls",
                    ),
                    cls="chat-input-row",
                ),
                id=form_id,
                method="POST",
                action=form_action,
                **{
                    "hx-post": form_action,
                    "hx-target": f"#{chat_log_id}",
                    "hx-swap": "beforeend",
                },
            ),
            cls="modal-content",
        ),
        cls="modal",
        **attrs,
    )
