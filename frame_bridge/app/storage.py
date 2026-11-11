"""Local image library: metadata in SQLite, files under /data, named only by generated ids."""
from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ID_RE = re.compile(r"^[0-9a-f]{32}$")


class InvalidImageId(ValueError):
    pass


@dataclass
class ImageRecord:
    id: str
    title: str
    created_at: str
    width: int
    height: int
    source_width: int
    source_height: int
    fit_mode: str
    original_name: str
    size_bytes: int
    tv_content_id: str | None = None
    matte: str = "none"

    def to_dict(self) -> dict:
        return asdict(self)


def validate_id(image_id: str) -> str:
    """Ids are 32 hex chars, so they can never contain a path separator or '..'."""
    if not isinstance(image_id, str) or not ID_RE.match(image_id):
        raise InvalidImageId(image_id)
    return image_id


class ImageStore:
    def __init__(self, db_path: Path, images_dir: Path, thumbs_dir: Path):
        self.images_dir = images_dir
        self.thumbs_dir = thumbs_dir
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                source_width INTEGER NOT NULL,
                source_height INTEGER NOT NULL,
                fit_mode TEXT NOT NULL,
                original_name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                tv_content_id TEXT,
                matte TEXT NOT NULL DEFAULT 'none'
            )"""
        )
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(images)")}
        if "matte" not in cols:  # databases from 0.1.x
            self._db.execute("ALTER TABLE images ADD COLUMN matte TEXT NOT NULL DEFAULT 'none'")
        self._db.commit()

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def image_path(self, image_id: str) -> Path:
        return self.images_dir / f"{validate_id(image_id)}.jpg"

    def thumb_path(self, image_id: str) -> Path:
        return self.thumbs_dir / f"{validate_id(image_id)}.jpg"

    def add(self, record: ImageRecord) -> ImageRecord:
        validate_id(record.id)
        if not record.created_at:
            record.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._db.execute(
                "INSERT INTO images VALUES (:id,:title,:created_at,:width,:height,:source_width,"
                ":source_height,:fit_mode,:original_name,:size_bytes,:tv_content_id,:matte)",
                record.to_dict(),
            )
            self._db.commit()
        return record

    def get(self, image_id: str) -> ImageRecord | None:
        validate_id(image_id)
        with self._lock:
            row = self._db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        return ImageRecord(**dict(row)) if row else None

    def list(self) -> list[ImageRecord]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM images ORDER BY created_at DESC").fetchall()
        return [ImageRecord(**dict(r)) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM images").fetchone()[0]

    def set_tv_content_id(self, image_id: str, content_id: str | None) -> None:
        validate_id(image_id)
        with self._lock:
            self._db.execute("UPDATE images SET tv_content_id=? WHERE id=?", (content_id, image_id))
            self._db.commit()

    def set_matte(self, image_id: str, matte: str) -> None:
        validate_id(image_id)
        with self._lock:
            self._db.execute("UPDATE images SET matte=? WHERE id=?", (matte, image_id))
            self._db.commit()

    def find_by_tv_content_id(self, content_id: str) -> ImageRecord | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM images WHERE tv_content_id=?", (content_id,)).fetchone()
        return ImageRecord(**dict(row)) if row else None

    def delete(self, image_id: str) -> bool:
        """Deletes the local copy only. Images on the TV are never touched here."""
        validate_id(image_id)
        with self._lock:
            cur = self._db.execute("DELETE FROM images WHERE id=?", (image_id,))
            self._db.commit()
        for p in (self.image_path(image_id), self.thumb_path(image_id)):
            p.unlink(missing_ok=True)
        return cur.rowcount > 0

    def disk_usage(self) -> int:
        return sum(p.stat().st_size for d in (self.images_dir, self.thumbs_dir) for p in d.glob("*.jpg"))
