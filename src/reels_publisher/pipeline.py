from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .adapters import AdapterContext, YouTubeDirectAdapter, ZernioAdapter
from .manifest import load_manifest, validate_manifest
from .models import ManifestRow
from .state import already_posted, load_state, record_result, save_state


def _is_due(row: ManifestRow, now: datetime) -> bool:
    return row.scheduled_at <= now


def _selected_platforms(row: ManifestRow) -> List[str]:
    out: List[str] = []
    if row.post_to_tiktok:
        out.append("tiktok")
    if row.post_to_instagram:
        out.append("instagram")
    if row.post_to_youtube:
        out.append("youtube")
    return out


def process_due_posts(manifest_path: Path, state_path: Path, repo_root: Path, dry_run: bool) -> Dict:
    rows = load_manifest(manifest_path)
    errors = validate_manifest(rows, repo_root)
    if errors:
        return {"ok": False, "errors": errors}

    state = load_state(state_path)
    now = datetime.now(timezone.utc)
    ctx = AdapterContext(dry_run=dry_run, repo_root=repo_root)
    zernio_adapter = ZernioAdapter()
    youtube_adapter = YouTubeDirectAdapter()

    processed = 0
    success = 0
    failed = 0
    skipped = 0

    for row in rows:
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
            if platform == "youtube":
                result = youtube_adapter.post(row, ctx)
            else:
                result = zernio_adapter.post(row, ctx, platform)
            record_result(state, row, result)
            processed += 1
            if result.success:
                success += 1
            else:
                failed += 1

    save_state(state_path, state)
    return {
        "ok": True,
        "processed": processed,
        "success": success,
        "failed": failed,
        "skipped": skipped,
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
