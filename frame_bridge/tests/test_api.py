import io

from fastapi.testclient import TestClient
from PIL import Image

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


def test_random_empty_and_single(env):
    c = env["ingress"]
    r = c.post("/api/frame/random")
    assert r.status_code == 404 and "lade zuerst" in r.json()["detail"]
    upload(c, display="true")
    r = c.post("/api/frame/random")
    assert r.status_code == 404 and "bereits angezeigt" in r.json()["detail"]


def test_random_never_repeats_current(env):
    c, frame = env["ingress"], env["frame"]
    for _ in range(3):
        upload(c)
    for _ in range(15):
        before = frame.current
        res = c.post("/api/frame/random").json()
        assert res["content_id"] == frame.current != before


def test_random_uses_tv_only_pictures(env):
    c, frame = env["ingress"], env["frame"]
    frame.images = {"MY_F0001": b"x", "MY_F0002": b"y"}  # put on the TV by other means
    frame.current = "MY_F0001"
    res = c.post("/api/frame/random").json()
    assert res == {"source": "tv", "content_id": "MY_F0002", "title": "MY_F0002", "image": None}
    assert frame.art_mode is True


def test_random_does_not_double_count_uploaded(env):
    c, frame = env["ingress"], env["frame"]
    img = upload(c, display="true").json()  # local and on the TV, showing now
    frame.images["MY_F0099"] = b"z"
    res = c.post("/api/frame/random").json()
    assert res["content_id"] == "MY_F0099"
    assert len(frame.images) == 2 and img["tv_content_id"] in frame.images


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


def test_mattes_list(env):
    m = env["ingress"].get("/api/frame/mattes").json()
    assert "shadowbox" in m["types"] and "polar" in m["colors"]


def test_upload_with_matte_sends_reduced_copy(env):
    img = upload(env["ingress"], display="true", matte="shadowbox_polar").json()
    cid = img["tv_content_id"]
    assert img["matte"] == "shadowbox_polar"
    assert env["frame"].matte_of[cid] == "shadowbox_polar"
    assert env["frame"].width_of[cid] == 1920
    full = env["ingress"].get(f"/api/images/{img['id']}/file")
    assert Image.open(io.BytesIO(full.content)).width == 3840  # original kept in 4K


def test_upload_rejects_bad_matte(env):
    assert upload(env["ingress"], matte="../etc").status_code == 422


def test_matte_on_4k_copy_uploads_fitting_copy_and_keeps_old(env):
    c, frame = env["ingress"], env["frame"]
    img = upload(c, display="true").json()
    old = img["tv_content_id"]
    assert frame.width_of[old] == 3840
    r = c.post(f"/api/images/{img['id']}/matte", json={"matte": "modern_antique"}).json()
    new = r["tv_content_id"]
    assert r["replaced_tv_content_id"] == old and new != old
    assert frame.width_of[new] == 1920 and frame.matte_of[new] == "modern_antique"
    assert old in frame.images and frame.deleted == []  # never deleted without confirmation
    assert frame.current == new  # was showing, so the new copy is shown

    # another matte on the reduced copy: changed in place
    r = c.post(f"/api/images/{img['id']}/matte", json={"matte": "shadowbox_black"}).json()
    assert r["replaced_tv_content_id"] is None and r["tv_content_id"] == new
    assert frame.matte_of[new] == "shadowbox_black"

    # back to none: the 4K copy still on the TV is reused, nothing uploaded
    uploads = frame.uploads
    r = c.post(f"/api/images/{img['id']}/matte", json={"matte": "none"}).json()
    assert r["replaced_tv_content_id"] is None and r["tv_content_id"] == old
    assert frame.uploads == uploads and frame.current == old


def test_tv_refusing_every_change_reuses_copies(env):
    """The user's case: the 2025 Frame refuses change_matte even on 1920x1080 copies."""
    c, frame = env["ingress"], env["frame"]
    frame.refuse_all_changes = True
    img = upload(c, display="true", matte="modernthin_warm").json()
    warm = img["tv_content_id"]
    assert frame.uploads == 1 and frame.width_of[warm] == 1920

    # A -> B: unavoidable new copy, old one offered for deletion
    r = c.post(f"/api/images/{img['id']}/matte", json={"matte": "modernwide_polar"}).json()
    polar = r["tv_content_id"]
    assert r["replaced_tv_content_id"] == warm and frame.uploads == 2 and frame.current == polar

    # B -> A (user kept the old copy): reused, no upload
    r = c.post(f"/api/images/{img['id']}/matte", json={"matte": "modernthin_warm"}).json()
    assert r["tv_content_id"] == warm and r["replaced_tv_content_id"] is None
    assert frame.uploads == 2 and frame.current == warm

    # same change via the TV tab on the reduced copy
    r = c.post(f"/api/frame/remote-images/{warm}/matte", json={"matte": "modernwide_polar"}).json()
    assert r["replaced_tv_content_id"] is None and frame.uploads == 2 and frame.current == polar

    # same matte again: nothing happens
    r = c.post(f"/api/images/{img['id']}/matte", json={"matte": "modernwide_polar"}).json()
    assert frame.uploads == 2 and r["replaced_tv_content_id"] is None

    # TV tab knows older copies belong to the gallery picture
    tv = c.get("/api/frame/remote-images").json()["images"]
    assert {i["local_title"] for i in tv} == {img["title"]}


def test_deleted_copy_is_not_reused(env):
    c, frame = env["ingress"], env["frame"]
    frame.refuse_all_changes = True
    img = upload(c, display="true", matte="modernthin_warm").json()
    warm = img["tv_content_id"]
    c.post(f"/api/images/{img['id']}/matte", json={"matte": "modernwide_polar"})
    assert c.delete(f"/api/frame/remote-images/{warm}?confirm=true").status_code == 204
    r = c.post(f"/api/images/{img['id']}/matte", json={"matte": "modernthin_warm"}).json()
    assert r["tv_content_id"] != warm and frame.uploads == 3


def test_copy_deleted_outside_is_forgotten(env):
    c, frame = env["ingress"], env["frame"]
    frame.refuse_all_changes = True
    img = upload(c, display="true", matte="modernthin_warm").json()
    warm = img["tv_content_id"]
    c.post(f"/api/images/{img['id']}/matte", json={"matte": "modernwide_polar"})
    frame.images.pop(warm)  # deleted on the TV with the remote
    r = c.post(f"/api/images/{img['id']}/matte", json={"matte": "modernthin_warm"}).json()
    assert r["tv_content_id"] != warm and frame.width_of[r["tv_content_id"]] == 1920


def test_matte_for_image_not_on_tv_is_remembered(env):
    c, frame = env["ingress"], env["frame"]
    img = upload(c).json()
    r = c.post(f"/api/images/{img['id']}/matte", json={"matte": "flexible_black"}).json()
    assert r["replaced_tv_content_id"] is None
    shown = c.post(f"/api/images/{img['id']}/display").json()
    assert frame.matte_of[shown["tv_content_id"]] == "flexible_black"
    assert frame.width_of[shown["tv_content_id"]] == 1920


def test_matte_via_tv_tab_on_gallery_picture_uses_original(env):
    c, frame = env["ingress"], env["frame"]
    img = upload(c, display="true").json()
    r = c.post(f"/api/frame/remote-images/{img['tv_content_id']}/matte", json={"matte": "modern_polar"}).json()
    assert r["replaced_tv_content_id"] == img["tv_content_id"]


def test_matte_on_small_tv_only_picture(env):
    c, frame = env["ingress"], env["frame"]
    frame.images["MY_F0002"] = b"x"
    frame.width_of["MY_F0002"] = 1671
    frame.matte_of["MY_F0002"] = "shadowbox_antique"
    assert c.post("/api/frame/remote-images/MY_F0002/matte", json={"matte": "none"}).status_code == 200
    assert frame.matte_of["MY_F0002"] == "none"
    assert c.get("/api/frame/remote-images").json()["images"][0]["matte_id"] == "none"


def test_matte_on_4k_tv_only_picture_explains(env):
    c, frame = env["ingress"], env["frame"]
    frame.images["MY_F0001"] = b"x"
    r = c.post("/api/frame/remote-images/MY_F0001/matte", json={"matte": "modern_polar"})
    assert r.status_code == 409 and "4K" in r.json()["detail"] and "The Frame Bridge" in r.json()["detail"]


def test_silently_ignored_matte_is_reported(env):
    c, frame = env["ingress"], env["frame"]
    frame.images["MY_F0002"] = b"x"
    frame.width_of["MY_F0002"] = 1671
    frame.ignore_matte = True
    r = c.post("/api/frame/remote-images/MY_F0002/matte", json={"matte": "modern_polar"})
    assert r.status_code == 409


def test_delete_on_tv_needs_confirmation_and_ingress(env):
    c, frame = env["ingress"], env["frame"]
    img = upload(c, display="true").json()
    cid = img["tv_content_id"]
    assert c.delete(f"/api/frame/remote-images/{cid}").status_code == 400
    assert frame.deleted == []
    assert env["external"].delete(f"/api/frame/remote-images/{cid}?confirm=true", headers=auth(env)).status_code in (404, 405)
    assert frame.deleted == []
    assert c.delete(f"/api/frame/remote-images/{cid}?confirm=true").status_code == 204
    assert frame.deleted == [cid]
    assert c.get(f"/api/images/{img['id']}").json()["tv_content_id"] is None  # local copy stays


def test_delete_on_tv_refuses_art_store(env):
    c = env["ingress"]
    assert c.delete("/api/frame/remote-images/SAM-S5954?confirm=true").status_code == 502


def test_bad_matte_rejected(env):
    c = env["ingress"]
    img = upload(c).json()
    for bad in ["../x", "Shadowbox_Polar", "a_b_c", ""]:
        assert c.post(f"/api/images/{img['id']}/matte", json={"matte": bad}).status_code == 422
    assert c.post("/api/frame/remote-images/MY_F0001/matte", json={"matte": "../x"}).status_code == 422


def test_old_database_is_migrated(tmp_path):
    import sqlite3
    from app.storage import ImageStore
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE images (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
        width INTEGER NOT NULL, height INTEGER NOT NULL, source_width INTEGER NOT NULL,
        source_height INTEGER NOT NULL, fit_mode TEXT NOT NULL, original_name TEXT NOT NULL,
        size_bytes INTEGER NOT NULL, tv_content_id TEXT)""")
    con.execute("INSERT INTO images VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("a" * 32, "Alt", "2026-11-11T08:00:00+00:00", 3840, 2160, 100, 100, "fit", "x.jpg", 1, "MY_F0003"))
    con.commit(); con.close()
    (tmp_path / "i").mkdir(); (tmp_path / "t").mkdir()
    store = ImageStore(db, tmp_path / "i", tmp_path / "t")
    assert store.get("a" * 32).matte == "none"


def test_page_inlines_current_script(env):
    from app import __version__
    html = env["ingress"].get("/")
    assert "no-store" in html.headers["cache-control"]
    assert 'src="static/app.js"' not in html.text and "static/style.css" not in html.text
    assert f'FRAME_BRIDGE_VERSION = "{__version__}"' in html.text
    assert "function pickMatte" in html.text and f"The Frame Bridge {__version__}" in html.text


def test_real_adapter_dedupes_tv_list(tmp_path):
    import asyncio
    from app.frame import SamsungFrame

    class Art:
        async def available(self, category=None, timeout=10):
            return [
                {"content_id": "MY_F0003", "category_id": "MY-C0008", "matte_id": "none", "width": 3840, "height": 2160},
                {"content_id": "SAM-S5954", "category_id": "MY-C0008", "matte_id": "SHADOWBOX_POLAR"},
                {"content_id": "MY_F0002", "category_id": "MY-C0002", "matte_id": "SHADOWBOX_ANTIQUE"},
                {"content_id": "MY_F0003", "category_id": "MY-C0002", "matte_id": "none", "width": 3840, "height": 2160},
            ]

    f = SamsungFrame("1.2.3.4", tmp_path / "t.txt")

    async def client():
        return Art()

    f._client = client
    items = asyncio.run(f.list_remote())
    assert [i["content_id"] for i in items] == ["MY_F0003", "MY_F0002"]
    assert items[1]["matte_id"] == "shadowbox_antique"
