from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import List

from .models import ManifestRow

REQUIRED_COLUMNS = {
    "video_file",
    "caption",
    "hashtags",
    "post_to_tiktok",
    "post_to_instagram",
    "post_to_youtube",
    "scheduled_at",
    "status",
    "notes",
}

ALLOWED_STATUSES = {"ready", "hold", "posted", "failed", "partial"}
ALLOWED_PRIVACY = {"private", "unlisted", "public"}


def _parse_bool(value: str) -> bool:
    v = (value or "").strip().lower()
    if v in {"1", "true", "yes", "y"}:
        return True
    if v in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _parse_datetime(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.strip())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid scheduled_at datetime: {value!r}") from exc
    if dt.tzinfo is None:
        raise ValueError("scheduled_at must include timezone offset")
    return dt


def load_manifest(path: Path) -> List[ManifestRow]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    rows: List[ManifestRow] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"Manifest missing required columns: {sorted(missing)}")

        for index, raw in enumerate(reader, start=2):
            row_id = f"{path.name}:{index}"
            status = (raw.get("status") or "").strip().lower()
            youtube_privacy = (raw.get("youtube_privacy") or "private").strip().lower()
            item = ManifestRow(
                row_id=row_id,
                video_file=Path((raw.get("video_file") or "").strip()),
                caption=(raw.get("caption") or "").strip(),
                hashtags=(raw.get("hashtags") or "").strip(),
                post_to_tiktok=_parse_bool(raw.get("post_to_tiktok", "")),
                post_to_instagram=_parse_bool(raw.get("post_to_instagram", "")),
                post_to_youtube=_parse_bool(raw.get("post_to_youtube", "")),
                scheduled_at=_parse_datetime(raw.get("scheduled_at", "")),
                status=status,
                notes=(raw.get("notes") or "").strip(),
                tiktok_caption=(raw.get("tiktok_caption") or "").strip(),
                instagram_caption=(raw.get("instagram_caption") or "").strip(),
                zernio_media_url=(
                    (raw.get("zernio_media_url") or "").strip()
                    or (raw.get("tiktok_video_url") or "").strip()
                    or (raw.get("instagram_video_url") or "").strip()
                ),
                zernio_profile_id=(raw.get("zernio_profile_id") or "").strip(),
                zernio_tiktok_account_id=(raw.get("zernio_tiktok_account_id") or "").strip(),
                zernio_instagram_account_id=(raw.get("zernio_instagram_account_id") or "").strip(),
                zernio_youtube_account_id=(raw.get("zernio_youtube_account_id") or "").strip(),
                youtube_title=(raw.get("youtube_title") or "").strip(),
                youtube_description=(raw.get("youtube_description") or "").strip(),
                youtube_privacy=youtube_privacy,
            )
            rows.append(item)
    return rows


def validate_manifest(rows: List[ManifestRow], repo_root: Path) -> List[str]:
    errors: List[str] = []
    for row in rows:
        if row.status not in ALLOWED_STATUSES:
            errors.append(f"{row.row_id}: invalid status {row.status!r}")
        if row.youtube_privacy not in ALLOWED_PRIVACY:
            errors.append(f"{row.row_id}: invalid youtube_privacy {row.youtube_privacy!r}")
        if not (row.post_to_tiktok or row.post_to_instagram or row.post_to_youtube):
            errors.append(f"{row.row_id}: no platforms selected")
        # Posted rows are historical and should not block future runs.
        if row.status == "posted":
            continue

        # Local file is only required when we cannot post from a hosted media URL.
        needs_local_file = row.post_to_youtube or not row.zernio_media_url
        if needs_local_file:
            if not row.video_file:
                errors.append(f"{row.row_id}: video_file is empty")
            else:
                abs_path = repo_root / row.video_file
                if not abs_path.exists():
                    errors.append(f"{row.row_id}: missing video file {row.video_file}")
    return errors
