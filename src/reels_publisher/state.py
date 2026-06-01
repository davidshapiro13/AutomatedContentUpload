from __future__ import annotations

import json
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path
from typing import Dict

from .models import ManifestRow, PostResult


def load_state(path: Path) -> Dict:
    if not path.exists():
        return {"posts": {}}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except JSONDecodeError:
        # Keep automation running even if a bad merge corrupts state JSON.
        return {"posts": {}}
    if "posts" not in data or not isinstance(data["posts"], dict):
        return {"posts": {}}
    return data


def save_state(path: Path, state: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def row_key(row: ManifestRow) -> str:
    return f"{row.video_file}|{row.scheduled_at.isoformat()}"


def already_posted(state: Dict, row: ManifestRow, platform: str) -> bool:
    key = row_key(row)
    entry = state["posts"].get(key, {})
    platform_data = entry.get("platforms", {}).get(platform, {})
    return bool(platform_data.get("success"))


def record_result(state: Dict, row: ManifestRow, result: PostResult) -> None:
    key = row_key(row)
    posts = state.setdefault("posts", {})
    entry = posts.setdefault(key, {"platforms": {}, "row_id": row.row_id, "video_file": str(row.video_file)})
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    entry["platforms"][result.platform] = {
        "success": result.success,
        "external_id": result.external_id,
        "external_url": result.external_url,
        "error": result.error,
    }
