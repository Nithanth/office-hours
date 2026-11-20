"""Shared controls for FastHTML pages and string-rendered fragments.

Keep behavior hooks in ``cls``/attributes, not in visual variants. String
templates use the same attribute helpers; client templates use controls.js.
"""

from __future__ import annotations

import html
from typing import Any, Literal

from fasthtml.common import A as _A
from fasthtml.common import Button as _Button
from fasthtml.common import Input as _Input
from fasthtml.common import Select as _Select
from fasthtml.common import Textarea as _Textarea

Variant = Literal["primary", "secondary", "ghost", "danger", "tab", "surface"]
Size = Literal["md", "sm"]


def button_classes(
    cls: str = "",
    *,
    variant: Variant = "secondary",
    size: Size = "md",
    icon: bool = False,
) -> str:
    return " ".join(
        filter(
            None,
            (
                "oh-button",
                f"oh-button--{variant}",
                f"oh-control--{size}",
                "oh-button--icon" if icon else "",
                cls,
            ),
        )
    )


def field_classes(cls: str = "", *, kind: str = "text", size: Size = "md") -> str:
    if kind == "hidden":
        return cls
    base = "oh-choice" if kind in {"checkbox", "radio"} else "oh-field"
    return " ".join(filter(None, (base, f"oh-control--{size}", cls)))


def button_attrs(
    cls: str = "",
    *,
    variant: Variant = "secondary",
    size: Size = "md",
    icon_label: str | None = None,
) -> str:
    """Escaped shared attributes for an existing button/link string template.

    The template retains its original type, URL and event/HTMX attributes.
    ``icon_label`` supplies an accessible name for icon-only controls.
    """
    classes = button_classes(cls, variant=variant, size=size, icon=bool(icon_label))
    attrs = f'class="{html.escape(classes, quote=True)}"'
    if icon_label:
        attrs += f' aria-label="{html.escape(icon_label, quote=True)}"'
    return attrs


def field_attrs(cls: str = "", *, kind: str = "text", size: Size = "md") -> str:
    return (
        f'class="{html.escape(field_classes(cls, kind=kind, size=size), quote=True)}"'
    )


def Button(
    *children: Any,
    variant: Variant = "secondary",
    size: Size = "md",
    cls: str = "",
    **attrs: Any,
) -> Any:
    attrs.setdefault("type", "button")
    return _Button(
        *children, cls=button_classes(cls, variant=variant, size=size), **attrs
    )


def IconButton(label: str, *children: Any, cls: str = "", **attrs: Any) -> Any:
    attrs.setdefault("type", "button")
    attrs.setdefault("title", label)
    variant = attrs.pop("variant", "ghost")
    size = attrs.pop("size", "sm")
    attrs["aria_label"] = label
    return _Button(
        *children,
        cls=button_classes(cls, variant=variant, size=size, icon=True),
        **attrs,
    )


def ButtonLink(
    *children: Any,
    variant: Variant = "secondary",
    size: Size = "md",
    cls: str = "",
    **attrs: Any,
) -> Any:
    return _A(*children, cls=button_classes(cls, variant=variant, size=size), **attrs)


def Input(*, cls: str = "", size: Size = "md", **attrs: Any) -> Any:
    return _Input(
        cls=field_classes(cls, kind=attrs.get("type", "text"), size=size), **attrs
    )


def Select(*children: Any, cls: str = "", size: Size = "md", **attrs: Any) -> Any:
    return _Select(*children, cls=field_classes(cls, size=size), **attrs)


def Textarea(*children: Any, cls: str = "", size: Size = "md", **attrs: Any) -> Any:
    return _Textarea(*children, cls=field_classes(cls, size=size), **attrs)
