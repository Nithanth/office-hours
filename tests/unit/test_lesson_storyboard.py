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


@pytest.mark.parametrize(
    "panel",
    [
        {
            "type": "lines",
            "x_label": "Step",
            "x": [1, 0],
            "series": [{"label": "Loss", "values": [2, 1]}],
        },
        {"type": "network", "layers": [1, 2, 1], "masked": [5]},
        {
            "type": "diagram",
            "nodes": [{"label": "A", "position": [0, 0]}, {"label": "B", "position": [1, 1]}],
            "arrows": [[1, 3]],
        },
        {"type": "bars", "labels": ["A", "B"], "values": [2, 1], "after": [0]},
    ],
)
def test_manim_rejects_inconsistent_geometry_before_render(panel: dict) -> None:
    from services.lessons.direction import ManimScene

    with pytest.raises(ValidationError):
        ManimScene.model_validate(
            {
                "title": "Read the graphic",
                "source": "PDF page 2",
                "narration": "Follow the change.",
                "panels": [{"heading": "Example", **panel}],
            }
        )


@pytest.mark.parametrize(
    ("tail", "expected_tail"),
    [
        (" Cue: 'Look at the mean.'", ""),
        (" Cue: a different phrase.", " Cue: a different phrase."),
    ],
)
def test_recording_omits_only_a_duplicate_production_cue(tail: str, expected_tail: str) -> None:
    from services.lessons.direction import PlannedScene

    spoken = "The two values average to four."
    scene = PlannedScene.model_validate(
        {
            "title": "Expected contribution",
            "source": "Page 3",
            "narration": spoken + tail,
            "cue": "Look at the mean.",
            "panels": [{"type": "bars", "heading": "Mean", "labels": ["Wire"], "values": [4]}],
        }
    )
    assert scene.narration == spoken + expected_tail


def test_illustration_requires_named_endpoints_and_a_route_for_flow() -> None:
    from services.lessons.direction import Illustration

    plan = {
        "type": "illustration",
        "heading": "Follow the signal",
        "objects": [
            {"id": "a", "kind": "node", "points": [[0.2, 0.5]]},
            {"id": "b", "kind": "node", "points": [[0.8, 0.5]]},
            {"id": "wire", "kind": "wire", "between": ["a", "b"]},
        ],
        "beats": [
            {"cue": "signal moves", "flow": ["wire"]},
            {"cue": "unit vanishes", "hide": ["b"]},
        ],
    }
    assert len(Illustration.model_validate(plan).beats) == 2
    with pytest.raises(ValidationError, match="Flow needs"):
        Illustration.model_validate(
            {**plan, "beats": [plan["beats"][0], {"cue": "arrives", "flow": ["a"]}]}
        )
    with pytest.raises(ValidationError, match="wire endpoints"):
        Illustration.model_validate(
            {
                **plan,
                "objects": [
                    *plan["objects"][:2],
                    {"id": "wire", "kind": "wire", "between": ["a", "missing"]},
                ],
            }
        )
