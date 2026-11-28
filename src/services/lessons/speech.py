"""Record one scene and retain word timings for captions and animation cues."""

import base64
import json
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from config import config


@dataclass
class Speech:
    wav: Path
    duration: float
    words: list[tuple[str, float, float]]

    def cue_time(self, narration: str, cue: str) -> float:
        offset = narration.casefold().find(cue.casefold()) if cue.strip() else -1
        if offset >= 0:
            index = len(re.findall(r"\S+", narration[:offset]))
            if index < len(self.words):
                return self.words[index][1]
        return self.duration * 0.43


def record(narration: str, directory: Path) -> Speech:
    wav = directory / "speech.wav"
    words = []
    key = config.ELEVENLABS_API_KEY
    if not key and config.ELEVENLABS_API_KEY_FILE:
        key = Path(config.ELEVENLABS_API_KEY_FILE).read_text().strip()
    if key:
        request = Request(
            "https://api.elevenlabs.io/v1/text-to-speech/nPczCjzI2devNBz1zQrb/with-timestamps",
            data=json.dumps({"text": narration, "model_id": "eleven_multilingual_v2"}).encode(),
            headers={"xi-api-key": key, "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=65) as response:
            data = json.load(response)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", "pipe:0", "-ar", "44100", "-ac", "1", str(wav)],
            input=base64.b64decode(data["audio_base64"]),
            check=True,
            timeout=30,
        )
        alignment = data.get("alignment")
        if alignment:
            for word in re.finditer(r"\S+", "".join(alignment["characters"])):
                words.append(
                    (
                        word.group(),
                        alignment["character_start_times_seconds"][word.start()],
                        alignment["character_end_times_seconds"][word.end() - 1],
                    )
                )
    else:
        (directory / "speech.txt").write_text(narration)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "flite=textfile=speech.txt:voice=slt",
                "-ar",
                "44100",
                "-ac",
                "1",
                str(wav),
            ],
            cwd=directory,
            check=True,
            timeout=30,
        )
    with wave.open(str(wav)) as stream:
        duration = stream.getnframes() / stream.getframerate()
    if not words:
        split = narration.split()
        words = [
            (w, duration * i / len(split), duration * (i + 1) / len(split))
            for i, w in enumerate(split)
        ]
    return Speech(wav, duration, words)
