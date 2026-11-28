"""Stage a director-authored illustration on a shared canvas, timed to spoken phrases."""

import textwrap

import numpy as np
from manim import (
    Animation,
    AnimationGroup,
    Circle,
    Create,
    Dot,
    FadeIn,
    FadeOut,
    Indicate,
    LaggedStart,
    Line,
    ReplacementTransform,
    Scene,
    ShowPassingFlash,
    VGroup,
    VMobject,
)

from services.lessons.direction import IllustratedObject, Illustration
from services.lessons.graphics import BACKGROUND, BLUE, GOLD, MINT, MUTED, PAPER, text
from services.lessons.speech import Speech

INK = {"mint": MINT, "gold": GOLD, "blue": BLUE, "coral": "#F58F80", "paper": PAPER, "muted": MUTED}


def position(xy: tuple[float, float]) -> np.ndarray:
    return np.array([(xy[0] - 0.5) * 12.4, (xy[1] - 0.5) * 5.05 - 0.03, 0.0])


def caption(value: str, x: float, y: float, size: int, width: float, color: str) -> VMobject:
    label: VMobject = text(textwrap.fill(value, width=23), x, y, size, width, color)
    # Keep annotations inside the canvas even when their anchor sits on a boundary.
    label.shift(
        np.array(
            [max(0.0, -6.7 - label.get_left()[0]) - max(0.0, label.get_right()[0] - 6.7), 0, 0]
        )
    )
    return label


def glyph(obj: IllustratedObject, nodes: dict[str, np.ndarray]) -> VMobject:
    ink = INK[obj.color]
    pts = [position(p) for p in obj.points]
    if obj.kind == "wire":
        assert obj.between
        a, b = obj.between
        return Line(nodes[a], nodes[b], color=ink, stroke_width=3, stroke_opacity=0.65).set_z_index(
            1
        )
    if obj.kind == "label":
        label = caption(obj.label, pts[0][0], pts[0][1], 28, 3.4, ink)
        return label.set_z_index(8)
    if obj.kind == "node":
        x, y = pts[0][:2]
        group = VGroup(
            Circle(
                radius=0.22, color=ink, stroke_width=2.5, fill_color=BACKGROUND, fill_opacity=1
            ).move_to(pts[0]),
            Dot(pts[0], radius=0.10, color=ink),
        )
        if obj.label:
            name = caption(obj.label, x, y - 0.44, 22, 2.3, ink)
            name.add_background_rectangle(color=BACKGROUND, opacity=0.94, buff=0.04)
            group.add(name)
        return group.set_z_index(6)
    path = VMobject(color=ink, stroke_width=4)
    if obj.kind == "region":
        return path.set_points_as_corners([*pts, pts[0]]).set_fill(ink, opacity=0.22)
    return path.set_points_as_corners(pts).set_z_index(2)


def cue_times(cues: list[str], speech: Speech, narration: str, seconds: float) -> list[float]:
    times: list[float] = []
    for i, cue in enumerate(cues):
        offset = narration.casefold().find(cue.casefold())
        if offset >= 0:
            t = (
                speech.cue_time(narration, cue)
                if speech.words
                else seconds * offset / max(len(narration), 1)
            )
        else:
            t = seconds * (i + 0.5) / len(cues)
        gap = min(1.1, seconds / (len(cues) * 2))
        times.append(min(max(t, times[-1] + gap if times else 0), seconds - gap * (len(cues) - i)))
    return times


def stage(
    scene: Scene, plate: Illustration, speech: Speech, narration: str, seconds: float
) -> None:
    nodes = {o.id: position(o.points[0]) for o in plate.objects if o.kind == "node"}
    objects = {o.id: glyph(o, nodes) for o in plate.objects}
    active = {o.id for o in plate.objects if o.visible}

    def visible() -> set[str]:
        return {
            o.id
            for o in plate.objects
            if o.id in active and (o.kind != "wire" or set(o.between or ()) <= active)
        }

    on_stage = visible()
    frame = 1 / float(scene.camera.frame_rate)
    # Unmatched or edited phrases keep their sequence using a proportional time slot.
    times = cue_times([beat.cue for beat in plate.beats], speech, narration, seconds)
    scene.add(*(objects[k] for k in objects if k in on_stage))
    for i, beat in enumerate(plate.beats):
        if times[i] - scene.time >= frame:
            scene.wait(times[i] - scene.time)
        active.difference_update(beat.hide)
        active.update(beat.show)
        if beat.morph:
            active.discard(beat.morph[0])
            active.add(beat.morph[1])
        following = visible()
        entered, exited = following - on_stage, on_stage - following
        motions: list[Animation] = []
        if beat.morph and beat.morph[0] in exited and beat.morph[1] in entered:
            old, new = beat.morph
            motions.append(ReplacementTransform(objects[old], objects[new]))
            exited.remove(old)
            entered.remove(new)
        motions.extend(FadeOut(objects[k]) for k in objects if k in exited)
        for obj in plate.objects:
            if obj.id in entered:
                motions.append(
                    Create(objects[obj.id])
                    if obj.kind in {"wire", "path", "region"}
                    else FadeIn(objects[obj.id])
                )
        motions.extend(
            Indicate(objects[k], color=GOLD, scale_factor=1.14)
            for k in beat.highlight
            if k in following and k not in entered
        )
        trails = [
            ShowPassingFlash(
                objects[k].copy().set_stroke(color=GOLD, width=8, opacity=1).set_z_index(5),
                time_width=0.36,
            )
            for k in beat.flow
            if k in following
        ]
        if trails:
            motions.append(LaggedStart(*trails, lag_ratio=0.36))
        available = (times[i + 1] if i + 1 < len(times) else seconds) - scene.time
        if motions and available >= frame:
            scene.play(AnimationGroup(*motions), run_time=min(available, 3.2 if trails else 1.65))
        on_stage = following
    if seconds - scene.time >= frame:
        scene.wait(seconds - scene.time)
