"""OpenAPI schema for a ChatGPT custom GPT action.

Hand-written and small on purpose: the descriptions tell ChatGPT when to use which operation, and
only what a chat assistant should do is included. Deleting pictures on the TV is not offered.
"""
from __future__ import annotations

from typing import Any

from . import __version__

_OBJ = {"type": "object"}


def _ok(description: str) -> dict[str, Any]:
    return {"200": {"description": description, "content": {"application/json": {"schema": _OBJ}}}}


def gpt_schema(server_url: str) -> dict[str, Any]:
    image_id = {
        "name": "image_id", "in": "path", "required": True,
        "description": "ID eines gespeicherten Bildes aus listImages (32 Hex-Zeichen).",
        "schema": {"type": "string"},
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "The Frame Bridge",
            "version": __version__,
            "description": "Bilder auf einem Samsung The Frame im Art Mode anzeigen und verwalten.",
        },
        "servers": [{"url": server_url}],
        "paths": {
            "/api/frame/status": {
                "get": {
                    "operationId": "getFrameStatus",
                    "summary": "Status des Fernsehers",
                    "description": "Ob der Fernseher erreichbar ist, ob der Art Mode an ist und welches Bild gerade angezeigt wird.",
                    "responses": _ok("Status"),
                }
            },
            "/api/images": {
                "get": {
                    "operationId": "listImages",
                    "summary": "Gespeicherte Bilder auflisten",
                    "description": "Alle in der Galerie gespeicherten Bilder mit id, Titel, Datum und Passepartout.",
                    "responses": {"200": {"description": "Liste", "content": {"application/json": {
                        "schema": {"type": "array", "items": _OBJ}}}}},
                }
            },
            "/api/images/import": {
                "post": {
                    "operationId": "importImages",
                    "summary": "Bild aus dem Chat auf den Fernseher bringen",
                    "description": (
                        "Speichert ein Bild aus diesem Gespräch (selbst erzeugt oder vom Nutzer hochgeladen) in der "
                        "Galerie und zeigt es mit display=true sofort im Art Mode. Genau ein Bild übergeben, außer der "
                        "Nutzer möchte mehrere. Erwartet JPG oder PNG, ideal im Querformat 16:9."
                    ),
                    "x-openai-isConsequential": False,
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["openaiFileIdRefs"],
                        "properties": {
                            "openaiFileIdRefs": {
                                "type": "array", "items": {"type": "string"},
                                "description": "Das Bild bzw. die Bilder aus dem Gespräch.",
                            },
                            "title": {"type": "string", "description": "Kurzer Titel für die Galerie, z. B. 'Happy Birthday Astrid'."},
                            "mode": {"type": "string", "enum": ["fit", "crop"],
                                     "description": "fit: ganzes Bild sichtbar (Standard). crop: füllt den Bildschirm, Ränder werden abgeschnitten."},
                            "display": {"type": "boolean", "description": "Sofort auf dem Fernseher anzeigen. Standard: true."},
                            "matte": {"type": "string",
                                      "description": "Passepartout: 'none' (Standard) oder <stil>_<farbe>, z. B. 'modern_polar'. Werte aus listMattes."},
                        },
                    }}}},
                    "responses": _ok("Gespeicherte Bilder, ob angezeigt, und Fehler je Datei"),
                }
            },
            "/api/images/{image_id}/display": {
                "post": {
                    "operationId": "showImage",
                    "summary": "Gespeichertes Bild anzeigen",
                    "description": "Zeigt ein Bild aus listImages im Art Mode an.",
                    "x-openai-isConsequential": False,
                    "parameters": [image_id],
                    "responses": _ok("Angezeigtes Bild"),
                }
            },
            "/api/frame/random": {
                "post": {
                    "operationId": "showRandomImage",
                    "summary": "Zufälliges Bild anzeigen",
                    "description": "Zeigt ein zufälliges eigenes Bild, nie das gerade angezeigte.",
                    "x-openai-isConsequential": False,
                    "responses": _ok("Angezeigtes Bild"),
                }
            },
            "/api/frame/mattes": {
                "get": {
                    "operationId": "listMattes",
                    "summary": "Verfügbare Passepartouts",
                    "description": "Stile (types) und Farben (colors), die der Fernseher anbietet. Ein Passepartout ist '<stil>_<farbe>'.",
                    "responses": _ok("Stile und Farben"),
                }
            },
            "/api/images/{image_id}/matte": {
                "post": {
                    "operationId": "setImageMatte",
                    "summary": "Passepartout eines Bildes ändern",
                    "description": "Setzt das Passepartout eines gespeicherten Bildes; 'none' entfernt es.",
                    "x-openai-isConsequential": False,
                    "parameters": [image_id],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["matte"],
                        "properties": {"matte": {"type": "string", "description": "'none' oder z. B. 'shadowbox_polar'"}},
                    }}}},
                    "responses": _ok("Bild mit neuem Passepartout"),
                }
            },
            "/api/frame/artmode": {
                "post": {
                    "operationId": "setArtMode",
                    "summary": "Art Mode ein- oder ausschalten",
                    "x-openai-isConsequential": False,
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["on"], "properties": {"on": {"type": "boolean"}},
                    }}}},
                    "responses": _ok("Neuer Zustand"),
                }
            },
        },
        "components": {
            "schemas": {},
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        },
        "security": [{"bearerAuth": []}],
    }
