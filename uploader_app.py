from __future__ import annotations

import csv
import os
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict
from urllib.parse import quote
from zoneinfo import ZoneInfo

import boto3
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "manifests" / "manifest.csv"
STATE_PATH = REPO_ROOT / "state" / "post_state.json"
EASTERN_TZ = ZoneInfo("America/New_York")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _s3_client():
    region = _env("MEDIA_S3_REGION", "us-east-1")
    endpoint_url = _env("MEDIA_S3_ENDPOINT_URL")
    return boto3.client("s3", region_name=region, endpoint_url=endpoint_url or None)


def _build_object_key(filename: str) -> str:
    prefix = _env("MEDIA_S3_KEY_PREFIX", "reels").strip("/")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    safe_name = Path(filename).name.replace(" ", "_")
    return f"{prefix}/{Path(safe_name).stem}-{timestamp}{Path(safe_name).suffix}"


def _build_public_url(bucket: str, key: str) -> str:
    public_base_url = _env("MEDIA_S3_PUBLIC_BASE_URL")
    if public_base_url:
        return f"{public_base_url.rstrip('/')}/{quote(key)}"
    endpoint_url = _env("MEDIA_S3_ENDPOINT_URL").rstrip("/")
    if endpoint_url:
        return f"{endpoint_url}/{bucket}/{quote(key)}"
    region = _env("MEDIA_S3_REGION", "us-east-1")
    return f"https://{bucket}.s3.{region}.amazonaws.com/{quote(key)}"


def upload_to_r2(file_name: str, payload: bytes) -> Dict[str, str]:
    bucket = _env("MEDIA_S3_BUCKET")
    if not bucket:
        raise RuntimeError("Missing MEDIA_S3_BUCKET")
    key = _build_object_key(file_name)
    extra = {}
    acl = _env("MEDIA_S3_ACL")
    if acl:
        extra["ACL"] = acl

    client = _s3_client()
    client.put_object(Bucket=bucket, Key=key, Body=payload, **extra)
    return {"bucket": bucket, "key": key, "url": _build_public_url(bucket, key)}


def append_manifest_row(row: Dict[str, str]) -> None:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Manifest not found: {MANIFEST_PATH}")

    with MANIFEST_PATH.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
    if not fieldnames:
        raise RuntimeError("Manifest header is missing")

    clean_row = {name: row.get(name, "") for name in fieldnames}
    with MANIFEST_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writerow(clean_row)


def _git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=REPO_ROOT, check=True)


def _discard_local_state_changes() -> None:
    rel_state = str(STATE_PATH.relative_to(REPO_ROOT))
    subprocess.run(["git", "restore", "--", rel_state], cwd=REPO_ROOT, check=False)


def commit_and_push(message: str) -> None:
    _discard_local_state_changes()
    _git("add", str(MANIFEST_PATH.relative_to(REPO_ROOT)))
    commit_proc = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if commit_proc.returncode != 0:
        output = f"{commit_proc.stdout}\n{commit_proc.stderr}".lower()
        if "nothing to commit" in output:
            return
        raise subprocess.CalledProcessError(
            commit_proc.returncode, commit_proc.args, output=commit_proc.stdout, stderr=commit_proc.stderr
        )

    _git("pull", "--rebase", "--autostash")
    push_proc = subprocess.run(["git", "push"], cwd=REPO_ROOT, text=True, capture_output=True)
    if push_proc.returncode == 0:
        return

    push_text = f"{push_proc.stdout}\n{push_proc.stderr}".lower()
    if "non-fast-forward" in push_text or "fetch first" in push_text or "rejected" in push_text:
        _git("pull", "--rebase", "--autostash")
        _git("push")
        return

    raise subprocess.CalledProcessError(
        push_proc.returncode, push_proc.args, output=push_proc.stdout, stderr=push_proc.stderr
    )


def default_schedule_date() -> date:
    return (datetime.now(EASTERN_TZ) + timedelta(days=1)).date()


def default_schedule_time() -> time:
    return time(hour=15, minute=30)


def build_scheduled_at(scheduled_date: date, scheduled_time: time) -> str:
    return datetime.combine(scheduled_date, scheduled_time, tzinfo=EASTERN_TZ).replace(microsecond=0).isoformat()


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").strip())
    except Exception:  # noqa: BLE001
        return None


def load_upcoming_posts(limit: int = 25) -> list[dict[str, str]]:
    if not MANIFEST_PATH.exists():
        return []

    now = datetime.now().astimezone()
    rows: list[tuple[datetime, dict[str, str]]] = []
    with MANIFEST_PATH.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            dt = _parse_iso_datetime(raw.get("scheduled_at", ""))
            if dt is None or dt < now:
                continue
            if (raw.get("status") or "").strip().lower() != "ready":
                continue
            rows.append(
                (
                    dt,
                    {
                        "scheduled_at": dt.astimezone(EASTERN_TZ).strftime("%a, %b %d, %Y at %I:%M %p %Z"),
                        "caption": (raw.get("caption") or "").strip(),
                        "platforms": ",".join(
                            [
                                p
                                for p, flag in (
                                    ("tiktok", raw.get("post_to_tiktok", "")),
                                    ("instagram", raw.get("post_to_instagram", "")),
                                    ("youtube", raw.get("post_to_youtube", "")),
                                )
                                if (flag or "").strip().lower() == "true"
                            ]
                        ),
                        "status": (raw.get("status") or "").strip(),
                        "video_file": (raw.get("video_file") or "").strip(),
                    },
                )
            )
    rows.sort(key=lambda item: item[0])
    return [row for _, row in rows[:limit]]


def main() -> None:
    _load_env_file(REPO_ROOT / ".env")

    st.set_page_config(page_title="Reels Uploader", layout="centered")
    st.title("Local Reels Uploader")
    st.caption("Uploads to R2, appends manifest row, commits and pushes to GitHub.")

    st.subheader("Upcoming Posts")
    refresh = st.button("Refresh Upcoming")
    if refresh:
        st.rerun()
    upcoming = load_upcoming_posts(limit=50)
    st.caption(f"{len(upcoming)} upcoming ready posts")
    if not upcoming:
        st.info("No upcoming ready posts found.")
    else:
        st.dataframe(upcoming, use_container_width=True, hide_index=True)

    with st.form("upload_form", clear_on_submit=False):
        video = st.file_uploader("Video file", type=["mp4", "mov", "m4v", "webm"])
        caption = st.text_area("Caption", height=100)
        hashtags = st.text_input("Hashtags", placeholder="#travel #shorts")
        col_date, col_time = st.columns(2)
        scheduled_date = col_date.date_input(
            "Scheduled date",
            value=default_schedule_date(),
            min_value=datetime.now(EASTERN_TZ).date(),
            key="scheduled_date",
        )
        scheduled_time = col_time.time_input(
            "Scheduled time",
            value=default_schedule_time(),
            step=timedelta(minutes=15),
            key="scheduled_time",
        )
        col1, col2, col3 = st.columns(3)
        post_to_tiktok = col1.checkbox("TikTok", value=True)
        post_to_instagram = col2.checkbox("Instagram", value=False)
        post_to_youtube = col3.checkbox("YouTube", value=False)
        youtube_title = st.text_input("YouTube title (optional)")
        youtube_description = st.text_area("YouTube description (optional)", height=80)
        submitted = st.form_submit_button("Upload + Add + Push")

    if submitted:
        scheduled_at = build_scheduled_at(scheduled_date, scheduled_time)
        scheduled_dt = datetime.fromisoformat(scheduled_at)
        if video is None:
            st.error("Select a video file.")
        elif not (post_to_tiktok or post_to_instagram or post_to_youtube):
            st.error("Select at least one platform.")
        elif scheduled_dt <= datetime.now(EASTERN_TZ):
            st.error("Scheduled time must be in the future.")
        else:
            try:
                media = upload_to_r2(video.name, video.getvalue())
                row = {
                    "video_file": f"videos/inbox/{Path(video.name).name}",
                    "caption": caption.strip(),
                    "hashtags": hashtags.strip(),
                    "post_to_tiktok": str(post_to_tiktok).lower(),
                    "post_to_instagram": str(post_to_instagram).lower(),
                    "post_to_youtube": str(post_to_youtube).lower(),
                    "scheduled_at": scheduled_at.strip(),
                    "status": "ready",
                    "notes": "",
                    "tiktok_caption": caption.strip(),
                    "instagram_caption": caption.strip(),
                    "zernio_media_url": media["url"],
                    "zernio_profile_id": "",
                    "youtube_title": youtube_title.strip(),
                    "youtube_description": youtube_description.strip(),
                    "youtube_privacy": "public",
                }
                append_manifest_row(row)
                commit_and_push(f"Add reel: {Path(video.name).name} @ {scheduled_at.strip()}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Failed: {exc}")
            else:
                st.success("Uploaded to R2, manifest updated, and pushed to GitHub.")
                st.code(f"R2 URL: {media['url']}")

    st.divider()


if __name__ == "__main__":
    main()
