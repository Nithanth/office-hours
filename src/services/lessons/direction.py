"""A paper director composes bounded Manim graphics as data in a shared scene."""

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=42)]
Number = Annotated[float, Field(ge=-1e8, le=1e8, allow_inf_nan=False)]
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Graphic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: Short


class Bars(Graphic):
    type: Literal["bars"]
    labels: list[Short] = Field(min_length=1, max_length=5)
    values: list[Number] = Field(min_length=1, max_length=5)
    after: list[Number] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def dimensions(self) -> Self:
        if len(self.labels) != len(self.values) or (
            self.after and len(self.after) != len(self.labels)
        ):
            raise ValueError("Each bar needs a label and a value in each state.")
        return self


class Network(Graphic):
    type: Literal["network"]
    layers: list[Annotated[int, Field(ge=1, le=6)]] = Field(min_length=2, max_length=4)
    masked: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def valid_mask(self) -> Self:
        if any(unit > sum(self.layers) for unit in self.masked):
            raise ValueError("Mask indices start at 1 across successive layers.")
        return self


class Series(BaseModel):
    label: Short
    values: list[Number] = Field(min_length=2, max_length=32)


class Lines(Graphic):
    type: Literal["lines"]
    x_label: Short
    x: list[Number] = Field(min_length=2, max_length=32)
    series: list[Series] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def coordinates(self) -> Self:
        if any(b <= a for a, b in zip(self.x, self.x[1:], strict=False)):
            raise ValueError("Use increasing x coordinates.")
        if any(len(line.values) != len(self.x) for line in self.series):
            raise ValueError("Each line needs one value per x coordinate.")
        return self


class Node(BaseModel):
    label: Short
    position: tuple[Fraction, Fraction]
    move_to: tuple[Fraction, Fraction] | None = None


class Diagram(Graphic):
    type: Literal["diagram"]
    nodes: list[Node] = Field(min_length=2, max_length=8)
    arrows: list[tuple[int, int]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_arrows(self) -> Self:
        if any(a == b or min(a, b) < 1 or max(a, b) > len(self.nodes) for a, b in self.arrows):
            raise ValueError("Arrows connect distinct nodes numbered from 1.")
        return self


class Stroke(BaseModel):
    label: Short
    points: list[tuple[Fraction, Fraction]] = Field(min_length=2, max_length=64)
    target: list[tuple[Fraction, Fraction]] = Field(default_factory=list, max_length=64)


class Drawing(Graphic):
    type: Literal["drawing"]
    strokes: list[Stroke] = Field(min_length=1, max_length=4)


ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")]
ObjectIds = Annotated[list[ObjectId], Field(max_length=32)]
Position = tuple[Fraction, Fraction]


class IllustratedObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: ObjectId
    kind: Literal["node", "label", "path", "region", "wire"]
    color: Literal["mint", "gold", "blue", "coral", "paper", "muted"] = "mint"
    label: str = Field(default="", max_length=80)
    points: list[Position] = Field(default_factory=list, max_length=64)
    between: tuple[ObjectId, ObjectId] | None = None
    visible: bool = True

    @model_validator(mode="after")
    def geometry(self) -> Self:
        if self.kind == "wire":
            if not self.between or self.between[0] == self.between[1]:
                raise ValueError("A wire needs two distinct node IDs.")
        elif self.kind in {"label", "node"}:
            if len(self.points) != 1 or (self.kind == "label" and not self.label.strip()):
                raise ValueError("Give a node or label a single position, and a label some text.")
        elif len(set(self.points)) < (3 if self.kind == "region" else 2):
            raise ValueError("Supply distinct vertices for a path or region.")
        return self


class VisualBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cue: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=450)]
    show: ObjectIds = Field(default_factory=list)
    hide: ObjectIds = Field(default_factory=list)
    flow: ObjectIds = Field(default_factory=list)
    highlight: ObjectIds = Field(default_factory=list)
    morph: tuple[ObjectId, ObjectId] | None = None


class Illustration(Graphic):
    heading: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    type: Literal["illustration"]
    objects: list[IllustratedObject] = Field(min_length=2, max_length=32)
    beats: list[VisualBeat] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def references(self) -> Self:
        objects = {o.id: o for o in self.objects}
        if len(objects) != len(self.objects):
            raise ValueError("Object IDs must be unique in a scene.")
        nodes = {o.id for o in self.objects if o.kind == "node"}
        for obj in self.objects:
            if obj.between and not set(obj.between) <= nodes:
                raise ValueError("Both wire endpoints must name nodes in this scene.")
        for beat in self.beats:
            targets = set(beat.show + beat.hide + beat.flow + beat.highlight) | set(
                beat.morph or ()
            )
            if not targets or not targets <= objects.keys() or set(beat.show) & set(beat.hide):
                raise ValueError("Every beat must change named objects with consistent visibility.")
            if any(objects[k].kind not in {"wire", "path"} for k in beat.flow):
                raise ValueError("Flow needs a wire or path to travel along.")
            if beat.morph and beat.morph[0] == beat.morph[1]:
                raise ValueError("Morph an object into a different object.")
        return self


class WaveTerm(BaseModel):
    # Frequencies count complete cycles across the unit square, phase is in cycles.
    amplitude: Annotated[float, Field(gt=0, le=2)]
    fx: Annotated[float, Field(ge=-10, le=10)]
    fy: Annotated[float, Field(ge=-10, le=10)]
    phase: Fraction = 0


class FieldSweep(BaseModel):
    cue: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=450)]
    mode: Literal["sample", "trace", "slope"]
    to_x: Fraction


class SampledField(Graphic):
    type: Literal["sampled_field"]
    terms: list[WaveTerm] = Field(min_length=1, max_length=5)
    y: Fraction
    start_x: Fraction
    sweeps: list[FieldSweep] = Field(min_length=2, max_length=5)


Panel = Annotated[
    Bars | Network | Lines | Diagram | Drawing | Illustration | SampledField,
    Field(discriminator="type"),
]


class ManimScene(BaseModel):
    kind: Literal["manim"] = "manim"
    title: str = Field(min_length=1, max_length=64)
    narration: str = Field(min_length=5, max_length=450)
    source: str = Field(min_length=1, max_length=90)
    cue: str = Field(default="", max_length=64)
    panels: list[Panel] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def full_canvas(self) -> Self:
        if len(self.panels) > 1 and any(
            isinstance(p, (Illustration, SampledField)) for p in self.panels
        ):
            raise ValueError("An illustration occupies the full scene. Use one panel.")
        return self

    def remove_appended_cue(self) -> None:
        """A director may accidentally copy its cue into a trailing production note."""
        match = re.search(r"""\s+Cue:\s*["'“‘]?(.*?)["'”’]?\s*$""", self.narration, re.I)
        if match and self.cue and match[1].strip().casefold() == self.cue.strip().casefold():
            spoken = self.narration[: match.start()].rstrip()
            if len(spoken) >= 5:
                self.narration = spoken


class PlannedScene(ManimScene):
    cue: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def spoken_narration(self) -> Self:
        self.remove_appended_cue()
        return self


class FilmPlan(BaseModel):
    scenes: list[PlannedScene] = Field(min_length=1, max_length=4)
