"""Local-disk photo storage.

Kept as a small, swappable interface (`save`, `path_for`) so a production
deployment can drop in an S3/GCS-backed implementation without touching
the agents or API routes that call it.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import get_settings


@dataclass(frozen=True)
class StoredPhoto:
    photo_id: str
    storage_path: str
    thumbnail_path: str


def save(raw_bytes: bytes) -> StoredPhoto:
    settings = get_settings()
    settings.photos_dir.mkdir(parents=True, exist_ok=True)
    settings.thumbnails_dir.mkdir(parents=True, exist_ok=True)

    photo_id = uuid.uuid4().hex
    original_path = settings.photos_dir / f"{photo_id}.jpg"
    thumbnail_path = settings.thumbnails_dir / f"{photo_id}.jpg"

    image = Image.open(io.BytesIO(raw_bytes))
    image = image.convert("RGB")
    image.save(original_path, format="JPEG", quality=90)

    thumb = image.copy()
    thumb.thumbnail((settings.thumbnail_max_px, settings.thumbnail_max_px))
    thumb.save(thumbnail_path, format="JPEG", quality=80)

    return StoredPhoto(
        photo_id=photo_id,
        storage_path=str(original_path),
        thumbnail_path=str(thumbnail_path),
    )


def read_bytes(path: str) -> bytes:
    return Path(path).read_bytes()
