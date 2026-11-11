import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import make_jpeg

CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(e, uris=(CALLBACK,), name="ChatGPT"):
    r = e.post("/oauth/register", json={"redirect_uris": list(uris), "client_name": name,
                                        "token_endpoint_auth_method": "none"})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def authorize(env, client_id, challenge, token=None, redirect=CALLBACK, state="xyz", decision="allow"):
    e = env["external"]
    params = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256",
              "resource": "https://frame.fenner.berlin/mcp", "scope": "frame"}
    page = e.get("/oauth/authorize", params=params)
    assert page.status_code == 200 and "Zugriff erlauben" in page.text
    form = dict(params, api_token=token if token is not None else env["settings"].api_token(), decision=decision)
    return e.post("/oauth/authorize", data=form, follow_redirects=False)


def full_login(env, name="ChatGPT"):
    e = env["external"]
    cid = register(e, name=name)
    verifier, challenge = pkce()
    r = authorize(env, cid, challenge)
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["state"] == ["xyz"] and q["iss"] == ["https://frame.fenner.berlin"]
    tok = e.post("/oauth/token", data={"grant_type": "authorization_code", "code": q["code"][0], "client_id": cid,
                                       "redirect_uri": CALLBACK, "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    return cid, tok.json()


def rpc(env, token, method, params=None, req_id=1):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return env["external"].post("/mcp", json=msg, headers={"Authorization": f"Bearer {token}",
                                                           "Accept": "application/json, text/event-stream"})


def call(env, token, name, args=None):
    r = rpc(env, token, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


@pytest.fixture(autouse=True)
def public_url(env):
    env["settings"].public_url = "https://frame.fenner.berlin"


# ---------------- discovery ----------------

def test_unauthenticated_mcp_points_to_metadata(env):
    r = env["external"].post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    assert ('resource_metadata="https://frame.fenner.berlin/.well-known/oauth-protected-resource/mcp"'
            in r.headers["www-authenticate"])


def test_discovery_documents(env):
    e = env["external"]
    prm = e.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == "https://frame.fenner.berlin/mcp"
    assert prm["authorization_servers"] == ["https://frame.fenner.berlin"]
    asm = e.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == "https://frame.fenner.berlin"
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["registration_endpoint"].endswith("/oauth/register")
    assert "none" in asm["token_endpoint_auth_methods_supported"]


# ---------------- OAuth ----------------

def test_full_flow_and_refresh_rotation(env):
    cid, tok = full_login(env)
    assert tok["token_type"] == "Bearer" and tok["expires_in"] == 3600
    assert rpc(env, tok["access_token"], "ping").json()["result"] == {}

    e = env["external"]
    r = e.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                     "client_id": cid})
    new = r.json()
    assert r.status_code == 200 and new["refresh_token"] != tok["refresh_token"]
    # the old refresh token is spent
    again = e.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_wrong_api_token_is_rejected_and_rate_limited(env):
    e = env["external"]
    cid = register(e)
    _, challenge = pkce()
    for _ in range(5):
        r = authorize(env, cid, challenge, token="falsch")
        assert r.status_code == 401 and "stimmt nicht" in r.text
    r = authorize(env, cid, challenge)  # right token, but locked for a while
    assert r.status_code == 429


def test_deny_redirects_with_error(env):
    cid = register(env["external"])
    _, challenge = pkce()
    r = authorize(env, cid, challenge, decision="deny")
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert r.status_code == 302 and q["error"] == ["access_denied"] and q["state"] == ["xyz"]


def test_code_single_use_pkce_and_redirect_checks(env):
    e = env["external"]
    cid = register(e)
    verifier, challenge = pkce()
    code = parse_qs(urlsplit(authorize(env, cid, challenge).headers["location"]).query)["code"][0]
    bad = e.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                       "redirect_uri": CALLBACK, "code_verifier": "wrong" * 10})
    assert bad.json()["error"] == "invalid_grant"
    # the failed attempt burned the code
    ok = e.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                      "redirect_uri": CALLBACK, "code_verifier": verifier})
    assert ok.json()["error"] == "invalid_grant"

    code = parse_qs(urlsplit(authorize(env, cid, challenge).headers["location"]).query)["code"][0]
    r = e.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                     "redirect_uri": "https://evil.example/cb", "code_verifier": verifier})
    assert r.json()["error"] == "invalid_grant"


def test_authorize_rejects_unregistered_redirect_and_missing_pkce(env):
    e = env["external"]
    cid = register(e)
    r = e.get("/oauth/authorize", params={"client_id": cid, "redirect_uri": "https://evil.example/cb",
                                          "response_type": "code", "code_challenge": "x",
                                          "code_challenge_method": "S256"})
    assert r.status_code == 400
    r = e.get("/oauth/authorize", params={"client_id": cid, "redirect_uri": CALLBACK, "response_type": "code"},
              follow_redirects=False)
    assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]
    assert e.get("/oauth/authorize", params={"client_id": "nope", "redirect_uri": CALLBACK}).status_code == 400


def test_register_validation(env):
    e = env["external"]
    assert e.post("/oauth/register", json={"redirect_uris": ["http://evil.example/cb"]}).status_code == 400
    assert e.post("/oauth/register", json={"redirect_uris": []}).status_code == 400
    assert e.post("/oauth/register", json={"redirect_uris": [CALLBACK],
                                           "token_endpoint_auth_method": "client_secret_basic"}).status_code == 400
    # Claude Code style loopback with any port
    cid = register(e, uris=["http://localhost/callback"])
    _, challenge = pkce()
    r = authorize(env, cid, challenge, redirect="http://localhost:53122/callback")
    assert r.status_code == 302 and r.headers["location"].startswith("http://localhost:53122/callback?code=")


def test_rotating_api_token_signs_out_assistants(env):
    _, tok = full_login(env)
    assert env["ingress"].get("/api/settings/assistants").json()["assistants"][0]["client_name"] == "ChatGPT"
    env["ingress"].post("/api/settings/api-token/rotate")
    assert rpc(env, tok["access_token"], "ping").status_code == 401
    assert env["ingress"].get("/api/settings/assistants").json()["assistants"] == []


def test_revoke_all_button(env):
    _, tok = full_login(env)
    env["ingress"].post("/api/settings/assistants/revoke")
    assert rpc(env, tok["access_token"], "ping").status_code == 401


def test_api_token_works_directly_for_mcp(env):
    assert rpc(env, env["settings"].api_token(), "ping").status_code == 200


# ---------------- MCP protocol ----------------

def test_initialize_and_tools_list(env):
    _, tok = full_login(env)
    init = rpc(env, tok["access_token"], "initialize",
               {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}).json()
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"]["tools"] == {"listChanged": False}
    note = env["external"].post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                                headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert note.status_code == 202
    tools = {t["name"]: t for t in rpc(env, tok["access_token"], "tools/list").json()["result"]["tools"]}
    assert set(tools) == {"get_frame_status", "list_images", "list_tv_images", "list_mattes", "import_image",
                          "show_image", "show_random_image", "set_image_matte", "set_art_mode"}
    assert not any("delete" in n for n in tools)
    assert tools["import_image"]["_meta"]["openai/fileParams"] == ["image"]
    assert tools["list_images"]["annotations"]["readOnlyHint"] is True
    assert tools["show_image"]["annotations"]["readOnlyHint"] is False
    assert rpc(env, tok["access_token"], "nope/method").json()["error"]["code"] == -32601


def test_tools_end_to_end(env, monkeypatch):
    _, tok = full_login(env)
    a = tok["access_token"]

    async def fake_fetch(url, max_bytes):
        assert url == "https://files.oaiusercontent.com/file-abc"
        return make_jpeg(1792, 1024, fmt="PNG"), "image/png"

    env["bridge"].fetch = fake_fetch
    res = call(env, a, "import_image", {
        "image": {"download_url": "https://files.oaiusercontent.com/file-abc", "file_id": "file-abc",
                  "mime_type": "image/png", "file_name": "herbst.png"},
        "title": "Happy Birthday Astrid", "passepartout": True})
    assert res["isError"] is False
    img = res["structuredContent"]["image"]
    assert img["title"] == "Happy Birthday Astrid" and img["matte"] == "modernthin_warm"
    assert res["structuredContent"]["displayed"] is True
    assert env["frame"].matte_of[img["on_tv_as"]] == "modernthin_warm"

    lst = call(env, a, "list_images")["structuredContent"]["images"]
    assert lst[0]["id"] == img["id"]
    st = call(env, a, "get_frame_status")["structuredContent"]
    assert st["showing"] == "Happy Birthday Astrid" and st["art_mode"] is True

    r = call(env, a, "set_image_matte", {"image_id": img["id"], "passepartout": False})
    assert r["structuredContent"]["image"]["matte"] == "none"
    assert call(env, a, "show_image", {"image_id": img["id"]})["isError"] is False
    assert call(env, a, "list_mattes")["structuredContent"]["types"]
    assert call(env, a, "set_art_mode", {"on": False})["structuredContent"] == {"art_mode": False}
    assert call(env, a, "list_tv_images")["structuredContent"]["supported"] is True


def test_tool_errors_are_reported_not_raised(env):
    a = env["settings"].api_token()
    r = call(env, a, "show_image", {"image_id": "../../etc"})
    assert r["isError"] is True and "nicht gefunden" in r["content"][0]["text"]
    r = call(env, a, "show_random_image")
    assert r["isError"] is True and "lade zuerst" in r["content"][0]["text"]
    r = call(env, a, "import_image", {"image_url": "https://192.168.0.150/x.png"})
    assert r["isError"] is True and "lokalen Netz" in r["content"][0]["text"]
    r = call(env, a, "import_image", {})
    assert r["isError"] is True
    r = call(env, a, "set_image_matte", {"image_id": "0" * 32})
    assert r["isError"] is True
    r = rpc(env, a, "tools/call", {"name": "delete_everything", "arguments": {}}).json()
    assert r["error"]["code"] == -32602


def test_batch_and_bad_json(env):
    a = env["settings"].api_token()
    r = env["external"].post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                           {"jsonrpc": "2.0", "method": "notifications/initialized"}],
                             headers={"Authorization": f"Bearer {a}"})
    assert r.json() == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
    r = env["external"].post("/mcp", content=b"{nope", headers={"Authorization": f"Bearer {a}",
                                                                "Content-Type": "application/json"})
    assert r.status_code == 400 and json.loads(r.text)["error"]["code"] == -32700


def test_get_mcp_is_405(env):
    r = env["external"].get("/mcp", headers={"Authorization": f"Bearer {env['settings'].api_token()}"})
    assert r.status_code == 405
