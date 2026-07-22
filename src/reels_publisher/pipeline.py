from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .adapters import AdapterContext, FacebookDirectAdapter, InstagramDirectAdapter, YouTubeDirectAdapter, ZernioAdapter
from .manifest import load_manifest, validate_manifest
from .models import ManifestRow
from .state import already_posted, load_state, previous_failure, record_result, save_state


def _is_due(row: ManifestRow, now: datetime) -> bool:
    return row.scheduled_at <= now


def _selected_platforms(row: ManifestRow) -> List[str]:
    out: List[str] = []
    if row.post_to_tiktok:
        out.append("tiktok")
    if row.post_to_instagram:
        out.append("instagram")
    if row.post_to_facebook:
        out.append("facebook")
    if row.post_to_youtube:
        out.append("youtube")
    return out


def _matches_row_number(row: ManifestRow, row_number: int | None) -> bool:
    return row_number is None or row.row_id.rsplit(":", 1)[-1] == str(row_number)


def _selected_platform_results(state: Dict, row: ManifestRow) -> Dict[str, str]:
    results: Dict[str, str] = {}
    for platform in _selected_platforms(row):
        if already_posted(state, row, platform):
            results[platform] = "success"
        elif previous_failure(state, row, platform):
            results[platform] = "failed"
        else:
            results[platform] = "pending"
    return results


def _completion_status(state: Dict, row: ManifestRow) -> str | None:
    results = _selected_platform_results(state, row)
    if not results:
        return None
    values = set(results.values())
    if values == {"success"}:
        return "posted"
    if "success" in values and "failed" in values and "pending" not in values:
        return "partial"
    if values == {"failed"}:
        return "failed"
    return None


def _missing_env(name: str) -> bool:
    return not os.getenv(name, "").strip()


def _youtube_missing_env() -> List[str]:
    auto_refresh = os.getenv("YOUTUBE_AUTO_REFRESH", "").strip().lower()
    if auto_refresh in {"1", "true", "yes", "on"}:
        required = ["YOUTUBE_REFRESH_TOKEN", "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"]
    else:
        required = ["YOUTUBE_ACCESS_TOKEN"]
    return [name for name in required if _missing_env(name)]


def _preflight_errors(rows: List[ManifestRow], state: Dict, row_number: int | None, retry_failed: bool) -> List[str]:
    errors: List[str] = []
    now = datetime.now(timezone.utc)
    for row in rows:
        if not _matches_row_number(row, row_number):
            continue
        if row.status != "ready":
            continue
        if not _is_due(row, now):
            continue
        for platform in _selected_platforms(row):
            if already_posted(state, row, platform):
                continue
            if previous_failure(state, row, platform) and not retry_failed:
                continue
            if platform == "tiktok":
                if _missing_env("ZERNIO_API_KEY"):
                    errors.append(f"{row.row_id}: missing ZERNIO_API_KEY for tiktok")
                if not (row.zernio_profile_id or os.getenv("ZERNIO_PROFILE_ID", "").strip()):
                    errors.append(f"{row.row_id}: missing zernio_profile_id or ZERNIO_PROFILE_ID for tiktok")
            elif platform == "instagram":
                if _missing_env("INSTAGRAM_USER_ID"):
                    errors.append(f"{row.row_id}: missing INSTAGRAM_USER_ID")
                if _missing_env("INSTAGRAM_ACCESS_TOKEN"):
                    errors.append(f"{row.row_id}: missing INSTAGRAM_ACCESS_TOKEN")
            elif platform == "facebook":
                if not (row.facebook_page_id or os.getenv("FACEBOOK_PAGE_ID", "").strip()):
                    errors.append(f"{row.row_id}: missing facebook_page_id or FACEBOOK_PAGE_ID")
                if _missing_env("FACEBOOK_PAGE_ACCESS_TOKEN"):
                    errors.append(f"{row.row_id}: missing FACEBOOK_PAGE_ACCESS_TOKEN")
            elif platform == "youtube":
                errors.extend(f"{row.row_id}: missing {name}" for name in _youtube_missing_env())
    return errors


def _archive_video(repo_root: Path, video_file: Path) -> Path | None:
    src = repo_root / video_file
    if not src.exists():
        return None
    archive_dir = repo_root / "videos" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / src.name
    if dest.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        dest = archive_dir / f"{src.stem}-{stamp}{src.suffix}"
    src.rename(dest)
    return dest.relative_to(repo_root)


def _apply_manifest_updates(manifest_path: Path, updates: Dict[str, Dict[str, str]]) -> None:
    if not updates:
        return
    with manifest_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0].keys()) if rows else []
    for line_no, row in enumerate(rows, start=2):
        row_id = f"{manifest_path.name}:{line_no}"
        update = updates.get(row_id)
        if not update:
            continue
        for key, value in update.items():
            row[key] = value
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def process_due_posts(
    manifest_path: Path,
    state_path: Path,
    repo_root: Path,
    dry_run: bool,
    row_number: int | None = None,
    retry_failed: bool = False,
) -> Dict:
    rows = load_manifest(manifest_path)
    errors = validate_manifest(rows, repo_root)
    if errors:
        return {"ok": False, "errors": errors}

    state = load_state(state_path)
    errors = _preflight_errors(rows, state, row_number, retry_failed)
    if errors and not dry_run:
        return {"ok": False, "errors": errors}

    now = datetime.now(timezone.utc)
    ctx = AdapterContext(dry_run=dry_run, repo_root=repo_root)
    zernio_adapter = ZernioAdapter()
    facebook_adapter = FacebookDirectAdapter()
    instagram_adapter = InstagramDirectAdapter()
    youtube_adapter = YouTubeDirectAdapter()

    processed = 0
    success = 0
    failed = 0
    partial = 0
    skipped = 0
    blocked = 0
    manifest_updates: Dict[str, Dict[str, str]] = {}

    for row in rows:
        if not _matches_row_number(row, row_number):
            skipped += 1
            continue
        if row.status != "ready":
            skipped += 1
            continue
        if not _is_due(row, now):
            skipped += 1
            continue

        platforms = _selected_platforms(row)
        for platform in platforms:
            if already_posted(state, row, platform):
                skipped += 1
                continue
            if previous_failure(state, row, platform) and not retry_failed:
                skipped += 1
                blocked += 1
                continue
            if platform == "youtube":
                result = youtube_adapter.post(row, ctx)
            elif platform == "facebook":
                result = facebook_adapter.post(row, ctx)
            elif platform == "instagram":
                result = instagram_adapter.post(row, ctx)
            else:
                result = zernio_adapter.post(row, ctx, platform)
            record_result(state, row, result)
            processed += 1
            if result.success:
                success += 1
            else:
                failed += 1

        if dry_run:
            continue

        status = _completion_status(state, row)
        if status is None:
            continue

        if status == "partial":
            partial += 1
        update: Dict[str, str] = {"status": status}
        if status == "posted":
            archived_path = _archive_video(repo_root, row.video_file)
            if archived_path is not None:
                update["video_file"] = str(archived_path)
        manifest_updates[row.row_id] = update

    save_state(state_path, state)
    _apply_manifest_updates(manifest_path, manifest_updates)
    return {
        "ok": True,
        "processed": processed,
        "success": success,
        "failed": failed,
        "partial": partial,
        "skipped": skipped,
        "blocked_previous_failures": blocked,
    }


def retry_failures(state_path: Path) -> Dict:
    state = load_state(state_path)
    failures: List[Dict] = []
    for key, entry in state.get("posts", {}).items():
        for platform, result in entry.get("platforms", {}).items():
            if not result.get("success"):
                failures.append({"key": key, "platform": platform, "error": result.get("error")})
    return {"ok": True, "failures": failures, "count": len(failures)}


def summarize_state(state_path: Path) -> Dict:
    state = load_state(state_path)
    total = 0
    success = 0
    failed = 0
    for entry in state.get("posts", {}).values():
        for result in entry.get("platforms", {}).values():
            total += 1
            if result.get("success"):
                success += 1
            else:
                failed += 1
    return {"ok": True, "total_platform_attempts": total, "success": success, "failed": failed}
