from app.db import connect, init_db
from app.group_tags import group_tag_names


PLAYLIST = """#EXTM3U
#EXTINF:-1 group-title="Animation;Education;Family",Cartoon
http://example.test/cartoon.ts
#EXTINF:-1 group-title="Russia",One
http://example.test/one.ts
#EXTINF:-1 group-title="Russian",Two
http://example.test/two.ts
#EXTINF:-1 group-title="Взрослые 🔞",Night
http://example.test/night.ts
#EXTINF:-1 group-title="BoKeP",Odd
http://example.test/odd.ts
"""


def test_group_titles_split_and_filters_hide_adult(tmp_path, client):
    assert group_tag_names(" Animation; Education ;Family ") == ["Animation", "Education", "Family"]
    uploaded = client.post(
        "/api/playlists/upload",
        data={"name": "Messy"},
        files={"file": ("m.m3u", PLAYLIST, "audio/mpegurl")},
    )
    assert uploaded.status_code == 201
    filters = client.get("/api/guide-filters").json()
    names = {tag["name"] for tag in filters["tags"]}
    assert "Animation" in names
    assert "Education" in names
    assert "Family" in names
    assert "Animation;Education;Family" not in names
    assert "Взрослые" not in names
    shown = client.get("/api/guide-filters", params={"include_adult": "true"}).json()
    adult = next(tag for tag in shown["tags"] if "зросл" in tag["name"].casefold() or tag["adult"])
    assert adult["adult"] is True
    saved = client.put(
        "/api/guide-filters",
        json={"hidden": ["BoKeP"], "pinned": ["Russian"], "labels": {"russian": "Russian language"}},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert "BoKeP" in body["hidden"] or any(tag["name"] == "BoKeP" and tag["hidden"] for tag in body["tags"])
    merged = client.post("/api/guide-filters/merge", json={"source": "Russia", "target": "Russian"})
    assert merged.status_code == 200
    conn = connect(str(tmp_path / "home-iptv.sqlite"))
    try:
        left = conn.execute("SELECT name FROM tags WHERE name = 'Russia' COLLATE NOCASE").fetchone()
        assert left is None
    finally:
        conn.close()
    init_db(str(tmp_path / "t.sqlite"))
