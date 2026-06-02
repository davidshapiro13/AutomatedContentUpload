from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from .models import ManifestRow, PostResult


@dataclass
class AdapterContext:
    dry_run: bool
    repo_root: Path
    media_url_cache: dict[str, str] = field(default_factory=dict)


class ZernioAdapter:
    @staticmethod
    def _stub_external_id(platform: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"zernio_{platform}_{ts}"

    @staticmethod
    def _http_json(method: str, url: str, *, headers: dict | None = None, payload: dict | None = None) -> dict:
        req_headers = {"Accept": "application/json"}
        if headers:
            req_headers.update(headers)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        req = Request(url=url, data=data, headers=req_headers, method=method)
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{exc.code} {exc.reason}: {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"Network error: {exc.reason}") from exc

    @staticmethod
    def _platform_caption(row: ManifestRow, platform: str) -> str:
        if platform == "tiktok" and row.tiktok_caption:
            return f"{row.tiktok_caption} {row.hashtags}".strip()
        if platform == "instagram" and row.instagram_caption:
            return f"{row.instagram_caption} {row.hashtags}".strip()
        return f"{row.caption} {row.hashtags}".strip()

    @staticmethod
    def _build_public_url(bucket: str, key: str, region: str, endpoint_url: str, public_base_url: str) -> str:
        encoded_key = quote(key, safe="/")
        if public_base_url:
            return f"{public_base_url.rstrip('/')}/{encoded_key}"
        if endpoint_url:
            return f"{endpoint_url.rstrip('/')}/{bucket}/{encoded_key}"
        return f"https://{bucket}.s3.{region}.amazonaws.com/{encoded_key}"

    @staticmethod
    def _account_id_for_platform(row: ManifestRow, platform: str) -> str:
        env_map = {
            "tiktok": "ZERNIO_TIKTOK_ACCOUNT_ID",
            "instagram": "ZERNIO_INSTAGRAM_ACCOUNT_ID",
            "youtube": "ZERNIO_YOUTUBE_ACCOUNT_ID",
        }
        row_map = {
            "tiktok": row.zernio_tiktok_account_id,
            "instagram": row.zernio_instagram_account_id,
            "youtube": row.zernio_youtube_account_id,
        }
        row_value = (row_map.get(platform) or "").strip()
        if row_value:
            return row_value
        env_var = env_map.get(platform, "")
        env_value = os.getenv(env_var, "").strip() if env_var else ""
        if env_value:
            return env_value
        return ""

    def _upload_media_for_row(self, row: ManifestRow, ctx: AdapterContext) -> str:
        cached = ctx.media_url_cache.get(row.row_id)
        if cached:
            return cached

        local_path = ctx.repo_root / row.video_file
        if not local_path.exists():
            raise RuntimeError(f"Missing video file: {row.video_file}")

        bucket = os.getenv("MEDIA_S3_BUCKET", "").strip()
        region = os.getenv("MEDIA_S3_REGION", "us-east-1").strip()
        endpoint_url = os.getenv("MEDIA_S3_ENDPOINT_URL", "").strip()
        public_base_url = os.getenv("MEDIA_S3_PUBLIC_BASE_URL", "").strip()
        key_prefix = os.getenv("MEDIA_S3_KEY_PREFIX", "reels").strip().strip("/")
        acl = os.getenv("MEDIA_S3_ACL", "").strip()
        if not bucket:
            raise RuntimeError("Missing zernio_media_url and MEDIA_S3_BUCKET for auto-upload")

        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install boto3 to enable auto-upload: pip install boto3") from exc

        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        key = f"{key_prefix}/{local_path.stem}-{ts}{local_path.suffix}" if key_prefix else f"{local_path.stem}-{ts}{local_path.suffix}"
        client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url or None)
        extra_args = {"ContentType": "video/mp4"}
        if acl:
            extra_args["ACL"] = acl
        try:
            client.upload_file(str(local_path), bucket, key, ExtraArgs=extra_args)
        except Exception as exc:  # noqa: BLE001
            if "not seekable" not in str(exc):
                raise
            put_args = {
                "Bucket": bucket,
                "Key": key,
                "Body": local_path.read_bytes(),
                "ContentType": extra_args["ContentType"],
            }
            if acl:
                put_args["ACL"] = acl
            client.put_object(**put_args)
        media_url = self._build_public_url(bucket, key, region, endpoint_url, public_base_url)
        ctx.media_url_cache[row.row_id] = media_url
        return media_url

    def post(self, row: ManifestRow, ctx: AdapterContext, platform: str) -> PostResult:
        if ctx.dry_run:
            return PostResult(True, platform, external_id=self._stub_external_id(platform))

        api_key = os.getenv("ZERNIO_API_KEY", "").strip()
        profile_id = row.zernio_profile_id or os.getenv("ZERNIO_PROFILE_ID", "").strip()
        account_id = self._account_id_for_platform(row, platform) or profile_id
        if not api_key:
            return PostResult(False, platform, error="Missing ZERNIO_API_KEY")
        if not profile_id:
            return PostResult(False, platform, error="Missing zernio_profile_id and ZERNIO_PROFILE_ID")
        if not account_id:
            return PostResult(False, platform, error=f"Missing account ID for {platform}")
        try:
            media_url = row.zernio_media_url or self._upload_media_for_row(row, ctx)
        except Exception as exc:  # noqa: BLE001
            return PostResult(False, platform, error=str(exc))

        payload = {
            "profileId": profile_id,
            "content": self._platform_caption(row, platform),
            "platforms": [{"platform": platform, "accountId": account_id}],
            "mediaItems": [{"type": "video", "url": media_url}],
            "scheduledFor": row.scheduled_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if platform == "tiktok":
            payload["tiktokSettings"] = {
                "privacy_level": os.getenv("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY").strip(),
                "allow_comment": True,
                "allow_duet": True,
                "allow_stitch": True,
                "content_preview_confirmed": True,
                "express_consent_given": True,
            }
        if platform == "youtube" and row.youtube_title:
            payload["title"] = row.youtube_title
        if platform == "youtube" and row.youtube_description:
            payload["description"] = row.youtube_description

        try:
            data = self._http_json(
                "POST",
                "https://zernio.com/api/v1/posts",
                headers={"Authorization": f"Bearer {api_key}"},
                payload=payload,
            )
            post_id = data.get("post", {}).get("_id") or data.get("id")
            if not post_id:
                return PostResult(False, platform, error=f"Unexpected Zernio response: {data}")
            return PostResult(True, platform, external_id=str(post_id))
        except Exception as exc:  # noqa: BLE001
            return PostResult(False, platform, error=f"{exc} | media_url={media_url}")


class YouTubeDirectAdapter:
    TRUTHY = {"1", "true", "yes", "on"}
    FALSEY = {"0", "false", "no", "off", ""}

    @staticmethod
    def _stub_external_id() -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"youtube_{ts}"

    @staticmethod
    def _http_json(method: str, url: str, *, headers: dict, payload: bytes) -> dict:
        req = Request(url=url, data=payload, headers=headers, method=method)
        try:
            with urlopen(req, timeout=300) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{exc.code} {exc.reason}: {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"Network error: {exc.reason}") from exc

    @staticmethod
    def _http_form(method: str, url: str, *, payload: dict[str, str]) -> dict:
        data = urlencode(payload).encode("utf-8")
        req = Request(
            url=url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{exc.code} {exc.reason}: {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"Network error: {exc.reason}") from exc

    @classmethod
    def _refresh_enabled(cls) -> bool:
        raw = os.getenv("YOUTUBE_AUTO_REFRESH", "").strip()
        if raw.lower() in cls.FALSEY:
            return False
        if raw.lower() in cls.TRUTHY:
            return True
        # Backward-compatible with a refresh token accidentally stored in this var.
        return True

    @classmethod
    def _refresh_token(cls) -> str:
        refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip()
        if refresh_token:
            return refresh_token

        auto_refresh = os.getenv("YOUTUBE_AUTO_REFRESH", "").strip()
        if auto_refresh.lower() not in cls.TRUTHY | cls.FALSEY:
            return auto_refresh
        return ""

    def _access_token(self) -> str:
        if not self._refresh_enabled():
            return os.getenv("YOUTUBE_ACCESS_TOKEN", "").strip()

        refresh_token = self._refresh_token()
        client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
        client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
        missing = [
            name
            for name, value in {
                "YOUTUBE_REFRESH_TOKEN": refresh_token,
                "YOUTUBE_CLIENT_ID": client_id,
                "YOUTUBE_CLIENT_SECRET": client_secret,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing YouTube OAuth refresh config: {', '.join(missing)}")

        data = self._http_form(
            "POST",
            "https://oauth2.googleapis.com/token",
            payload={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError(f"Unexpected YouTube token refresh response: {data}")
        return access_token

    @staticmethod
    def _build_multipart_body(metadata: dict, media_bytes: bytes, media_content_type: str) -> tuple[bytes, str]:
        boundary = f"===============codex_{uuid4().hex}=="
        meta_json = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                meta_json,
                b"\r\n",
                f"--{boundary}\r\n".encode("utf-8"),
                f"Content-Type: {media_content_type}\r\n\r\n".encode("utf-8"),
                media_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        return body, boundary

    def post(self, row: ManifestRow, ctx: AdapterContext) -> PostResult:
        if ctx.dry_run:
            return PostResult(True, "youtube", external_id=self._stub_external_id())

        try:
            access_token = self._access_token()
        except Exception as exc:  # noqa: BLE001
            return PostResult(False, "youtube", error=str(exc))
        if not access_token:
            return PostResult(False, "youtube", error="Missing YOUTUBE_ACCESS_TOKEN")

        local_path = ctx.repo_root / row.video_file
        if not local_path.exists():
            return PostResult(False, "youtube", error=f"Missing video file: {row.video_file}")

        title = (row.youtube_title or row.caption or local_path.stem).strip()
        description = row.youtube_description.strip() or f"{row.caption} {row.hashtags}".strip()
        if "#shorts" not in description.lower():
            description = f"{description}\n#Shorts".strip()

        metadata = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "categoryId": os.getenv("YOUTUBE_CATEGORY_ID", "22").strip() or "22",
            },
            "status": {
                "privacyStatus": row.youtube_privacy or "public",
            },
        }

        media_bytes = local_path.read_bytes()
        body, boundary = self._build_multipart_body(metadata, media_bytes, "video/mp4")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Accept": "application/json",
        }
        url = "https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=multipart"

        try:
            data = self._http_json("POST", url, headers=headers, payload=body)
            vid = data.get("id")
            if not vid:
                return PostResult(False, "youtube", error=f"Unexpected YouTube response: {data}")
            return PostResult(True, "youtube", external_id=str(vid), external_url=f"https://www.youtube.com/watch?v={vid}")
        except Exception as exc:  # noqa: BLE001
            return PostResult(False, "youtube", error=str(exc))
