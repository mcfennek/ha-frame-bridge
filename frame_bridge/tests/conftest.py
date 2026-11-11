import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings  # noqa: E402
from app.frame import FrameError, MatteRefused, valid_content_id  # noqa: E402
from app.service import Bridge  # noqa: E402
from app.storage import ImageStore  # noqa: E402
from app.web import create_external_app, create_ingress_app  # noqa: E402


class FakeFrame:
    """Stands in for the TV: remembers uploads and the selected picture."""

    def __init__(self):
        self.images: dict[str, bytes] = {}
        self.current: str | None = None
        self.art_mode = False
        self.fail = False
        self._n = 0
        self.matte_of: dict[str, str] = {}
        self.width_of: dict[str, int] = {}
        self.ignore_matte = False  # like a TV that silently refuses a matte
        self.deleted: list[str] = []

    configured = True
    paired = True

    def _check(self):
        if self.fail:
            raise FrameError("Fernseher nicht erreichbar")

    async def pair(self, timeout=60):
        return True

    async def status(self):
        return {"configured": True, "host": "1.2.3.4", "paired": True, "reachable": not self.fail,
                "power_state": "art" if self.art_mode else "tv", "art_mode": self.art_mode,
                "current": {"content_id": self.current} if self.current else None,
                "api_version": "4.3.4", "error": "down" if self.fail else None}

    async def list_remote(self):
        self._check()
        return [{"content_id": c, "category_id": "MY-C0002", "width": self.width_of.get(c, 3840), "height": 2160,
                 "image_date": "2026:11:11 10:00:00", "matte_id": self.matte_of.get(c, "none")} for c in self.images]

    async def thumbnail(self, content_id):
        self._check()
        if content_id not in self.images:
            raise FrameError("kein Vorschaubild")
        return self.images[content_id][:100]

    async def upload(self, jpeg, matte="none"):
        self._check()
        self._n += 1
        cid = f"MY_F{self._n:04d}"
        self.images[cid] = jpeg
        try:
            self.width_of[cid] = Image.open(io.BytesIO(jpeg)).width
        except Exception:
            self.width_of[cid] = 3840
        # like the real 2025 Frame: a full 4K picture keeps no matte
        self.matte_of[cid] = matte if self.width_of[cid] < 3840 else "none"
        return cid

    async def select(self, content_id, show=True):
        self._check()
        valid_content_id(content_id)
        if content_id not in self.images:
            raise FrameError("unbekannt")
        self.current = content_id

    async def set_artmode(self, on):
        self._check()
        self.art_mode = on

    async def mattes(self):
        self._check()
        return {"types": ["modern", "shadowbox", "flexible"], "colors": ["polar", "antique", "black"]}

    async def change_matte(self, content_id, matte_id):
        self._check()
        if matte_id != "none" and self.width_of.get(content_id, 3840) >= 3840:
            raise MatteRefused("Der Fernseher lässt für dieses Bild kein Passepartout zu.")  # error -7
        if not self.ignore_matte:
            self.matte_of[content_id] = matte_id

    async def delete_remote(self, content_id):
        self._check()
        self.deleted.append(content_id)
        self.images.pop(content_id, None)

    async def close(self):
        pass


class FakeHA:
    available = True

    async def discover(self):
        return [{"device_id": "d1", "name": "The Frame 65", "model": "x",
                 "entities": [{"entity_id": "media_player.frame", "name": "The Frame 65", "state": "on"}]}]

    async def state(self, entity_id):
        return {"state": "on", "attributes": {"friendly_name": "The Frame 65"}}

    async def close(self):
        pass


def make_jpeg(w=1200, h=900, color=(120, 60, 40), fmt="JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path):
    (tmp_path / "options.json").write_text('{"tv_host": "1.2.3.4", "max_upload_mb": 2, "api_enabled": true}')
    settings = load_settings(tmp_path)
    store = ImageStore(settings.db_path, settings.images_dir, settings.thumbs_dir)
    frame = FakeFrame()
    bridge = Bridge(settings, store, frame, FakeHA())
    ingress = TestClient(create_ingress_app(lambda: bridge, allow_any_client=True))
    external = TestClient(create_external_app(lambda: bridge))
    return {"bridge": bridge, "frame": frame, "ingress": ingress, "external": external, "settings": settings}
