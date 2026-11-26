"""On-screen text extraction: sample frames and OCR them.

Frames are piped through ffmpeg into memory instead of being written to disk,
because bulk temp-file cleanup trips the sandbox delete guard.
"""

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

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


def _frames(video: str, interval: float) -> list:
    """Sample the whole clip in one ffmpeg pass and decode every frame in memory."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                video,
                "-vf",
                f"fps=1/{interval:.4f},scale=720:-2",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-",
            ],
            capture_output=True,
            timeout=900,
        )
    except Exception:
        return []

    data = proc.stdout or b""
    frames = []
    position = 0
    while True:
        start = data.find(b"\xff\xd8", position)
        if start < 0:
            break
        end = data.find(b"\xff\xd9", start)
        if end < 0:
            break
        frame = cv2.imdecode(np.frombuffer(data[start : end + 2], np.uint8), cv2.IMREAD_COLOR)
        if frame is not None:
            frames.append(frame)
        position = end + 2
    return frames


def _key(text: str) -> str:
    return _PUNCT.sub("", text)


def _scan(
    frames: list,
    caption_only: bool,
    min_score: float,
) -> tuple[list[str], list[str]]:
    ocr = _get_ocr()

    targets: list[tuple] = []
    for frame in frames:
        if caption_only:
            # Crop to the bottom band: far fewer boxes and less work per frame.
            target = frame[int(frame.shape[0] * 0.68) :, :]
            if target.size == 0:
                continue
        else:
            target = frame
        targets.append((frame, target))

    def recognise(pair: tuple):
        frame, target = pair
        result, _ = ocr(target, use_cls=False)
        return frame, result

    # OCR is CPU bound and single-frame work dominates; fan out across cores
    # while keeping the results in playback order.
    workers = max(1, min(4, (os.cpu_count() or 2)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outputs = list(pool.map(recognise, targets))

    seen: set[str] = set()
    subtitle: list[str] = []
    other: list[str] = []

    for frame, result in outputs:
        height = frame.shape[0] or 1
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

            # Captions must cover the whole clip, so never stop scanning early.
            # Non-caption text is only context, so cap it to keep noise down.
            if caption_only:
                subtitle.append(text)
            elif center_y / height >= 0.78 and len(key) >= 4:
                subtitle.append(text)
            elif len(other) < 40:
                other.append(text)

    return subtitle, other


def extract_text(video: str, max_frames: int = 100, min_score: float = 0.55) -> dict[str, str]:
    """Captions first; only fall back to a full-frame pass when there are none."""
    duration = _duration(video) or 0.0
    if duration <= 0:
        return {"subtitle": "", "other": ""}

    interval = 2.8
    if duration / interval > max_frames:
        interval = duration / max_frames

    frames = _frames(video, interval)
    if not frames:
        return {"subtitle": "", "other": ""}

    subtitle, _ = _scan(frames, caption_only=True, min_score=min_score)
    if len("".join(subtitle).strip()) >= 8:
        return {"subtitle": "\n".join(subtitle), "other": ""}

    subtitle, other = _scan(frames, caption_only=False, min_score=min_score)
    return {"subtitle": "\n".join(subtitle), "other": "\n".join(other)}
