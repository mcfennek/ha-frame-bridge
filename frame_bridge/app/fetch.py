"""Downloads a file the AI assistant points to (e.g. ChatGPT's openaiFileIdRefs download_link).

Only public HTTPS addresses are fetched: every address a host name resolves to must be a public
internet address, checked again on each redirect, so a link can never reach Home Assistant, the
Supervisor, the TV or anything else in the home network.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import aiohttp

MAX_REDIRECTS = 3
TIMEOUT = aiohttp.ClientTimeout(total=60, sock_connect=10)


class FetchError(ValueError):
    """Shown to the caller as is."""


async def check_public_https(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise FetchError("Nur https-Adressen werden geladen.")
    if parts.username or parts.password:
        raise FetchError("Adressen mit Zugangsdaten werden nicht geladen.")
    host = parts.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise FetchError(f"Adresse {host} ist nicht auflösbar.") from None
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise FetchError(f"Adresse {host} ist nicht auflösbar.")
    for addr in addresses:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
        if not ip.is_global:
            raise FetchError("Adressen im lokalen Netz werden nicht geladen.")


async def fetch_public(url: str, max_bytes: int) -> tuple[bytes, str | None]:
    """Returns (content, content type)."""
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        for _ in range(MAX_REDIRECTS + 1):
            await check_public_https(url)
            async with session.get(url, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise FetchError("Weiterleitung ohne Ziel.")
                    url = urljoin(url, location)
                    continue
                if resp.status != 200:
                    raise FetchError(f"Download fehlgeschlagen (HTTP {resp.status}). Der Link ist evtl. abgelaufen.")
                if resp.content_length and resp.content_length > max_bytes:
                    raise FetchError(f"Datei zu groß (maximal {max_bytes // (1024 * 1024)} MB).")
                chunks, size = [], 0
                async for chunk in resp.content.iter_chunked(256 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FetchError(f"Datei zu groß (maximal {max_bytes // (1024 * 1024)} MB).")
                    chunks.append(chunk)
                return b"".join(chunks), resp.headers.get("Content-Type")
        raise FetchError("Zu viele Weiterleitungen.")
