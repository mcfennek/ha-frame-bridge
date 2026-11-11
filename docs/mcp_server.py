"""Minimal MCP server for The Frame Bridge (Claude Desktop / Claude Code).

Environment:
  FRAME_BRIDGE_URL    e.g. http://homeassistant.local:8787
  FRAME_BRIDGE_TOKEN  bearer token from the add-on settings

Run: uvx --with mcp --with httpx python mcp_server.py
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("FRAME_BRIDGE_URL", "http://homeassistant.local:8787").rstrip("/")
TOKEN = os.environ["FRAME_BRIDGE_TOKEN"]

mcp = FastMCP("frame-bridge")


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=120)


def _check(r: httpx.Response):
    if r.is_error:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        raise RuntimeError(f"{r.status_code}: {detail}")
    return r.json() if r.content else None


@mcp.tool()
def frame_status() -> dict:
    """Verbindung, Art-Mode-Status und aktuell angezeigtes Bild des Samsung The Frame."""
    with _client() as c:
        return _check(c.get("/api/frame/status"))


@mcp.tool()
def list_images() -> list:
    """Alle in The Frame Bridge gespeicherten Bilder (id, Titel, Datum, Auflösung)."""
    with _client() as c:
        return [{k: i[k] for k in ("id", "title", "created_at", "source_width", "source_height")}
                for i in _check(c.get("/api/images"))]


@mcp.tool()
def upload_image(path: str, title: str = "", mode: str = "fit", display: bool = True) -> dict:
    """Lädt eine lokale JPG/PNG-Datei hoch. mode: 'fit' (ganzes Bild) oder 'crop' (füllt den Bildschirm).
    display=True zeigt das Bild sofort im Art Mode."""
    p = Path(path).expanduser()
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    with _client() as c, p.open("rb") as fh:
        r = c.post("/api/images", files={"file": (p.name, fh, mime)},
                   data={"title": title, "mode": mode, "display": str(display).lower()})
        return _check(r)


@mcp.tool()
def show_image(image_id: str) -> dict:
    """Zeigt ein gespeichertes Bild (id aus list_images) im Art Mode an."""
    with _client() as c:
        return _check(c.post(f"/api/images/{image_id}/display"))


@mcp.tool()
def show_random_image() -> dict:
    """Zeigt ein zufälliges gespeichertes Bild im Art Mode an."""
    with _client() as c:
        return _check(c.post("/api/frame/random"))


if __name__ == "__main__":
    mcp.run()
