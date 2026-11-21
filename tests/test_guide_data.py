from app.db import connect, init_db
from app.guide_data import refresh_guide
from app.playlist import parse_playlist

PLAYLIST = """#EXTM3U url-tvg="https://guide.example/epg.xml" x-tvg-url="https://ignored.example/epg.xml"
#EXTINF:-1,BBC News
http://example.test/news.ts
#EXTINF:-1 tvg-logo="http://example.test/mine.png",Manual Logo
http://example.test/manual.ts
"""

CHANNELS = [
    {"id": "BBCNews.uk", "name": "BBC News", "country": "UK", "alt_names": ["BBC News HD"]},
    {"id": "Closed.us", "name": "Gone", "country": "US", "closed": "2020-01-01"},
]
LOGOS = [
    {"channel": "BBCNews.uk", "feed": None, "in_use": True, "format": "PNG", "width": 200, "url": "https://logos.example/bbc.png"},
]
GUIDES = [
    {
        "channel": "BBCNews.uk",
        "sources": [{"url": "https://guide.example/iptv.xml", "format": "XML"}],
    }
]
XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="BBCNews.uk"><display-name>BBC News</display-name></channel>
  <programme start="20261121000000 +0000" stop="20261121010000 +0000" channel="BBCNews.uk">
    <title>Night Desk</title>
  </programme>
</tv>
"""


def test_playlist_header_keeps_the_epg_url():
    parsed = parse_playlist(PLAYLIST)
    assert parsed.epg_url == "https://guide.example/epg.xml"
    assert len(parsed.channels) == 2


def test_match_fills_logo_and_keeps_a_manual_logo(tmp_path):
    database = tmp_path / "home-iptv.sqlite"
    init_db(str(database))
    files = {
        "https://iptv-org.github.io/api/channels.json": CHANNELS,
        "https://iptv-org.github.io/api/logos.json": LOGOS,
        "https://iptv-org.github.io/api/guides.json": GUIDES,
        "https://guide.example/epg.xml": XMLTV,
        "https://guide.example/iptv.xml": XMLTV,
    }

    def fetch(url):
        import json
        body = files[url]
        if isinstance(body, str):
            return body.encode()
        return json.dumps(body).encode()

    from fastapi.testclient import TestClient
    from app.main import create_app
    import os

    os.environ["DATABASE_PATH"] = str(database)
    os.environ["DATA_DIR"] = str(tmp_path / "data")
    os.environ["EMBED_WORKER"] = "0"
    os.environ["EMBED_HUB"] = "0"
    os.environ["EMBED_DISCOVERY"] = "0"
    with TestClient(create_app()) as client:
        uploaded = client.post(
            "/api/playlists/upload",
            data={"name": "News"},
            files={"file": ("news.m3u", PLAYLIST, "audio/mpegurl")},
        )
        assert uploaded.status_code == 201, uploaded.text
        manual = next(channel for channel in client.get("/api/channels").json()["channels"] if channel["title"] == "Manual Logo")
        patched = client.patch(f"/api/channels/{manual['id']}", json={"logo_url": "http://example.test/mine.png"})
        assert patched.status_code == 200, patched.text
        # The playlist already had a logo, so mark the override after clearing it.
        conn = connect(str(database))
        conn.execute("UPDATE channels SET logo_url = NULL, logo_source = NULL WHERE id = ?", (manual["id"],))
        conn.execute(
            "UPDATE channels SET country = 'GB', country_source = 'detected' WHERE title = 'BBC News'"
        )
        conn.commit()
        conn.close()
        client.patch(f"/api/channels/{manual['id']}", json={"logo_url": "http://example.test/kept.png"})
        summary = refresh_guide(str(database), fetch=fetch)
    assert summary["matched"] == 1
    assert summary["missed"] == 1
    assert summary["logos"] == 1
    assert summary["programmes"] >= 1
    conn = connect(str(database))
    news = conn.execute("SELECT * FROM channels WHERE title = 'BBC News'").fetchone()
    kept = conn.execute("SELECT * FROM channels WHERE title = 'Manual Logo'").fetchone()
    shows = conn.execute("SELECT title FROM programs WHERE channel_id = ?", (news["id"],)).fetchall()
    conn.close()
    assert news["guide_source"] == "iptv-org"
    assert news["guide_match_id"] == "BBCNews.uk"
    assert news["logo_url"] == "https://logos.example/bbc.png"
    assert news["logo_source"] == "iptv-org"
    assert kept["logo_url"] == "http://example.test/kept.png"
    assert kept["logo_source"] == "manual"
    assert shows[0]["title"] == "Night Desk"
