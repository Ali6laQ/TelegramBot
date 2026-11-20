from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

URL_REGEX = re.compile(r"https?://[^\s<>\"'()]+", re.IGNORECASE)

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "ddinstagram.com",
    "instagr.am",
    "www.instagr.am",
}

TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}

PLATFORM_LABELS = {
    "youtube": "يوتيوب",
    "instagram": "إنستقرام",
    "tiktok": "تيك توك",
}


@dataclass(frozen=True)
class SupportedLink:
    url: str
    platform: str

    @property
    def label(self) -> str:
        return PLATFORM_LABELS.get(self.platform, self.platform)


def detect_platform(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    host = (parsed.hostname or "").lower()
    if host in YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        return "youtube"
    if host in INSTAGRAM_HOSTS or host.endswith(".instagram.com"):
        return "instagram"
    if host in TIKTOK_HOSTS or host.endswith(".tiktok.com"):
        return "tiktok"
    return None


def extract_supported_link(text: str | None) -> SupportedLink | None:
    if not text:
        return None
    for match in URL_REGEX.finditer(text):
        candidate = match.group(0).rstrip(".,!?;:)]}>")
        platform = detect_platform(candidate)
        if platform:
            return SupportedLink(url=candidate, platform=platform)
    return None
