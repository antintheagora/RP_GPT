"""Build the local title ambience from deterministic synthesis.

No recording, sample pack, or downloaded melody goes into this file.  Keeping
the small score as code gives the music the same provenance as the rendered UI
plates: anyone can rebuild it, inspect its ingredients, or replace it without
having to trust an unexplained binary.

FFmpeg is used only to compress a temporary PCM wave to Ogg Vorbis::

    .venv/Scripts/python.exe art/generate_theme.py
"""

from __future__ import annotations

import argparse
from array import array
import math
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import wave


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "ui" / "webapp" / "static" / "audio" / "title_theme.ogg"
SAMPLE_RATE = 22_050

# Four slow modal colours.  Frequencies are ordinary equal-tempered pitches;
# the sequence and synthesis are original to this project.
CHORDS = (
    (73.416, 110.000, 146.832, 174.614),   # D, A, D, F
    (58.270, 87.307, 116.541, 146.832),    # Bb, F, Bb, D
    (65.406, 97.999, 130.813, 164.814),    # C, G, C, E
    (55.000, 82.407, 110.000, 146.832),    # A, E, A, D
)
BELL_NOTES = (293.665, 349.228, 440.000, 391.995, 329.628, 293.665)


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _pad_sample(t: float, channel: int) -> float:
    chord_time = 18.0
    position = t / chord_time
    index = int(position) % len(CHORDS)
    blend = _smoothstep((position % 1.0 - 0.72) / 0.28)

    def voice(chord_index: int) -> float:
        chord = CHORDS[chord_index % len(CHORDS)]
        total = 0.0
        for note_index, frequency in enumerate(chord):
            detune = 1.0 + (0.0017 if (note_index + channel) % 2 else -0.0013)
            phase = channel * 0.37 + note_index * 0.61
            fundamental = math.sin(math.tau * frequency * detune * t + phase)
            octave = math.sin(math.tau * frequency * 2.0 * t + phase * 1.7)
            total += (fundamental + octave * 0.16) / (1.0 + note_index * 0.32)
        return total / 3.2

    current = voice(index)
    following = voice(index + 1)
    breath = 0.82 + 0.12 * math.sin(math.tau * t / 11.0 + channel * 1.1)
    return ((1.0 - blend) * current + blend * following) * breath


def _bell_sample(t: float, channel: int) -> float:
    spacing = 12.0
    bell_index = int(t // spacing)
    age = t - bell_index * spacing
    if age < 1.1 or age > 7.0:
        return 0.0
    age -= 1.1
    frequency = BELL_NOTES[bell_index % len(BELL_NOTES)]
    envelope = math.exp(-age * 0.72) * min(1.0, age * 8.0)
    pan = 0.72 if bell_index % 2 == channel else 0.38
    tone = math.sin(math.tau * frequency * age)
    shimmer = math.sin(math.tau * frequency * 2.007 * age + 0.4) * 0.31
    upper = math.sin(math.tau * frequency * 3.99 * age) * 0.08
    return (tone + shimmer + upper) * envelope * pan


def _write_wave(path: Path, duration: float) -> None:
    rng = random.Random(0xA5FA11)
    frames = max(1, int(duration * SAMPLE_RATE))
    wind = [0.0, 0.0]
    chunk = array("h")

    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)

        for frame in range(frames):
            t = frame / SAMPLE_RATE
            fade_in = _smoothstep(t / 4.0)
            fade_out = _smoothstep((duration - t) / 6.0)
            master = fade_in * fade_out

            for channel in range(2):
                # A very low, independently filtered noise floor reads as air
                # between notes without importing a field recording.
                wind[channel] = wind[channel] * 0.9975 + rng.uniform(-1.0, 1.0) * 0.0025
                sample = (
                    _pad_sample(t, channel) * 0.24
                    + _bell_sample(t, channel) * 0.14
                    + wind[channel] * 0.035
                ) * master
                chunk.append(int(max(-1.0, min(1.0, sample)) * 32767))

            if len(chunk) >= 16_384:
                output.writeframes(chunk.tobytes())
                chunk = array("h")

        if chunk:
            output.writeframes(chunk.tobytes())


def build(output: Path = DEFAULT_OUTPUT, duration: float = 96.0) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to encode the generated title theme.")
    if duration < 12:
        raise ValueError("The title ambience must be at least 12 seconds long.")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rp-gpt-theme-") as temporary:
        wave_path = Path(temporary) / "theme.wav"
        _write_wave(wave_path, duration)
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wave_path),
                "-c:a",
                "libvorbis",
                "-q:a",
                "4",
                # Apply bit-exactness to the *output*.  Without this, FFmpeg's
                # Ogg muxer chooses a fresh random stream serial on every run
                # even though the generated PCM and encoded packets match.
                "-flags:a",
                "+bitexact",
                "-fflags",
                "+bitexact",
                "-metadata",
                "title=Ashfall at the Gate",
                "-metadata",
                "artist=RP-GPT",
                "-metadata",
                "comment=Deterministic procedural synthesis; generated by art/generate_theme.py",
                str(output),
            ],
            check=True,
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--duration", type=float, default=96.0)
    args = parser.parse_args()
    built = build(args.output, args.duration)
    print(built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
