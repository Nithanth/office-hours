from __future__ import annotations

import random
from dataclasses import dataclass, field

from models.paper import Paper

__all__ = ["CachedSearch"]


@dataclass
class CachedSearch:
    """Encapsulates the results of a single search and all UX-level lists.

    Attributes
    ----------
    paper_by_id
        Mapping of ``paper.id`` ⇢ ``Paper`` objects (set of *M* cached items).
    k
        Number of papers the UI should show (max 10).
    orig_ids
        The canonical *top-k* ids in the order returned by Exa (used by *reset*).
    display_ids
        The ids currently shown in the UI (mutable).
    """

    paper_by_id: dict[str, Paper]
    k: int
    orig_ids: list[str]
    display_ids: list[str] = field(default_factory=list)
    pinned_id: str | None = None  # If set, this paper stays in position 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _random_not_in(self, exclude: set[str]) -> str:
        pool = [pid for pid in self.paper_by_id if pid not in exclude]
        if not pool:  # Fallback – shouldn’t normally happen
            pool = list(self.paper_by_id.keys())
        return random.choice(pool)

    # ------------------------------------------------------------------
    # Public operations invoked by API endpoints
    # ------------------------------------------------------------------
    def init_display(self) -> None:
        """Populate *display_ids* for initial UI display.

        If a ``pinned_id`` is set it will always occupy index 0, with the
        remaining ``k-1`` items filled from ``orig_ids`` (skipping the pinned
        item if already present).
        """
        if self.pinned_id and self.pinned_id in self.paper_by_id:
            remaining_slots = self.k - 1
            other_ids = [pid for pid in self.orig_ids if pid != self.pinned_id]
            self.display_ids = [self.pinned_id] + other_ids[:remaining_slots]
        else:
            self.display_ids = self.orig_ids[: self.k]

    def get_display_papers(self) -> list[Paper]:
        return [self.paper_by_id[pid] for pid in self.display_ids]

    def replace_wildcard(self) -> Paper:
        """Replace the last (wild-card) slot with a random unseen paper.

        The pinned first item (if any) is never replaced.
        """
        exclude = set(self.display_ids)
        new_id = self._random_not_in(exclude)
        # Replace the last slot (acts as wildcard anchor in the UI)
        self.display_ids[-1] = new_id
        return self.paper_by_id[new_id]

    def shuffle_k(self) -> list[Paper]:
        """Shuffle the current display while keeping the pinned paper fixed."""
        pool_ids = list(self.paper_by_id.keys())
        if self.pinned_id and self.pinned_id in pool_ids:
            pool_ids.remove(self.pinned_id)
            sample_size = min(self.k - 1, len(pool_ids))
            shuffled = random.sample(pool_ids, sample_size)
            self.display_ids = [self.pinned_id] + shuffled
            # Pad if we didn't get enough due to small pool
            if len(self.display_ids) < self.k:
                for pid in self.orig_ids:
                    if pid == self.pinned_id or pid in self.display_ids:
                        continue
                    self.display_ids.append(pid)
                    if len(self.display_ids) >= self.k:
                        break
        else:
            if len(pool_ids) >= self.k:
                self.display_ids = random.sample(pool_ids, self.k)
            else:
                self.display_ids = pool_ids[:]
                for pid in self.orig_ids:
                    if len(self.display_ids) >= self.k:
                        break
                    if pid not in self.display_ids:
                        self.display_ids.append(pid)
        return self.get_display_papers()

    def reset_display(self) -> list[Paper]:
        """Reset to the deterministic, relevance-ordered view."""
        if self.pinned_id and self.pinned_id in self.orig_ids:
            remaining = [pid for pid in self.orig_ids if pid != self.pinned_id]
            self.display_ids = [self.pinned_id] + remaining[: self.k - 1]
        else:
            self.display_ids = self.orig_ids[: self.k]
        return self.get_display_papers()
