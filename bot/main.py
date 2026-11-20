from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from pyrogram import Client, enums, filters
from pyrogram.types import InputMediaPhoto, InputMediaVideo, Message

from bot.config import Settings, load_settings
from bot.downloader import DownloadedItem, DownloadError, download_media
from bot.media import has_ffmpeg, prepare_thumbnail, probe_video
from bot.progress import ProgressReporter, format_bytes
from bot.urls import SupportedLink, extract_supported_link

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("bot")


def _cleanup_stale_downloads(downloads_dir: Path) -> None:
    if not downloads_dir.exists():
        return
    for entry in downloads_dir.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except OSError:
            pass


def _trim_caption(title: str, max_len: int = 950) -> str:
    cleaned = " ".join(title.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"


def _check_tgcrypto() -> bool:
    try:
        import tgcrypto  # noqa: F401
        return True
    except ImportError:
        return False


def _check_curl_cffi() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def create_bot(settings: Settings) -> Client:
    semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)
    active_jobs = 0

    app = Client(
        name="downloader_bot",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        workdir=str(settings.downloads_dir.parent),
        workers=8,
        max_concurrent_transmissions=4,
    )

    def _is_authorized(message: Message) -> bool:
        if not settings.allowed_users:
            return True
        user = message.from_user
        return bool(user and user.id in settings.allowed_users)

    @app.on_message(filters.command(["start", "help"]))
    async def cmd_start(_: Client, message: Message) -> None:
        if not _is_authorized(message):
            await message.reply_text("⛔ هذا البوت مخصص لمستخدمين محددين فقط.")
            return

        await message.reply_text(
            "👋 **أهلاً بك في بوت التحميل السريع!**\n\n"
            "أرسل لي أي رابط من:\n"
            "• **يوتيوب** (فيديو / شورتس)\n"
            "• **إنستقرام** (ريلز / بوستات / ألبومات)\n"
            "• **تيك توك** (بدون علامة مائية)\n\n"
            "⚡ **المميزات:**\n"
            "• تحميل تلقائي مباشر بأعلى جودة متاحة\n"
            "• دعم رفع ملفات كبيرة حتى **2 جيجابايت**\n"
            "• عرض حالة وسرعة التحميل والرفع مباشرة\n\n"
            "فقط أرسل الرابط الآن! 🚀"
        )

    @app.on_message(filters.command("status"))
    async def cmd_status(_: Client, message: Message) -> None:
        if not _is_authorized(message):
            return

        ffmpeg_state = "✅ متوفر" if has_ffmpeg() else "❌ غير متوفر"
        node_state = "✅ متوفر" if shutil.which("node") else "⚠️ غير متوفر"
        crypto_state = "✅ TgCrypto مفعّل (سرعة قصوى)" if _check_tgcrypto() else "⚠️ غير مفعّل"
        cffi_state = "✅ مفعّل (لتيك توك)" if _check_curl_cffi() else "⚠️ غير مفعّل"
        cookies_state = "✅ مفعّل" if settings.cookies_file else "➖ غير مضاف"

        await message.reply_text(
            "📊 **حالة البوت:**\n"
            f"• التحميلات النشطة حالياً: `{active_jobs}/{settings.max_concurrent_downloads}`\n"
            f"• الحد الأقصى للملف: `{settings.max_file_size_mb} MB`\n"
            f"• FFmpeg: {ffmpeg_state}\n"
            f"• Node.js (ليوتيوب): {node_state}\n"
            f"• curl_cffi (لتيك توك): {cffi_state}\n"
            f"• تشفير سريع: {crypto_state}\n"
            f"• ملف الكوكيز: {cookies_state}"
        )

    async def _send_single_item(
        message: Message,
        item: DownloadedItem,
        reporter: ProgressReporter,
    ) -> None:
        caption = _trim_caption(item.title) if item.title else ""
        reporter.reset_stage()

        if item.kind == "photo":
            await message.reply_photo(
                photo=str(item.file_path),
                caption=caption,
                parse_mode=enums.ParseMode.DISABLED,
                quote=True,
                progress=reporter.report_upload,
            )
            return

        meta = await probe_video(item.file_path)
        width = meta.width or item.width
        height = meta.height or item.height
        duration = meta.duration or item.duration
        thumb_path = await prepare_thumbnail(item.file_path, item.thumbnail_path, duration=duration)

        await message.reply_video(
            video=str(item.file_path),
            caption=caption,
            parse_mode=enums.ParseMode.DISABLED,
            duration=duration,
            width=width,
            height=height,
            thumb=str(thumb_path) if thumb_path else None,
            supports_streaming=True,
            quote=True,
            progress=reporter.report_upload,
        )

    async def _send_album(
        message: Message,
        items: list[DownloadedItem],
        title: str,
        reporter: ProgressReporter,
    ) -> None:
        await reporter.set_text(
            f"⬆️ **جاري رفع الألبوم ({len(items)} ملفات) إلى تيليقرام...**",
            force=True,
        )

        # Telegram allows up to 10 media items per album
        for chunk_start in range(0, len(items), 10):
            chunk = items[chunk_start : chunk_start + 10]
            media_group: list[InputMediaPhoto | InputMediaVideo] = []

            for idx, item in enumerate(chunk):
                caption = _trim_caption(title) if (chunk_start == 0 and idx == 0 and title) else ""
                if item.kind == "photo":
                    media_group.append(
                        InputMediaPhoto(
                            media=str(item.file_path),
                            caption=caption,
                            parse_mode=enums.ParseMode.DISABLED,
                        )
                    )
                else:
                    meta = await probe_video(item.file_path)
                    width = meta.width or item.width
                    height = meta.height or item.height
                    duration = meta.duration or item.duration
                    thumb_path = await prepare_thumbnail(
                        item.file_path, item.thumbnail_path, duration=duration
                    )
                    media_group.append(
                        InputMediaVideo(
                            media=str(item.file_path),
                            caption=caption,
                            parse_mode=enums.ParseMode.DISABLED,
                            duration=duration,
                            width=width,
                            height=height,
                            thumb=str(thumb_path) if thumb_path else None,
                            supports_streaming=True,
                        )
                    )

            await message.reply_media_group(media=media_group, quote=True)

    async def _process_link(message: Message, link: SupportedLink) -> None:
        nonlocal active_jobs

        queued_notice = semaphore.locked()
        initial_text = (
            f"⏳ **تمت إضافة رابط {link.label} إلى قائمة الانتظار...**"
            if queued_notice
            else f"🔎 **جاري تجهيز التحميل من {link.label}...**"
        )
        status_msg = await message.reply_text(initial_text, quote=True)
        reporter = ProgressReporter(status_msg, link.label)

        job_dir: Path | None = None
        async with semaphore:
            active_jobs += 1
            try:
                if queued_notice:
                    await reporter.set_text(f"🔎 **جاري تجهيز التحميل من {link.label}...**", force=True)

                result = await download_media(
                    url=link.url,
                    platform=link.platform,
                    settings=settings,
                    on_progress=reporter.report_download,
                    on_merging=reporter.report_merging,
                )
                job_dir = result.job_dir

                total_size = sum(item.file_path.stat().st_size for item in result.items)
                logger.info(
                    "Downloaded %d item(s) (%s) from %s",
                    len(result.items),
                    format_bytes(total_size),
                    link.platform,
                )

                if len(result.items) == 1:
                    await _send_single_item(message, result.items[0], reporter)
                else:
                    await _send_album(message, result.items, result.title, reporter)

                await status_msg.delete()
            except DownloadError as exc:
                await reporter.set_text(f"❌ {exc}", force=True)
            except Exception as exc:
                logger.exception("Failed handling %s", link.url)
                await reporter.set_text(f"❌ حدث خطأ أثناء الرفع أو المعالجة: {exc}", force=True)
            finally:
                active_jobs -= 1
                if job_dir and job_dir.exists():
                    shutil.rmtree(job_dir, ignore_errors=True)

    @app.on_message((filters.text | filters.caption) & ~filters.command(["start", "help", "status"]))
    async def handle_message(_: Client, message: Message) -> None:
        if not _is_authorized(message):
            return

        text = message.text or message.caption or ""
        link = extract_supported_link(text)
        if not link:
            if message.chat.type.name == "PRIVATE":
                await message.reply_text(
                    "📎 أرسل رابطاً من **يوتيوب** أو **إنستقرام** أو **تيك توك** وسأحمله لك فوراً بأعلى جودة."
                )
            return

        await _process_link(message, link)

    return app


def main() -> None:
    try:
        settings = load_settings()
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc

    _cleanup_stale_downloads(settings.downloads_dir)

    if not has_ffmpeg():
        logger.warning("FFmpeg/FFprobe not found in PATH! High-quality YouTube merging may fail.")
    if not _check_tgcrypto():
        logger.warning("TgCrypto is not installed; uploads will be slower.")

    # Ensure an event loop exists on Python 3.13+ before instantiating Pyrofork Client
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = create_bot(settings)
    logger.info("Bot is starting (max upload: %d MB)...", settings.max_file_size_mb)
    app.run()


if __name__ == "__main__":
    main()
