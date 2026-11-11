"""Entry point: starts the ingress server and, when enabled, the external API server."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import uvicorn

from . import __version__
from .config import load_settings
from .frame import SamsungFrame
from .ha import HomeAssistant
from .service import Bridge
from .storage import ImageStore
from .web import create_external_app, create_ingress_app

INGRESS_PORT = 8099
API_PORT = 8080


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("frame_bridge")
    log.info("The Frame Bridge %s startet", __version__)
    log.info("Fernseher: %s, Media Player: %s, externe API: %s",
             settings.tv_host or "(nicht gesetzt)", settings.media_player_entity or "(nicht gesetzt)",
             "an" if settings.api_enabled else "aus")

    store = ImageStore(settings.db_path, settings.images_dir, settings.thumbs_dir)
    frame = SamsungFrame(settings.tv_host, settings.tv_token_file)
    ha = HomeAssistant()
    bridge = Bridge(settings, store, frame, ha)
    settings.api_token()  # make sure a token exists before the API can be enabled

    servers = [
        uvicorn.Server(uvicorn.Config(create_ingress_app(lambda: bridge), host="0.0.0.0", port=INGRESS_PORT,
                                      log_level="warning", proxy_headers=False))
    ]
    if settings.api_enabled:
        servers.append(
            uvicorn.Server(uvicorn.Config(create_external_app(lambda: bridge), host="0.0.0.0", port=API_PORT,
                                          log_level="warning", proxy_headers=False))
        )
        log.info("Externe API auf Port %s aktiv (Bearer-Token erforderlich)", API_PORT)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: [setattr(s, "should_exit", True) for s in servers])
    for s in servers:
        # we handle SIGTERM ourselves for both servers; uvicorn would only stop the last one
        s.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]
    try:
        await asyncio.gather(*(s.serve() for s in servers))
    finally:
        await frame.close()
        await ha.close()
        log.info("beendet")


if __name__ == "__main__":
    asyncio.run(run())
