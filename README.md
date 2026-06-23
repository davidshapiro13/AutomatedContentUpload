# Automated Reels Publisher (Python)

Simple CSV-driven automation scaffold for posting short-form videos to:
- TikTok
- Instagram Reels
- Facebook Reels
- YouTube Shorts

Platform adapters:
- TikTok + Instagram use Zernio
- Facebook uses direct Meta Graph API upload
- YouTube Shorts uses direct YouTube Data API upload (no Zernio)

This v1 is production-structured and uses direct platform adapters where needed. It supports:
- Manifest validation
- Due-post processing
- Retry failed posts
- Status reports
- Dry-run mode

## Project Layout

- `videos/inbox/` - place source videos here
- `manifests/manifest.csv` - controls what/where/when to post
- `state/post_state.json` - stores posting status and per-platform results
- `src/reels_publisher/` - Python package
- `main.py` - CLI entrypoint

## Quick Start

1. Create virtual env and activate (optional)
2. Run:

```bash
python3 main.py validate-manifest --manifest manifests/manifest.csv
python3 main.py post-due --manifest manifests/manifest.csv --state state/post_state.json --dry-run
python3 main.py report --state state/post_state.json
```

## Local Uploader (V2 Intake)

Run a local-only UI (not hosted) to upload a video to R2, append a manifest row, then `git add/commit/push`.

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run uploader_app.py
```

Notes:
- Requires `git` remote/auth already configured for push.
- Uses `.env` in repo root for `MEDIA_S3_*` values and credentials.
- The uploader writes `zernio_media_url` so posting does not require local video files.
- Streamlit upload limit is configured at 1GB in `.streamlit/config.toml`.
- Uploader commits only `manifests/manifest.csv`; `state/post_state.json` is managed by GitHub Actions.

## Manifest Columns

Required columns:
- `video_file` (path relative to repo root, e.g. `videos/inbox/my_clip.mp4`)
- `caption`
- `hashtags` (space-separated, e.g. `#ai #python`)
- `post_to_tiktok` (`true`/`false`)
- `post_to_instagram` (`true`/`false`)
- `post_to_facebook` (`true`/`false`)
- `post_to_youtube` (`true`/`false`)
- `scheduled_at` (ISO timestamp, e.g. `2026-05-29T20:30:00-04:00`)
- `status` (`ready`, `hold`, `posted`, `failed`, `partial`)
- `notes`

Optional columns:
- `tiktok_caption`
- `instagram_caption`
- `facebook_caption`
- `facebook_page_id` (optional per-row override; otherwise use env var)
- `zernio_media_url` (used for Zernio platforms; if empty, project auto-uploads from `video_file`)
- `zernio_profile_id` (optional per-row override; otherwise use env var)
- `zernio_tiktok_account_id` (optional per-row override for TikTok account)
- `zernio_instagram_account_id` (optional per-row override for Instagram account)
- `zernio_youtube_account_id` (optional per-row override for YouTube account)
- `youtube_title`
- `youtube_description`
- `youtube_privacy` (`public`, `unlisted`, `private`; default `public`)

Backward compatibility:
- If `zernio_media_url` is missing, parser will fall back to `tiktok_video_url` or `instagram_video_url` if present.

## Environment Variables

- `ZERNIO_API_KEY`
- `ZERNIO_PROFILE_ID` (default profile if not set in manifest row)
- `ZERNIO_TIKTOK_ACCOUNT_ID` (recommended for multi-account setups)
- `ZERNIO_INSTAGRAM_ACCOUNT_ID` (recommended for multi-account setups)
- `ZERNIO_YOUTUBE_ACCOUNT_ID` (recommended for multi-account setups)
- `FACEBOOK_PAGE_ID` (Facebook Page ID for direct Meta uploads)
- `FACEBOOK_PAGE_ACCESS_TOKEN` (Page access token with publish permissions)
- `FACEBOOK_GRAPH_API_VERSION` (optional, default `v23.0`)
- `YOUTUBE_ACCESS_TOKEN` (required for direct YouTube upload if auto-refresh is off)
- `YOUTUBE_AUTO_REFRESH` (`1` to refresh before each direct YouTube upload)
- `YOUTUBE_REFRESH_TOKEN` (required when `YOUTUBE_AUTO_REFRESH=1`)
- `YOUTUBE_CLIENT_ID` (required when `YOUTUBE_AUTO_REFRESH=1`)
- `YOUTUBE_CLIENT_SECRET` (required when `YOUTUBE_AUTO_REFRESH=1`)
- `YOUTUBE_CATEGORY_ID` (optional, default `22`)

Auto-upload settings (optional, used when `zernio_media_url` is blank):
- `MEDIA_S3_BUCKET` (required for auto-upload)
- `MEDIA_S3_REGION` (default `us-east-1`)
- `MEDIA_S3_ENDPOINT_URL` (for S3-compatible providers like R2/Wasabi/MinIO)
- `MEDIA_S3_PUBLIC_BASE_URL` (recommended for custom endpoints; e.g. your CDN/domain base)
- `MEDIA_S3_KEY_PREFIX` (default `reels`)
- `MEDIA_S3_ACL` (optional, e.g. `public-read` if your bucket model requires ACLs)

Notes for auto-upload:
- The runtime must have valid AWS-style credentials in env (for example `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`).
- Install dependency: `pip install boto3`.

Notes:
- You connect TikTok/Instagram accounts in Zernio once via OAuth.
- TikTok/Instagram publish through Zernio.
- Facebook publishes directly to the Meta Graph API with a Page access token.
- YouTube publishes directly to the YouTube API.
- None of these are required for `--dry-run`.
