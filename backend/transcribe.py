"""Local speech-to-text for downloaded videos (faster-whisper, CPU)."""

import os

# Must be set before faster-whisper pulls the model from Hugging Face.
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

_MODEL = None
_MODEL_NAME = "small"


def _get_model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel

        _MODEL = WhisperModel(_MODEL_NAME, device="cpu", compute_type="int8")
    return _MODEL


def transcribe_file(path: str) -> str:
    model = _get_model()
    segments, _ = model.transcribe(
        path,
        language="zh",
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    lines: list[str] = []
    for segment in segments:
        text = (segment.text or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines)
