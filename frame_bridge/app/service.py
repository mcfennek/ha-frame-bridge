"""Business logic shared by the web UI and the REST API."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

from . import imaging
from .config import Settings
from .frame import FrameBackend, FrameError, valid_matte
from .ha import HomeAssistant
from .storage import ImageRecord, ImageStore

_LOGGER = logging.getLogger(__name__)

MAX_TITLE = 120


def clean_title(title: str | None, fallback: str) -> str:
    t = " ".join((title or "").split())[:MAX_TITLE]
    if t:
        return t
    stem = fallback.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0]
    return " ".join(stem.replace("_", " ").replace("-", " ").split())[:MAX_TITLE] or "Ohne Titel"


class Bridge:
    def __init__(self, settings: Settings, store: ImageStore, frame: FrameBackend, ha: HomeAssistant):
        self.settings = settings
        self.store = store
        self.frame = frame
        self.ha = ha

    # ---------- local library ----------
    async def add_image(
        self, data: bytes, filename: str, title: str | None, mode: str = "fit", display: bool = False,
        matte: str | None = None,
    ) -> tuple[ImageRecord, str | None]:
        matte = valid_matte(matte or self.settings.matte or "none")
        prepared = await asyncio.to_thread(imaging.prepare, data, mode)
        image_id = self.store.new_id()
        self.store.image_path(image_id).write_bytes(prepared.jpeg)
        self.store.thumb_path(image_id).write_bytes(prepared.thumb)
        record = ImageRecord(
            id=image_id,
            title=clean_title(title, filename or ""),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            width=prepared.width,
            height=prepared.height,
            source_width=prepared.source_width,
            source_height=prepared.source_height,
            fit_mode=mode,
            original_name=(filename or "")[-200:],
            size_bytes=len(prepared.jpeg),
            matte=matte,
        )
        self.store.add(record)
        display_error = None
        if display:
            try:
                await self.display(image_id)
                record = self.store.get(image_id) or record
            except FrameError as exc:
                display_error = str(exc)
        return record, display_error

    # ---------- TV ----------
    async def display(self, image_id: str, art_mode: bool = True) -> ImageRecord:
        record = self.store.get(image_id)
        if record is None:
            raise KeyError(image_id)
        content_id = record.tv_content_id
        if content_id:
            try:
                await self.frame.select(content_id)
            except FrameError:
                _LOGGER.info("content %s not selectable any more, uploading again", content_id)
                content_id = None
        if not content_id:
            jpeg = self.store.image_path(image_id).read_bytes()
            content_id = await self.frame.upload(jpeg, matte=record.matte)
            self.store.set_tv_content_id(image_id, content_id)
            await self.frame.select(content_id)
        if art_mode:
            await self.frame.set_artmode(True)
        return self.store.get(image_id) or record

    async def display_random(self) -> dict[str, Any]:
        """Picks from the user's own pictures on the TV and in the local gallery, never the one
        showing now. TV pictures are selected directly, local ones are uploaded if needed."""
        st = await self.frame.status()
        current = (st.get("current") or {}).get("content_id")
        try:
            remote_ids = [i["content_id"] for i in await self.frame.list_remote()]
        except FrameError:
            remote_ids = []
        local = self.store.list()
        on_tv_local = {i.tv_content_id for i in local if i.tv_content_id in remote_ids}

        # one entry per picture: TV pictures by content id, local ones not on the TV by local id
        candidates: list[tuple[str, str]] = [("tv", cid) for cid in remote_ids if cid != current]
        candidates += [("local", i.id) for i in local
                       if i.tv_content_id not in on_tv_local and (not current or i.tv_content_id != current)]
        if not candidates:
            if not remote_ids and not local:
                raise LookupError("Es gibt noch keine eigenen Bilder – lade zuerst eines hoch.")
            raise LookupError("Es gibt nur ein Bild, und das wird bereits angezeigt.")

        kind, ref = random.choice(candidates)
        if kind == "local":
            rec = await self.display(ref)
            return {"source": "local", "content_id": rec.tv_content_id, "title": rec.title, "image": rec}
        await self.frame.select(ref)
        await self.frame.set_artmode(True)
        rec = self.store.find_by_tv_content_id(ref)
        return {"source": "tv", "content_id": ref, "title": rec.title if rec else ref, "image": rec}

    async def mattes(self) -> dict[str, list[str]]:
        if not getattr(self, "_mattes", None):
            self._mattes = await self.frame.mattes()
        return self._mattes

    async def _apply_matte_on_tv(self, content_id: str, matte: str) -> None:
        """change_matte gives no reply on failure, so read the TV's list back to be sure."""
        await self.frame.change_matte(content_id, matte)
        for item in await self.frame.list_remote():
            if item["content_id"] == content_id:
                if (item.get("matte_id") or "none") != matte:
                    raise FrameError(
                        "Der Fernseher hat dieses Passepartout für das Bild nicht übernommen. "
                        "Manche Passepartouts lässt er nur bei bestimmten Bildgrößen zu – bitte ein anderes wählen."
                    )
                return
        raise FrameError("Das Bild ist nicht mehr auf dem Fernseher.")

    async def set_local_matte(self, image_id: str, matte: str) -> ImageRecord:
        """Remembered for the next upload; changed on the TV right away if the picture is there."""
        valid_matte(matte)
        record = self.store.get(image_id)
        if record is None:
            raise KeyError(image_id)
        if record.tv_content_id:
            try:
                remote = {i["content_id"] for i in await self.frame.list_remote()}
            except FrameError:
                remote = set()
            if record.tv_content_id in remote:
                await self._apply_matte_on_tv(record.tv_content_id, matte)
        self.store.set_matte(image_id, matte)
        return self.store.get(image_id) or record

    async def set_remote_matte(self, content_id: str, matte: str) -> None:
        valid_matte(matte)
        await self._apply_matte_on_tv(content_id, matte)
        local = self.store.find_by_tv_content_id(content_id)
        if local:
            self.store.set_matte(local.id, matte)

    async def remote_images(self) -> dict[str, Any]:
        """Only what the TV really reports; never invented entries."""
        try:
            items = await self.frame.list_remote()
        except FrameError as exc:
            return {"supported": False, "images": [], "message": str(exc)}
        for item in items:
            local = self.store.find_by_tv_content_id(item["content_id"])
            item["local_id"] = local.id if local else None
            item["local_title"] = local.title if local else None
        return {"supported": True, "images": items, "message": None}

    async def remote_thumbnail(self, content_id: str) -> bytes:
        cache = self.settings.tv_thumbs_dir / f"{content_id}.jpg"
        if cache.exists():
            return cache.read_bytes()
        data = await self.frame.thumbnail(content_id)
        cache.write_bytes(data)
        return data

    async def status(self) -> dict[str, Any]:
        frame_status = await self.frame.status()
        ha_state = None
        entity = self.settings.media_player_entity
        if entity:
            try:
                s = await self.ha.state(entity)
                if s:
                    ha_state = {"entity_id": entity, "state": s.get("state"),
                                "name": s.get("attributes", {}).get("friendly_name")}
            except Exception as exc:  # noqa: BLE001
                ha_state = {"entity_id": entity, "state": None, "error": str(exc)}
        current = frame_status.get("current") or {}
        local = self.store.find_by_tv_content_id(current["content_id"]) if current.get("content_id") else None
        return {
            "frame": frame_status,
            "home_assistant": ha_state,
            "current_local_image": local.to_dict() if local else None,
            "image_count": self.store.count(),
            "storage_bytes": self.store.disk_usage(),
        }
