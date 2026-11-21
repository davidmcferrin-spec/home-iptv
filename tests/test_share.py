PLAYLIST = """#EXTM3U
#EXTINF:-1 tvg-id="news" tvg-name="News" tvg-logo="http://example.test/news.png" tvg-language="en" tvg-country="US" group-title="News",Harbor News
http://example.test/news.ts
#EXTINF:-1 tvg-language="fr" tvg-country="FR" group-title="Cinema",Soir
http://example.test/soir.ts
#EXTINF:-1 group-title="Adult",Midnight XXX
http://example.test/xxx.ts
"""


def _upload(client):
    uploaded = client.post(
        "/api/playlists/upload",
        data={"name": "Room"},
        files={"file": ("room.m3u", PLAYLIST, "audio/mpegurl")},
    )
    assert uploaded.status_code == 201, uploaded.text
    listed = client.get("/api/channels", params={"include_adult": "true", "limit": 20})
    return {row["title"]: row for row in listed.json()["channels"]}


def _mark_online(client, channel_id):
    from app.db import connect

    conn = connect(client.app.state.cfg.database_path)
    try:
        conn.execute("UPDATE channels SET status = 'online' WHERE id = ?", (channel_id,))
        conn.commit()
    finally:
        conn.close()


def test_manual_fields_survive_reimport(client):
    by_title = _upload(client)
    news = by_title["Harbor News"]
    patched = client.patch(
        f"/api/channels/{news['id']}",
        json={
            "title": "Desk",
            "logo_url": "http://example.test/manual.png",
            "tvg_id": "desk.manual",
            "language": "de",
            "country": "DE",
            "adult": True,
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["title_source"] == "manual"
    assert body["logo_source"] == "manual"
    assert body["guide_source"] == "manual"
    assert body["language_source"] == "manual"
    again = client.post(f"/api/playlists/{news['source']['id']}/import")
    assert again.status_code == 200, again.text
    listed = client.get("/api/channels", params={"include_adult": "true", "limit": 20}).json()
    kept = next(row for row in listed["channels"] if row["id"] == news["id"])
    assert kept["title"] == "Desk"
    assert kept["logo_url"] == "http://example.test/manual.png"
    assert kept["tvg_id"] == "desk.manual"
    assert kept["language"] == "de"
    assert kept["country"] == "DE"
    assert kept["adult"] is True


def test_bulk_sets_language_country_adult_and_tag(client):
    by_title = _upload(client)
    tag = client.post("/api/tags", json={"name": "Later"})
    assert tag.status_code == 201, tag.text
    ids = [by_title["Harbor News"]["id"], by_title["Soir"]["id"]]
    updated = client.post(
        "/api/channels/bulk",
        json={"ids": ids, "language": "es", "country": "ES", "adult": False, "add_tag_ids": [tag.json()["id"]]},
    )
    assert updated.status_code == 200, updated.text
    listed = client.get("/api/channels", params={"tag": "Later", "include_adult": "true"}).json()
    found = {row["id"]: row for row in listed["channels"]}
    assert set(found) == set(ids)
    assert found[ids[0]]["language"] == "es"
    assert found[ids[0]]["country"] == "ES"
    assert found[ids[0]]["language_source"] == "manual"


def test_playlist_and_guide_filters(client):
    by_title = _upload(client)
    _mark_online(client, by_title["Harbor News"]["id"])
    _mark_online(client, by_title["Midnight XXX"]["id"])
    playlist = client.get("/playlist.m3u")
    assert playlist.status_code == 200
    text = playlist.text
    assert text.startswith("#EXTM3U")
    assert 'url-tvg="http://testserver/guide.xml"' in text
    assert "Harbor News" in text
    assert "Midnight XXX" not in text
    assert "Soir" not in text
    assert 'tvg-id="news"' in text
    assert 'tvg-logo="http://example.test/news.png"' in text
    assert 'group-title="News"' in text
    assert f"http://testserver/play/{by_title['Harbor News']['id']}" in text
    adult = client.get("/playlist.m3u", params={"include_adult": "1"})
    assert "Midnight XXX" in adult.text
    french = client.get("/playlist.m3u", params={"language": "fr", "online": "0"})
    assert "Soir" in french.text
    assert "Harbor News" not in french.text
    guide = client.get("/guide.xml")
    assert "<channel id=\"news\">" in guide.text
    assert "Midnight XXX" not in guide.text


def test_play_redirects_to_the_hub_stream(client):
    by_title = _upload(client)

    class Stub:
        def ensure(self, channel, device, origin="watch"):
            return {"path": f"ch{channel['id']}"}

        def advertise_host(self, host=None):
            return None

    client.app.state.hub = Stub()
    response = client.get(f"/play/{by_title['Harbor News']['id']}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == f"http://testserver:8888/ch{by_title['Harbor News']['id']}/index.m3u8"
    hidden = client.get(f"/play/{by_title['Midnight XXX']['id']}", follow_redirects=False)
    assert hidden.status_code == 404


def test_share_qr_and_folder_setting(client):
    share = client.get("/api/share")
    assert share.status_code == 200
    body = share.json()
    assert body["playlist_url"] == "http://testserver/playlist.m3u"
    assert body["guide_url"] == "http://testserver/guide.xml"
    assert body["playlist_qr"].startswith("<svg")
    bad = client.patch("/api/settings", json={"playlist_group": "folder"})
    assert bad.status_code == 400
    saved = client.patch("/api/settings", json={"playlist_group": "language"})
    assert saved.json()["playlist_group"] == "language"
