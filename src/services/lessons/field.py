"""A coordinate probe links a continuous field to its cross-section and local derivative."""

import math

import numpy as np
from manim import (
    Camera,
    DecimalNumber,
    Dot,
    Line,
    Rectangle,
    Scene,
    Text,
    ValueTracker,
    VGroup,
    VMobject,
    always_redraw,
    linear,
)

from services.lessons.direction import SampledField
from services.lessons.field_values import field_value
from services.lessons.graphics import BLUE, GOLD, GRID, MINT, MUTED, PAPER, point, text
from services.lessons.illustration import cue_times
from services.lessons.speech import Speech


def animate_field(
    scene: Scene, plate: SampledField, speech: Speech, narration: str, seconds: float
) -> None:
    left, bottom = -3.65 - 4.45 / 2, -0.1 - 4.45 / 2
    border = Rectangle(width=4.45, height=4.45, color=MUTED, stroke_width=1.1).move_to(
        point(-3.65, -0.1)
    )
    corners = np.array([point(left, bottom + 4.45), point(left + 4.45, bottom)])
    camera = scene.camera
    assert isinstance(camera, Camera) and camera.background is not None
    (x0, y0), (x1, y1) = camera.points_to_pixel_coords(border, corners)
    # Rasterize the fixed field once at the camera resolution; overlays stay vector-based.
    grid = field_value(
        plate, np.linspace(0, 1, x1 - x0)[None, :], np.linspace(1, 0, y1 - y0)[:, None]
    )
    palette = np.array(
        [[11, 27, 40], [23, 72, 96], [42, 145, 154], [137, 214, 192], [255, 220, 145]]
    )
    colors = np.stack(
        [np.interp(grid, np.linspace(0, 1, len(palette)), c) for c in palette.T], axis=-1
    )
    background = camera.background.copy()
    background[y0:y1, x0:x1, :3] = colors.astype(np.uint8)
    camera.set_background(background)
    gx, gy, gw, gh = -0.38, -1.65, 6.15, 3.48
    row = bottom + 4.45 * plate.y
    scene.add(
        border,
        text("Coordinate field", -3.65, 2.47, 25, 4.65),
        text("Brightness along the marked row", gx + gw / 2, 2.47, 25, 6.05),
        Line(
            point(left, row),
            point(left + 4.45, row),
            color=PAPER,
            stroke_opacity=0.8,
            stroke_width=1.6,
        ),
    )
    for v in (0, 0.5, 1):
        scene.add(
            Line(point(gx, gy + gh * v), point(gx + gw, gy + gh * v), color=GRID, stroke_width=1.2),
            text(f"{v:g}", gx - 0.25, gy + gh * v, 15, 0.39, MUTED),
        )
    scene.add(
        text("x = 0", gx + 0.19, gy - 0.26, 16, 1, MUTED),
        text("x = 1", gx + gw - 0.19, gy - 0.26, 16, 1, MUTED),
    )
    tracker = ValueTracker(plate.start_x)

    def value(x: float, derivative: bool = False) -> float:
        return float(field_value(plate, np.asarray(x), np.asarray(plate.y), derivative=derivative))

    def graph_at(x: float) -> np.ndarray:
        return np.array([gx + gw * x, gy + gh * value(x), 0])

    for sample_x in np.linspace(0, 1, 5):
        scene.add(
            Dot(point(left + 4.45 * sample_x, row), radius=0.047, color=PAPER),
            Dot(graph_at(float(sample_x)), radius=0.05, color=PAPER),
        )

    def cursor() -> VGroup:
        x = tracker.get_value()
        mark = Dot(
            point(left + 4.45 * x, row),
            radius=0.105,
            color=GOLD,
            stroke_color="#162422",
            stroke_width=2,
        )
        return VGroup(mark, Dot(graph_at(x), radius=0.082, color=GOLD))

    scene.add(always_redraw(cursor))
    # Glyph-based numerals avoid external LaTeX and keep a continuous numeric readout.

    readout = DecimalNumber(
        value(plate.start_x),
        num_decimal_places=2,
        mob_class=Text,  # type: ignore[arg-type]
        color=GOLD,
    ).scale(0.64)
    readout.move_to(point(-2.6, -2.79))
    readout.add_updater(lambda m: m.set_value(value(tracker.get_value())))
    scene.add(text("Brightness", -4, -2.79, 22, 1.8, MUTED), readout)
    trace = VMobject(color=MINT, stroke_width=4)
    tangent = Line(color=BLUE, stroke_width=4)
    scene.add(trace, tangent)
    tangent.set_opacity(0)
    intervals: list[tuple[float, float]] = []
    trace_start = plate.start_x
    tracing = False

    def update_trace(m: VMobject) -> None:
        spans = intervals + ([(trace_start, tracker.get_value())] if tracing else [])
        m.clear_points()
        for a, b in spans:
            if abs(b - a) < 1e-6:
                continue
            x = np.linspace(a, b, max(4, math.ceil(abs(b - a) * 220)))
            y = field_value(plate, x, np.asarray(plate.y))
            verts = np.column_stack((gx + gw * x, gy + gh * y, np.zeros_like(x)))
            m.start_new_path(verts[0])
            m.add_points_as_corners(verts[1:])

    trace.add_updater(lambda _: update_trace(trace))

    def update_tangent(m: Line) -> None:
        x = tracker.get_value()
        slope = value(x, True) * gh / gw
        delta = point(1, slope) * (0.74 / math.hypot(1, slope))
        center = graph_at(x)
        m.put_start_and_end_on(center - delta, center + delta)

    tangent.add_updater(lambda _: update_tangent(tangent))
    times = cue_times([s.cue for s in plate.sweeps], speech, narration, seconds)
    frame = 1 / float(scene.camera.frame_rate)
    for i, sweep in enumerate(plate.sweeps):
        if times[i] - scene.time >= frame:
            scene.wait(times[i] - scene.time)
        trace_start = tracker.get_value()
        tracing = sweep.mode in {"trace", "slope"}
        tangent.set_opacity(1 if sweep.mode == "slope" else 0)
        available = (times[i + 1] if i + 1 < len(times) else seconds) - scene.time
        if available >= frame:
            scene.play(
                tracker.animate.set_value(sweep.to_x),
                run_time=min(available, 6.0),
                rate_func=linear,
            )
        if tracing:
            intervals.append((trace_start, sweep.to_x))
        tracing = False
    if seconds - scene.time >= frame:
        scene.wait(seconds - scene.time)
