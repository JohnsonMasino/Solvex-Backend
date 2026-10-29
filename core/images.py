"""
core/images.py  -  ONE place for every Cloudinary operation in the project.

Used by wallet (deposit screenshots), KYC (ID front/back) and profile pictures.

Why server-side uploads?
    The frontend sends the file to OUR API (multipart/form-data). We validate it (size + real file
    type, not just the extension) and only then push it to Cloudinary. The Cloudinary API secret
    therefore never leaves the backend, and nobody can upload arbitrary files to your account.

Private vs public images
    private=True  (default) -> stored with delivery type "authenticated". The raw URL does NOT work;
                               only URLs signed by our backend (image_url / thumbnail_url) do.
                               Use for payment proofs, KYC documents, anything sensitive.
    private=False           -> stored with delivery type "upload" (normal public URL).
                               Fine for things like avatars.
    IMPORTANT: use the same `private` value when you upload, build URLs and delete.

What we store in the database
    Only the Cloudinary `public_id` (a short string). URLs are generated on demand, so you can change
    sizes/transformations later without touching the data.

Typical usage
    data, kind = read_and_validate(request.FILES["id_front"])      # returns a TUPLE (bytes, type)
    public_id = upload_image(data, folder="kyc/1", private=True)    # upload_image wants the BYTES
"""
import io
import logging
import uuid
from typing import Optional

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from django.conf import settings

from core.exceptions import BusinessError

logger = logging.getLogger(__name__)

ALLOWED_FORMATS = ["jpg", "png", "webp", "heic"]
ALLOWED_LABEL = "JPG, PNG, WEBP or HEIC"
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def detect_image_type(head: bytes) -> Optional[str]:
    """Identify the REAL file type from its first bytes (the filename / Content-Type can be faked)."""
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS:
        return "heic"
    return None


def read_and_validate(uploaded, *, max_mb=None):
    """Validate a Django UploadedFile. Returns (bytes, detected_type) or raises BusinessError."""
    max_mb = max_mb or settings.IMAGE_UPLOAD_MAX_MB
    max_bytes = int(max_mb * 1024 * 1024)

    size = getattr(uploaded, "size", None)
    if size is not None and size > max_bytes:
        raise BusinessError(
            "image_too_large", f"The image is too large. The maximum size is {max_mb} MB.",
            data={"max_mb": max_mb},
        )
    uploaded.seek(0)
    data = uploaded.read(max_bytes + 1)
    if not data:
        raise BusinessError("invalid_image", "The uploaded file is empty.")
    if len(data) > max_bytes:
        raise BusinessError(
            "image_too_large", f"The image is too large. The maximum size is {max_mb} MB.",
            data={"max_mb": max_mb},
        )
    kind = detect_image_type(data[:16])
    if kind is None:
        raise BusinessError("invalid_image", f"Upload a {ALLOWED_LABEL} image.")
    return data, kind


def _as_bytes(data) -> bytes:
    """Turn whatever the caller passed into plain bytes for upload.

    Accepts:
      * bytes / bytearray / memoryview
      * the (bytes, kind) tuple that read_and_validate() returns (this was the cause of
        "a bytes-like object is required, not 'tuple'")
      * a Django UploadedFile (it is validated with read_and_validate first)
    """
    if isinstance(data, (tuple, list)):
        if not data:
            raise TypeError("upload_image() received an empty tuple")
        data = data[0]

    if hasattr(data, "read"):  # an UploadedFile / file-like object
        data, _kind = read_and_validate(data)

    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)

    if not isinstance(data, bytes):
        raise TypeError(f"upload_image() needs image bytes, got {type(data).__name__}")
    return data


# --------------------------------------------------------------------------- #
# Cloudinary operations
# --------------------------------------------------------------------------- #
def is_configured() -> bool:
    cfg = cloudinary.config()
    return bool(cfg.cloud_name and cfg.api_key and cfg.api_secret)


def _delivery_type(private: bool) -> str:
    return "authenticated" if private else "upload"


def upload_image(data, *, folder: str, private: bool = True, tags=None, public_id: str = "") -> str:
    """Upload image bytes and return the Cloudinary public_id (folder included).

    `data` should be the raw bytes. For safety it also accepts the (bytes, kind) tuple from
    read_and_validate() or an UploadedFile, so a caller mistake no longer breaks the upload.
    """
    data = _as_bytes(data)  # BusinessError from validation passes through; a wrong type raises TypeError

    if not is_configured():
        logger.error("Cloudinary is not configured (set CLOUDINARY_URL).")
        raise BusinessError(
            "upload_unavailable", "Image uploads are temporarily unavailable. Please try again later.", status=503
        )
    try:
        result = cloudinary.uploader.upload(
            io.BytesIO(data),
            folder=folder,
            public_id=public_id or uuid.uuid4().hex,
            resource_type="image",
            type=_delivery_type(private),
            overwrite=False,
            unique_filename=False,
            use_filename=False,
            allowed_formats=ALLOWED_FORMATS,
            tags=list(tags or []),
            timeout=settings.CLOUDINARY_UPLOAD_TIMEOUT,
        )
    except Exception:  # network error, bad credentials, rejected format ...
        logger.exception("Cloudinary upload failed")
        raise BusinessError(
            "upload_failed", "We couldn't upload your image. Please try again.", status=502
        )
    return result["public_id"]


def delete_image(public_id: str, *, private: bool = True) -> None:
    """Best-effort delete. Never raises (a leftover file must not break a user request)."""
    if not public_id:
        return
    try:
        cloudinary.uploader.destroy(
            public_id, resource_type="image", type=_delivery_type(private), invalidate=True
        )
    except Exception:
        logger.exception("Cloudinary delete failed for %s", public_id)


def image_url(public_id: str, *, private: bool = True, **options) -> Optional[str]:
    """HTTPS URL for a stored image. Private images get a signed URL. `options` are Cloudinary
    transformations (width=, height=, crop=, gravity=, fetch_format=, quality= ...)."""
    if not public_id:
        return None
    try:
        url, _ = cloudinary.utils.cloudinary_url(
            public_id,
            secure=True,
            resource_type="image",
            type=_delivery_type(private),
            sign_url=private,
            **options,
        )
        return url
    except Exception:  # e.g. cloud_name missing
        logger.exception("Could not build Cloudinary URL for %s", public_id)
        return None


def thumbnail_url(public_id: str, *, width: int = 320, private: bool = True) -> Optional[str]:
    return image_url(
        public_id, private=private, width=width, crop="limit", fetch_format="auto", quality="auto"
    )