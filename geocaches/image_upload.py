"""Image processing and upload helpers for log image attachments."""
from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_VALID_FORMATS = {"JPEG", "PNG", "GIF"}
_MIME_MAP = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif"}


@dataclass
class ImageAttachment:
    file_bytes: bytes
    filename: str
    title: str = ""
    description: str = ""    # GC only (500 chars max)
    is_spoiler: bool = False  # OC only
    rotate: int = 0           # degrees: 0, 90, 180, 270
    max_dimension: int = 1024
    strip_exif: bool = True


@dataclass
class ImageUploadResult:
    filename: str
    gc_url: str = ""
    oc_ok: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.gc_url) or self.oc_ok


def process_image(attachment: ImageAttachment) -> tuple[bytes, str]:
    """
    Process an image: optionally strip EXIF, rotate, resize, re-encode.
    Returns (processed_bytes, mime_type).
    Animated GIFs pass through unmodified.
    """
    from PIL import Image, ExifTags

    buf = io.BytesIO(attachment.file_bytes)
    img = Image.open(buf)
    fmt = img.format or "JPEG"

    if fmt not in _VALID_FORMATS:
        raise ValueError(f"Unsupported image format: {fmt}")

    # Animated GIF — pass through without modification
    if fmt == "GIF":
        try:
            img.seek(1)
            img.seek(0)
            return attachment.file_bytes, "image/gif"
        except EOFError:
            pass  # static GIF — process normally

    # Rotate (before resize so aspect ratio is correct)
    if attachment.rotate:
        img = img.rotate(-attachment.rotate, expand=True)

    # Resize (longest side)
    if attachment.max_dimension and attachment.max_dimension > 0:
        w, h = img.size
        longest = max(w, h)
        if longest > attachment.max_dimension:
            scale = attachment.max_dimension / longest
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # Strip EXIF by re-encoding without metadata
    out = io.BytesIO()
    save_fmt = "JPEG" if fmt in ("JPEG", "GIF") else fmt
    if save_fmt == "JPEG":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if attachment.strip_exif:
            # Re-encode into fresh buffer — drops all metadata
            img.save(out, format="JPEG", quality=85, optimize=True)
        else:
            # Preserve EXIF if present
            exif = img.info.get("exif", b"")
            img.save(out, format="JPEG", quality=85, optimize=True,
                     **({"exif": exif} if exif else {}))
    else:
        img.save(out, format=save_fmt)

    return out.getvalue(), _MIME_MAP.get(save_fmt, "image/jpeg")


def upload_images_to_gc(gc_ref: str, attachments: list[ImageAttachment]) -> list[ImageUploadResult]:
    """Upload a list of processed images to a GC log. Returns one result per image."""
    from geocaches.sync.gc_client import GCClient
    import requests as _requests

    client = GCClient()
    access_token = client._api.access_token
    url = f"https://api.groundspeak.com/v1/geocachelogs/{gc_ref}/images"
    results = []

    for att in attachments:
        r = ImageUploadResult(filename=att.filename)
        try:
            processed_bytes, mime_type = process_image(att)
            b64 = base64.b64encode(processed_bytes).decode("ascii")
            payload = {"base64ImageData": b64}
            if att.title:
                payload["name"] = att.title[:100]
            if att.description:
                payload["description"] = att.description[:500]

            resp = _requests.post(
                url,
                headers={"Authorization": f"bearer {access_token}", "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
            if resp.status_code == 201:
                r.gc_url = resp.json().get("url", "")
                logger.info("GC image uploaded for log %s: %s", gc_ref, r.gc_url)
            else:
                r.error = f"GC image upload failed ({resp.status_code}): {resp.text[:200]}"
                logger.warning("GC image upload error for %s: %s", gc_ref, r.error)
        except Exception as exc:
            r.error = str(exc)
            logger.warning("GC image upload exception for %s/%s: %s", gc_ref, att.filename, exc)
        results.append(r)

    return results


def upload_images_to_oc(platform: str, log_uuid: str, attachments: list[ImageAttachment]) -> list[ImageUploadResult]:
    """Upload a list of processed images to an OC log via OKAPI."""
    from geocaches.sync.oc_client import OCClient
    from accounts.models import UserAccount

    acct = UserAccount.objects.filter(platform=platform).first()
    if not acct:
        return [ImageUploadResult(filename=a.filename, error="No OC account configured") for a in attachments]

    client = OCClient(platform=platform, user_id=acct.user_id)
    results = []

    for att in attachments:
        r = ImageUploadResult(filename=att.filename)
        try:
            processed_bytes, mime_type = process_image(att)
            r.oc_ok, r.error = client.upload_log_image(
                log_uuid, processed_bytes, mime_type,
                caption=att.title,
                is_spoiler=att.is_spoiler,
            )
            if r.oc_ok:
                logger.info("OC image uploaded for log %s on %s", log_uuid, platform)
            else:
                logger.warning("OC image upload failed for %s on %s: %s", log_uuid, platform, r.error)
        except Exception as exc:
            r.error = str(exc)
            logger.warning("OC image upload exception for %s/%s: %s", log_uuid, att.filename, exc)
        results.append(r)

    return results
