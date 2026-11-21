PLAYLIST = """#EXTM3U
#EXTINF:-1 tvg-language="en" tvg-country="US" group-title="Test",Color Bars
http://example.test/bars.ts
#EXTINF:-1 group-title="Test",Tone
http://example.test/tone.ts
"""


def test_upload_lists_channel_count_and_checking_status(client):
    uploaded = client.post(
        "/api/playlists/upload",
        data={"name": "Room"},
        files={"file": ("room.m3u", PLAYLIST, "audio/mpegurl")},
    )
    assert uploaded.status_code == 201, uploaded.text
    listed = client.get("/api/playlists")
    assert listed.status_code == 200
    row = listed.json()["playlists"][0]
    assert row["name"] == "Room"
    assert row["channel_count"] == 2
    assert row["pending"] == 2
    assert row["status"] == "checking"
    assert row["last_imported_at"]


def test_bad_playlist_url_names_the_problem(client):
    response = client.post("/api/playlists", json={"name": "Remote", "url": "ftp://example.test/list.m3u"})
    assert response.status_code == 422
    assert response.json()["detail"] == "playlist URL must be http or https"


def test_parse_failure_is_specific(client):
    response = client.post(
        "/api/playlists/upload",
        data={"name": "Empty"},
        files={"file": ("empty.m3u", "#EXTM3U\n", "audio/mpegurl")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "playlist did not contain any channels"
    assert client.get("/api/playlists").json()["playlists"] == []


def test_duplicate_name_and_url(client, monkeypatch):
    monkeypatch.setattr("app.api.fetch_playlist_text", lambda url: PLAYLIST)
    first = client.post("/api/playlists", json={"name": "Remote", "url": "https://example.test/list.m3u"})
    assert first.status_code == 201, first.text
    again = client.post("/api/playlists", json={"name": "Other", "url": "https://example.test/list.m3u"})
    assert again.status_code == 409
    assert again.json()["detail"] == "a playlist with this URL already exists"
    named = client.post(
        "/api/playlists/upload",
        data={"name": "Remote"},
        files={"file": ("other.m3u", PLAYLIST, "audio/mpegurl")},
    )
    assert named.status_code == 409
    assert named.json()["detail"] == "a playlist with this name already exists"


def test_reimport_and_delete(client):
    uploaded = client.post(
        "/api/playlists/upload",
        data={"name": "Room", "interval_minutes": "30"},
        files={"file": ("room.m3u8", PLAYLIST, "audio/mpegurl")},
    )
    assert uploaded.status_code == 201, uploaded.text
    playlist_id = uploaded.json()["playlist_id"]
    again = client.post(f"/api/playlists/{playlist_id}/import")
    assert again.status_code == 200, again.text
    assert again.json()["updated"] == 2
    removed = client.delete(f"/api/playlists/{playlist_id}")
    assert removed.status_code == 204
    assert client.get("/api/playlists").json()["playlists"] == []
