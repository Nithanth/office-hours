"""Verify user edits cannot produce invalid layer geometry or comparison scales."""

import pytest
from pydantic import ValidationError

from services.lessons.storyboard import Scene


def test_network_accepts_variable_layers_and_one_based_dropped_units() -> None:
    scene = Scene(
        kind="network",
        title="Dropout",
        narration="Trace the remaining wires.",
        source="PDF page 2",
        labels=["Inputs", "Hidden", "Output"],
        values=[5, 3, 1],
        dropped=[1, 9],
    )
    assert scene.values == [5, 3, 1]


@pytest.mark.parametrize(
    ("kind", "values", "dropped"),
    [
        ("network", [2.5, 3], []),
        ("network", [2, 3], [6]),
        ("comparison", [0, 0], []),
        ("comparison", [1], []),
        ("sequence", [], [1]),
    ],
)
def test_invalid_edits_are_rejected(kind: str, values: list[float], dropped: list[int]) -> None:
    with pytest.raises(ValidationError):
        Scene.model_validate(
            dict(
                kind=kind,
                title="Scene",
                narration="Follow the arrows.",
                source="Topic overview",
                labels=["A", "B"],
                values=values,
                dropped=dropped,
            )
        )
