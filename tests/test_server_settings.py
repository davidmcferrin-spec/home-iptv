def test_server_settings_persist_and_detect_the_request_host(client):
    detected = client.get("/api/settings", headers={"host": "192.0.2.10:9595"})
    assert detected.status_code == 200, detected.text
    body = detected.json()
    assert body["effective_webrtc_base"] == "http://192.0.2.10:8889"
    assert body["effective_hls_base"] == "http://192.0.2.10:8888"
    assert body["ports"]["http"] == 9595
    assert "Compose" in body["ports_note"]
    assert body["ports"]["webrtc_udp"] == 8189

    saved = client.patch(
        "/api/settings",
        json={
            "public_webrtc_base": "http://lan.example:8889",
            "verify_concurrency": 2,
            "verify_timeout_seconds": 9,
            "idle_timeout_seconds": 30,
            "hdhr_emulation": True,
            "webrtc_additional_host": "192.0.2.10",
        },
    )
    assert saved.status_code == 200, saved.text
    again = saved.json()
    assert again["public_webrtc_base"] == "http://lan.example:8889"
    assert again["effective_webrtc_base"] == "http://lan.example:8889"
    assert again["verify_concurrency"] == 2
    assert again["verify_timeout_seconds"] == 9
    assert again["idle_timeout_seconds"] == 30
    assert again["hdhr_emulation"] is True
    assert again["effective_webrtc_host"] == "192.0.2.10"
    assert again["playback_profile"] == "smooth"

    profile = client.patch("/api/settings", json={"playback_profile": "low"})
    assert profile.status_code == 200, profile.text
    assert profile.json()["playback_profile"] == "low"
    rejected = client.patch("/api/settings", json={"playback_profile": "fast"})
    assert rejected.status_code == 400

    bad = client.patch("/api/settings", json={"public_hls_base": "ftp://example.test/a"})
    assert bad.status_code == 400
    assert "public_hls_base" in bad.json()["detail"]
