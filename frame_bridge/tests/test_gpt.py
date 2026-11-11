import asyncio

import pytest

from app.fetch import FetchError, check_public_https
from conftest import make_jpeg


def auth(env):
    return {"Authorization": f"Bearer {env['settings'].api_token()}"}


@pytest.fixture
def links(env):
    """Fake downloads: link -> bytes."""
    store = {}

    async def fake_fetch(url, max_bytes):
        if url not in store:
            raise FetchError("Download fehlgeschlagen (HTTP 404). Der Link ist evtl. abgelaufen.")
        data = store[url]
        if len(data) > max_bytes:
            raise FetchError("Datei zu groß")
        return data, "image/png"

    env["bridge"].fetch = fake_fetch
    return store


def ref(link, name="bild.png"):
    return {"name": name, "id": "file-123", "mime_type": "image/png", "download_link": link}


def test_import_and_display_like_chatgpt(env, links):
    links["https://files.example/a"] = make_jpeg(1792, 1024, fmt="PNG")
    r = env["external"].post("/api/images/import", headers=auth(env), json={
        "openaiFileIdRefs": [ref("https://files.example/a", "herbst.png")],
        "title": "Happy Birthday Astrid", "mode": "crop", "display": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["displayed"] is True and body["errors"] == []
    img = body["images"][0]
    assert img["title"] == "Happy Birthday Astrid" and img["source_width"] == 1792
    assert env["frame"].current == img["tv_content_id"] and env["frame"].art_mode is True


def test_import_with_matte_sends_reduced_copy(env, links):
    links["https://files.example/a"] = make_jpeg(fmt="PNG")
    body = env["external"].post("/api/images/import", headers=auth(env), json={
        "openaiFileIdRefs": [ref("https://files.example/a")], "matte": "modern_polar"}).json()
    cid = body["images"][0]["tv_content_id"]
    assert env["frame"].matte_of[cid] == "modern_polar" and env["frame"].width_of[cid] == 1920


def test_import_partial_errors_and_title_fallback(env, links):
    links["https://files.example/ok"] = make_jpeg()
    body = env["external"].post("/api/images/import", headers=auth(env), json={
        "openaiFileIdRefs": [ref("https://files.example/expired", "alt.png"), ref("https://files.example/ok", "Strand_Ahlbeck.jpg"),
                             {"name": "ohne-link"}],
        "display": False}).json()
    assert [i["title"] for i in body["images"]] == ["Strand Ahlbeck"]
    assert len(body["errors"]) == 2 and "abgelaufen" in body["errors"][0] and "Download-Link" in body["errors"][1]
    assert body["displayed"] is False


def test_import_nothing_usable_is_422(env, links):
    links["https://files.example/txt"] = b"hello"
    r = env["external"].post("/api/images/import", headers=auth(env),
                             json={"openaiFileIdRefs": [ref("https://files.example/txt")]})
    assert r.status_code == 422 and "JPG" in r.json()["detail"]


def test_import_needs_token_and_validates(env, links):
    e = env["external"]
    assert e.post("/api/images/import", json={"openaiFileIdRefs": [ref("https://x/a")]}).status_code == 401
    assert e.post("/api/images/import", headers=auth(env), json={"openaiFileIdRefs": []}).status_code == 422
    assert e.post("/api/images/import", headers=auth(env),
                  json={"openaiFileIdRefs": [ref("https://x/a")], "mode": "stretch"}).status_code == 422
    assert e.post("/api/images/import", headers=auth(env),
                  json={"openaiFileIdRefs": [ref("https://x/a")], "matte": "../x"}).status_code == 422


@pytest.mark.parametrize("url", [
    "http://example.com/a.png",            # not https
    "https://127.0.0.1/a.png",             # loopback
    "https://localhost/a.png",
    "https://10.0.0.5/a.png",              # home network
    "https://192.168.0.150:8002/x",        # the TV
    "https://172.30.32.1/core/api",        # supervisor network
    "https://169.254.169.254/latest",      # link-local
    "https://[::1]/a",
    "https://user:pw@example.com/a",
    "ftp://example.com/a",
    "file:///data/options.json",
])
def test_only_public_https_is_fetched(url):
    with pytest.raises(FetchError):
        asyncio.run(check_public_https(url))


def test_public_ip_literal_passes():
    asyncio.run(check_public_https("https://1.1.1.1/x"))


def test_gpt_schema_public_and_complete(env):
    env["settings"].public_url = "https://frame.fenner.berlin/"
    r = env["external"].get("/gpt/openapi.json")  # no token needed to import the schema
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"] == "3.1.0" and spec["servers"] == [{"url": "https://frame.fenner.berlin"}]
    ops = {op["operationId"] for p in spec["paths"].values() for op in p.values()}
    assert ops == {"getFrameStatus", "listImages", "importImages", "showImage", "showRandomImage",
                   "listMattes", "setImageMatte", "setArtMode"}
    assert "delete" not in str(spec["paths"]).lower()
    body = spec["paths"]["/api/images/import"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["properties"]["openaiFileIdRefs"]["type"] == "array"
    # every path in the GPT schema exists in the real API
    real = env["external"].get("/openapi.json").json()["paths"]
    for path, ops_ in spec["paths"].items():
        for method in ops_:
            assert method in real[path], (method, path)


def test_gpt_schema_falls_back_to_forwarded_host(env):
    spec = env["external"].get("/gpt/openapi.json", headers={"x-forwarded-host": "frame.example.org"}).json()
    assert spec["servers"][0]["url"] == "https://frame.example.org"
