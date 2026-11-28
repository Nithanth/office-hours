"""Render a validated paper film with Manim's Cairo renderer and word-aligned audio."""

import math
import subprocess
import sys
import tempfile
from pathlib import Path

from manim import AnimationGroup, FadeIn, Line, Scene, VGroup, tempconfig
from PIL import Image

from services.lessons.direction import FilmPlan, Illustration, ManimScene, SampledField
from services.lessons.field import animate_field
from services.lessons.graphics import BACKGROUND, GRID, MINT, MUTED, compose, point, text
from services.lessons.illustration import stage
from services.lessons.speech import Speech, record

FPS = 24


class PaperScene(Scene):
    def __init__(self, section: ManimScene, speech: Speech, seconds: float) -> None:
        super().__init__()
        self.section = section
        self.speech = speech
        self.seconds = seconds

    def construct(self) -> None:
        scene = self.section
        if isinstance(scene.panels[0], (Illustration, SampledField)):
            panel = scene.panels[0]
            heading = text(panel.heading, 0, 3.24, 34, 12.5, serif=True)
            heading.shift(point(-6.25 - heading.get_left()[0], 0))
            self.add(heading, text(scene.source, 0, -3.58, 16, 12.5, MUTED))
            if isinstance(panel, SampledField):
                animate_field(self, panel, self.speech, scene.narration, self.seconds)
            else:
                stage(self, panel, self.speech, scene.narration, self.seconds)
            return
        self.add(text(scene.title, 0, 2.98, 37, 12.95, serif=True))
        self.add(Line(point(-6.46, 2.43), point(6.46, 2.43), color=GRID, stroke_width=1))
        self.add(
            Line(point(-6.46, -2.85), point(6.46, -2.85), color=GRID, stroke_width=1),
            text(scene.source, 0, -3.25, 17, 12.3, MUTED),
        )
        visuals = []
        motions = []
        count = len(scene.panels)
        for i, panel in enumerate(scene.panels):
            width = 12.65 / count
            cx = (i - (count - 1) / 2) * width
            self.add(text(panel.heading, cx, 1.97, 24, width * 0.90, MINT))
            objects, motion = compose(panel, cx, width)
            visuals.append(objects)
            motions.append(motion)
        if count == 2:
            self.add(Line(point(0, 2.12), point(0, -2.46), color=GRID, stroke_width=1))
        opening = min(0.85, self.seconds * 0.15)
        self.play(FadeIn(VGroup(*visuals)), run_time=opening)
        cue = min(
            max(self.speech.cue_time(scene.narration, scene.cue), opening), self.seconds * 0.82
        )
        if cue - opening >= 1 / FPS:
            self.wait(cue - opening)
        motion_time = min(max(1.1, self.seconds * 0.18), 4.0, self.seconds - cue)
        self.play(AnimationGroup(*motions, lag_ratio=0.12), run_time=motion_time)
        hold = self.seconds - self.time
        if hold >= 1 / FPS:
            self.wait(hold)


def stamp(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def encode_film(sections: list[ManimScene], dest: Path) -> None:
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        work = Path(tmp)
        clips = []
        captions: list[str] = []
        elapsed = 0.0
        for i, section in enumerate(sections):
            directory = work / str(i)
            directory.mkdir()
            speech = record(section.narration, directory)
            frames = math.ceil((speech.duration + 0.45) * FPS)
            seconds = frames / FPS
            with tempconfig(
                {
                    "pixel_width": 1280,
                    "pixel_height": 720,
                    "frame_rate": FPS,
                    "renderer": "cairo",
                    "media_dir": str(directory),
                    "output_file": "scene",
                    "disable_caching": True,
                    "write_to_movie": True,
                    "progress_bar": "none",
                    "verbosity": "WARNING",
                    "background_color": BACKGROUND,
                }
            ):
                scene = PaperScene(section, speech, seconds)
                scene.render()
                movie = scene.renderer.file_writer.movie_file_path
            clip = f"scene-{i}.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(movie),
                    "-i",
                    str(speech.wav),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-af",
                    "apad",
                    "-t",
                    f"{seconds:.5f}",
                    str(work / clip),
                ],
                check=True,
                timeout=90,
            )
            clips.append(f"file '{clip}'")
            for j in range(0, len(speech.words), 8):
                chunk = speech.words[j : j + 8]
                captions.append(
                    f"{len(captions) + 1}\n{stamp(elapsed + chunk[0][1])} --> {stamp(elapsed + chunk[-1][2])}\n"
                    + " ".join(w for w, _, _ in chunk)
                )
            elapsed += seconds
        (work / "clips.txt").write_text("\n".join(clips))
        (work / "words.srt").write_text("\n\n".join(captions) + "\n")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "concat",
                "-i",
                "clips.txt",
                "-i",
                "words.srt",
                "-map",
                "0:v",
                "-map",
                "0:a",
                "-map",
                "1:s",
                "-c",
                "copy",
                "-c:s",
                "mov_text",
                "-metadata:s:s:0",
                "language=eng",
                "-movflags",
                "+faststart",
                "film.mp4",
            ],
            cwd=work,
            check=True,
            timeout=90,
        )
        (work / "film.mp4").replace(dest / "lesson.mp4")


def draw_review_frames(directory: Path) -> None:
    """Draw both states with the same scene and geometry code as the final film."""
    board = FilmPlan.model_validate_json((directory / "plan.json").read_bytes())
    sheet = Image.new("RGB", (1440, 270 * len(board.scenes)))
    for i, section in enumerate(board.scenes):
        with tempconfig(
            {
                "pixel_width": 480,
                "pixel_height": 270,
                "frame_rate": 8,
                "media_dir": str(directory),
                "output_file": f"draft-{i}",
                "renderer": "cairo",
                "disable_caching": True,
                "write_to_movie": True,
                "progress_bar": "none",
                "verbosity": "WARNING",
                "background_color": BACKGROUND,
            }
        ):
            draft = PaperScene(section, Speech(directory / "unused.wav", 2.5, []), 3.0)
            draft.render()
            movie = draft.renderer.file_writer.movie_file_path
        for j, when in enumerate(("0.5", "1.5", "2.75")):
            frame = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    when,
                    "-i",
                    str(movie),
                    "-frames:v",
                    "1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "pipe:1",
                ],
                timeout=15,
            )
            sheet.paste(Image.frombytes("RGB", (480, 270), frame), (480 * j, 270 * i))
    sheet.save(directory / "review.jpg", quality=83)


if __name__ == "__main__":
    draw_review_frames(Path(sys.argv[1]))
