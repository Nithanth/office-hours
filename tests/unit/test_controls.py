"""Control markup must retain native form behavior and existing JS/HTMX hooks."""

from html.parser import HTMLParser

import pytest
from fasthtml.common import I, Option, to_xml

from pages.controls import Button, ButtonLink, IconButton, Input, Select, button_attrs
from pages.main_page import SharedPageData, home_page
from pages.settings_page import settings_page


class Elements(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.items = []
        self.feed(markup if isinstance(markup, str) else to_xml(markup))

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


def attrs(markup):
    return Elements(markup).items[0][1]


def test_buttons_keep_form_semantics_and_htmx_hooks():
    assert attrs(Button("Cancel"))["type"] == "button"
    actual = attrs(
        Button(
            "Search",
            variant="primary",
            type="submit",
            name="mode",
            value="auto",
            id="search",
            hx_post="/search",
            hx_target="#results",
            disabled=True,
        )
    )
    assert actual["type"] == "submit"
    assert actual["name"] == "mode"
    assert actual["value"] == "auto"
    assert actual["id"] == "search"
    assert actual["hx-post"] == "/search"
    assert actual["hx-target"] == "#results"
    assert "disabled" in actual
    assert "oh-button--primary" in actual["class"].split()


def test_string_and_fasthtml_buttons_share_contract_and_escape_attributes():
    label = 'Copy "quoted" <paper>'
    actual = attrs(IconButton(label, I(cls="fa-copy"), cls="copy-icon-btn"))
    template = attrs(
        f'<button {button_attrs("copy-icon-btn", variant="ghost", size="sm", icon_label=label)}>Copy</button>'
    )
    assert actual["class"] == template["class"]
    assert actual["aria-label"] == template["aria-label"] == label
    assert actual["title"] == label
    malicious = attrs(
        f'<button {button_attrs(chr(34) + " onclick=" + chr(34) + "bad")}>Safe</button>'
    )
    assert "onclick" not in malicious


def test_fields_preserve_values_selections_and_native_choice_inputs():
    text = attrs(Input(type="text", name="model", value='a"<b>', required=True))
    assert text["value"] == 'a"<b>'
    assert "required" in text
    assert "oh-field" in text["class"].split()
    choice = attrs(Input(type="checkbox", checked=True, name="paper", value="one"))
    assert "checked" in choice
    assert "oh-choice" in choice["class"].split()
    assert "oh-field" not in choice["class"].split()
    hidden = attrs(Input(type="hidden", name="paper", value="one"))
    assert "oh-field" not in hidden.get("class", "").split()
    select = Elements(Select(Option("One", value="one", selected=True), name="status"))
    assert select.items[0][1]["name"] == "status"
    assert "selected" in select.items[1][1]
    link = Elements(ButtonLink("Settings", href="/settings"))
    assert link.items[0][0] == "a"
    assert link.items[0][1]["href"] == "/settings"
    assert "type" not in link.items[0][1]


@pytest.mark.parametrize(
    "render",
    [
        lambda: home_page(SharedPageData()),
        lambda: settings_page(pins={"fast": "provider/custom"}, openrouter_key=None),
    ],
)
def test_page_controls_are_shared_and_named(render):
    for tag, element in Elements(render()).items:
        classes = element.get("class", "").split()
        if tag == "button":
            assert "oh-button" in classes
            if "oh-button--icon" in classes:
                assert element.get("aria-label")
        if tag in {"input", "select", "textarea"} and element.get("type") != "hidden":
            assert {"oh-choice", "oh-field"}.intersection(classes)
