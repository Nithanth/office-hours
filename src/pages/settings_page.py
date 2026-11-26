"""Settings page: per-user model-tier pinning and BYOK OpenRouter key."""

from __future__ import annotations

from fasthtml.common import (
    H1,
    Body,
    Div,
    Form,
    Header,
    Html,
    Label,
    Link,
    Main,
    Option,
    P,
    Span,
)

import config as _cfg
from pages.controls import Button, Input, Select
from pages.shell import page_head, top_bar
from utils.static_assets import static_url

_TIER_DESCRIPTIONS = {
    "fast": "Quick lookups, summaries, glossary terms. Cheapest models.",
    "standard": "Default. Chat, explainers, and most reports.",
    "deep": "Hardest reasoning: multi-paper synthesis and research reports.",
}


def _tier_row(
    tier: str, pinned_id: str | None, default_id: str, draft: tuple[str, str] | None
) -> Div:
    """One tier selector row: curated options + custom model ID field."""
    raw, custom = draft or ("", "")
    if draft is not None:
        pinned_id = raw if raw != "__custom__" else None

    curated = _cfg.config.CURATED_OPENROUTER_MODELS
    curated_ids = {m["id"] for m in curated}

    options = [
        Option(
            f"Default: {_label_for(default_id)}",
            value="",
            selected=not pinned_id and raw != "__custom__",
        )
    ]
    for m in curated:
        # Only offer models whose declared tiers include this one — a flash
        # model can't be pinned to Deep, an opus-class model can't go on Fast.
        if tier not in m.get("tiers", []):
            continue
        options.append(
            Option(m["label"], value=m["id"], selected=(pinned_id == m["id"]))
        )
    offered_ids = {o.attrs["value"] for o in options[1:]}
    if pinned_id and pinned_id in curated_ids and pinned_id not in offered_ids:
        # Existing pin no longer fits this tier — keep it visible so the
        # user sees it (server validation rejects it on save, forcing a fix).
        options.append(
            Option(
                f"{_label_for(pinned_id)} (current, off-tier)",
                value=pinned_id,
                selected=True,
            )
        )
    is_custom = raw == "__custom__" or (bool(pinned_id) and pinned_id not in curated_ids)
    options.append(Option("Custom model ID…", value="__custom__", selected=is_custom))

    sel = Select(
        *options,
        name=f"pin_{tier}",
        id=f"pin_{tier}",
        aria_label=f"{tier.capitalize()} model",
        onchange=(
            f"var c=document.getElementById('custom_{tier}');"
            "c.style.display=this.value==='__custom__'?'inline-block':'none';"
            "c.required=this.value==='__custom__';"
            f"if(this.value!=='__custom__'){{c.value='';}}"
        ),
    )
    custom_input = Input(
        type="text",
        name=f"custom_{tier}",
        id=f"custom_{tier}",
        value=custom if draft is not None else (pinned_id if is_custom else ""),
        placeholder="author/slug (e.g. anthropic/claude-opus-4.8)",
        aria_label=f"Custom {tier} model ID",
        required=is_custom,
        style=f"display:{'inline-block' if is_custom else 'none'};",
    )
    return Div(
        Label(
            Span(
                tier.capitalize(),
                style="font-weight:600;display:inline-block;min-width:80px;",
            ),
            Span(
                _TIER_DESCRIPTIONS[tier],
                style="opacity:0.7;font-size:0.85rem;margin-left:8px;",
            ),
        ),
        Div(sel, custom_input, cls="oh-field-row settings-model-fields"),
    )


def _label_for(model_id: str) -> str:
    for m in _cfg.config.CURATED_OPENROUTER_MODELS:
        if m["id"] == model_id:
            return str(m["label"])
    return model_id


def _masked(key: str) -> str:
    return "sk-or-…" + key[-4:] if len(key) > 8 else "saved key"


def settings_page(
    *,
    pins: dict[str, str],
    openrouter_key: str | None,
    saved: bool = False,
    error: str | None = None,
    draft: dict[str, tuple[str, str]] | None = None,
) -> Html:
    """Render the settings page."""
    defaults = {
        tier: str(_cfg.config.AVAILABLE_MODELS[tier]["id"])
        for tier in ("fast", "standard", "deep")
    }
    key_status = (
        f"A personal OpenRouter key is saved ({_masked(openrouter_key)}). It overrides the shared server key for all your requests."
        if openrouter_key
        else "Using the shared server OpenRouter key. Add your own to pick more models and use your own limits."
    )

    flash = None
    if error:
        flash = Div(
            error,
            style="color:#d9534f;border:1px solid #d9534f;border-radius:4px;padding:8px 12px;margin:0 0 16px 0;",
        )
    elif saved:
        flash = Div(
            "Settings saved.",
            style="color:#3fb950;border:1px solid #3fb950;border-radius:4px;padding:8px 12px;margin:0 0 16px 0;",
        )

    return Html(
        page_head(
            "Settings · Office Hours",
            Link(rel="stylesheet", href=static_url("/static/css/misc.css")),
        ),
        Body(
            top_bar("settings"),
            Main(
                Header(H1("Settings"), cls="workspace-topic-title"),
                P(
                    "Model preferences are saved to your account (or this browser session if you're not signed in).",
                    style="opacity:0.7;margin:0 0 20px 0;font-size:0.9rem;",
                ),
                flash if flash else "",
                Form(
                    Div(
                        "Model tiers",
                        style="font-weight:600;margin:0 0 8px 0;",
                    ),
                    *(
                        _tier_row(tier, pins.get(tier), defaults[tier], (draft or {}).get(tier))
                        for tier in ("fast", "standard", "deep")
                    ),
                    Button(
                        "Save model preferences",
                        variant="primary",
                        type="submit",
                        cls="btn-secondary",
                    ),
                    method="post",
                    action="/settings/models",
                ),
                Div(style="margin:28px 0;border-top:1px solid var(--border,#444);"),
                Div(
                    "OpenRouter API key",
                    style="font-weight:600;margin:0 0 8px 0;",
                ),
                P(key_status, style="opacity:0.8;font-size:0.85rem;margin:0 0 10px 0;"),
                Form(
                    Input(
                        type="password",
                        name="openrouter_key",
                        placeholder="sk-or-…",
                        autocomplete="off",
                        aria_label="OpenRouter API key",
                    ),
                    Button(
                        "Save key",
                        type="submit",
                        cls="btn-secondary",
                    ),
                    method="post",
                    action="/settings/openrouter-key",
                    cls="oh-field-row settings-key-fields",
                ),
                (
                    Form(
                        Button(
                            "Remove saved key",
                            type="submit",
                            variant="danger",
                            style="margin-top:10px;",
                        ),
                        method="post",
                        action="/settings/openrouter-key/delete",
                    )
                    if openrouter_key
                    else ""
                ),
                Div(style="margin:28px 0;border-top:1px solid var(--border,#444);"),
                Div(
                    "Notion integration",
                    style="font-weight:600;margin:0 0 8px 0;",
                ),
                Div(
                    Span("Checking…", style="opacity:0.7;font-size:0.85rem;"),
                    id="settings-notion",
                ),
                Div(style="margin:28px 0;border-top:1px solid var(--border,#444);"),
                Div(
                    "Account",
                    style="font-weight:600;margin:0 0 8px 0;",
                ),
                Div(
                    Span("Checking…", style="opacity:0.7;font-size:0.85rem;"),
                    id="settings-account",
                ),
                cls="page-wrap settings-page",
            ),
            # target for the toolbar assistant's chat modal
            Div(id="modal-container"),
        ),
        doctype=False,
    )
