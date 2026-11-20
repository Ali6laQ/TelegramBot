from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bot.downloader import _build_format_selector, _friendly_error_message
from bot.media import has_ffmpeg, prepare_thumbnail, probe_video
from bot.progress import format_bytes, render_bar
from bot.urls import extract_supported_link


class TestUrlExtraction(unittest.TestCase):
    def test_youtube_variants(self) -> None:
        cases = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?si=abc",
            "شوف هذا المقطع https://youtube.com/shorts/abc123XYZ! رهيب",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        ]
        for text in cases:
            link = extract_supported_link(text)
            self.assertIsNotNone(link, f"Failed for: {text}")
            assert link is not None
            self.assertEqual(link.platform, "youtube")
            self.assertFalse(link.url.endswith("!"))

    def test_instagram_variants(self) -> None:
        cases = [
            "https://www.instagram.com/reel/Cx123456789/?igsh=xyz",
            "https://instagram.com/p/Cx123456789/",
            "Check this out: https://instagr.am/p/Cx123456789/.",
        ]
        for text in cases:
            link = extract_supported_link(text)
            self.assertIsNotNone(link, f"Failed for: {text}")
            assert link is not None
            self.assertEqual(link.platform, "instagram")

    def test_tiktok_variants(self) -> None:
        cases = [
            "https://www.tiktok.com/@user/video/7123456789012345678",
            "https://vm.tiktok.com/ZM8abcdef/",
            "شاهد الفيديو https://vt.tiktok.com/ZS8abcdef/",
        ]
        for text in cases:
            link = extract_supported_link(text)
            self.assertIsNotNone(link, f"Failed for: {text}")
            assert link is not None
            self.assertEqual(link.platform, "tiktok")

    def test_unsupported_links(self) -> None:
        self.assertIsNone(extract_supported_link("https://example.com/video.mp4"))
        self.assertIsNone(extract_supported_link("مرحبا كيف حالك"))
        self.assertIsNone(extract_supported_link(""))


class TestHelpers(unittest.TestCase):
    def test_format_bytes_and_bar(self) -> None:
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1024 * 1024 * 5), "5.0 MB")
        self.assertEqual(render_bar(50, length=10), "█████░░░░░")
        self.assertEqual(render_bar(100, length=10), "██████████")

    def test_format_selector(self) -> None:
        sel = _build_format_selector(max_height=1080, max_bytes=None)
        self.assertIn("height<=1080", sel)
        constrained = _build_format_selector(max_height=None, max_bytes=1000 * 1024 * 1024)
        self.assertIn("filesize<", constrained)

    def test_friendly_errors(self) -> None:
        msg = _friendly_error_message("ERROR: Private video. Sign in if you have been granted access", "youtube")
        self.assertIn("cookies.txt", msg)


class TestMediaTools(unittest.TestCase):
    def test_probe_and_thumbnail(self) -> None:
        if not has_ffmpeg():
            self.skipTest("ffmpeg not available")

        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "sample.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=360x640:d=2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(video),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            async def _run() -> None:
                meta = await probe_video(video)
                self.assertEqual(meta.width, 360)
                self.assertEqual(meta.height, 640)
                self.assertGreaterEqual(meta.duration, 1)

                thumb = await prepare_thumbnail(video, duration=meta.duration)
                self.assertIsNotNone(thumb)
                assert thumb is not None
                self.assertTrue(thumb.is_file())
                self.assertLessEqual(thumb.stat().st_size, 200 * 1024)

            asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
