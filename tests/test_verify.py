import subprocess
from datetime import datetime, timedelta, timezone

from app.catalog import enqueue_stale, upsert_parsed_channel
from app.db import connect, init_db, stamp
from app.playlist import ParsedChannel
from app.verify import probe_url
from app.worker import process_queue


def test_ffprobe_reads_generated_source(tmp_path):
    video = tmp_path / "bars.mp4"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    result = probe_url(str(video), timeout_seconds=20)
    assert result.status == "online"
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.width == 320
    assert result.height == 240


def test_worker_marks_unreachable_stream_offline(tmp_path):
    database = tmp_path / "t.sqlite"
    init_db(str(database))
    conn = connect(str(database))
    try:
        upsert_parsed_channel(
            conn,
            ParsedChannel(url="http://127.0.0.1:9/missing.ts", title="Missing"),
            None,
            None,
        )
        conn.commit()
    finally:
        conn.close()
    assert process_queue(str(database), concurrency=1, timeout_seconds=3) == 1
    conn = connect(str(database))
    try:
        row = conn.execute("SELECT status, last_checked_at FROM channels").fetchone()
        assert row["status"] == "offline"
        assert row["last_checked_at"]
        added = enqueue_stale(conn, 360, now=datetime.now(timezone.utc))
        assert added == 0
        added_later = enqueue_stale(
            conn,
            360,
            now=datetime.now(timezone.utc) + timedelta(hours=7),
        )
        assert added_later == 1
        assert stamp(datetime.now(timezone.utc)).endswith("Z")
    finally:
        conn.close()
