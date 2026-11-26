"""Bounded scene vocabulary shared by the planner, editor and renderer."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]


class Scene(BaseModel):
    kind: Literal["sequence", "comparison", "prediction", "network"]
    title: str = Field(min_length=1, max_length=64)
    narration: str = Field(min_length=5, max_length=450)
    source: str = Field(min_length=1, max_length=90)
    labels: list[Label] = Field(min_length=2, max_length=4)
    values: list[float] = Field(default_factory=list, max_length=4)

    dropped: list[int] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        if self.kind == "network":
            if len(self.labels) != len(self.values) or any(
                n not in range(1, 7) for n in self.values
            ):
                raise ValueError("Provide one layer size from 1 to 6 for each network label.")
            if any(n < 1 or n > sum(self.values) for n in self.dropped):
                raise ValueError("Dropped unit outside the network.")
            return self
        if self.dropped or len(self.labels) > 3:
            raise ValueError("Only networks support dropped units or four labels.")
        if self.kind == "comparison" and (
            len(self.values) != len(self.labels)
            or any(not 0 <= n <= 1e12 for n in self.values)
            or not max(self.values)
        ):
            raise ValueError(
                "Comparisons need one nonnegative value per label and a positive maximum."
            )
        if self.kind != "comparison" and self.values:
            raise ValueError("Only comparisons use numeric values.")
        return self


class Board(BaseModel):
    scenes: list[Scene] = Field(min_length=1, max_length=6)
