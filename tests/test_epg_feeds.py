import gzip

from fastapi.testclient import TestClient

from app.db import connect, init_db
from app.epg_feeds import BUILTIN, refresh_programme_feeds
from app.main import create_app

UK_URL = "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"
US_URL = "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"
PW_URL = "https://epg.pw/xmltv/epg_GB.xml.gz"

XML = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="BBC.NEWS.HD.uk"><display-name>BBC NEWS HD</display-name></channel>
  <channel id="BBCNews.uk"><display-name>Other Desk</display-name></channel>
  <programme start="20261121000000 +0000" stop="20261121010000 +0000" channel="BBC.NEWS.HD.uk">
    <title>Newsroom</title>
  </programme>
  <programme start="20261121010000 +0000" stop="20261121020000 +0000" channel="BBCNews.uk">
    <title>By Id</title>
  </programme>
</tv>
"""


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "home-iptv.sqlite"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EMBED_WORKER", "0")
    monkeypatch.setenv("EMBED_HUB", "0")
    monkeypatch.setenv("EMBED_DISCOVERY", "0")
    return TestClient(create_app())


def _channel(client, title, country, tvg_id=None):
    body = f'#EXTM3U\n#EXTINF:-1 tvg-country="{country}"'
    if tvg_id:
        body += f' tvg-id="{tvg_id}"'
    body += f",{title}\nhttp://example.test/{title}.ts\n"
    uploaded = client.post(
        "/api/playlists/upload",
        data={"name": title},
        files={"file": ("room.m3u", body, "audio/mpegurl")},
    )
    assert uploaded.status_code == 201, uploaded.text
    listed = client.get("/api/channels", params={"include_adult": "true"}).json()["channels"]
    return next(row for row in listed if row["title"] == title)


def test_builtin_list_has_the_feeds_that_answered():
    urls = {url for _country, url in BUILTIN}
    assert UK_URL in urls
    assert US_URL in urls
    assert PW_URL in urls


def test_name_and_id_matches_from_gzip(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        news = _channel(client, "BBC News", "GB")
        exact = _channel(client, "Exact", "GB", tvg_id="BBCNews.uk")
        payload = gzip.compress(XML.encode())
        seen = []

        def fetch(url):
            seen.append(url)
            if url == UK_URL:
                return payload
            raise OSError(f"offline {url}")

        summary = refresh_programme_feeds(str(tmp_path / "home-iptv.sqlite"), fetch=fetch)
    assert UK_URL in seen
    assert US_URL not in seen
    assert summary["failed"] >= 1
    assert summary["matched"] == 2
    conn = connect(str(tmp_path / "home-iptv.sqlite"))
    news_shows = conn.execute("SELECT title FROM programs WHERE channel_id = ? ORDER BY start_at", (news["id"],)).fetchall()
    exact_shows = conn.execute("SELECT title FROM programs WHERE channel_id = ?", (exact["id"],)).fetchall()
    news_row = conn.execute("SELECT guide_source, programme_source FROM channels WHERE id = ?", (news["id"],)).fetchone()
    feed = conn.execute("SELECT last_status, last_matched FROM epg_feeds WHERE url = ?", (UK_URL,)).fetchone()
    conn.close()
    assert [row["title"] for row in news_shows] == ["Newsroom"]
    assert exact_shows[0]["title"] == "By Id"
    assert news_row["programme_source"] == "epgshare01.online"
    assert news_row["guide_source"] == "epgshare01.online"
    assert feed["last_status"] == "ok"
    assert feed["last_matched"] == 2


def test_disabled_feed_is_not_fetched(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _channel(client, "BBC News", "GB")
        feeds = client.get("/api/epg/feeds").json()["feeds"]
        uk = next(feed for feed in feeds if feed["url"] == UK_URL)
        assert uk["active"] is True
        patched = client.patch(f"/api/epg/feeds/{uk['id']}", json={"enabled": False})
        assert patched.status_code == 200
        assert patched.json()["enabled"] is False
        added = client.post("/api/epg/feeds", json={"url": "https://guides.example/extra.xml"})
        assert added.status_code == 201, added.text
        again = client.post("/api/epg/feeds", json={"url": "https://guides.example/extra.xml"})
        assert again.status_code == 409
        bad = client.post("/api/epg/feeds", json={"url": "ftp://guides.example/extra.xml"})
        assert bad.status_code == 400

        def fetch(url):
            raise AssertionError(url)

        conn = connect(str(tmp_path / "home-iptv.sqlite"))
        conn.execute("UPDATE epg_feeds SET enabled = 0")
        conn.commit()
        conn.close()
        summary = refresh_programme_feeds(str(tmp_path / "home-iptv.sqlite"), fetch=fetch)
    assert summary["feeds"] == 0


def test_streamed_parse_accepts_gzip_without_building_a_tree(tmp_path):
    path = tmp_path / "guide.xml.gz"
    path.write_bytes(gzip.compress(XML.encode()))
    from app.epg import iter_xmltv

    events = list(iter_xmltv(path))
    assert events[0][0] == "channel"
    assert events[-1][1]["title"] == "By Id"
    assert b"<tv" not in path.read_bytes()
