"""End-to-end regression suite — full request flows through the real app
with providers stubbed at the boundary.

Run alongside the rest of the suite: `uv run pytest tests/e2e -q`.
Live provider (Exa/S2/OpenAlex rate limits, real Notion workspace,
Railway deploy) checks stay manual.
"""
