"""Business logic shared by the web UI and the REST API."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

from . import imaging
from .fetch import FetchError, fetch_public
from .config import Settings
from .frame import FrameBackend, FrameError, MatteRefused, valid_matte
from .ha import HomeAssistant
from .storage import ImageRecord, ImageStore

_LOGGER = logging.getLogger(__name__)

MAX_TITLE = 120

FULL, MATTE = "full", "matte"


def variant_for(matte: str) -> str:
    return FULL if (matte or "none") == "none" else MATTE



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
        self.fetch = fetch_public  # replaceable in tests

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

    async def import_files(
        self, refs: list[dict[str, Any]], title: str | None, mode: str, display: bool, matte: str | None,
    ) -> dict[str, Any]:
        """Takes files an AI assistant hands over by download link (ChatGPT: openaiFileIdRefs).
        Every file is stored; with display=True the last one that arrived is shown."""
        stored: list[ImageRecord] = []
        errors: list[str] = []
        for i, ref in enumerate(refs):
            name = str(ref.get("name") or f"bild-{i + 1}.png")[-200:]
            link = ref.get("download_link")
            if not link:
                errors.append(f"{name}: kein Download-Link übergeben.")
                continue
            try:
                data, _ctype = await self.fetch(link, self.settings.max_upload_bytes)
                t = title if (title and len(refs) == 1) else (f"{title} {i + 1}" if title else None)
                rec, _ = await self.add_image(data, name, t, mode, False, matte)
                stored.append(rec)
            except (FetchError, imaging.ImageError, FrameError) as exc:
                errors.append(f"{name}: {exc}")
        display_error = None
        if display and stored:
            try:
                stored[-1] = await self.display(stored[-1].id)
            except FrameError as exc:
                display_error = str(exc)
        return {"images": stored, "errors": errors, "displayed": bool(display and stored and not display_error),
                "display_error": display_error}

    # ---------- TV ----------
    async def display(self, image_id: str, art_mode: bool = True) -> ImageRecord:
        record = self.store.get(image_id)
        if record is None:
            raise KeyError(image_id)
        content_id = None
        for cid in self._copy_candidates(record, record.matte):
            try:
                await self.frame.select(cid)
                content_id = cid
                self.store.set_tv_content_id(image_id, cid, variant_for(record.matte), record.matte)
                break
            except FrameError:
                _LOGGER.info("content %s not selectable any more", cid)
                self.store.forget_copy(cid)
                record = self.store.get(image_id) or record
        if not content_id:
            content_id = await self._upload_variant(record)
            await self.frame.select(content_id)
        if art_mode:
            await self.frame.set_artmode(True)
        return self.store.get(image_id) or record

    async def _upload_variant(self, record: ImageRecord) -> str:
        """4K without matte; 1920x1080 with matte, because the TV refuses mattes on 4K pictures."""
        jpeg = self.store.image_path(record.id).read_bytes()
        variant = variant_for(record.matte)
        if variant == MATTE:
            jpeg = await asyncio.to_thread(imaging.reduce_for_matte, jpeg)
        content_id = await self.frame.upload(jpeg, matte=record.matte)
        self.store.set_tv_content_id(record.id, content_id, variant, record.matte)
        return content_id

    def _copy_candidates(self, record: ImageRecord, matte: str, remote: dict[str, dict] | None = None) -> list[str]:
        """Copies of this picture that already show `matte` (by the TV's own list when given),
        current copy first. Without a matte only the full-resolution copy counts."""
        want_variant = variant_for(matte)
        out = []
        for c in self.store.copies(record.id):
            cid = c["content_id"]
            if c["variant"] != want_variant:
                continue
            if remote is not None:
                if cid not in remote or (remote[cid].get("matte_id") or "none") != matte:
                    continue
            elif c["matte"] != matte:
                continue
            out.append(cid)
        out.sort(key=lambda cid: cid != record.tv_content_id)
        return out

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
        _LOGGER.info("Passepartout %s für %s", matte, content_id)
        await self.frame.change_matte(content_id, matte)
        for item in await self.frame.list_remote():
            if item["content_id"] == content_id:
                if (item.get("matte_id") or "none") != matte:
                    _LOGGER.warning("TV kept matte %s for %s instead of %s", item.get("matte_id"), content_id, matte)
                    raise MatteRefused("Der Fernseher hat dieses Passepartout für das Bild nicht übernommen.")
                return
        raise FrameError("Das Bild ist nicht mehr auf dem Fernseher.")

    async def set_local_matte(self, image_id: str, matte: str) -> tuple[ImageRecord, str | None]:
        """Sets the matte of a gallery picture, in this order:
        1. a copy with that matte already on the TV is reused (selected, nothing uploaded),
        2. the current copy is changed in place, if the TV allows it,
        3. otherwise a fitting copy is uploaded (4K without matte, 1920x1080 with matte).
        Returns the record and, only in case 3, the copy that was superseded; it stays on the TV
        until the user decides to delete it."""
        valid_matte(matte)
        record = self.store.get(image_id)
        if record is None:
            raise KeyError(image_id)
        try:
            remote = {i["content_id"]: i for i in await self.frame.list_remote()}
        except FrameError:
            self.store.set_matte(image_id, matte)  # TV unreachable: applies on next display
            return self.store.get(image_id) or record, None
        for c in self.store.copies(image_id):  # copies deleted on the TV in the meantime
            if c["content_id"] not in remote:
                self.store.forget_copy(c["content_id"])
        record = self.store.get(image_id) or record
        cur = record.tv_content_id
        showing = False
        if cur:
            try:
                showing = ((await self.frame.status()).get("current") or {}).get("content_id") == cur
            except FrameError:
                pass

        # 1. reuse
        reuse = self._copy_candidates(record, matte, remote)
        if reuse:
            cid = reuse[0]
            self.store.set_matte(image_id, matte)
            self.store.set_tv_content_id(image_id, cid, variant_for(matte), matte)
            if showing and cid != cur:
                await self.frame.select(cid)
            _LOGGER.info("Passepartout %s: vorhandene Kopie %s wiederverwendet", matte, cid)
            return self.store.get(image_id) or record, None

        # 2. in place
        if cur and record.tv_variant == variant_for(matte):
            try:
                await self._apply_matte_on_tv(cur, matte)
                self.store.set_matte(image_id, matte)
                self.store.set_copy_matte(cur, matte)
                return self.store.get(image_id) or record, None
            except MatteRefused:
                _LOGGER.info("TV refused matte %s on %s, uploading a fitting copy", matte, cur)

        self.store.set_matte(image_id, matte)
        record = self.store.get(image_id) or record
        if not cur:
            return record, None  # not on the TV yet: the right copy goes up on the next display

        # 3. upload
        new_cid = await self._upload_variant(record)
        if showing:
            await self.frame.select(new_cid)
        return self.store.get(image_id) or record, cur

    async def set_remote_matte(self, content_id: str, matte: str) -> str | None:
        """Returns the content id of a replaced copy (see set_local_matte), else None."""
        valid_matte(matte)
        local = self.store.find_by_tv_content_id(content_id)
        if local:  # from our gallery: we have the original and can upload a fitting copy
            return (await self.set_local_matte(local.id, matte))[1]
        try:
            await self._apply_matte_on_tv(content_id, matte)
        except MatteRefused:
            raise MatteRefused(
                "Der Fernseher lässt bei diesem Bild kein Passepartout zu – bei Bildern in voller "
                "4K-Auflösung ist das so. Lade das Bild über The Frame Bridge hoch; dann wird für das "
                "Passepartout automatisch eine passende Version erstellt."
            ) from None
        return None

    async def delete_remote(self, content_id: str) -> None:
        """Deletes a picture on the TV. Only reachable from the web UI after explicit confirmation."""
        if not content_id.startswith("MY_"):
            raise FrameError("Nur eigene Bilder können gelöscht werden, keine Werke aus dem Art Store.")
        await self.frame.delete_remote(content_id)
        self.store.forget_copy(content_id)
        (self.settings.tv_thumbs_dir / f"{content_id}.jpg").unlink(missing_ok=True)

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
