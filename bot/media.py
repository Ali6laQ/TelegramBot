from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

TELEGRAM_THUMB_MAX_SIDE = 320
TELEGRAM_THUMB_MAX_BYTES = 200 * 1024


@dataclass(frozen=True)
class VideoMetadata:
    width: int = 0
    height: int = 0
    duration: int = 0


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def probe_video(video_path: Path) -> VideoMetadata:
    """Extract accurate width, height (accounting for rotation), and duration via ffprobe."""
    if not shutil.which("ffprobe"):
        return VideoMetadata()

    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(video_path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0 or not stdout:
            return VideoMetadata()

        data = json.loads(stdout.decode("utf-8", errors="ignore"))
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            None,
        )
        if not video_stream:
            return VideoMetadata()

        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)

        # Handle rotation metadata on vertical mobile videos
        rotation = 0
        tags = video_stream.get("tags") or {}
        if "rotate" in tags:
            try:
                rotation = int(tags["rotate"])
            except (TypeError, ValueError):
                rotation = 0
        else:
            for side_data in video_stream.get("side_data_list") or []:
                if "rotation" in side_data:
                    try:
                        rotation = int(float(side_data["rotation"]))
                        break
                    except (TypeError, ValueError):
                        pass

        if abs(rotation) in (90, 270):
            width, height = height, width

        duration_raw = video_stream.get("duration") or (data.get("format") or {}).get("duration")
        duration = int(round(float(duration_raw))) if duration_raw else 0

        return VideoMetadata(width=width, height=height, duration=max(0, duration))
    except Exception as exc:
        logger.debug("ffprobe failed for %s: %s", video_path, exc)
        return VideoMetadata()


def _convert_image_to_telegram_thumb(source: Path, dest: Path) -> Path | None:
    """Convert any image to a Telegram-compliant JPEG thumbnail (<=320px, <=200KB)."""
    try:
        with Image.open(source) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((TELEGRAM_THUMB_MAX_SIDE, TELEGRAM_THUMB_MAX_SIDE), Image.Resampling.LANCZOS)

            for quality in (88, 75, 60, 45):
                img.save(dest, format="JPEG", quality=quality, optimize=True)
                if dest.stat().st_size <= TELEGRAM_THUMB_MAX_BYTES:
                    return dest
            return dest
    except Exception as exc:
        logger.debug("Thumbnail conversion failed for %s: %s", source, exc)
        return None


async def prepare_thumbnail(
    video_path: Path,
    candidate_thumb: Path | None = None,
    duration: int = 0,
) -> Path | None:
    """Return a valid JPEG thumbnail path for Telegram video uploads."""
    dest = video_path.with_suffix(".tg_thumb.jpg")

    if candidate_thumb and candidate_thumb.is_file():
        converted = await asyncio.to_thread(_convert_image_to_telegram_thumb, candidate_thumb, dest)
        if converted:
            return converted

    if not shutil.which("ffmpeg"):
        return None

    seek_seconds = "00:00:01" if duration >= 2 else "00:00:00"
    raw_frame = video_path.with_suffix(".raw_frame.jpg")

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        seek_seconds,
        "-i",
        str(video_path),
        "-vframes",
        "1",
        "-q:v",
        "3",
        str(raw_frame),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if raw_frame.is_file():
            converted = await asyncio.to_thread(_convert_image_to_telegram_thumb, raw_frame, dest)
            raw_frame.unlink(missing_ok=True)
            return converted
    except Exception as exc:
        logger.debug("FFmpeg thumbnail generation failed for %s: %s", video_path, exc)

    return None
