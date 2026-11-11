"""Add-on settings: options.json from the Supervisor plus files kept in /data."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(os.environ.get("FRAME_BRIDGE_DATA", "/data"))

# The Supervisor's ingress proxy connects from this address.
INGRESS_PROXY_IP = "172.30.32.2"


@dataclass
class Settings:
    tv_host: str = ""
    media_player_entity: str = ""
    api_enabled: bool = False
    max_upload_mb: int = 25
    matte: str = "none"
    log_level: str = "info"
    public_url: str = ""
    ai_matte: str = "modernthin_warm"  # "with matte" for AI assistants
    data_dir: Path = field(default_factory=lambda: DATA_DIR)

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def tv_thumbs_dir(self) -> Path:
        return self.data_dir / "tv_thumbs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "frame_bridge.sqlite"

    @property
    def tv_token_file(self) -> Path:
        return self.data_dir / "tv_token.txt"

    @property
    def api_token_file(self) -> Path:
        return self.data_dir / "api_token.txt"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.images_dir, self.thumbs_dir, self.tv_thumbs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def api_token(self) -> str:
        """Bearer token for the external API, created on first use, kept in /data."""
        if self.api_token_file.exists():
            token = self.api_token_file.read_text().strip()
            if token:
                return token
        return self.rotate_api_token()

    def rotate_api_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self.api_token_file.write_text(token)
        os.chmod(self.api_token_file, 0o600)
        return token


def load_settings(data_dir: Path | None = None) -> Settings:
    base = data_dir or DATA_DIR
    options: dict = {}
    options_file = base / "options.json"
    if options_file.exists():
        options = json.loads(options_file.read_text() or "{}")
    known = {k: v for k, v in options.items() if k in Settings.__dataclass_fields__ and k != "data_dir"}
    settings = Settings(**known, data_dir=base)
    settings.tv_host = (settings.tv_host or "").strip()
    settings.media_player_entity = (settings.media_player_entity or "").strip()
    settings.ensure_dirs()
    return settings
