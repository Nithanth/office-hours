"""Report "Ask Assistant About This Paper" must open the viewer for that paper.

Exa paper ids are usually the paper URL; ids like OpenReview's
``https://openreview.net/forum?id=...`` contain ``?`` and must be
path-encoded or the viewer receives a truncated id and no ``url``.
"""

from __future__ import annotations

import html
import re

import pytest

from api.routers.report_stream import _ask_assistant_button
from models.paper import Paper

_PAPERS = [
    Paper(
        id="https://arxiv.org/abs/1706.03762",
        title="Attention Is All You Need",
        url="https://arxiv.org/pdf/1706.03762.pdf",
    ),
    Paper(
        id="https://openreview.net/forum?id=NG7sS51zVF",
        title="Efficient Streaming Language Models with Attention Sinks",
        url="https://openreview.net/pdf?id=NG7sS51zVF",
    ),
    Paper(
        id="https://example.org/paper#v2",
        title="Fragment & 'quoted' id",
        url="https://example.org/paper.pdf",
    ),
]


def _hx_get(button_html: str) -> str:
    match = re.search(r"hx-get='([^']*)'", button_html)
    assert match, button_html
    return html.unescape(match.group(1))


@pytest.mark.parametrize("paper", _PAPERS, ids=lambda p: p.id)
def test_ask_assistant_opens_viewer_for_that_paper(client, paper: Paper):
    resp = client.get(_hx_get(_ask_assistant_button(paper, "technical", "default")))

    assert resp.status_code == 200
    assert 'class="modal-content pdf-viewer' in resp.text
    assert f'data-paper-id="{html.escape(paper.id)}"' in resp.text
    assert html.escape(paper.title) in resp.text
