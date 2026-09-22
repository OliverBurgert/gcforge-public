"""Image processing and upload helpers for log image attachments."""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

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
    from PIL import Image

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


def upload_images_to_gc_trackable_log(
    client, tb_log_ref: str, attachments: list[ImageAttachment],
) -> list[ImageUploadResult]:
    """Upload images to a GC trackable log via the trackable client that
    submitted it. Returns one result per image.

    Only the official-API trackable client can attach images; the website
    backend has no TB-log image upload, so each image reports an error there.
    """
    upload = getattr(client, "upload_trackable_log_image", None)
    results = []

    for att in attachments:
        r = ImageUploadResult(filename=att.filename)
        if upload is None:
            r.error = "image upload is not supported for trackable logs submitted via the website"
            results.append(r)
            continue
        try:
            processed_bytes, mime_type = process_image(att)
            resp = upload(
                tb_log_ref, processed_bytes, mime_type,
                name=att.title, description=att.description,
            )
            r.gc_url = resp.get("url", "")
            logger.info("GC TB-log image uploaded for %s: %s", tb_log_ref, r.gc_url)
        except Exception as exc:  # noqa: BLE001
            r.error = str(exc)
            logger.warning("TB image upload exception for %s/%s: %s", tb_log_ref, att.filename, exc)
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
