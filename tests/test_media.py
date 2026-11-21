import json
from pathlib import Path

import pytest

from app.db import connect, init_db
from app.media_names import parse_video
from app.media_paths import PathEscape, file_inside, suggest_libraries
from app.media_play import choose_mode, detect_accel, hls_command
from app.media_scan import ProbeResult, scan_library


def test_episode_names_from_filename_and_season_folder():
    parsed = parse_video("The Office/Season 2/The Office - S02E03 - Dinner Party.mkv", "tv")
    assert parsed.show == "The Office"
    assert parsed.season == 2
    assert parsed.episode == 3
    assert parsed.title == "Dinner Party"
    dotted = parse_video("Show.Name.S01E02.mkv", "tv")
    assert dotted.show == "Show Name"
    assert (dotted.season, dotted.episode) == (1, 2)
    alt = parse_video("Pilot 1x02.mkv", "tv")
    assert (alt.season, alt.episode) == (1, 2)
    assert alt.title == "Pilot"


def test_movie_year_and_misc_title():
    movie = parse_video("Inception (2010).mkv", "movies")
    assert movie.title == "Inception"
    assert movie.year == 2010
    dotted = parse_video("Movie.Name.2014.1080p.mkv", "movies")
    assert dotted.title == "Movie Name"
    assert dotted.year == 2014
    kept = parse_video("Blade Runner 2049.mkv", "movies")
    assert kept.year is None
    assert "2049" in kept.title
    misc = parse_video("Home Video.mp4", "misc")
    assert misc.title == "Home Video"
    assert misc.season is None


def test_paths_stay_inside_the_library(tmp_path):
    root = tmp_path / "media"
    library = root / "TV"
    library.mkdir(parents=True)
    inside = library / "clip.mp4"
    inside.write_bytes(b"video")
    assert file_inside(library, "clip.mp4") == inside.resolve()
    with pytest.raises(PathEscape):
        file_inside(library, "../secret.mp4")
    with pytest.raises(PathEscape):
        file_inside(library, "/etc/passwd")
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"nope")
    (library / "linked.mp4").symlink_to(outside)
    with pytest.raises(PathEscape):
        file_inside(library, "linked.mp4")


def test_suggestions_match_common_folder_names(tmp_path):
    (tmp_path / "TV Shows").mkdir()
    (tmp_path / "movies").mkdir()
    (tmp_path / "Misc").mkdir()
    (tmp_path / "Photos").mkdir()
    found = suggest_libraries(tmp_path, set())
    assert {(item["path"], item["kind"]) for item in found} == {
        ("TV Shows", "tv"),
        ("movies", "movies"),
        ("Misc", "misc"),
    }
    again = suggest_libraries(tmp_path, {"movies"})
    assert "movies" not in {item["path"] for item in again}


def test_playback_mode_and_ffmpeg_command():
    assert choose_mode("mov,mp4,m4a", "h264", ["aac"]) == "direct"
    assert choose_mode("matroska,webm", "vp9", ["opus"]) == "direct"
    assert choose_mode("matroska", "h264", ["aac"]) == "remux"
    assert choose_mode("matroska", "h264", ["ac3"]) == "audio"
    assert choose_mode("matroska", "hevc", ["aac"]) == "transcode"
    vaapi = hls_command(
        ffmpeg="ffmpeg", source="/media/TV/show.mkv", playlist="/tmp/index.m3u8",
        segment_pattern="/tmp/seg%05d.ts", mode="transcode", start=12, audio_index=1,
        burn_subtitle=None, vaapi_device="/dev/dri/renderD128",
    )
    assert "-hwaccel" in vaapi and "vaapi" in vaapi
    assert "h264_vaapi" in vaapi
    assert vaapi[vaapi.index("-ss") + 1] == "12.000"
    cpu = hls_command(
        ffmpeg="ffmpeg", source="/media/TV/show.mkv", playlist="/tmp/index.m3u8",
        segment_pattern="/tmp/seg%05d.ts", mode="transcode", start=0, audio_index=0,
        burn_subtitle=None, vaapi_device=None,
    )
    assert "libx264" in cpu
    assert "h264_vaapi" not in cpu
    burned = hls_command(
        ffmpeg="ffmpeg", source="/media/TV/show.mkv", playlist="/tmp/index.m3u8",
        segment_pattern="/tmp/seg%05d.ts", mode="transcode", start=0, audio_index=0,
        burn_subtitle=2, vaapi_device="/dev/dri/renderD128",
    )
    assert "libx264" in burned
    assert "-hwaccel" not in burned
    missing = detect_accel(Path("/tmp/does-not-exist-dri"))
    assert missing["available"] is False
    assert "/dev/dri is not mounted" in missing["reason"]


def test_scan_is_incremental_and_uses_local_poster(tmp_path):
    root = tmp_path / "media"
    folder = root / "TV" / "Example" / "Season 1"
    folder.mkdir(parents=True)
    (folder / "Example - S01E02 - Pilot.mkv").write_bytes(b"video")
    (folder / "folder.jpg").write_bytes(b"poster")
    init_db(str(tmp_path / "db.sqlite"))
    conn = connect(str(tmp_path / "db.sqlite"))
    library_id = conn.execute(
        "INSERT INTO media_libraries (name, rel_path, kind, created_at) VALUES ('TV', 'TV', 'tv', '2020-01-01T00:00:00Z')"
    ).lastrowid
    conn.commit()
    calls = []

    def probe(path):
        calls.append(path.name)
        return ProbeResult(20, "matroska", "h264", 320, 240, [{"index": 1, "codec": "aac", "language": "eng", "title": None, "channels": 2}], [])

    scan_library(conn, library_id, root, tmp_path / "data", probe=probe, thumbs=False)
    scan_library(conn, library_id, root, tmp_path / "data", probe=probe, thumbs=False)
    assert calls == ["Example - S01E02 - Pilot.mkv"]
    show = conn.execute("SELECT title, poster_rel FROM media_items WHERE kind = 'show'").fetchone()
    episode = conn.execute("SELECT title, season_number, episode_number FROM media_items WHERE kind = 'episode'").fetchone()
    assert show["title"] == "Example"
    assert show["poster_rel"] == "Example/Season 1/folder.jpg"
    assert (episode["title"], episode["season_number"], episode["episode_number"]) == ("Pilot", 1, 2)
    tracks = json.loads(conn.execute("SELECT audio_tracks FROM media_files").fetchone()["audio_tracks"])
    assert tracks[0]["codec"] == "aac"
    conn.close()


def test_library_api_rejects_paths_outside_media(client, monkeypatch, tmp_path):
    media = tmp_path / "media"
    (media / "TV").mkdir(parents=True)
    monkeypatch.setattr("app.media_paths.MEDIA_ROOT", media)
    monkeypatch.setattr("app.media_api.start_scan", lambda library_id: None)
    created = client.post("/api/media/libraries", json={"name": "TV", "path": "TV", "kind": "tv"})
    assert created.status_code == 201, created.text
    escaped = client.post("/api/media/libraries", json={"name": "Bad", "path": "../secret", "kind": "tv"})
    assert escaped.status_code == 400
    missing = client.post("/api/media/libraries", json={"name": "Gone", "path": "Nope", "kind": "movies"})
    assert missing.status_code == 400
    suggestions = client.get("/api/media/suggestions")
    assert suggestions.status_code == 200
    assert suggestions.json()["suggestions"] == []
    saved = client.put("/api/media/settings", json={"tmdb_api_key": "secret-key"})
    assert saved.json()["tmdb_configured"] is True
    listed = client.get("/api/media/settings")
    assert listed.json() == {"tmdb_configured": True}
    assert "secret-key" not in listed.text
