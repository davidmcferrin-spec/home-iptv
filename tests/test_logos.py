from app.icons import apple_touch_png, favicon_ico, favicon_svg
from app.logos import acceptable_logo, initials_svg, strip_emoji
from app.verify import _from_payload


def test_malformed_logo_hosts_are_rejected():
    assert acceptable_logo("https://uplo_logo.png/") is None
    assert acceptable_logo("https://example.com/logo.png") == "https://example.com/logo.png"
    assert acceptable_logo("ftp://example.com/a.png") is None
    assert initials_svg("News").startswith(b"<svg")


def test_probe_keeps_profile_and_b_frames():
    result = _from_payload(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "profile": "High",
                    "has_b_frames": 2,
                    "width": 1280,
                    "height": 720,
                }
            ]
        }
    )
    assert result.video_profile == "High"
    assert result.has_b_frames == 2
    assert result.video_codec == "h264"


def test_icon_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_WORKER", "0")
    monkeypatch.setenv("EMBED_HUB", "0")
    monkeypatch.setenv("EMBED_DISCOVERY", "0")
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    ico = client.get("/favicon.ico")
    assert ico.status_code == 200
    assert ico.content[:4] == favicon_ico()[:4]
    assert client.get("/favicon.svg").content == favicon_svg()
    png = client.get("/apple-touch-icon.png")
    assert png.status_code == 200
    assert png.content[:8] == apple_touch_png()[:8]


def test_emoji_strip():
    assert strip_emoji("News 😀 HD") == "News HD"
