"""Small OAuth 2.1 authorization server for the MCP endpoint (ChatGPT plugins and Claude connectors).

- Discovery: protected resource metadata (RFC 9728) and authorization server metadata (RFC 8414)
- Dynamic client registration (RFC 7591), public clients only
- Authorization code with PKCE S256; the user approves on a page of this add-on by entering the
  add-on's API token, so only someone who already has that token can grant access
- Access tokens (1 h) and rotating refresh tokens (90 days), stored only as SHA-256 hashes
- Rotating the API token revokes every grant
"""
from __future__ import annotations

import base64
import hashlib
import html
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

ACCESS_TTL = 3600
REFRESH_TTL = 90 * 24 * 3600
CODE_TTL = 600
MAX_CLIENTS = 50
SCOPE = "frame"
FAIL_WINDOW = 600
MAX_FAILS = 5


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status

    def body(self) -> dict:
        return {"error": self.error, "error_description": self.description}


def valid_redirect_uri(uri: str) -> bool:
    """https anywhere, plain http only for loopback (desktop clients like Claude Code)."""
    try:
        p = urlsplit(uri)
    except ValueError:
        return False
    if p.fragment or not p.hostname:
        return False
    if p.scheme == "https":
        return True
    return p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1")


def _loopback_match(registered: str, given: str) -> bool:
    """RFC 8252: loopback redirects match regardless of port."""
    r, g = urlsplit(registered), urlsplit(given)
    return (r.scheme == g.scheme == "http" and r.hostname == g.hostname
            and r.hostname in ("localhost", "127.0.0.1", "::1") and r.path == g.path and r.query == g.query)


@dataclass
class Client:
    client_id: str
    client_name: str
    redirect_uris: list[str]

    def redirect_ok(self, uri: str) -> bool:
        return uri in self.redirect_uris or any(_loopback_match(r, uri) for r in self.redirect_uris)


class OAuthStore:
    def __init__(self, db_path: Path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY, client_name TEXT NOT NULL, redirect_uris TEXT NOT NULL,
                created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL,
                challenge TEXT NOT NULL, resource TEXT, scope TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, client_id TEXT NOT NULL,
                scope TEXT NOT NULL, resource TEXT, expires REAL NOT NULL, created REAL NOT NULL);
            """
        )
        self._db.commit()
        self._fails: dict[str, list[float]] = {}

    # ----- clients -----
    def register(self, meta: dict) -> dict:
        uris = meta.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and valid_redirect_uri(u) for u in uris):
            raise OAuthError("invalid_redirect_uri", "redirect_uris müssen https- oder Loopback-Adressen sein.")
        auth_method = meta.get("token_endpoint_auth_method", "none")
        if auth_method != "none":
            raise OAuthError("invalid_client_metadata", "Nur öffentliche Clients (token_endpoint_auth_method=none).")
        name = str(meta.get("client_name") or "Unbenannter Client")[:100]
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0]
            if n >= MAX_CLIENTS:  # drop the oldest registration that holds no tokens
                self._db.execute(
                    "DELETE FROM oauth_clients WHERE client_id = (SELECT client_id FROM oauth_clients "
                    "WHERE client_id NOT IN (SELECT client_id FROM oauth_tokens) ORDER BY created LIMIT 1)")
            client_id = "fb_" + secrets.token_urlsafe(18)
            self._db.execute("INSERT INTO oauth_clients VALUES (?,?,?,?)",
                             (client_id, name, "\n".join(uris), time.time()))
            self._db.commit()
        return {
            "client_id": client_id, "client_name": name, "redirect_uris": uris,
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none", "client_id_issued_at": int(time.time()),
        }

    def client(self, client_id: str | None) -> Client | None:
        if not client_id:
            return None
        with self._lock:
            row = self._db.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        return Client(row["client_id"], row["client_name"], row["redirect_uris"].split("\n")) if row else None

    # ----- approval -----
    def too_many_failures(self, who: str) -> bool:
        now = time.time()
        recent = [t for t in self._fails.get(who, []) if now - t < FAIL_WINDOW]
        self._fails[who] = recent
        return len(recent) >= MAX_FAILS

    def note_failure(self, who: str) -> None:
        self._fails.setdefault(who, []).append(time.time())

    def issue_code(self, client: Client, redirect_uri: str, challenge: str, resource: str | None) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._db.execute("INSERT INTO oauth_codes VALUES (?,?,?,?,?,?,?)",
                             (_h(code), client.client_id, redirect_uri, challenge, resource, SCOPE,
                              time.time() + CODE_TTL))
            self._db.commit()
        return code

    # ----- token endpoint -----
    def _issue_tokens(self, client_id: str, scope: str, resource: str | None) -> dict:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(40)
        now = time.time()
        self._db.execute("INSERT INTO oauth_tokens VALUES (?,?,?,?,?,?,?)",
                         (_h(access), "access", client_id, scope, resource, now + ACCESS_TTL, now))
        self._db.execute("INSERT INTO oauth_tokens VALUES (?,?,?,?,?,?,?)",
                         (_h(refresh), "refresh", client_id, scope, resource, now + REFRESH_TTL, now))
        self._db.commit()
        return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL,
                "refresh_token": refresh, "scope": scope}

    def exchange_code(self, form: dict) -> dict:
        code, client_id = form.get("code"), form.get("client_id")
        verifier, redirect_uri = form.get("code_verifier"), form.get("redirect_uri")
        if not code or not client_id or not verifier:
            raise OAuthError("invalid_request", "code, client_id und code_verifier sind nötig.")
        with self._lock:
            row = self._db.execute("SELECT * FROM oauth_codes WHERE code_hash=?", (_h(code),)).fetchone()
            # single use, whatever happens next
            self._db.execute("DELETE FROM oauth_codes WHERE code_hash=? OR expires < ?", (_h(code), time.time()))
            self._db.commit()
            if row is None or row["expires"] < time.time():
                raise OAuthError("invalid_grant", "Code ungültig oder abgelaufen.")
            if row["client_id"] != client_id:
                raise OAuthError("invalid_grant", "Code gehört zu einem anderen Client.")
            if redirect_uri is not None and redirect_uri != row["redirect_uri"]:
                raise OAuthError("invalid_grant", "redirect_uri stimmt nicht überein.")
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not secrets.compare_digest(digest, row["challenge"]):
                raise OAuthError("invalid_grant", "PKCE-Prüfung fehlgeschlagen.")
            return self._issue_tokens(client_id, row["scope"], row["resource"])

    def refresh(self, form: dict) -> dict:
        token, client_id = form.get("refresh_token"), form.get("client_id")
        if not token:
            raise OAuthError("invalid_request", "refresh_token fehlt.")
        with self._lock:
            row = self._db.execute("SELECT * FROM oauth_tokens WHERE token_hash=? AND kind='refresh'",
                                   (_h(token),)).fetchone()
            if row is None or row["expires"] < time.time():
                raise OAuthError("invalid_grant", "Refresh-Token ungültig oder abgelaufen.")
            if client_id and client_id != row["client_id"]:
                raise OAuthError("invalid_grant", "Refresh-Token gehört zu einem anderen Client.")
            self._db.execute("DELETE FROM oauth_tokens WHERE token_hash=?", (_h(token),))  # rotation
            return self._issue_tokens(row["client_id"], row["scope"], row["resource"])

    # ----- resource side -----
    def check_access(self, token: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT expires FROM oauth_tokens WHERE token_hash=? AND kind='access'",
                                   (_h(token),)).fetchone()
        return bool(row) and row["expires"] > time.time()

    def grants(self) -> list[dict]:
        """Connected assistants: clients holding a valid refresh token."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.client_id, c.client_name, MAX(t.created) AS since FROM oauth_tokens t "
                "JOIN oauth_clients c ON c.client_id = t.client_id WHERE t.kind='refresh' AND t.expires > ? "
                "GROUP BY c.client_id ORDER BY since DESC", (time.time(),)).fetchall()
        return [dict(r) for r in rows]

    def revoke_all(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM oauth_tokens")
            self._db.execute("DELETE FROM oauth_codes")
            self._db.commit()


def consent_page(client: Client, params: dict[str, str], error: str | None = None) -> str:
    host = urlsplit(params["redirect_uri"]).hostname or ""
    hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                     for k, v in params.items())
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Frame Bridge – Zugriff erlauben</title>
<style>
:root{{--bg:#eceef0;--paper:#fff;--ink:#1d2226;--slate:#5a646d;--line:#d9dde1;--accent:#2c5b86;--bad:#b23a3a;--frame:#23282d}}
@media (prefers-color-scheme:dark){{:root{{--bg:#15181b;--paper:#1f2327;--ink:#e8eaec;--slate:#9aa3ab;--line:#343a40;--accent:#8fb8de;--bad:#e07a7a;--frame:#07090a}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;display:grid;place-items:center;min-height:100vh;padding:16px}}
main{{background:var(--paper);border-radius:18px;padding:24px;max-width:420px;width:100%}}
.mark{{width:44px;height:32px;border:4px solid var(--frame);background:#f7f6f2;margin-bottom:14px;border-radius:2px}}
h1{{font-size:1.2rem;margin:0 0 8px}}p{{margin:0 0 12px;color:var(--slate)}}strong{{color:var(--ink)}}
label{{display:grid;gap:6px;margin:16px 0;font-size:.9rem;color:var(--slate)}}
input[type=password]{{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px}}
.row{{display:flex;gap:10px;justify-content:flex-end}}button{{font:inherit;font-weight:600;border-radius:999px;padding:9px 18px;border:1px solid var(--line);background:var(--paper);color:var(--ink);cursor:pointer}}
button.primary{{background:var(--accent);border-color:var(--accent);color:var(--paper)}}.err{{color:var(--bad)}}
</style></head><body><main>
<div class="mark" aria-hidden="true"></div>
<h1>Zugriff auf The Frame erlauben?</h1>
<p><strong>{html.escape(client.client_name)}</strong> möchte deinen Samsung The Frame steuern: Bilder ansehen, anzeigen, hochladen und das Passepartout ändern. Löschen ist nicht möglich.</p>
<p>Nach der Freigabe geht es weiter zu <strong>{html.escape(host)}</strong>.</p>
{err}
<form method="post" action="authorize">{hidden}
<label>API-Token aus The Frame Bridge (Einstellungen → Externe REST-API)
<input type="password" name="api_token" autocomplete="off" required autofocus></label>
<div class="row"><button type="submit" name="decision" value="deny">Ablehnen</button>
<button class="primary" type="submit" name="decision" value="allow">Zugriff erlauben</button></div>
</form></main></body></html>"""


def redirect_with(uri: str, **params: str | None) -> str:
    q = urlencode({k: v for k, v in params.items() if v is not None})
    return uri + ("&" if urlsplit(uri).query else "?") + q
