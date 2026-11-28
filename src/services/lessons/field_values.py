"""Evaluate the illustrative brightness field used by the director and renderer."""

import numpy as np

from services.lessons.direction import SampledField


def field_value(
    plate: SampledField, x: np.ndarray, y: np.ndarray, *, derivative: bool = False
) -> np.ndarray:
    total = np.zeros(np.broadcast_shapes(x.shape, y.shape))
    scale = 2 * sum(term.amplitude for term in plate.terms)
    for term in plate.terms:
        angle = 2 * np.pi * (term.fx * x + term.fy * y + term.phase)
        wave = 2 * np.pi * term.fx * np.cos(angle) if derivative else np.sin(angle)
        total += term.amplitude * wave / scale
    return total if derivative else 0.5 + total
