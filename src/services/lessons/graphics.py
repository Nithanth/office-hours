"""Compose the director's panel data into geometric Manim animations."""

import numpy as np
from manim import (
    Animation,
    AnimationGroup,
    Arrow,
    Create,
    Dot,
    FadeIn,
    FadeOut,
    Indicate,
    Line,
    MoveAlongPath,
    Rectangle,
    Text,
    Transform,
    VGroup,
    VMobject,
)

from services.lessons.direction import Bars, Diagram, Drawing, Lines, Network, Panel

PAPER = "#F1EBDD"
MUTED = "#A8BABA"
GRID = "#354845"
MINT = "#69DBC4"
GOLD = "#FFBC71"
BLUE = "#84AFFF"
COLORS = (MINT, GOLD, BLUE)
BACKGROUND = "#102021"


def point(x: float, y: float) -> np.ndarray:
    return np.array([x, y, 0.0])


def text(
    value: str,
    x: float,
    y: float,
    size: int,
    width: float,
    color: str = PAPER,
    *,
    serif: bool = False,
) -> Text:
    obj = Text(
        value,
        font="DejaVu Serif" if serif else "DejaVu Sans",
        font_size=size,
        color=color,
        disable_ligatures=True,
    )
    if obj.width > width:
        obj.scale_to_fit_width(width)
    return obj.move_to(point(x, y))


def fmt(n: float) -> str:
    return f"{n:.3g}"


def chart_bars(data: Bars, cx: float, width: float) -> tuple[VGroup, Animation]:
    left = cx - width * 0.26
    right = cx + width * 0.31
    lo: float = min([0.0, *data.values, *data.after])
    hi: float = max([0.0, *data.values, *data.after])

    def xpos(value: float) -> float:
        return left + (value - lo) / (hi - lo or 1) * (right - left)

    zero = xpos(0)
    static = VGroup()
    initial = VGroup()
    final = VGroup()

    def bar(n: float, y: float, color: str) -> VGroup:
        end = xpos(n)
        rect = Rectangle(
            width=max(0.012, abs(end - zero)),
            height=0.31,
            stroke_width=0,
            fill_color=color,
            fill_opacity=0.95,
        ).move_to(point((end + zero) / 2, y))
        return VGroup(rect, text(fmt(n), cx + width * 0.407, y, 21, width * 0.12))

    for i, (label, value) in enumerate(zip(data.labels, data.values, strict=True)):
        y = 1.02 - (i + 0.5) * 3.32 / len(data.labels)
        static.add(text(label, cx - width * 0.385, y, 21, width * 0.21))
        static.add(Line(point(left, y - 0.26), point(right, y - 0.26), stroke_width=1, color=GRID))
        initial.add(bar(value if data.after else 0, y, COLORS[i % 3]))
        final.add(bar(data.after[i] if data.after else value, y, COLORS[i % 3]))
    return VGroup(static, initial), Transform(initial, final)


def chart_network(data: Network, cx: float, width: float) -> tuple[VGroup, Animation]:
    layers = []
    nodes = VGroup()
    edges = VGroup()
    removed = VGroup()
    affected = VGroup()
    marked = VGroup()
    for i, count in enumerate(data.layers):
        layer = []
        for j in range(count):
            pos = point(
                cx + width * (i / (len(data.layers) - 1) - 0.5) * 0.80,
                -0.52 + (0.5 * (count - 1) - j) * 0.59,
            )
            dot = Dot(pos, radius=0.105, color=MINT)
            index = len(nodes) + 1
            nodes.add(dot)
            layer.append((index, pos))
            if index in data.masked:
                marked.add(dot)
                affected.add(
                    Line(
                        pos + point(-0.085, -0.085),
                        pos + point(0.085, 0.085),
                        color=GOLD,
                        stroke_width=2,
                    ),
                    Line(
                        pos + point(-0.085, 0.085),
                        pos + point(0.085, -0.085),
                        color=GOLD,
                        stroke_width=2,
                    ),
                )
        layers.append(layer)
    for left, right in zip(layers, layers[1:], strict=False):
        for a, src in left:
            for b, dst in right:
                edge = Line(src, dst, color="#668983", stroke_width=2)
                edges.add(edge)
                if a in data.masked or b in data.masked:
                    removed.add(edge)
    motion = (
        AnimationGroup(FadeOut(removed), FadeOut(marked), FadeIn(affected))
        if len(marked)
        else Indicate(nodes, color=GOLD, scale_factor=1.045)
    )
    return VGroup(edges, nodes), motion


def chart_lines(data: Lines, cx: float, width: float) -> tuple[VGroup, Animation]:
    left, right = cx - width * 0.37, cx + width * 0.41
    bottom, top = -1.72, 1.13
    low = min(v for series in data.series for v in series.values)
    high = max(v for series in data.series for v in series.values)
    if low == high:
        low -= 1
        high += 1
    group = VGroup()
    for i in range(4):
        y = bottom + (top - bottom) * i / 3
        group.add(
            Line(point(left, y), point(right, y), color=GRID, stroke_width=1),
            text(fmt(low + (high - low) * i / 3), left - width * 0.057, y, 17, width * 0.1, MUTED),
        )
    group.add(text(data.x_label, cx, -2.25, 19, width * 0.63, MUTED))
    for x, value in [(left, data.x[0]), (right, data.x[-1])]:
        group.add(text(fmt(value), x, bottom - 0.2, 16, width * 0.12, MUTED))
    traces = []
    for i, s in enumerate(data.series):
        color = COLORS[i]
        n = len(data.series)
        group.add(
            text(
                s.label,
                cx + width * ((i + 0.5) / n - 0.5) * 0.86,
                1.55,
                18,
                width * 0.79 / n,
                color,
            )
        )
        coords = [
            point(
                left + (x - data.x[0]) / (data.x[-1] - data.x[0]) * (right - left),
                bottom + (v - low) / (high - low) * (top - bottom),
            )
            for x, v in zip(data.x, s.values, strict=True)
        ]
        trace = VMobject(color=color, stroke_width=4).set_points_as_corners(coords)
        traces.append(Create(trace))
    return group, AnimationGroup(*traces, lag_ratio=0.22)


def chart_diagram(data: Diagram, cx: float, width: float) -> tuple[VGroup, Animation]:
    def state(moved: bool) -> VGroup:
        loc = []
        dots = VGroup()
        for i, node in enumerate(data.nodes):
            x, y = node.move_to if moved and node.move_to else node.position
            pos = point(cx + (x - 0.5) * width * 0.77, -0.56 + (y - 0.5) * 2.85)
            loc.append(pos)
            dots.add(
                Dot(pos, radius=0.105, color=COLORS[i % 3]),
                text(node.label, pos[0], pos[1] - 0.27, 19, width * 0.27),
            )
        arrows = VGroup()
        for a, b in data.arrows:
            if np.linalg.norm(loc[b - 1] - loc[a - 1]) > 0.29:
                arrows.add(
                    Arrow(
                        loc[a - 1],
                        loc[b - 1],
                        buff=0.14,
                        color=MUTED,
                        stroke_width=2.8,
                        max_tip_length_to_length_ratio=0.10,
                    )
                )
        return VGroup(arrows, dots)

    initial = state(False)
    if any(n.move_to is not None for n in data.nodes):
        return initial, Transform(initial, state(True))
    arrows, dots = initial
    if len(arrows):
        return VGroup(dots), Create(arrows)
    return VGroup(dots[0:2]), FadeIn(dots[2:])


def chart_drawing(data: Drawing, cx: float, width: float) -> tuple[VGroup, Animation]:
    """Trace or morph the director's own paths in one shared coordinate system."""
    objects = VGroup()
    changes: list[Animation] = []
    for i, stroke in enumerate(data.strokes):
        color = COLORS[i % len(COLORS)]

        def path(coords: list[tuple[float, float]], ink: str) -> VMobject:
            vertices = [
                point(cx + (x - 0.5) * width * 0.82, -0.57 + (y - 0.5) * 3.05) for x, y in coords
            ]
            return VMobject(color=ink, stroke_width=4).set_points_as_corners(vertices)

        shape = path(stroke.points, color)
        n = len(data.strokes)
        objects.add(
            text(
                stroke.label,
                cx + width * ((i + 0.5) / n - 0.5) * 0.88,
                1.5,
                19,
                width * 0.80 / n,
                color,
            )
        )
        if stroke.target:
            objects.add(shape)
            changes.append(Transform(shape, path(stroke.target, color)))
        else:
            tracer = Dot(shape.get_start(), radius=0.065, color=color)
            objects.add(tracer)
            changes.append(AnimationGroup(Create(shape), MoveAlongPath(tracer, shape)))
    return objects, AnimationGroup(*changes, lag_ratio=0.14)


def compose(panel: Panel, cx: float, width: float) -> tuple[VGroup, Animation]:
    if isinstance(panel, Bars):
        return chart_bars(panel, cx, width)
    if isinstance(panel, Network):
        return chart_network(panel, cx, width)
    if isinstance(panel, Lines):
        return chart_lines(panel, cx, width)
    if isinstance(panel, Drawing):
        return chart_drawing(panel, cx, width)
    if isinstance(panel, Diagram):
        return chart_diagram(panel, cx, width)
    raise ValueError("Render a full-canvas illustration with its own timeline.")
