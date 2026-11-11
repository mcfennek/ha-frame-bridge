"""Samsung art-channel adapter.

The Home Assistant samsungtv integration has no art functions, so this talks to the TV's art
channel directly, as its own client ("FrameBridge") with its own token in /data. Home Assistant's
pairing and connection are not touched.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)

CLIENT_NAME = "FrameBridge"
PORT = 8002
MY_PHOTOS_CATEGORY = "MY-C0002"
OWN_PHOTO_PREFIX = "MY_"  # own uploads are MY_F…, Art Store pieces SAM-…
CONTENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MATTE_RE = re.compile(r"^(none|[a-z0-9]{1,32}_[a-z0-9]{1,32})$")


class FrameError(Exception):
    """A TV-side failure; the message is shown to the user."""


class FrameNotConfigured(FrameError):
    pass


def valid_content_id(content_id: str) -> str:
    if not isinstance(content_id, str) or not CONTENT_ID_RE.match(content_id):
        raise FrameError("Ungültige Bild-ID des Fernsehers.")
    return content_id


def valid_matte(matte_id: str) -> str:
    """'none' or '<type>_<colour>' as the TV names them, e.g. 'shadowbox_polar'."""
    if not isinstance(matte_id, str) or not MATTE_RE.match(matte_id):
        raise FrameError("Ungültiges Passepartout.")
    return matte_id


class FrameBackend(Protocol):
    @property
    def configured(self) -> bool: ...
    @property
    def paired(self) -> bool: ...
    async def pair(self, timeout: float = 60) -> bool: ...
    async def status(self) -> dict[str, Any]: ...
    async def list_remote(self) -> list[dict[str, Any]]: ...
    async def thumbnail(self, content_id: str) -> bytes: ...
    async def upload(self, jpeg: bytes, matte: str = "none") -> str: ...
    async def select(self, content_id: str, show: bool = True) -> None: ...
    async def set_artmode(self, on: bool) -> None: ...
    async def mattes(self) -> dict[str, list[str]]: ...
    async def change_matte(self, content_id: str, matte_id: str) -> None: ...
    async def close(self) -> None: ...


class SamsungFrame:
    """One long-lived art connection, serialised by a lock and reopened after failures."""

    def __init__(self, host: str, token_file: Path, timeout: float = 10):
        self.host = host
        self.token_file = token_file
        self.timeout = timeout
        self._art = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.host)

    @property
    def paired(self) -> bool:
        return self.token_file.exists() and bool(self.token_file.read_text().strip())

    def _require(self) -> None:
        if not self.configured:
            raise FrameNotConfigured("Keine IP-Adresse des Fernsehers eingestellt (Option tv_host).")

    async def _client(self):
        if self._art is None:
            from samsungframetv.async_art import SamsungTVAsyncArt

            # The constructor does blocking setup, keep it off the event loop.
            self._art = await asyncio.to_thread(
                SamsungTVAsyncArt,
                self.host,
                token_file=str(self.token_file),
                port=PORT,
                timeout=self.timeout,
                name=CLIENT_NAME,
            )
        return self._art

    async def _reset(self) -> None:
        if self._art is not None:
            try:
                await self._art.close()
            except Exception:  # noqa: BLE001 - closing a broken socket may fail any way
                pass
        self._art = None

    async def _call(self, what: str, fn, *args, **kwargs):
        self._require()
        async with self._lock:
            try:
                art = await self._client()
                return await fn(art, *args, **kwargs)
            except FrameError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("%s failed: %r", what, exc)
                await self._reset()
                raise FrameError(f"{what} fehlgeschlagen: {exc or type(exc).__name__}") from exc

    async def pair(self, timeout: float = 60) -> bool:
        self._require()
        async with self._lock:
            await self._reset()
            from samsungframetv.async_art import SamsungTVAsyncArt

            art = await asyncio.to_thread(
                SamsungTVAsyncArt, self.host, token_file=str(self.token_file), port=PORT,
                timeout=self.timeout, name=CLIENT_NAME,
            )
            try:
                token = await art.pair(timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                raise FrameError(f"Kopplung fehlgeschlagen: {exc or type(exc).__name__}") from exc
            finally:
                try:
                    await art.close()
                except Exception:  # noqa: BLE001
                    pass
            return bool(token)

    async def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "configured": self.configured,
            "host": self.host or None,
            "paired": self.paired if self.configured else False,
            "reachable": False,
            "power_state": None,
            "art_mode": None,
            "current": None,
            "api_version": None,
            "error": None,
        }
        if not self.configured:
            result["error"] = "Keine IP-Adresse des Fernsehers eingestellt."
            return result

        async def _do(art):
            power = await art.get_power_state()
            result["power_state"] = power
            result["reachable"] = power != "off"
            if power in ("off",):
                return
            try:
                result["art_mode"] = (await art.get_artmode()) == "on"
                result["api_version"] = await art.get_api_version()
                current = await art.get_current()
                if isinstance(current, dict):
                    result["current"] = {
                        k: current.get(k) for k in ("content_id", "category_id", "matte_id") if k in current
                    }
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"Art-Kanal antwortet nicht: {exc or type(exc).__name__}"

        try:
            await self._call("Statusabfrage", _do)
        except FrameError as exc:
            result["error"] = str(exc)
        return result

    async def list_remote(self) -> list[dict[str, Any]]:
        async def _do(art):
            # The 2025 Frame does not always report own photos under MY-C0002, so fetch everything
            # and recognise own photos by their id.
            items = await art.available(None, timeout=15)
            out = []
            for item in items or []:
                cid = item.get("content_id")
                if not cid or not CONTENT_ID_RE.match(cid) or not cid.startswith(OWN_PHOTO_PREFIX):
                    continue
                out.append(
                    {
                        "content_id": cid,
                        "category_id": item.get("category_id"),
                        "width": _int(item.get("width")),
                        "height": _int(item.get("height")),
                        "image_date": item.get("image_date"),
                        "matte_id": (item.get("matte_id") or "none").lower(),
                    }
                )
            return out

        return await self._call("Bildliste vom Fernseher", _do)

    async def thumbnail(self, content_id: str) -> bytes:
        valid_content_id(content_id)

        async def _do(art):
            data = await art.get_thumbnail(content_id)
            if isinstance(data, dict):
                data = next(iter(data.values()), None)
            if not data:
                raise FrameError("Der Fernseher liefert für dieses Bild kein Vorschaubild.")
            return bytes(data)

        return await self._call("Vorschaubild", _do)

    async def upload(self, jpeg: bytes, matte: str = "none") -> str:
        async def _do(art):
            content_id = await art.upload(
                jpeg, file_type="jpg", matte=matte or "none", portrait_matte=matte or "none", timeout=60
            )
            if not content_id:
                raise FrameError("Der Fernseher hat den Empfang des Bildes nicht bestätigt.")
            return content_id

        return await self._call("Upload zum Fernseher", _do)

    async def select(self, content_id: str, show: bool = True) -> None:
        valid_content_id(content_id)

        async def _do(art):
            await art.select_image(content_id, show=show)

        await self._call("Bildauswahl", _do)

    async def set_artmode(self, on: bool) -> None:
        async def _do(art):
            await art.set_artmode(on)

        await self._call("Art Mode schalten", _do)

    async def mattes(self) -> dict[str, list[str]]:
        """Matte styles and colours this TV offers."""

        async def _do(art):
            types, colours = await art.get_matte_list(include_colour=True)
            return {
                "types": [t["matte_type"] for t in types if t.get("matte_type") and t["matte_type"] != "none"],
                "colors": [c["color"] for c in colours if c.get("color")],
            }

        return await self._call("Passepartout-Liste", _do)

    async def change_matte(self, content_id: str, matte_id: str) -> None:
        valid_content_id(content_id)
        valid_matte(matte_id)

        async def _do(art):
            await art.change_matte(content_id, matte_id, portrait_matte=matte_id)

        await self._call("Passepartout ändern", _do)

    async def close(self) -> None:
        async with self._lock:
            await self._reset()


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
