from app.public_url import playback_base, rewrite_playback

PLAYLIST = """#EXTM3U
#EXTINF:-1,Color Bars
http://example.test/bars.ts
"""


def test_loopback_bases_follow_the_request_host():
    info = rewrite_playback(
        {"path": "ch1", "whep_url": "http://127.0.0.1:8889/ch1/whep", "llhls_url": "http://127.0.0.1:8888/ch1/index.m3u8"},
        "192.0.2.10",
        "http://127.0.0.1:8889",
        "http://127.0.0.1:8888",
    )
    assert info["whep_url"] == "http://192.0.2.10:8889/ch1/whep"
    assert info["llhls_url"] == "http://192.0.2.10:8888/ch1/index.m3u8"
    assert playback_base("http://lan.example:8889", "192.0.2.10", 8889) == "http://lan.example:8889"


def test_watch_rewrites_urls_and_advertises_the_lan_host(client):
    uploaded = client.post(
        "/api/playlists/upload",
        data={"name": "Room"},
        files={"file": ("room.m3u", PLAYLIST, "audio/mpegurl")},
    )
    assert uploaded.status_code == 201, uploaded.text
    channel_id = client.get("/api/channels").json()["channels"][0]["id"]
    advertised = []

    def ensure(channel, device, origin):
        return {
            "channel_id": channel["id"],
            "path": f"ch{channel['id']}",
            "whep_url": f"http://127.0.0.1:8889/ch{channel['id']}/whep",
            "llhls_url": f"http://127.0.0.1:8888/ch{channel['id']}/index.m3u8",
            "transcode": "copy",
            "idle_timeout_seconds": 60,
            "upstream_pulls": 1,
        }

    client.app.state.hub.ensure = ensure
    client.app.state.hub.advertise_host = lambda host=None: advertised.append(host)
    response = client.post(f"/api/channels/{channel_id}/watch", headers={"host": "192.0.2.10:9595"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["whep_url"] == f"http://192.0.2.10:8889/ch{channel_id}/whep"
    assert body["llhls_url"] == f"http://192.0.2.10:8888/ch{channel_id}/index.m3u8"
    assert advertised == ["192.0.2.10"]
