def test_bulk_reports_partial_failure_and_renumbers(client):
    uploaded = client.post(
        "/api/playlists/upload",
        data={"name": "Bulk"},
        files={
            "file": (
                "bulk.m3u",
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-language="en" tvg-country="US" group-title="News",Alpha',
                        "http://example.test/alpha.ts",
                        '#EXTINF:-1 tvg-language="en" tvg-country="US" group-title="News",Beta',
                        "http://example.test/beta.ts",
                    ]
                ),
                "audio/mpegurl",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    listed = client.get("/api/channels", params={"include_hidden": "true", "limit": 20}).json()
    ids = [row["id"] for row in listed["channels"]]
    missing = max(ids) + 50
    updated = client.post(
        "/api/channels/bulk",
        json={"ids": [ids[0], missing, ids[1]], "group_title": "Desk", "renumber_from": 40, "hidden": True},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 1
    assert body["errors"] == [{"id": missing, "error": "not found"}]
    by_id = {row["id"]: row for row in body["channels"]}
    assert by_id[ids[0]]["group_title"] == "Desk"
    assert by_id[ids[0]]["guide_number"] == "40"
    assert by_id[ids[0]]["hidden"] is True
    assert by_id[ids[1]]["guide_number"] == "41"
    undone = client.post("/api/channels/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["restored"] == 2
    restored = client.get(f"/api/channels/{ids[0]}", params={"include_hidden": "true"}).json()
    assert restored["group_title"] == "News"
    assert restored["hidden"] is False


def test_bulk_invalid_language_does_not_keep_the_other_edits(client):
    uploaded = client.post(
        "/api/playlists/upload",
        data={"name": "Rollback"},
        files={
            "file": (
                "rollback.m3u",
                "#EXTM3U\n#EXTINF:-1 group-title=\"News\",Keep\nhttp://example.test/keep.ts\n",
                "audio/mpegurl",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    channel_id = client.get("/api/channels", params={"limit": 5}).json()["channels"][0]["id"]
    updated = client.post(
        "/api/channels/bulk",
        json={"ids": [channel_id, channel_id + 80], "language": "not-a-language", "group_title": "Nope"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["succeeded"] == 0
    assert body["failed"] == 2
    row = client.get(f"/api/channels/{channel_id}").json()
    assert row["group_title"] == "News"
    assert row["language"] != "not-a-language"


def test_guide_online_only_keeps_unchecked(client):
    uploaded = client.post(
        "/api/playlists/upload",
        data={"name": "Lineup"},
        files={
            "file": (
                "lineup.m3u",
                "\n".join(
                    [
                        "#EXTM3U",
                        "#EXTINF:-1,Live One",
                        "http://example.test/one.ts",
                        "#EXTINF:-1,Dead Two",
                        "http://example.test/two.ts",
                        "#EXTINF:-1,New Three",
                        "http://example.test/three.ts",
                    ]
                ),
                "audio/mpegurl",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    rows = client.get("/api/channels", params={"include_hidden": "true", "limit": 20}).json()["channels"]
    by_title = {row["title"]: row for row in rows}
    from app.db import connect

    conn = connect(client.app.state.cfg.database_path)
    try:
        conn.execute("UPDATE channels SET status = 'online' WHERE id = ?", (by_title["Live One"]["id"],))
        conn.execute("UPDATE channels SET status = 'offline' WHERE id = ?", (by_title["Dead Two"]["id"],))
        conn.commit()
    finally:
        conn.close()
    visible = client.get(
        "/api/channels",
        params={"exclude_status": "offline", "include_hidden": "true", "limit": 20},
    ).json()
    titles = {row["title"] for row in visible["channels"]}
    assert "Live One" in titles
    assert "New Three" in titles
    assert "Dead Two" not in titles
    saved = client.patch("/api/settings", json={"guide_online_only": True, "guide_hide_unchecked": False})
    assert saved.status_code == 200, saved.text
    assert saved.json()["guide_online_only"] is True
    assert saved.json()["guide_hide_unchecked"] is False
    hidden_unchecked = client.get(
        "/api/channels",
        params={"exclude_status": ["offline", "unknown"], "include_hidden": "true", "limit": 20},
    ).json()
    only = {row["title"] for row in hidden_unchecked["channels"]}
    assert only == {"Live One"}
    cleared = client.patch("/api/settings", json={"guide_online_only": False})
    assert cleared.json()["guide_online_only"] is False
