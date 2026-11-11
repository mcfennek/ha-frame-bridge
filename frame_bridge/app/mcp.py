"""MCP server (Streamable HTTP, stateless, JSON responses) for ChatGPT plugins and Claude connectors.

Only what a chat assistant should do is offered. Deleting pictures on the TV is not.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import __version__
from .fetch import FetchError
from .frame import FrameError, valid_matte
from .imaging import FIT_MODES, ImageError
from .service import Bridge
from .storage import InvalidImageId

_LOGGER = logging.getLogger(__name__)

PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
SECURITY = [{"type": "oauth2", "scopes": ["frame"]}]

INSTRUCTIONS = (
    "Steuert einen Samsung The Frame im Art Mode. Ein Bild aus dem Gespräch zeigst du mit import_image "
    "(display=true). passepartout=true bedeutet 'mit Rahmen' im Standardstil des Nutzers, false 'ohne'; "
    "lass es weg, wenn der Nutzer nichts dazu sagt. Antworte kurz und sag, was auf dem Fernseher passiert ist."
)

_FILE = {
    "type": "object",
    "description": "Bilddatei aus dem Gespräch",
    "properties": {
        "download_url": {"type": "string"}, "file_id": {"type": "string"},
        "mime_type": {"type": "string"}, "file_name": {"type": "string"},
    },
    "required": ["download_url", "file_id"],
}
_IMAGE_ID = {"type": "string", "description": "ID eines gespeicherten Bildes aus list_images"}
_PASSEPARTOUT = {"type": "boolean", "description": "true: mit Rahmen (Standard-Passepartout des Nutzers), false: ohne"}
_MATTE = {"type": "string", "description": "Nur für einen bestimmten Stil: <stil>_<farbe>, z. B. 'modern_polar' (siehe list_mattes)"}


def _tool(name, title, description, props=None, required=None, read_only=False, meta=None):
    t = {
        "name": name, "title": title, "description": description,
        "inputSchema": {"type": "object", "properties": props or {}, "required": required or [],
                        "additionalProperties": False},
        "annotations": {"title": title, "readOnlyHint": read_only, "destructiveHint": False,
                        "idempotentHint": read_only, "openWorldHint": False},
        "_meta": {"securitySchemes": SECURITY, **(meta or {})},
    }
    t["securitySchemes"] = SECURITY
    return t


TOOLS = [
    _tool("get_frame_status", "Status des Fernsehers",
          "Ob der Fernseher erreichbar ist, ob der Art Mode an ist und welches Bild gerade angezeigt wird.",
          read_only=True),
    _tool("list_images", "Gespeicherte Bilder", "Alle Bilder der Galerie mit id, Titel, Datum und Passepartout.",
          read_only=True),
    _tool("list_tv_images", "Bilder auf dem Fernseher",
          "Eigene Bilder, die auf dem Fernseher liegen (ohne Art-Store-Werke), mit Passepartout.", read_only=True),
    _tool("list_mattes", "Verfügbare Passepartouts",
          "Stile und Farben, die der Fernseher anbietet. Ein Passepartout heißt '<stil>_<farbe>'.", read_only=True),
    _tool("import_image", "Bild auf den Frame bringen",
          "Speichert ein Bild aus dem Gespräch (vom Nutzer hochgeladen oder selbst erzeugt) in der Galerie und zeigt "
          "es mit display=true sofort im Art Mode. Alternativ image_url mit einer öffentlichen https-Adresse. "
          "JPG oder PNG, ideal im Querformat 16:9.",
          {
              "image": _FILE,
              "image_url": {"type": "string", "description": "Öffentliche https-Adresse eines Bildes (statt image)"},
              "title": {"type": "string", "description": "Kurzer Titel für die Galerie"},
              "display": {"type": "boolean", "description": "Sofort anzeigen (Standard true)"},
              "mode": {"type": "string", "enum": list(FIT_MODES),
                       "description": "fit: ganzes Bild sichtbar (Standard), crop: füllt den Bildschirm"},
              "passepartout": _PASSEPARTOUT,
              "matte": _MATTE,
          },
          meta={"openai/fileParams": ["image"]}),
    _tool("show_image", "Gespeichertes Bild anzeigen", "Zeigt ein Bild aus list_images im Art Mode an.",
          {"image_id": _IMAGE_ID}, ["image_id"]),
    _tool("show_random_image", "Zufälliges Bild anzeigen", "Zeigt ein zufälliges eigenes Bild, nie das gerade angezeigte."),
    _tool("set_image_matte", "Passepartout ändern",
          "Mit oder ohne Rahmen für ein gespeichertes Bild (passepartout), oder ein bestimmter Stil (matte).",
          {"image_id": _IMAGE_ID, "passepartout": _PASSEPARTOUT, "matte": _MATTE}, ["image_id"]),
    _tool("set_art_mode", "Art Mode schalten", "Art Mode ein- oder ausschalten.",
          {"on": {"type": "boolean"}}, ["on"]),
]
TOOL_NAMES = {t["name"] for t in TOOLS}


class ToolError(Exception):
    """Reported to the assistant as a tool result with isError, message shown as is."""


def _image_summary(rec) -> dict:
    return {"id": rec.id, "title": rec.title, "created_at": rec.created_at, "matte": rec.matte,
            "on_tv_as": rec.tv_content_id, "original": f"{rec.source_width}x{rec.source_height}"}


def _record(bridge: Bridge, args: dict):
    try:
        rec = bridge.store.get(str(args.get("image_id", "")))
    except InvalidImageId:
        rec = None
    if rec is None:
        raise ToolError("Bild nicht gefunden – erst list_images aufrufen.")
    return rec


def _matte_arg(args: dict, bridge: Bridge, required: bool) -> str | None:
    if args.get("matte") is not None:
        value = args["matte"]
    elif args.get("passepartout") is not None:
        value = bridge.settings.ai_matte if args["passepartout"] else "none"
    elif required:
        raise ToolError("passepartout (true/false) oder matte angeben.")
    else:
        return None
    try:
        return valid_matte(str(value))
    except FrameError as exc:
        raise ToolError(str(exc)) from None


async def call_tool(bridge: Bridge, name: str, args: dict) -> dict:
    if name == "get_frame_status":
        s = await bridge.status()
        f = s["frame"]
        return {"reachable": f["reachable"], "paired": f["paired"], "art_mode": f["art_mode"],
                "showing": (s["current_local_image"] or {}).get("title") or (f.get("current") or {}).get("content_id"),
                "showing_content_id": (f.get("current") or {}).get("content_id"),
                "gallery_images": s["image_count"], "error": f["error"]}
    if name == "list_images":
        return {"images": [_image_summary(r) for r in bridge.store.list()]}
    if name == "list_tv_images":
        res = await bridge.remote_images()
        return {"supported": res["supported"], "message": res["message"],
                "images": [{"content_id": i["content_id"], "matte": i.get("matte_id"),
                            "size": f"{i.get('width')}x{i.get('height')}", "gallery_title": i.get("local_title")}
                           for i in res["images"]]}
    if name == "list_mattes":
        return await bridge.mattes()
    if name == "import_image":
        ref = args.get("image")
        if isinstance(ref, dict) and ref.get("download_url"):
            refs = [{"name": ref.get("file_name"), "download_link": ref["download_url"]}]
        elif args.get("image_url"):
            refs = [{"name": None, "download_link": str(args["image_url"])}]
        else:
            raise ToolError("Kein Bild übergeben (image oder image_url).")
        mode = args.get("mode") or "fit"
        if mode not in FIT_MODES:
            raise ToolError("mode muss 'fit' oder 'crop' sein.")
        res = await bridge.import_files(refs, args.get("title"), mode, bool(args.get("display", True)),
                                        _matte_arg(args, bridge, False))
        if not res["images"]:
            raise ToolError("Bild nicht übernommen: " + " ".join(res["errors"]))
        out = {"image": _image_summary(res["images"][0]), "displayed": res["displayed"]}
        if res["display_error"]:
            out["display_error"] = res["display_error"]
        return out
    if name == "show_image":
        rec = _record(bridge, args)
        return {"showing": _image_summary(await bridge.display(rec.id))}
    if name == "show_random_image":
        try:
            r = await bridge.display_random()
        except LookupError as exc:
            raise ToolError(str(exc)) from None
        return {"showing": r["title"], "content_id": r["content_id"]}
    if name == "set_image_matte":
        rec = _record(bridge, args)
        new, replaced = await bridge.set_local_matte(rec.id, _matte_arg(args, bridge, True))
        out = {"image": _image_summary(new)}
        if replaced:
            out["note"] = (f"Der Fernseher ändert Passepartouts nicht nachträglich; es wurde eine neue Kopie "
                           f"hochgeladen. Die alte Kopie {replaced} liegt noch auf dem Fernseher.")
        return out
    if name == "set_art_mode":
        on = bool(args.get("on"))
        await bridge.frame.set_artmode(on)
        return {"art_mode": on}
    raise ToolError(f"Unbekanntes Werkzeug {name}.")


def _result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def handle_message(bridge: Bridge, msg: Any) -> dict | None:
    """One JSON-RPC message; None for notifications and responses."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    method, req_id = msg.get("method"), msg.get("id")
    if method is None or req_id is None:
        return None  # notification (e.g. notifications/initialized) or a response
    params = msg.get("params") or {}
    if method == "initialize":
        wanted = params.get("protocolVersion")
        version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(req_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "the-frame-bridge", "title": "The Frame Bridge", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOL_NAMES:
            return _error(req_id, -32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return _error(req_id, -32602, "arguments must be an object")
        try:
            data = await call_tool(bridge, name, args)
            return _result(req_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                                    "structuredContent": data, "isError": False})
        except (ToolError, FrameError, FetchError, ImageError, InvalidImageId) as exc:
            msg_text = str(exc) or type(exc).__name__
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the assistant
            _LOGGER.exception("tool %s failed", name)
            msg_text = f"Interner Fehler: {type(exc).__name__}"
        return _result(req_id, {"content": [{"type": "text", "text": msg_text}], "isError": True})
    if method in ("resources/list", "prompts/list"):
        return _result(req_id, {method.split("/")[0]: []})
    return _error(req_id, -32601, f"Method not found: {method}")
