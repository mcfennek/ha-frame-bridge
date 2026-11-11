from fastapi.testclient import TestClient

from app.web import create_ingress_app
from conftest import make_jpeg


def upload(client, data=None, **form):
    files = {"file": ("Herbst_Wald.jpg", data or make_jpeg(), "image/jpeg")}
    return client.post("/api/images", files=files, data=form)


def auth(env):
    return {"Authorization": f"Bearer {env['settings'].api_token()}"}


def test_health_open(env):
    assert env["ingress"].get("/health").json()["status"] == "ok"
    assert env["external"].get("/health").status_code == 200


def test_upload_list_get_delete(env):
    c = env["ingress"]
    r = upload(c, title="  Herbst   im Wald ")
    assert r.status_code == 201, r.text
    img = r.json()
    assert img["title"] == "Herbst im Wald"
    assert (img["width"], img["height"]) == (3840, 2160)
    assert (img["source_width"], img["source_height"]) == (1200, 900)
    assert img["displayed"] is False

    assert [i["id"] for i in c.get("/api/images").json()] == [img["id"]]
    assert c.get(f"/api/images/{img['id']}").json()["title"] == "Herbst im Wald"
    f = c.get(f"/api/images/{img['id']}/file")
    assert f.headers["content-type"] == "image/jpeg" and f.content[:2] == b"\xff\xd8"
    assert c.get(f"/api/images/{img['id']}/thumbnail").status_code == 200

    assert c.delete(f"/api/images/{img['id']}").status_code == 204
    assert c.get(f"/api/images/{img['id']}").status_code == 404
    assert c.get("/api/images").json() == []


def test_title_falls_back_to_filename(env):
    assert upload(env["ingress"]).json()["title"] == "Herbst Wald"


def test_upload_and_display(env):
    r = upload(env["ingress"], display="true", mode="crop")
    body = r.json()
    assert body["displayed"] is True and body["tv_content_id"]
    assert env["frame"].current == body["tv_content_id"]
    assert env["frame"].art_mode is True


def test_display_reuses_and_reuploads(env):
    c, frame = env["ingress"], env["frame"]
    img = upload(c).json()
    first = c.post(f"/api/images/{img['id']}/display").json()["tv_content_id"]
    c.post(f"/api/images/{img['id']}/display")
    assert len(frame.images) == 1  # selected again, not uploaded twice
    frame.images.clear()  # deleted on the TV in the meantime
    second = c.post(f"/api/images/{img['id']}/display").json()["tv_content_id"]
    assert second != first and frame.current == second


def test_display_error_is_502_and_upload_kept(env):
    env["frame"].fail = True
    r = upload(env["ingress"], display="true")
    assert r.status_code == 201
    assert r.json()["displayed"] is False and "nicht erreichbar" in r.json()["display_error"]
    assert env["ingress"].post(f"/api/images/{r.json()['id']}/display").status_code == 502


def test_random(env):
    c = env["ingress"]
    assert c.post("/api/frame/random").status_code == 404
    ids = {upload(c).json()["id"] for _ in range(3)}
    assert c.post("/api/frame/random").json()["id"] in ids


def test_remote_images_and_thumbnail(env):
    c = env["ingress"]
    img = upload(c, display="true").json()
    res = c.get("/api/frame/remote-images").json()
    assert res["supported"] is True
    assert res["images"][0]["local_id"] == img["id"]
    cid = res["images"][0]["content_id"]
    assert c.get(f"/api/frame/remote-images/{cid}/thumbnail").status_code == 200
    assert c.post(f"/api/frame/remote-images/{cid}/display").status_code == 200


def test_remote_images_unsupported_is_honest(env):
    env["frame"].fail = True
    res = env["ingress"].get("/api/frame/remote-images").json()
    assert res == {"supported": False, "message": "Fernseher nicht erreichbar", "images": []}


def test_status(env):
    upload(env["ingress"], display="true")
    s = env["ingress"].get("/api/frame/status").json()
    assert s["image_count"] == 1
    assert s["frame"]["art_mode"] is True
    assert s["current_local_image"]["title"] == "Herbst Wald"


def test_artmode_toggle(env):
    assert env["ingress"].post("/api/frame/artmode", json={"on": True}).json() == {"on": True}
    assert env["frame"].art_mode is True


def test_rejects_non_image(env):
    r = upload(env["ingress"], data=b"not an image at all")
    assert r.status_code == 422 and "JPG" in r.json()["detail"]


def test_rejects_gif(env):
    r = upload(env["ingress"], data=make_jpeg(fmt="GIF"))
    assert r.status_code == 422


def test_rejects_too_large(env):
    r = upload(env["ingress"], data=b"\xff\xd8" + b"0" * (3 * 1024 * 1024))
    assert r.status_code == 413


def test_bad_mode(env):
    assert upload(env["ingress"], mode="stretch").status_code == 422


def test_path_traversal_and_bad_ids(env):
    c = env["ingress"]
    for bad in ["..%2F..%2Fetc%2Fpasswd", "../options", "abc", "0" * 31 + "g"]:
        assert c.get(f"/api/images/{bad}").status_code == 404
        assert c.get(f"/api/images/{bad}/file").status_code in (404,)
        assert c.delete(f"/api/images/{bad}").status_code == 404
    assert c.get("/api/frame/remote-images/..%2Fx/thumbnail").status_code == 404
    assert c.post("/api/frame/remote-images/a b/display").status_code == 502


def test_external_requires_token(env):
    e = env["external"]
    assert e.get("/api/images").status_code == 401
    assert e.get("/api/images", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert e.get("/api/images", headers=auth(env)).status_code == 200
    r = e.post("/api/images", headers=auth(env), files={"file": ("x.png", make_jpeg(fmt="PNG"), "image/png")})
    assert r.status_code == 201


def test_external_has_no_settings_or_ui(env):
    e = env["external"]
    assert e.get("/api/settings", headers=auth(env)).status_code == 404
    assert e.post("/api/settings/pair", headers=auth(env)).status_code == 404
    assert e.get("/", headers=auth(env)).status_code == 404


def test_token_rotation(env):
    old = auth(env)
    new = env["ingress"].post("/api/settings/api-token/rotate").json()["token"]
    assert env["external"].get("/api/images", headers=old).status_code == 401
    assert env["external"].get("/api/images", headers={"Authorization": f"Bearer {new}"}).status_code == 200


def test_ingress_blocks_other_clients(env):
    strict = TestClient(create_ingress_app(lambda: env["bridge"]))  # testclient is not the ingress proxy
    assert strict.get("/api/images").status_code == 403
    assert strict.get("/").status_code == 403
    assert strict.get("/health").status_code == 200


def test_settings_and_ui(env):
    s = env["ingress"].get("/api/settings").json()
    assert s["devices"][0]["entities"][0]["entity_id"] == "media_player.frame"
    assert "The Frame Bridge" in env["ingress"].get("/").text
    assert env["ingress"].get("/static/app.js").status_code == 200


def test_persistence_across_restart(env):
    from app.service import Bridge
    from app.storage import ImageStore
    img = upload(env["ingress"]).json()
    s = env["settings"]
    store2 = ImageStore(s.db_path, s.images_dir, s.thumbs_dir)
    b2 = Bridge(s, store2, env["frame"], None)
    assert b2.store.get(img["id"]).title == img["title"]
    assert s.api_token() == s.api_token()


def test_openapi_is_31(env):
    spec = env["external"].get("/openapi.json").json()
    assert spec["openapi"].startswith("3.1")
    paths = spec["paths"]
    for p in ["/health", "/api/frame/status", "/api/images", "/api/images/{image_id}",
              "/api/images/{image_id}/display", "/api/frame/random", "/api/frame/remote-images"]:
        assert p in paths
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
