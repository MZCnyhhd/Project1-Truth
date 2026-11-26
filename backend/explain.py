"""Explain a video by extracting text with a clear priority.

Priority: burned-in captions (OCR) first, then speech (ASR). A video with
neither is an explicit failure rather than an empty string, so the UI can say
why instead of showing a blank box.
"""

import subprocess

from . import ocr, transcribe

_MIN_CHARS = 8


class ExplainError(Exception):
    pass


def _duration(video: str) -> float:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                video,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def _looks_like_hallucination(text: str, duration: float) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    # Whisper repeats a memorised phrase when it hears music instead of speech.
    if len(set(lines)) == 1 and len(lines) > 2:
        return True
    if duration > 60 and len("".join(lines)) < 30:
        return True
    return False


def explain_video(path: str) -> dict:
    duration = _duration(path)

    screen = {"subtitle": "", "other": ""}
    try:
        screen = ocr.extract_text(path)
    except Exception:
        screen = {"subtitle": "", "other": ""}

    subtitle = (screen.get("subtitle") or "").strip()
    other = (screen.get("other") or "").strip()

    if len(subtitle) >= _MIN_CHARS:
        return {
            "text": subtitle,
            "source": "subtitle",
            "duration": duration,
            "screen_text": subtitle,
            "speech_text": "",
        }

    if len(other) >= _MIN_CHARS:
        return {
            "text": other,
            "source": "screen",
            "duration": duration,
            "screen_text": other,
            "speech_text": "",
        }

    speech = ""
    try:
        speech = transcribe.transcribe_file(path)
    except Exception:
        speech = ""
    if _looks_like_hallucination(speech, duration):
        speech = ""
    speech = speech.strip()

    if len(speech) >= _MIN_CHARS:
        return {
            "text": speech,
            "source": "speech",
            "duration": duration,
            "screen_text": "",
            "speech_text": speech,
        }

    raise ExplainError("提取失败：无字幕无语音")
