import time

import pytest

from app.config import settings
from app.db import init_db
from app.hub import Hub, TunersBusy, delivery_for, ffmpeg_command, transcode_plan


class FakeProc:
    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.returncode = 1

    def wait(self, timeout=None):
        self.returncode = 1 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self):
        self.returncode = -9


class Recorder:
    def __init__(self) -> None:
        self.commands = []
        self.procs = []

    def __call__(self, command, **kwargs):
        assert isinstance(command, list)
        self.commands.append(command)
        proc = FakeProc()
        self.procs.append(proc)
        return proc


class FakeMTX:
    def __init__(self) -> None:
        self.readers = []

    def get_path(self, name):
        return {"name": name, "ready": True, "readers": list(self.readers), "bytesReceived": 50}

    def ensure_path(self, name):
        self.ensured = name

    def patch_global(self, payload):
        self.payload = payload


def _hub(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_WEBRTC_BASE", "http://lan:8889")
    monkeypatch.setenv("PUBLIC_HLS_BASE", "http://lan:8888")
    monkeypatch.setenv("MEDIAMTX_RTSP", "rtsp://mediamtx:8554")
    monkeypatch.setenv("IDLE_TIMEOUT_SECONDS", "5")
    init_db(str(tmp_path / "t.sqlite"))
    mtx = FakeMTX()
    recorder = Recorder()
    hub = Hub(settings(), mtx, popen=recorder, poll_interval=0)
    return hub, recorder, mtx


def _channel(channel_id, codec_video="h264", codec_audio="aac", device_id=None, has_b_frames=0, profile="Baseline"):
    return {
        "id": channel_id,
        "url": f"http://example.test/{channel_id}.ts",
        "video_codec": codec_video,
        "audio_codec": codec_audio,
        "video_profile": profile,
        "has_b_frames": has_b_frames,
        "playback_mode": "auto",
        "status": "online",
        "status_detail": None,
        "device_id": device_id,
    }


def test_transcode_plan_copies_webrtc_codecs_and_converts_the_rest():
    assert transcode_plan("h264", "opus") == "copy"
    assert transcode_plan("h264", "aac") == "audio"
    assert transcode_plan("hevc", "opus") == "video"
    assert transcode_plan("hevc", "aac") == "both"
    audio = ffmpeg_command("http://example.test/a.ts", "rtsp://mediamtx:8554/ch1", "audio")
    assert audio[audio.index("-c:v") + 1] == "copy"
    assert "libopus" in audio
    both = ffmpeg_command("http://example.test/a.ts", "rtsp://mediamtx:8554/ch1", "both")
    assert "libx264" in both
    assert "veryfast" in both
    assert both[both.index("-bf") + 1] == "0"
    assert "libopus" in both
    bframes = _channel(9, has_b_frames=2, profile="High")
    plan, delivery = delivery_for(bframes)
    assert plan == "copy"
    assert delivery == "hls"
    forced = dict(bframes)
    forced["playback_mode"] = "transcode"
    plan, delivery = delivery_for(forced)
    assert plan == "both"
    assert delivery == "webrtc"


def test_second_viewer_does_not_open_a_second_pull(tmp_path, monkeypatch):
    hub, recorder, mtx = _hub(tmp_path, monkeypatch)
    channel = _channel(1)
    first = hub.ensure(channel, None, "watch")
    second = hub.ensure(channel, None, "watch")
    assert len(recorder.commands) == 1
    assert first["upstream_pulls"] == 1
    assert second["upstream_pulls"] == 1
    assert second["whep_url"] == "http://lan:8889/ch1/whep"
    assert second["llhls_url"] == "http://lan:8888/ch1/index.m3u8"
    assert second["transcode"] == "audio"
    mtx.readers = [{"type": "webRTCSession", "id": "a"}, {"type": "webRTCSession", "id": "b"}]
    status = hub.status(1)
    assert status["upstream_pulls"] == 1
    assert status["reader_count"] == 2


def test_hdhomerun_tuner_limit_is_per_channel(tmp_path, monkeypatch):
    hub, recorder, _mtx = _hub(tmp_path, monkeypatch)
    device = {"id": 7, "tuner_count": 1}
    hub.ensure(_channel(1, device_id=7), device, "watch")
    hub.ensure(_channel(1, device_id=7), device, "watch")
    with pytest.raises(TunersBusy):
        hub.ensure(_channel(2, device_id=7), device, "watch")
    assert len(recorder.commands) == 1


def test_idle_stream_stops_when_nobody_is_reading(tmp_path, monkeypatch):
    hub, _recorder, mtx = _hub(tmp_path, monkeypatch)
    hub.ensure(_channel(1), None, "watch")
    hub._active[1].last_reader = time.monotonic() - 30
    mtx.readers = [{"type": "webRTCSession", "id": "a"}]
    hub.reap()
    assert hub.status(1)["upstream_pulls"] == 1
    mtx.readers = []
    hub._active[1].last_reader = time.monotonic() - 30
    hub.reap()
    assert hub.status(1)["upstream_pulls"] == 0
