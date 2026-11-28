"""Isolated CPU worker for a single validated storyboard."""

import math
import subprocess
import sys
import textwrap
import wave
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from services.lessons.direction import ManimScene
from services.lessons.storyboard import Board, Scene

INK, MUTED, ACCENT = "#f6f3e8", "#b3c1c7", "#ffa965"
FPS = 12


@lru_cache(maxsize=16)
def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype("DejaVuSans.ttf", size)


def network(draw: ImageDraw.ImageDraw, scene: Scene, progress: float) -> None:
    layers = []
    unit = 1
    for i, (label, size) in enumerate(zip(scene.labels, scene.values, strict=True)):
        x = 150 + i * 660 / (len(scene.labels) - 1)
        layer = []
        for j in range(int(size)):
            y = 235 + (j - (size - 1) / 2) * 31
            active = progress < 0.4 or unit not in scene.dropped
            layer.append((x, y, active))
            unit += 1
        layers.append(layer)
        draw.multiline_text(
            (x, 328), textwrap.fill(label, 17), font=font(16), fill=INK, anchor="mm", align="center"
        )
    for left, right in zip(layers[:-1], layers[1:], strict=True):
        for x, y, active in left:
            for x2, y2, active2 in right:
                if active and active2:
                    draw.line((x, y, x2, y2), fill="#698a95", width=2)
    for layer in layers:
        for x, y, active in layer:
            draw.ellipse(
                (x - 10, y - 10, x + 10, y + 10),
                fill=ACCENT if active else "#12212b",
                outline=MUTED,
            )
            if not active:
                draw.line((x - 5, y - 5, x + 5, y + 5), fill=MUTED, width=2)
                draw.line((x - 5, y + 5, x + 5, y - 5), fill=MUTED, width=2)


def frame(scene: Scene, index: int, progress: float, caption: str) -> Image.Image:
    image = Image.new("RGB", (960, 540), "#12212b")
    draw = ImageDraw.Draw(image)

    def write(
        x: float, y: float, value: str, size: int, color: str = INK, anchor: str = "la"
    ) -> None:
        draw.text((x, y), value, fill=color, font=font(size), anchor=anchor)

    write(40, 24, f"OFFICE HOURS   /   SCENE {index + 1}", 15, ACCENT)
    title = textwrap.fill(scene.title, 44)
    write(40, 57, title, 30)
    if scene.kind == "network":
        network(draw, scene, progress)
    elif scene.kind == "comparison":
        for j, (label, value) in enumerate(zip(scene.labels, scene.values, strict=True)):
            y = 173 + j * 60
            write(42, y, label, 18)
            length = 400 * value / max(scene.values) * min(1, progress * 3)
            draw.rectangle((462, y, 462 + length, y + 20), fill=ACCENT)
            write(900, y + 30, f"{value:g}", 16, MUTED, "ra")
    else:
        count = len(scene.labels)
        for j, label in enumerate(scene.labels):
            left = 42 + j * (884 // count)
            width = 884 // count - 20
            revealed = j == 0 or progress >= j / count
            draw.rounded_rectangle(
                (left, 178, left + width, 310),
                radius=12,
                outline=ACCENT if revealed else "#415661",
                width=2,
            )
            write(
                left + width / 2,
                246,
                textwrap.fill(label, 18) if revealed else "…",
                22,
                INK if revealed else MUTED,
                "mm",
            )
    write(40, 367, textwrap.fill(scene.source, 85), 16, MUTED)
    draw.line((40, 420, 920, 420), fill="#475760")
    write(480, 477, textwrap.fill(caption, 66), 23, INK, "mm")
    return image


def encode(board: Board, dest: Path) -> None:
    directed = [s for s in board.scenes if isinstance(s, ManimScene)]
    if directed:
        from services.lessons.film import encode_film

        encode_film(directed, dest)
        return
    playlist = []
    for i, scene in enumerate(board.scenes):
        assert isinstance(scene, Scene)
        # Only the controlled filename enters the FFmpeg filter expression.
        (dest / "speech.txt").write_text(scene.narration)
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "flite=textfile=speech.txt:voice=slt",
                "voice.wav",
            ],
            cwd=dest,
            check=True,
        )
        with wave.open(str(dest / "voice.wav")) as speech:
            duration = speech.getnframes() / speech.getframerate()
        count = math.ceil((duration + 0.7) * FPS)
        words = scene.narration.split()
        name = f"scene-{i}.mp4"
        args = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "960x540",
            "-r",
            str(FPS),
            "-i",
            "-",
            "-i",
            "voice.wav",
            "-af",
            "apad",
            "-shortest",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            name,
        ]
        with subprocess.Popen(args, cwd=dest, stdin=subprocess.PIPE) as proc:
            assert proc.stdin
            for k in range(count):
                word = min(len(words) - 1, int(k / FPS / duration * len(words)))
                start = word // 9 * 9
                picture = frame(scene, i, k / count, " ".join(words[start : start + 9]))
                proc.stdin.write(picture.tobytes())
            proc.stdin.close()
            if proc.wait():
                raise RuntimeError("Video encoding failed")
        playlist.append(f"file '{name}'")
    (dest / "clips.txt").write_text("\n".join(playlist))
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-i",
            "clips.txt",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "lesson.tmp.mp4",
        ],
        cwd=dest,
        check=True,
    )
    (dest / "lesson.tmp.mp4").replace(dest / "lesson.mp4")


def main(dest: Path) -> None:
    try:
        encode(Board.model_validate_json((dest / "board.json").read_text()), dest)
    except Exception:
        (dest / "failed").touch()
        raise
    finally:
        for pattern in ("scene-*.mp4", "*.wav", "*.txt", "*.tmp.mp4"):
            for path in dest.glob(pattern):
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
