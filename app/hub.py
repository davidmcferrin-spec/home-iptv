import logging
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.catalog import save_probe
from app.config import Settings
from app.db import connect, setting
from app.hdhomerun import tuner_slot_open
from app.verify import probe_url

log = logging.getLogger(__name__)

WEBRTC_VIDEO = {"h264", "vp8", "vp9", "av1"}
WEBRTC_AUDIO = {"opus", "pcmu", "pcma", "g722"}
HLS_AUDIO = {"aac", "mp3", "ac3", "eac3"}
PLAYBACK_MODES = {"auto", "webrtc", "hls", "transcode"}


class TunersBusy(Exception):
    pass


class StreamOffline(Exception):
    pass


class StreamStartError(Exception):
    pass


class MediaMTXError(Exception):
    pass


def transcode_plan(video_codec: str | None, audio_codec: str | None) -> str:
    video = (video_codec or "").lower()
    audio = (audio_codec or "").lower()
    video_ok = video in WEBRTC_VIDEO
    audio_ok = audio in WEBRTC_AUDIO or audio == ""
    if video_ok and audio_ok:
        return "copy"
    if video_ok:
        return "audio"
    if audio_ok and video == "":
        return "both"
    if audio_ok:
        return "video"
    return "both"


def webrtc_safe(video: str | None, profile: str | None, has_b_frames: int | None) -> bool:
    name = (video or "").lower()
    if name not in WEBRTC_VIDEO:
        return False
    if name != "h264":
        return True
    if has_b_frames is None or int(has_b_frames) > 0:
        return False
    if (profile or "").lower().startswith("high"):
        return False
    return True


def delivery_for(channel: dict) -> tuple[str, str]:
    mode = (channel.get("playback_mode") or "auto").lower()
    if mode not in PLAYBACK_MODES:
        mode = "auto"
    video = (channel.get("video_codec") or "").lower()
    audio = (channel.get("audio_codec") or "").lower()
    safe = webrtc_safe(video, channel.get("video_profile"), channel.get("has_b_frames"))
    hls_audio = audio in HLS_AUDIO or audio == ""
    if mode == "hls" or (mode == "auto" and not safe):
        if video == "h264":
            return ("copy" if hls_audio else "hls-audio", "hls")
        return ("video" if hls_audio else "both", "hls")
    if mode == "transcode" or not safe:
        audio_ok = audio in WEBRTC_AUDIO or audio == ""
        return ("video" if audio_ok else "both", "webrtc")
    return transcode_plan(video, audio), "webrtc"


def ffmpeg_command(source_url: str, publish_url: str, plan: str) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rw_timeout",
        "15000000",
        "-fflags",
        "+genpts+discardcorrupt+nobuffer",
        "-flags",
        "low_delay",
        "-i",
        source_url,
    ]
    video_encode = [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "baseline",
        "-bf",
        "0",
        "-g",
        "60",
        "-keyint_min",
        "60",
    ]
    if plan == "copy":
        command += ["-c", "copy"]
    elif plan == "audio":
        command += ["-c:v", "copy", "-c:a", "libopus", "-ar", "48000", "-ac", "2", "-b:a", "96k"]
    elif plan == "hls-audio":
        command += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    elif plan == "video":
        command += [*video_encode, "-c:a", "copy"]
    else:
        command += [*video_encode, "-c:a", "libopus", "-ar", "48000", "-ac", "2", "-b:a", "96k"]
    command += ["-muxdelay", "0", "-muxpreload", "0", "-f", "rtsp", "-rtsp_transport", "tcp", publish_url]
    return command


class MediaMTXClient:
    def __init__(self, api_base: str) -> None:
        self.api_base = api_base.rstrip("/")

    def get_path(self, name: str) -> dict | None:
        try:
            response = httpx.get(f"{self.api_base}/v3/paths/get/{name}", timeout=3)
        except httpx.HTTPError as exc:
            raise MediaMTXError(f"MediaMTX path request failed: {exc}") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise MediaMTXError(f"MediaMTX path lookup returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise MediaMTXError("MediaMTX path lookup was not JSON") from exc

    def ensure_path(self, name: str) -> None:
        try:
            response = httpx.post(
                f"{self.api_base}/v3/config/paths/add/{name}",
                json={"source": "publisher"},
                timeout=5,
            )
        except httpx.HTTPError as exc:
            raise MediaMTXError(f"MediaMTX path create failed: {exc}") from exc
        if response.status_code == 200:
            return
        if response.status_code == 400 and "already exists" in response.text:
            return
        raise MediaMTXError(f"MediaMTX path create returned HTTP {response.status_code}")

    def patch_global(self, payload: dict) -> None:
        try:
            response = httpx.patch(f"{self.api_base}/v3/config/global/patch", json=payload, timeout=5)
        except httpx.HTTPError as exc:
            raise MediaMTXError(f"MediaMTX config request failed: {exc}") from exc
        if response.status_code not in {200, 204}:
            raise MediaMTXError(f"MediaMTX config update returned HTTP {response.status_code}")


@dataclass
class ActiveStream:
    channel_id: int
    device_id: int | None
    path: str
    plan: str
    process: subprocess.Popen
    log_file: object
    started: float
    last_reader: float
    origins: set[str] = field(default_factory=set)
    delivery: str = "webrtc"


class Hub:
    def __init__(
        self,
        cfg: Settings,
        mediamtx: MediaMTXClient,
        popen=subprocess.Popen,
        ready_timeout: float = 15,
        poll_interval: float = 0.25,
    ) -> None:
        self.cfg = cfg
        self.mediamtx = mediamtx
        self.popen = popen
        self.ready_timeout = ready_timeout
        self.poll_interval = poll_interval
        self._lock = threading.Lock()
        self._active: dict[int, ActiveStream] = {}

    def advertise_host(self, host: str | None = None) -> None:
        chosen = (host or self.cfg.webrtc_additional_host or "").strip()
        if not chosen:
            return
        self.mediamtx.patch_global({"webrtcAdditionalHosts": [chosen]})

    def idle_seconds(self) -> int:
        conn = connect(self.cfg.database_path)
        try:
            raw = setting(conn, "idle_timeout_seconds") or ""
        finally:
            conn.close()
        if raw:
            try:
                return max(5, int(raw))
            except ValueError:
                log.warning("idle_timeout_seconds is not an integer: %s", raw)
        return self.cfg.idle_timeout_seconds

    def virtual_tuner_count(self) -> int:
        conn = connect(self.cfg.database_path)
        try:
            raw = setting(conn, "emulated_tuner_count") or "4"
        finally:
            conn.close()
        try:
            return max(1, int(raw))
        except ValueError:
            return 4

    def ensure(self, channel: dict, device: dict | None, origin: str) -> dict:
        channel_id = int(channel["id"])
        self._prepare_codecs(channel)
        with self._lock:
            self._drop_dead_locked()
            current = self._active.get(channel_id)
            reused = False
            if current is not None and current.process.poll() is None:
                current.last_reader = time.monotonic()
                current.origins.add(origin)
                stream = current
                reused = True
                log.info("upstream pull reused for channel %s", channel_id)
            else:
                self._reserve_locked(channel, device, origin)
                stream = self._spawn_locked(channel, device, origin)
        try:
            self._wait_ready(stream)
        except StreamStartError:
            if not reused:
                self._stop_stream(channel_id)
            raise
        log.info("upstream pull started for channel %s plan %s", channel_id, stream.plan)
        return self._describe(stream)

    def status(self, channel_id: int) -> dict:
        with self._lock:
            stream = self._active.get(channel_id)
            running = stream is not None and stream.process.poll() is None
            plan = stream.plan if running else None
            path_name = stream.path if stream else f"ch{channel_id}"
        readers: list = []
        received = 0
        error = None
        if running:
            try:
                path = self.mediamtx.get_path(path_name)
            except MediaMTXError as exc:
                error = str(exc)
                path = None
            if path:
                readers = list(path.get("readers") or [])
                received = path.get("bytesReceived") or 0
        return {
            "channel_id": channel_id,
            "upstream_pulls": 1 if running else 0,
            "reader_count": len(readers),
            "readers": readers,
            "bytes_received": received,
            "transcode": plan,
            "mediamtx_error": error,
        }

    def rtsp_url(self, channel_id: int) -> str:
        return f"{self.cfg.mediamtx_rtsp}/ch{channel_id}"

    def reap(self) -> None:
        idle = self.idle_seconds()
        now = time.monotonic()
        with self._lock:
            items = list(self._active.items())
        for channel_id, stream in items:
            if stream.process.poll() is not None:
                self._stop_stream(channel_id)
                continue
            try:
                path = self.mediamtx.get_path(stream.path)
            except MediaMTXError as exc:
                log.warning("MediaMTX path check failed for channel %s: %s", channel_id, exc)
                continue
            if path and path.get("readers"):
                stream.last_reader = now
            elif now - stream.last_reader >= idle:
                log.info("stopping idle channel %s", channel_id)
                self._stop_stream(channel_id)

    def reap_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.reap()
            except OSError as exc:
                log.warning("idle reap failed: %s", exc)
            stop.wait(2)

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._active)
        for channel_id in ids:
            self._stop_stream(channel_id)

    def _prepare_codecs(self, channel: dict) -> None:
        known = channel.get("video_codec") or channel.get("audio_codec")
        needs_frames = (channel.get("video_codec") or "").lower() == "h264" and channel.get("has_b_frames") is None
        if known and not needs_frames:
            if channel.get("status") == "offline":
                raise StreamOffline(channel.get("status_detail") or "stream is offline")
            return
        result = probe_url(channel["url"], self.cfg.verify_timeout_seconds)
        conn = connect(self.cfg.database_path)
        try:
            save_probe(conn, int(channel["id"]), result)
            conn.commit()
        finally:
            conn.close()
        if result.status != "online":
            raise StreamOffline(result.detail or "stream is offline")
        channel["video_codec"] = result.video_codec
        channel["audio_codec"] = result.audio_codec
        channel["video_profile"] = result.video_profile
        channel["has_b_frames"] = result.has_b_frames
        channel["status"] = "online"

    def _reserve_locked(self, channel: dict, device: dict | None, origin: str) -> None:
        channel_id = int(channel["id"])
        if device is not None:
            active = {
                item.channel_id
                for item in self._active.values()
                if item.device_id == device["id"] and item.process.poll() is None
            }
            if not tuner_slot_open(int(device["tuner_count"]), active, channel_id):
                raise TunersBusy()
        if origin == "emulation":
            active_ids = {item.channel_id for item in self._active.values() if item.process.poll() is None}
            if not tuner_slot_open(self.virtual_tuner_count(), active_ids, channel_id):
                raise TunersBusy()

    def _spawn_locked(self, channel: dict, device: dict | None, origin: str) -> ActiveStream:
        channel_id = int(channel["id"])
        path = f"ch{channel_id}"
        plan, delivery = delivery_for(channel)
        try:
            self.mediamtx.ensure_path(path)
        except MediaMTXError as exc:
            raise StreamStartError(str(exc)) from exc
        publish = self.rtsp_url(channel_id)
        command = ffmpeg_command(channel["url"], publish, plan)
        log_dir = Path(self.cfg.data_dir) / "streams"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / f"{path}.log", "wb")
        try:
            process = self.popen(command, stdout=subprocess.DEVNULL, stderr=log_file)
        except OSError as exc:
            log_file.close()
            raise StreamStartError(f"ffmpeg could not start: {exc.strerror or exc}") from exc
        stream = ActiveStream(
            channel_id=channel_id,
            device_id=None if device is None else int(device["id"]),
            path=path,
            plan=plan,
            process=process,
            log_file=log_file,
            started=time.monotonic(),
            last_reader=time.monotonic(),
            origins={origin},
            delivery=delivery,
        )
        self._active[channel_id] = stream
        return stream

    def _wait_ready(self, stream: ActiveStream) -> None:
        deadline = time.monotonic() + self.ready_timeout
        while True:
            if stream.process.poll() is not None:
                raise StreamStartError(self._log_tail(stream) or "ffmpeg exited before the path was ready")
            try:
                path = self.mediamtx.get_path(stream.path)
            except MediaMTXError as exc:
                raise StreamStartError(str(exc)) from exc
            if _published(path):
                return
            if time.monotonic() >= deadline:
                tail = self._log_tail(stream)
                detail = "MediaMTX path did not become ready"
                if tail:
                    detail = f"{detail}: {tail}"
                raise StreamStartError(detail)
            time.sleep(self.poll_interval)

    def _describe(self, stream: ActiveStream) -> dict:
        return {
            "channel_id": stream.channel_id,
            "path": stream.path,
            "whep_url": f"{self.cfg.public_webrtc_base}/{stream.path}/whep",
            "llhls_url": f"{self.cfg.public_hls_base}/{stream.path}/index.m3u8",
            "transcode": stream.plan,
            "playback": stream.delivery,
            "idle_timeout_seconds": self.idle_seconds(),
            "upstream_pulls": 1,
        }

    def _drop_dead_locked(self) -> None:
        dead = [channel_id for channel_id, stream in self._active.items() if stream.process.poll() is not None]
        for channel_id in dead:
            self._close_stream_locked(channel_id)

    def _stop_stream(self, channel_id: int) -> None:
        with self._lock:
            stream = self._active.get(channel_id)
            if stream is None:
                return
            process = stream.process
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        with self._lock:
            self._close_stream_locked(channel_id)

    def _close_stream_locked(self, channel_id: int) -> None:
        stream = self._active.pop(channel_id, None)
        if stream is None:
            return
        log_file = stream.log_file
        if log_file is not None and not log_file.closed:
            log_file.close()

    def _log_tail(self, stream: ActiveStream) -> str:
        log_file = stream.log_file
        if log_file is not None and not log_file.closed:
            log_file.flush()
        path = Path(self.cfg.data_dir) / "streams" / f"{stream.path}.log"
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        return data.decode("utf-8", errors="replace")[-400:].strip()


def _published(path: dict | None) -> bool:
    if not path or not path.get("ready"):
        return False
    if path.get("tracks"):
        return True
    return (path.get("bytesReceived") or 0) > 0
