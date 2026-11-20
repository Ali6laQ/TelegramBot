from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    downloads_dir: Path
    max_concurrent_downloads: int
    concurrent_fragments: int
    max_file_size_mb: int
    max_video_height: int | None
    prefer_compatible_codec: bool
    cookies_file: Path | None
    allowed_users: frozenset[int]

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value.strip())


def _parse_int_set(value: str | None) -> frozenset[int]:
    if not value or not value.strip():
        return frozenset()
    result: set[int] = set()
    for part in value.split(","):
        cleaned = part.strip()
        if cleaned:
            result.add(int(cleaned))
    return frozenset(result)


def load_settings() -> Settings:
    env_path = BASE_DIR / ".env"
    load_dotenv(env_path)

    missing: list[str] = []
    api_id_raw = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    bot_token = os.getenv("BOT_TOKEN", "").strip()

    if not api_id_raw or api_id_raw == "12345678":
        missing.append("API_ID")
    if not api_hash or api_hash == "your_api_hash_here":
        missing.append("API_HASH")
    if not bot_token or bot_token == "1234567890:your_bot_token_here":
        missing.append("BOT_TOKEN")

    if missing:
        raise ValueError(
            "يرجى تعبئة القيم المطلوبة في ملف .env قبل تشغيل البوت:\n"
            f"القيم الناقصة: {', '.join(missing)}\n"
            f"يمكنك نسخ .env.example إلى .env وتعديل القيم بداخله."
        )

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ValueError("قيمة API_ID يجب أن تكون رقماً صحيحاً.") from exc

    cookies_raw = os.getenv("COOKIES_FILE", "cookies.txt").strip()
    cookies_path: Path | None = None
    if cookies_raw:
        candidate = Path(cookies_raw)
        if not candidate.is_absolute():
            candidate = BASE_DIR / candidate
        if candidate.is_file():
            cookies_path = candidate

    downloads_dir = BASE_DIR / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    max_file_size_mb = min(int(os.getenv("MAX_FILE_SIZE_MB", "1990")), 2000)

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        downloads_dir=downloads_dir,
        max_concurrent_downloads=max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))),
        concurrent_fragments=max(1, int(os.getenv("CONCURRENT_FRAGMENTS", "8"))),
        max_file_size_mb=max(10, max_file_size_mb),
        max_video_height=_parse_optional_int(os.getenv("MAX_VIDEO_HEIGHT")),
        prefer_compatible_codec=_parse_bool(os.getenv("PREFER_COMPATIBLE_CODEC"), default=False),
        cookies_file=cookies_path,
        allowed_users=_parse_int_set(os.getenv("ALLOWED_USERS")),
    )
