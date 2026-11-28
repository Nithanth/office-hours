"""A paper director composes bounded Manim graphics as data in a shared scene."""

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
    labels: list[Short] = Field(min_length=2, max_length=5)
    values: list[Number] = Field(min_length=2, max_length=5)
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


Panel = Annotated[Bars | Network | Lines | Diagram | Drawing, Field(discriminator="type")]


class ManimScene(BaseModel):
    kind: Literal["manim"] = "manim"
    title: str = Field(min_length=1, max_length=64)
    narration: str = Field(min_length=5, max_length=450)
    source: str = Field(min_length=1, max_length=90)
    cue: str = Field(default="", max_length=64)
    panels: list[Panel] = Field(min_length=1, max_length=2)


class PlannedScene(ManimScene):
    cue: str = Field(min_length=1, max_length=64)


class FilmPlan(BaseModel):
    scenes: list[PlannedScene] = Field(min_length=1, max_length=4)
