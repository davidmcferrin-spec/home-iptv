from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "sample.m3u"
XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="news.us"><display-name>City News Updated</display-name></channel>
  <programme start="20261120190000 +0000" stop="20261120193000 +0000" channel="news.us">
    <title>Night Desk</title>
    <sub-title>The Flood</sub-title>
    <desc>A quiet hour.</desc>
    <episode-num system="xmltv_ns">1.4.0/1</episode-num>
  </programme>
</tv>
"""


def _import(client):
    response = client.post(
        "/api/playlists/upload",
        data={"name": "Sample"},
        files={"file": ("sample.m3u", FIXTURE.read_bytes(), "audio/mpegurl")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_search_filters_sort_and_hides_adult(client):
    imported = _import(client)
    assert imported["imported"] == 6
    hidden = client.get("/api/channels")
    assert hidden.status_code == 200
    titles = [channel["title"] for channel in hidden.json()["channels"]]
    assert "Midnight XXX" not in titles
    assert "Adult Swim East" in titles
    assert hidden.json()["total"] == 5

    revealed = client.get("/api/channels", params={"include_adult": "true"})
    assert revealed.json()["total"] == 6
    assert any(channel["title"] == "Midnight XXX" and channel["adult"] for channel in revealed.json()["channels"])

    found = client.get("/api/channels", params={"q": "city"})
    assert [channel["title"] for channel in found.json()["channels"]] == ["City News Updated"]

    french = client.get("/api/channels", params={"language": "fr"})
    assert [channel["title"] for channel in french.json()["channels"]] == ["Cinema FR"]

    british = client.get("/api/channels", params={"country": "GB", "sort": "title"})
    assert [channel["title"] for channel in british.json()["channels"]] == ["Match Night", "Travel Hour (UK)"]

    news = client.get("/api/channels", params={"tag": "News"})
    assert news.json()["total"] == 1

    offline = client.get("/api/channels", params={"status": "unknown", "sort": "title", "order": "asc"})
    assert offline.json()["total"] == 5

    tags = client.get("/api/tags").json()["tags"]
    counts = {tag["name"]: tag["channel_count"] for tag in tags}
    assert counts["Adult"] == 0
    assert counts["News"] == 1
    adult_counts = {tag["name"]: tag["channel_count"] for tag in client.get("/api/tags", params={"include_adult": "true"}).json()["tags"]}
    assert adult_counts["Adult"] == 1


def test_manual_language_survives_reimport_and_bulk_tags(client):
    _import(client)
    channels = client.get("/api/channels", params={"include_adult": "true"}).json()["channels"]
    match = next(channel for channel in channels if channel["title"] == "Match Night")
    assert match["country"] == "GB"
    assert match["country_source"] == "detected"
    patched = client.patch(f"/api/channels/{match['id']}", json={"language": "en", "country": "US"})
    assert patched.status_code == 200
    assert patched.json()["language_source"] == "manual"
    assert patched.json()["country"] == "US"

    again = client.post(
        "/api/playlists/upload",
        data={"name": "Sample again"},
        files={"file": ("sample.m3u", FIXTURE.read_bytes(), "audio/mpegurl")},
    )
    assert again.status_code == 201
    refreshed = client.get(f"/api/channels/{match['id']}").json()
    assert refreshed["language"] == "en"
    assert refreshed["language_source"] == "manual"
    assert refreshed["country"] == "US"
    assert refreshed["country_source"] == "manual"

    created = client.post("/api/tags", json={"name": "Favorites"})
    assert created.status_code == 201
    tag_id = created.json()["id"]
    renamed = client.patch(f"/api/tags/{tag_id}", json={"name": "Keepers"})
    assert renamed.status_code == 200
    bulk = client.post("/api/channels/bulk", json={"ids": [match["id"]], "add_tag_ids": [tag_id], "adult": False})
    assert bulk.status_code == 200
    tagged = client.get("/api/channels", params={"tag": "Keepers"}).json()
    assert [channel["id"] for channel in tagged["channels"]] == [match["id"]]
    assert tagged["channels"][0]["adult_source"] == "manual"
    removed = client.post(f"/api/tags/{tag_id}/channels", json={"channel_ids": [match["id"]], "action": "remove"})
    assert removed.status_code == 200
    assert client.get("/api/channels", params={"tag": "Keepers"}).json()["total"] == 0
    assert client.delete(f"/api/tags/{tag_id}").status_code == 204


def test_epg_and_direct_channel_lookup_hide_adult(client):
    _import(client)
    stored = client.post("/api/epg/upload", files={"file": ("guide.xml", XMLTV.encode(), "application/xml")})
    assert stored.status_code == 200
    assert stored.json()["stored"] == 1
    programmes = client.get(
        "/api/programs",
        params={"start": "2026-11-20T00:00:00Z", "end": "2026-11-21T00:00:00Z"},
    ).json()["programs"]
    assert programmes[0]["title"] == "Night Desk"
    assert programmes[0]["episode_title"] == "The Flood"
    assert programmes[0]["season"] == 2
    assert programmes[0]["episode"] == 5
    adult = client.get("/api/channels", params={"include_adult": "true", "q": "Midnight"}).json()["channels"][0]
    hidden = client.get(f"/api/channels/{adult['id']}")
    assert hidden.status_code == 404
    shown = client.get(f"/api/channels/{adult['id']}", params={"include_adult": "true"})
    assert shown.status_code == 200
