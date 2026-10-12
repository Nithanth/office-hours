"""Unit tests for ExaClient caching & shuffle helpers."""

from types import SimpleNamespace

import pytest

from models.paper import Paper
from services.core import exa_client as exa_mod


def _paper(pid: str, score: float = 0.9) -> Paper:
    return Paper(
        id=pid,
        title=f"Paper {pid}",
        url=f"https://example.com/{pid}",
        authors=["Author"],
        score=score,
        source="unit-test",
    )


def _dummy_adapter(
    api_key: str = "dummy",
) -> SimpleNamespace:  # noqa: D401 – simple namespace
    """Return a stub object in place of ExaAdapter."""

    return SimpleNamespace(sync_client=None)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> exa_mod.ExaClient:
    # Replace ExaAdapter class so constructor doesn't need real key / network
    monkeypatch.setattr(exa_mod, "ExaAdapter", lambda api_key: _dummy_adapter())

    # Provide a lightweight CachedSearch replacement (conftest stub has no __init__)
    class _FakeCacheSearch:
        def __init__(self, paper_by_id: dict[str, Paper], k: int, orig_ids: list[str]):
            self.paper_by_id = paper_by_id
            self.k = k
            self.orig_ids = orig_ids
            self.display_ids = orig_ids[:k]

        # API helpers used by ExaClient wrappers
        def init_display(self) -> None:  # noqa: D401
            self.display_ids = self.orig_ids[: self.k]

        def get_display_papers(self):  # noqa: D401
            return [self.paper_by_id[pid] for pid in self.display_ids]

        def shuffle_k(self):  # noqa: D401
            import random

            pool = list(self.paper_by_id.keys())
            random.shuffle(pool)
            self.display_ids = pool[: self.k]
            return self.get_display_papers()

        def reset_display(self):  # noqa: D401
            self.display_ids = self.orig_ids[: self.k]
            return self.get_display_papers()

        def replace_wildcard(self):  # noqa: D401
            import random

            choice = random.choice(list(self.paper_by_id.keys()))
            self.display_ids[-1] = choice
            return self.paper_by_id[choice]

    monkeypatch.setattr(exa_mod, "CachedSearch", _FakeCacheSearch, raising=True)

    # Instantiate client (adapter is stubbed)
    c = exa_mod.ExaClient(api_key="dummy")

    # Patch search_papers to return synthetic list
    monkeypatch.setattr(
        c,
        "search_papers",
        lambda query, num_results=10, **kw: [
            _paper(str(i)) for i in range(num_results)
        ],
    )
    return c


def test_search_and_cache_builds_display(client: exa_mod.ExaClient) -> None:
    display = client.search_and_cache("test", k=5)
    assert len(display) == 5
    # Ensure we received *k* unique papers
    ids = {p.id for p in display}
    assert len(ids) == 5


def test_shuffle_and_reset(client: exa_mod.ExaClient) -> None:
    first = client.search_and_cache("test", k=5)
    shuffled = client.shuffle_k()
    # Shuffled order differs from initial at least sometimes
    assert len(shuffled) == 5
    if shuffled != first:
        # Shuffled set should still contain *k* unique ids
        assert len({p.id for p in shuffled}) == 5

    reset = client.reset_display()
    assert [p.id for p in reset] == [p.id for p in first]


def test_replace_wildcard(client: exa_mod.ExaClient) -> None:
    client.search_and_cache("test", k=5)
    wildcard = client.replace_wildcard()
    # Either returns None (if no wildcard slot used) or a Paper
    if wildcard is not None:
        assert isinstance(wildcard, Paper)
