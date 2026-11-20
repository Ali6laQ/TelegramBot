from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from pyrogram.errors import FloodWait, MessageNotModified

if TYPE_CHECKING:
    from pyrogram.types import Message

logger = logging.getLogger(__name__)


def format_bytes(num_bytes: float) -> str:
    if num_bytes <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB")
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def render_bar(percent: float, length: int = 10) -> str:
    clamped = max(0.0, min(100.0, percent))
    filled = int(round((clamped / 100.0) * length))
    return "█" * filled + "░" * (length - filled)


class ProgressReporter:
    """Rate-limited Telegram status message updater for download and upload progress."""

    def __init__(self, status_msg: Message, platform_label: str, min_interval: float = 2.5) -> None:
        self._msg = status_msg
        self._platform = platform_label
        self._min_interval = min_interval
        self._last_edit_time = 0.0
        self._last_text = ""
        self._stage_start = time.monotonic()
        self._lock = asyncio.Lock()

    def reset_stage(self) -> None:
        self._stage_start = time.monotonic()
        self._last_edit_time = 0.0

    async def set_text(self, text: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_edit_time) < self._min_interval:
            return
        if text == self._last_text:
            return

        async with self._lock:
            now = time.monotonic()
            if not force and (now - self._last_edit_time) < self._min_interval:
                return
            if text == self._last_text:
                return
            try:
                await self._msg.edit_text(text)
                self._last_text = text
                self._last_edit_time = time.monotonic()
            except MessageNotModified:
                self._last_text = text
            except FloodWait as fw:
                self._last_edit_time = time.monotonic() + float(fw.value)
            except Exception as exc:
                logger.debug("Failed to update status message: %s", exc)

    async def report_download(
        self,
        downloaded: int,
        total: int,
        speed: float | None = None,
    ) -> None:
        if total > 0:
            pct = (downloaded / total) * 100.0
            speed_str = f" • ⚡ {format_bytes(speed)}/s" if speed else ""
            text = (
                f"⬇️ **جاري التحميل من {self._platform}...**\n"
                f"`{render_bar(pct)}` **{pct:.0f}%**\n"
                f"📦 {format_bytes(downloaded)} / {format_bytes(total)}{speed_str}"
            )
        else:
            speed_str = f" • ⚡ {format_bytes(speed)}/s" if speed else ""
            text = (
                f"⬇️ **جاري التحميل من {self._platform}...**\n"
                f"📦 {format_bytes(downloaded)}{speed_str}"
            )
        await self.set_text(text)

    async def report_merging(self) -> None:
        await self.set_text(
            f"⚙️ **جاري دمج الفيديو والصوت بأعلى جودة ({self._platform})...**",
            force=True,
        )

    async def report_upload(self, current: int, total: int) -> None:
        elapsed = max(0.1, time.monotonic() - self._stage_start)
        speed = current / elapsed
        pct = (current / total) * 100.0 if total > 0 else 0.0
        text = (
            f"⬆️ **جاري الرفع إلى تيليقرام...**\n"
            f"`{render_bar(pct)}` **{pct:.0f}%**\n"
            f"📦 {format_bytes(current)} / {format_bytes(total)} • ⚡ {format_bytes(speed)}/s"
        )
        await self.set_text(text)
