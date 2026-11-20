from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from bot.config import Settings

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class DownloadError(Exception):
    """User-friendly download failure."""


@dataclass
class DownloadedItem:
    file_path: Path
    kind: str  # "video" or "photo"
    title: str
    thumbnail_path: Path | None = None
    width: int = 0
    height: int = 0
    duration: int = 0


@dataclass
class DownloadResult:
    job_dir: Path
    title: str
    webpage_url: str
    items: list[DownloadedItem] = field(default_factory=list)


def _find_thumbnail_for(media_path: Path) -> Path | None:
    stem = media_path.stem
    parent = media_path.parent
    for ext in (".jpg", ".jpeg", ".webp", ".png"):
        candidate = parent / f"{stem}{ext}"
        if candidate.is_file() and candidate != media_path:
            return candidate
    return None


def _build_format_selector(max_height: int | None, max_bytes: int | None = None) -> str:
    height_filter = f"[height<={max_height}]" if max_height else ""
    if max_bytes is None:
        return (
            f"bv*{height_filter}+ba/"
            f"b{height_filter}/"
            "best"
        )
    # Fallback selector constrained by Telegram's 2GB limit
    video_budget = int(max_bytes * 0.90)
    audio_budget = int(max_bytes * 0.10)
    return (
        f"bv*{height_filter}[filesize<{video_budget}]+ba[filesize<{audio_budget}]/"
        f"bv*{height_filter}[filesize_approx<{video_budget}]+ba/"
        f"b{height_filter}[filesize<{max_bytes}]/"
        f"b[filesize_approx<{max_bytes}]/"
        "best"
    )


def _build_ydl_options(
    job_dir: Path,
    settings: Settings,
    platform: str,
    progress_hook: Callable[[dict[str, Any]], None],
    postprocessor_hook: Callable[[dict[str, Any]], None],
    constrain_filesize: bool = False,
) -> dict[str, Any]:
    format_sort = (
        ["vcodec:h264", "acodec:aac", "res", "fps", "ext:mp4:m4a"]
        if settings.prefer_compatible_codec
        else ["res", "fps", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]
    )

    max_bytes = settings.max_file_size_bytes if constrain_filesize else None
    opts: dict[str, Any] = {
        "outtmpl": str(job_dir / "%(autonumber)03d_%(id)s.%(ext)s"),
        "format": _build_format_selector(settings.max_video_height, max_bytes=max_bytes),
        "format_sort": format_sort,
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": settings.concurrent_fragments,
        "http_chunk_size": 10 * 1024 * 1024,  # 10 MB chunks reduce YouTube throttling
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "noplaylist": platform == "youtube",  # Avoid downloading huge YouTube playlists accidentally
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "windowsfilenames": True,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "postprocessor_args": {
            "merger": ["-movflags", "+faststart"],
        },
        "remote_components": ["ejs:github"],
    }

    # Enable Node.js as yt-dlp JS runtime for YouTube EJS challenges when installed
    if shutil.which("node"):
        opts["js_runtimes"] = {"node": {}}

    if settings.cookies_file and settings.cookies_file.is_file():
        opts["cookiefile"] = str(settings.cookies_file)

    return opts


def _collect_items_from_info(info: dict[str, Any], job_dir: Path) -> list[DownloadedItem]:
    items: list[DownloadedItem] = []
    seen_paths: set[Path] = set()

    entries = info.get("entries")
    raw_entries = [e for e in entries if e] if isinstance(entries, list) else [info]

    for entry in raw_entries:
        candidate_paths: list[Path] = []
        for req in entry.get("requested_downloads") or []:
            fp = req.get("filepath")
            if fp:
                candidate_paths.append(Path(fp))
        if entry.get("_filename"):
            candidate_paths.append(Path(entry["_filename"]))

        resolved_path: Path | None = None
        for p in candidate_paths:
            if p.is_file():
                resolved_path = p
                break
            mp4_alt = p.with_suffix(".mp4")
            if mp4_alt.is_file():
                resolved_path = mp4_alt
                break

        if not resolved_path or resolved_path in seen_paths:
            continue

        ext = resolved_path.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            kind = "video"
        elif ext in IMAGE_EXTENSIONS:
            kind = "photo"
        else:
            continue

        seen_paths.add(resolved_path)
        items.append(
            DownloadedItem(
                file_path=resolved_path,
                kind=kind,
                title=(entry.get("title") or info.get("title") or "").strip(),
                thumbnail_path=_find_thumbnail_for(resolved_path) if kind == "video" else None,
                width=int(entry.get("width") or 0),
                height=int(entry.get("height") or 0),
                duration=int(round(float(entry.get("duration") or 0))),
            )
        )

    # Fallback scan in job_dir if yt-dlp didn't populate requested_downloads
    if not items:
        for file_path in sorted(job_dir.iterdir()):
            if not file_path.is_file():
                continue
            ext = file_path.suffix.lower()
            if ext in VIDEO_EXTENSIONS:
                items.append(
                    DownloadedItem(
                        file_path=file_path,
                        kind="video",
                        title=(info.get("title") or "").strip(),
                        thumbnail_path=_find_thumbnail_for(file_path),
                    )
                )

    return items


def _friendly_error_message(raw_error: str, platform: str) -> str:
    lower = raw_error.lower()
    if "confirm your age" in lower or "age-restricted" in lower or "age restricted" in lower:
        return "هذا المقطع مقيد بالعمر ويتطلب ملف `cookies.txt`."
    if "private" in lower or "login required" in lower or "sign in" in lower or "log into" in lower:
        return (
            "هذا المحتوى خاص أو يتطلب تسجيل دخول (كوكيز).\n"
            "يمكنك إضافة ملف `cookies.txt` في مجلد البوت لتحميل المقاطع المقيدة."
        )
    if "unavailable" in lower or "removed" in lower or "not available" in lower:
        return "المقطع غير متاح أو تم حذفه."
    if "copyright" in lower:
        return "لا يمكن تحميل المقطع بسبب قيود حقوق النشر."
    if "live" in lower and "stream" in lower:
        return "لا يمكن تحميل البث المباشر أثناء استمراره."
    if "timed out" in lower or "connection" in lower:
        return "حدث بطء أو انقطاع في الاتصال بالموقع، حاول مرة أخرى."
    return f"تعذر التحميل من {platform}: {raw_error[:220]}"


async def download_media(
    url: str,
    platform: str,
    settings: Settings,
    on_progress: Callable[[int, int, float | None], Any] | None = None,
    on_merging: Callable[[], Any] | None = None,
) -> DownloadResult:
    """Download media in the highest quality using yt-dlp inside a worker thread."""
    job_dir = settings.downloads_dir / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()

    def progress_hook(data: dict[str, Any]) -> None:
        if not on_progress:
            return
        if data.get("status") == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            speed = data.get("speed")
            loop.call_soon_threadsafe(
                asyncio.create_task,
                on_progress(downloaded, total, float(speed) if speed else None),
            )

    def postprocessor_hook(data: dict[str, Any]) -> None:
        if not on_merging:
            return
        if data.get("status") == "started" and data.get("postprocessor") == "Merger":
            loop.call_soon_threadsafe(asyncio.create_task, on_merging())

    def _run_download(constrain_filesize: bool) -> DownloadResult:
        opts = _build_ydl_options(
            job_dir=job_dir,
            settings=settings,
            platform=platform,
            progress_hook=progress_hook,
            postprocessor_hook=postprocessor_hook,
            constrain_filesize=constrain_filesize,
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise DownloadError("لم يتم العثور على وسائط قابلة للتحميل في هذا الرابط.")
            items = _collect_items_from_info(info, job_dir)
            if not items:
                raise DownloadError("لم يتم العثور على فيديو أو صورة صالحة في هذا الرابط.")
            return DownloadResult(
                job_dir=job_dir,
                title=(info.get("title") or "").strip(),
                webpage_url=info.get("webpage_url") or url,
                items=items,
            )

    try:
        result = await asyncio.to_thread(_run_download, False)

        # If any downloaded file exceeds Telegram's 2GB limit, retry with size constraint
        oversized = any(
            item.file_path.stat().st_size > settings.max_file_size_bytes for item in result.items
        )
        if oversized:
            logger.info("Downloaded file exceeded %d MB; retrying with size cap.", settings.max_file_size_mb)
            shutil.rmtree(job_dir, ignore_errors=True)
            job_dir.mkdir(parents=True, exist_ok=True)
            result = await asyncio.to_thread(_run_download, True)

            still_oversized = any(
                item.file_path.stat().st_size > settings.max_file_size_bytes for item in result.items
            )
            if still_oversized:
                raise DownloadError(
                    f"حجم الفيديو يتجاوز الحد الأقصى المسموح به في تيليقرام ({settings.max_file_size_mb} MB)."
                )

        return result
    except DownloadError:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except yt_dlp.utils.DownloadError as exc:
        logger.warning("yt-dlp error (%s) %s: %s", platform, url, exc)
        shutil.rmtree(job_dir, ignore_errors=True)
        raise DownloadError(_friendly_error_message(str(exc), platform)) from exc
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.exception("Unexpected error downloading %s", url)
        raise DownloadError(f"حدث خطأ غير متوقع أثناء التحميل: {exc}") from exc
