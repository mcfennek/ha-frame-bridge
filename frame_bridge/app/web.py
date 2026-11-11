"""Two FastAPI apps.

ingress_app: web UI + API + settings, reachable only through Home Assistant Ingress
             (connections from the Supervisor's ingress proxy, so HA's login protects it).
external_app: the REST API alone, every request needs the bearer token. Only started when
              api_enabled is set. It has no settings, pairing or token endpoints.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import INGRESS_PROXY_IP
from .frame import FrameError, MatteRefused, valid_content_id, valid_matte
from .imaging import FIT_MODES, ImageError
from .gpt_schema import gpt_schema
from . import mcp as mcp_server
from .oauth import OAuthError, OAuthStore, consent_page, redirect_with
from .service import Bridge
from .storage import InvalidImageId

STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_CHUNK = 1024 * 1024


# ---------- schemas (they also shape the OpenAPI spec) ----------
class Image(BaseModel):
    id: str = Field(description="Lokale Bild-ID (32 Hex-Zeichen)")
    title: str
    created_at: str = Field(description="Upload-Zeitpunkt, ISO 8601 UTC")
    width: int
    height: int
    source_width: int
    source_height: int
    fit_mode: str = Field(description="fit = Einpassen, crop = Zuschneiden")
    original_name: str
    size_bytes: int
    tv_content_id: str | None = Field(None, description="ID auf dem Fernseher, sobald einmal übertragen")
    matte: str = Field("none", description="Passepartout, z. B. none oder shadowbox_polar")
    file_url: str
    thumbnail_url: str


class UploadResult(Image):
    displayed: bool
    display_error: str | None = None


class RemoteImage(BaseModel):
    content_id: str
    category_id: str | None = None
    width: int | None = None
    height: int | None = None
    image_date: str | None = None
    matte_id: str | None = None
    local_id: str | None = Field(None, description="Lokales Bild, aus dem es hochgeladen wurde")
    local_title: str | None = None
    thumbnail_url: str


class RemoteImages(BaseModel):
    supported: bool = Field(description="false, wenn der Fernseher keine Liste geliefert hat")
    message: str | None = None
    images: list[RemoteImage]


class MatteResult(Image):
    replaced_tv_content_id: str | None = Field(
        None, description="Frühere Kopie auf dem Fernseher, die durch eine passende Version ersetzt wurde; "
                          "sie bleibt dort, bis sie ausdrücklich gelöscht wird")


class FileRef(BaseModel):
    name: str | None = None
    id: str | None = None
    mime_type: str | None = None
    download_link: str | None = None


class ImportBody(BaseModel):
    openaiFileIdRefs: list[FileRef] = Field(min_length=1, max_length=10,
                                            description="Dateien mit download_link, wie ChatGPT-Aktionen sie senden")
    title: str | None = Field(None, max_length=200)
    mode: str = "fit"
    display: bool = True
    matte: str | None = None
    passepartout: bool | None = Field(None, description="true: mit Standard-Passepartout (ai_matte), false: ohne")


class ImportResult(BaseModel):
    images: list[Image]
    displayed: bool
    display_error: str | None = None
    errors: list[str]


class RandomResult(BaseModel):
    source: str = Field(description="tv = Bild lag schon auf dem Fernseher, local = aus der Galerie")
    content_id: str | None = Field(None, description="ID des jetzt angezeigten Bildes auf dem Fernseher")
    title: str
    image: Image | None = Field(None, description="Lokales Bild, falls vorhanden")


def resolve_matte(matte: str | None, passepartout: bool | None, ai_matte: str, default: str | None) -> str | None:
    """An explicit matte wins; passepartout=true means the configured AI matte, false means none."""
    if matte is not None:
        value = matte
    elif passepartout is not None:
        value = ai_matte if passepartout else "none"
    else:
        return default
    try:
        return valid_matte(value)
    except FrameError as exc:
        raise HTTPException(422, str(exc)) from None


class MatteBody(BaseModel):
    matte: str | None = Field(None, description="none oder <stil>_<farbe>, z. B. shadowbox_polar",
                              examples=["none", "modern_polar"])
    passepartout: bool | None = Field(None, description="true: Standard-Passepartout für KI (ai_matte), false: ohne")

    def checked(self, ai_matte: str = "modernthin_warm") -> str:
        value = resolve_matte(self.matte, self.passepartout, ai_matte, None)
        if value is None:
            raise HTTPException(422, "matte oder passepartout angeben.")
        return value


class Mattes(BaseModel):
    types: list[str]
    colors: list[str]


class ArtModeBody(BaseModel):
    on: bool


class Health(BaseModel):
    status: str
    version: str


def _image_out(record) -> dict[str, Any]:
    d = record.to_dict()
    d["file_url"] = f"api/images/{record.id}/file"
    d["thumbnail_url"] = f"api/images/{record.id}/thumbnail"
    return d


def _frame_http(exc: FrameError) -> HTTPException:
    # 409: the TV is fine but refuses this particular change
    return HTTPException(status_code=409 if isinstance(exc, MatteRefused) else 502, detail=str(exc))


def build_api_router(get_bridge: Callable[[], Bridge], max_bytes: Callable[[], int]) -> APIRouter:
    r = APIRouter(prefix="/api")

    def bridge() -> Bridge:
        return get_bridge()

    def record_or_404(b: Bridge, image_id: str):
        try:
            rec = b.store.get(image_id)
        except InvalidImageId:
            raise HTTPException(404, "Bild nicht gefunden.") from None
        if rec is None:
            raise HTTPException(404, "Bild nicht gefunden.")
        return rec

    @r.get("/frame/status", tags=["frame"], summary="Verbindungs- und Art-Mode-Status")
    async def frame_status(b: Bridge = Depends(bridge)) -> dict[str, Any]:
        return await b.status()

    @r.get("/images", tags=["images"], response_model=list[Image], summary="Lokale Bilder")
    async def list_images(b: Bridge = Depends(bridge)):
        return [_image_out(i) for i in b.store.list()]

    @r.post("/images", tags=["images"], response_model=UploadResult, status_code=201,
            summary="Bild hochladen (JPG/PNG), wird für 16:9 vorbereitet")
    async def upload_image(
        file: UploadFile = File(..., description="JPG- oder PNG-Datei"),
        title: str | None = Form(None, max_length=200),
        mode: str = Form("fit", description="fit (Einpassen) oder crop (Zuschneiden)"),
        display: bool = Form(False, description="Nach dem Upload direkt auf dem Fernseher zeigen"),
        matte: str | None = Form(None, description="Passepartout (none, shadowbox_polar …); Standard aus der Konfiguration"),
        b: Bridge = Depends(bridge),
    ):
        if mode not in FIT_MODES:
            raise HTTPException(422, "mode muss 'fit' oder 'crop' sein.")
        limit = max_bytes()
        chunks, size = [], 0
        while chunk := await file.read(UPLOAD_CHUNK):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"Datei zu groß (maximal {limit // (1024 * 1024)} MB).")
            chunks.append(chunk)
        if not size:
            raise HTTPException(422, "Die Datei ist leer.")
        try:
            rec, err = await b.add_image(b"".join(chunks), file.filename or "", title, mode, display, matte)
        except (ImageError, FrameError) as exc:
            raise HTTPException(422, str(exc)) from None
        out = _image_out(rec)
        out["displayed"] = display and err is None
        out["display_error"] = err
        return out

    @r.post("/images/import", tags=["images"], response_model=ImportResult,
            summary="Bilder per Download-Link übernehmen (ChatGPT openaiFileIdRefs)")
    async def import_images(body: ImportBody, b: Bridge = Depends(bridge)):
        if body.mode not in FIT_MODES:
            raise HTTPException(422, "mode muss 'fit' oder 'crop' sein.")
        matte = resolve_matte(body.matte, body.passepartout, b.settings.ai_matte, None)
        res = await b.import_files([r.model_dump() for r in body.openaiFileIdRefs], body.title, body.mode,
                                   body.display, matte)
        if not res["images"]:
            raise HTTPException(422, "Kein Bild übernommen: " + " ".join(res["errors"]))
        res["images"] = [_image_out(i) for i in res["images"]]
        return res

    @r.get("/images/{image_id}", tags=["images"], response_model=Image, summary="Metadaten eines Bildes")
    async def get_image(image_id: str, b: Bridge = Depends(bridge)):
        return _image_out(record_or_404(b, image_id))

    @r.get("/images/{image_id}/file", tags=["images"], summary="Vorbereitetes Bild (3840×2160 JPEG)",
           response_class=FileResponse)
    async def image_file(image_id: str, b: Bridge = Depends(bridge)):
        rec = record_or_404(b, image_id)
        return FileResponse(b.store.image_path(rec.id), media_type="image/jpeg")

    @r.get("/images/{image_id}/thumbnail", tags=["images"], summary="Vorschaubild",
           response_class=FileResponse)
    async def image_thumb(image_id: str, b: Bridge = Depends(bridge)):
        rec = record_or_404(b, image_id)
        return FileResponse(b.store.thumb_path(rec.id), media_type="image/jpeg",
                            headers={"Cache-Control": "max-age=86400"})

    @r.delete("/images/{image_id}", tags=["images"], status_code=204,
              summary="Lokales Bild löschen (Bilder auf dem Fernseher bleiben unberührt)")
    async def delete_image(image_id: str, b: Bridge = Depends(bridge)):
        rec = record_or_404(b, image_id)
        b.store.delete(rec.id)
        return Response(status_code=204)

    @r.post("/images/{image_id}/display", tags=["images"], response_model=Image,
            summary="Bild auf The Frame anzeigen (lädt es bei Bedarf hoch)")
    async def display_image(image_id: str, b: Bridge = Depends(bridge)):
        rec = record_or_404(b, image_id)
        try:
            return _image_out(await b.display(rec.id))
        except FrameError as exc:
            raise _frame_http(exc) from None

    @r.post("/images/{image_id}/matte", tags=["images"], response_model=MatteResult,
            summary="Passepartout eines Bildes festlegen (auf dem Fernseher sofort, sonst beim nächsten Upload)")
    async def image_matte(image_id: str, body: MatteBody, b: Bridge = Depends(bridge)):
        rec = record_or_404(b, image_id)
        try:
            new, replaced = await b.set_local_matte(rec.id, body.checked(b.settings.ai_matte))
        except FrameError as exc:
            raise _frame_http(exc) from None
        out = _image_out(new)
        out["replaced_tv_content_id"] = replaced
        return out

    @r.get("/frame/mattes", tags=["frame"], response_model=Mattes, summary="Passepartouts, die der Fernseher anbietet")
    async def mattes(b: Bridge = Depends(bridge)):
        try:
            return await b.mattes()
        except FrameError as exc:
            raise _frame_http(exc) from None

    @r.post("/frame/remote-images/{content_id}/matte", tags=["frame"],
            summary="Passepartout eines Bildes auf dem Fernseher ändern")
    async def remote_matte(content_id: str, body: MatteBody, b: Bridge = Depends(bridge)) -> dict[str, str | None]:
        matte = body.checked(b.settings.ai_matte)
        try:
            valid_content_id(content_id)
            replaced = await b.set_remote_matte(content_id, matte)
        except FrameError as exc:
            raise _frame_http(exc) from None
        return {"content_id": content_id, "matte": matte, "replaced_tv_content_id": replaced}

    @r.post("/frame/random", tags=["frame"], response_model=RandomResult,
            summary="Zufälliges eigenes Bild anzeigen (Fernseher und Galerie, nie das aktuelle)")
    async def random_image(b: Bridge = Depends(bridge)):
        try:
            res = await b.display_random()
            res["image"] = _image_out(res["image"]) if res["image"] else None
            return res
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except FrameError as exc:
            raise _frame_http(exc) from None

    @r.post("/frame/artmode", tags=["frame"], summary="Art Mode ein- oder ausschalten")
    async def artmode(body: ArtModeBody, b: Bridge = Depends(bridge)) -> dict[str, bool]:
        try:
            await b.frame.set_artmode(body.on)
        except FrameError as exc:
            raise _frame_http(exc) from None
        return {"on": body.on}

    @r.get("/frame/remote-images", tags=["frame"], response_model=RemoteImages,
           summary="Eigene Bilder auf dem Fernseher (nur wenn der Fernseher sie liefert)")
    async def remote_images(b: Bridge = Depends(bridge)):
        res = await b.remote_images()
        for i in res["images"]:
            i["thumbnail_url"] = f"api/frame/remote-images/{i['content_id']}/thumbnail"
        return res

    @r.get("/frame/remote-images/{content_id}/thumbnail", tags=["frame"], summary="Vorschaubild vom Fernseher",
           response_class=Response)
    async def remote_thumb(content_id: str, b: Bridge = Depends(bridge)):
        try:
            valid_content_id(content_id)
            data = await b.remote_thumbnail(content_id)
        except FrameError as exc:
            raise HTTPException(404, str(exc)) from None
        media = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        return Response(data, media_type=media, headers={"Cache-Control": "max-age=86400"})

    @r.post("/frame/remote-images/{content_id}/display", tags=["frame"],
            summary="Vorhandenes Bild auf dem Fernseher im Art Mode anzeigen")
    async def remote_display(content_id: str, b: Bridge = Depends(bridge)) -> dict[str, str]:
        try:
            valid_content_id(content_id)
            await b.frame.select(content_id)
            await b.frame.set_artmode(True)
        except FrameError as exc:
            raise _frame_http(exc) from None
        return {"content_id": content_id}

    return r


def _health_route(app: FastAPI) -> None:
    @app.get("/health", response_model=Health, tags=["system"], summary="Lebenszeichen")
    async def health():
        return {"status": "ok", "version": __version__}


def create_ingress_app(get_bridge: Callable[[], Bridge], allow_any_client: bool = False,
                       oauth: OAuthStore | None = None) -> FastAPI:
    app = FastAPI(title="The Frame Bridge (Ingress)", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.middleware("http")
    async def only_ingress(request: Request, call_next):
        client = request.client.host if request.client else ""
        if request.url.path != "/health" and not allow_any_client and client != INGRESS_PROXY_IP:
            return JSONResponse({"detail": "Nur über Home Assistant Ingress erreichbar."}, status_code=403)
        return await call_next(request)

    _health_route(app)
    app.include_router(build_api_router(get_bridge, lambda: get_bridge().settings.max_upload_bytes))

    settings_router = APIRouter(prefix="/api/settings")

    @settings_router.get("")
    async def get_settings() -> dict[str, Any]:
        b = get_bridge()
        s = b.settings
        devices: list = []
        discovery_error = None
        try:
            devices = await b.ha.discover()
        except Exception as exc:  # noqa: BLE001
            discovery_error = str(exc)
        return {
            "version": __version__,
            "tv_host": s.tv_host,
            "media_player_entity": s.media_player_entity,
            "api_enabled": s.api_enabled,
            "api_port": 8080,
            "public_url": s.public_url.rstrip("/"),
            "max_upload_mb": s.max_upload_mb,
            "matte": s.matte,
            "paired": b.frame.paired if b.frame.configured else False,
            "devices": devices,
            "discovery_error": discovery_error,
            "image_count": b.store.count(),
            "storage_bytes": b.store.disk_usage(),
        }

    @settings_router.post("/pair")
    async def pair() -> dict[str, Any]:
        try:
            ok = await get_bridge().frame.pair(timeout=60)
        except FrameError as exc:
            raise _frame_http(exc) from None
        return {"paired": ok}

    @settings_router.get("/api-token")
    async def show_token() -> dict[str, str]:
        return {"token": get_bridge().settings.api_token()}

    def oauth_store() -> OAuthStore:
        nonlocal oauth
        if oauth is None:
            oauth = OAuthStore(get_bridge().settings.db_path)
        return oauth

    @settings_router.post("/api-token/rotate")
    async def rotate_token() -> dict[str, str]:
        token = get_bridge().settings.rotate_api_token()
        oauth_store().revoke_all()  # a new token also signs out every connected assistant
        return {"token": token}

    @settings_router.get("/assistants")
    async def assistants() -> dict[str, Any]:
        return {"assistants": oauth_store().grants()}

    @settings_router.post("/assistants/revoke")
    async def revoke_assistants() -> dict[str, bool]:
        oauth_store().revoke_all()
        return {"revoked": True}

    app.include_router(settings_router)

    # Deleting on the TV exists only in the web UI (never in the external API) and needs confirm=true,
    # which the UI sends only after the user confirmed a dialog naming the picture.
    tv_router = APIRouter(prefix="/api/frame")

    @tv_router.delete("/remote-images/{content_id}", status_code=204)
    async def delete_remote(content_id: str, confirm: bool = False):
        if not confirm:
            raise HTTPException(400, "Löschen auf dem Fernseher braucht eine ausdrückliche Bestätigung.")
        try:
            valid_content_id(content_id)
            await get_bridge().delete_remote(content_id)
        except FrameError as exc:
            raise _frame_http(exc) from None
        return Response(status_code=204)

    app.include_router(tv_router)

    # Script and styles are inlined into the page, so a browser can never combine a new page with
    # an old cached script after an add-on update.
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    index_html = index_html.replace('<link rel="stylesheet" href="static/style.css">', f"<style>\n{css}\n</style>")
    index_html = index_html.replace('<script src="static/app.js"></script>',
                                    f'<script>\nwindow.FRAME_BRIDGE_VERSION = "{__version__}";\n{js}\n</script>')
    index_html = index_html.replace("{{VERSION}}", __version__)

    @app.get("/", include_in_schema=False)
    async def index():
        return HTMLResponse(index_html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def create_external_app(get_bridge: Callable[[], Bridge], oauth: OAuthStore | None = None) -> FastAPI:
    app = FastAPI(
        title="The Frame Bridge API",
        version=__version__,
        description=(
            "REST-API für The Frame Bridge. Alle Endpunkte unter /api erfordern "
            "`Authorization: Bearer <Token>`. Das Token steht in der Weboberfläche unter Einstellungen. "
            "Unter /mcp gibt es einen MCP-Server mit OAuth-Anmeldung für ChatGPT und Claude."
        ),
        docs_url="/docs",
        redoc_url=None,
    )
    bearer = HTTPBearer(auto_error=False, description="API-Token aus den Add-on-Einstellungen")
    if oauth is None:
        oauth = OAuthStore(get_bridge().settings.db_path)
    app.state.oauth = oauth

    def api_token_ok(token: str | None) -> bool:
        expected = get_bridge().settings.api_token()
        return bool(token) and secrets.compare_digest(token.encode(), expected.encode())

    async def require_token(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if creds is None or not api_token_ok(creds.credentials):
            raise HTTPException(401, "Ungültiges oder fehlendes API-Token.",
                                headers={"WWW-Authenticate": "Bearer"})

    def base_url(request: Request) -> str:
        url = get_bridge().settings.public_url.rstrip("/")
        if url:
            return url
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
        return f"https://{host}"

    _health_route(app)

    @app.get("/gpt/openapi.json", tags=["system"], summary="Schema für eine ChatGPT-Aktion (ohne Token abrufbar)")
    async def gpt_openapi(request: Request):
        return gpt_schema(base_url(request))

    # ---------------- OAuth discovery ----------------
    def resource_metadata(request: Request) -> dict:
        b = base_url(request)
        return {"resource": f"{b}/mcp", "authorization_servers": [b], "scopes_supported": ["frame"],
                "bearer_methods_supported": ["header"], "resource_name": "The Frame Bridge"}

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    async def prm(request: Request):
        return resource_metadata(request)

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    @app.get("/.well-known/oauth-authorization-server/mcp", include_in_schema=False)
    @app.get("/.well-known/openid-configuration", include_in_schema=False)
    async def asm(request: Request):
        b = base_url(request)
        return {
            "issuer": b,
            "authorization_endpoint": f"{b}/oauth/authorize",
            "token_endpoint": f"{b}/oauth/token",
            "registration_endpoint": f"{b}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["frame"],
        }

    def oauth_json(exc: OAuthError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status, headers={"Cache-Control": "no-store"})

    @app.post("/oauth/register", include_in_schema=False)
    async def register(request: Request):
        try:
            meta = await request.json()
        except Exception:  # noqa: BLE001
            return oauth_json(OAuthError("invalid_client_metadata", "JSON erwartet."))
        try:
            return JSONResponse(oauth.register(meta if isinstance(meta, dict) else {}), status_code=201)
        except OAuthError as exc:
            return oauth_json(exc)

    def authorize_params(source) -> tuple[dict[str, str], object]:
        p = {k: str(source.get(k) or "") for k in
             ("client_id", "redirect_uri", "response_type", "state", "code_challenge", "code_challenge_method",
              "resource", "scope")}
        client = oauth.client(p["client_id"])
        if client is None:
            raise HTTPException(400, "Unbekannter Client – bitte die Verbindung im Assistenten neu einrichten.")
        if not p["redirect_uri"] or not client.redirect_ok(p["redirect_uri"]):
            raise HTTPException(400, "Ungültige redirect_uri.")
        return p, client

    def authorize_error(p: dict, error: str, description: str) -> RedirectResponse:
        return RedirectResponse(redirect_with(p["redirect_uri"], error=error, error_description=description,
                                              state=p["state"] or None), status_code=302)

    @app.get("/oauth/authorize", include_in_schema=False)
    async def authorize_page(request: Request):
        p, client = authorize_params(request.query_params)
        if p["response_type"] != "code":
            return authorize_error(p, "unsupported_response_type", "Nur response_type=code.")
        if not p["code_challenge"] or p["code_challenge_method"] != "S256":
            return authorize_error(p, "invalid_request", "PKCE mit S256 ist Pflicht.")
        return HTMLResponse(consent_page(client, p), headers={"Cache-Control": "no-store",
                                                              "X-Frame-Options": "DENY"})

    @app.post("/oauth/authorize", include_in_schema=False)
    async def authorize_submit(request: Request):
        form = await request.form()
        p, client = authorize_params(form)
        if p["response_type"] != "code" or not p["code_challenge"] or p["code_challenge_method"] != "S256":
            return authorize_error(p, "invalid_request", "Ungültige Anfrage.")
        if form.get("decision") != "allow":
            return authorize_error(p, "access_denied", "Zugriff abgelehnt.")
        who = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
        if oauth.too_many_failures(who):
            return HTMLResponse(consent_page(client, p, "Zu viele Fehlversuche. Bitte in 10 Minuten erneut versuchen."),
                                status_code=429)
        if not api_token_ok(str(form.get("api_token") or "")):
            oauth.note_failure(who)
            return HTMLResponse(consent_page(client, p, "Das Token stimmt nicht."), status_code=401)
        code = oauth.issue_code(client, p["redirect_uri"], p["code_challenge"], p["resource"] or None)
        return RedirectResponse(redirect_with(p["redirect_uri"], code=code, state=p["state"] or None,
                                              iss=base_url(request)), status_code=302)

    @app.post("/oauth/token", include_in_schema=False)
    async def token(request: Request):
        form = dict(await request.form())
        try:
            grant = form.get("grant_type")
            if grant == "authorization_code":
                res = oauth.exchange_code(form)
            elif grant == "refresh_token":
                res = oauth.refresh(form)
            else:
                raise OAuthError("unsupported_grant_type", "Nur authorization_code und refresh_token.")
        except OAuthError as exc:
            return oauth_json(exc)
        return JSONResponse(res, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    # ---------------- MCP ----------------
    def mcp_unauthorized(request: Request) -> JSONResponse:
        meta = f"{base_url(request)}/.well-known/oauth-protected-resource/mcp"
        return JSONResponse({"error": "invalid_token", "error_description": "Anmeldung erforderlich."},
                            status_code=401,
                            headers={"WWW-Authenticate": f'Bearer resource_metadata="{meta}", scope="frame"'})

    def mcp_authorized(request: Request) -> bool:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return False
        tok = auth[7:].strip()
        return oauth.check_access(tok) or api_token_ok(tok)  # the API token works too (Claude Code, tests)

    @app.post("/mcp", include_in_schema=False)
    async def mcp_post(request: Request):
        if not mcp_authorized(request):
            return mcp_unauthorized(request)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                                status_code=400)
        b = get_bridge()
        if isinstance(body, list):
            replies = [r for r in [await mcp_server.handle_message(b, m) for m in body] if r is not None]
            return JSONResponse(replies) if replies else Response(status_code=202)
        reply = await mcp_server.handle_message(b, body)
        return JSONResponse(reply) if reply is not None else Response(status_code=202)

    @app.get("/mcp", include_in_schema=False)
    @app.delete("/mcp", include_in_schema=False)
    async def mcp_other(request: Request):
        if not mcp_authorized(request):
            return mcp_unauthorized(request)
        return Response(status_code=405, headers={"Allow": "POST"})

    app.include_router(
        build_api_router(get_bridge, lambda: get_bridge().settings.max_upload_bytes),
        dependencies=[Depends(require_token)],
    )
    return app
