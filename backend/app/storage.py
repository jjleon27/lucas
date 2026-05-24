"""
Pluggable storage layer. Writes uploaded images to local disk by default,
or to S3 when STORAGE_BACKEND=s3. Returns a URL the frontend can load.
"""
import os
import uuid
from pathlib import Path
from typing import Optional

from .config import settings


def _local_save(data: bytes, ext: str) -> str:
    Path(settings.local_storage_dir).mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.{ext.lstrip('.')}"
    fpath = Path(settings.local_storage_dir) / fname
    fpath.write_bytes(data)
    # Served back via the /files static mount in main.py
    return f"/files/{fname}"


def _s3_save(data: bytes, ext: str) -> str:
    import boto3
    client = boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
    )
    key = f"uploads/{uuid.uuid4().hex}.{ext.lstrip('.')}"
    client.put_object(Bucket=settings.aws_bucket, Key=key, Body=data, ContentType=f"image/{ext}")
    return f"https://{settings.aws_bucket}.s3.{settings.aws_region}.amazonaws.com/{key}"


def save_image(data: bytes, filename: str) -> str:
    """Persist an uploaded image and return a URL to it."""
    ext = (os.path.splitext(filename)[1] or ".png").lstrip(".").lower() or "png"
    if settings.storage_backend == "s3" and settings.aws_bucket:
        return _s3_save(data, ext)
    return _local_save(data, ext)


def read_local(url: str) -> Optional[bytes]:
    """For tests / debugging."""
    if not url.startswith("/files/"):
        return None
    path = Path(settings.local_storage_dir) / url.removeprefix("/files/")
    return path.read_bytes() if path.exists() else None
