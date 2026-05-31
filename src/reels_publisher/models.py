from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class ManifestRow:
    row_id: str
    video_file: Path
    caption: str
    hashtags: str
    post_to_tiktok: bool
    post_to_instagram: bool
    post_to_youtube: bool
    scheduled_at: datetime
    status: str
    notes: str
    tiktok_caption: str = ""
    instagram_caption: str = ""
    zernio_media_url: str = ""
    zernio_profile_id: str = ""
    zernio_tiktok_account_id: str = ""
    zernio_instagram_account_id: str = ""
    zernio_youtube_account_id: str = ""
    youtube_title: str = ""
    youtube_description: str = ""
    youtube_privacy: str = "private"


@dataclass
class PostResult:
    success: bool
    platform: str
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    error: Optional[str] = None
