"""On-screen text extraction: sample frames and OCR them.

Frames are piped through ffmpeg into memory instead of being written to disk,
because bulk temp-file cleanup trips the sandbox delete guard.
"""

import re
import subprocess

import cv2
import numpy as np

_OCR = None
_PUNCT = re.compile(r"[\s，。,.、；;：:！!？?\"'“”‘’()（）\[\]【】\-—–~·]+")


def _get_ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR = RapidOCR()
    return _OCR


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


def _frame_at(video: str, seconds: float):
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{seconds:.2f}",
                "-i",
                video,
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-",
            ],
            capture_output=True,
            timeout=60,
        )
    except Exception:
        return None
    if not proc.stdout:
        return None
    buffer = np.frombuffer(proc.stdout, np.uint8)
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def _key(text: str) -> str:
    return _PUNCT.sub("", text)


def extract_text(video: str, max_frames: int = 100, min_score: float = 0.55) -> dict[str, str]:
    """Split recognised text into bottom-area captions and other on-screen text."""
    ocr = _get_ocr()
    duration = _duration(video) or 0.0
    if duration <= 0:
        return {"subtitle": "", "other": ""}

    interval = 2.2
    if duration / interval > max_frames:
        interval = duration / max_frames

    seen: set[str] = set()
    subtitle: list[str] = []
    other: list[str] = []
    moment = 0.0
    while moment <= duration:
        frame = _frame_at(video, moment)
        moment += interval
        if frame is None:
            continue
        height = frame.shape[0] or 1
        result, _ = ocr(frame)
        for item in result or []:
            try:
                text = str(item[1]).strip()
                score = float(item[2])
                box = item[0]
                center_y = sum(point[1] for point in box) / len(box)
            except (IndexError, TypeError, ValueError):
                continue
            if score < min_score:
                continue
            key = _key(text)
            if len(key) < 2 or key in seen:
                continue
            if any(len(existing) > 6 and (key in existing or existing in key) for existing in seen):
                continue
            seen.add(key)
            is_caption = center_y / height >= 0.78 and len(key) >= 4
            bucket = subtitle if is_caption else other
            bucket.append(text)
            if len(seen) >= 90:
                break
        if len(seen) >= 90:
            break

    return {"subtitle": "\n".join(subtitle), "other": "\n".join(other)}
