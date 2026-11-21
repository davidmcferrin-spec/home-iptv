import json
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeResult:
    status: str
    detail: str | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    audio_language: str | None
    bitrate: int | None
    video_profile: str | None = None
    has_b_frames: int | None = None


def probe_url(url: str, timeout_seconds: int) -> ProbeResult:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-hide_banner",
        "-show_streams",
        "-show_format",
        "-print_format",
        "json",
        "-rw_timeout",
        str(timeout_seconds * 1_000_000),
        "-user_agent",
        "HomeIPTV/0.1",
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout_seconds + 5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult("offline", "ffprobe timed out", None, None, None, None, None, None)
    except OSError as exc:
        return ProbeResult("offline", f"ffprobe could not start: {exc.strerror}", None, None, None, None, None, None)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        return ProbeResult("offline", detail[:400] or "ffprobe failed", None, None, None, None, None, None)
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return ProbeResult("offline", "ffprobe returned invalid JSON", None, None, None, None, None, None)
    return _from_payload(payload)


def _b_frames(video: dict | None) -> int | None:
    if not video or video.get("has_b_frames") is None:
        return None
    try:
        return int(video["has_b_frames"])
    except (TypeError, ValueError):
        return None


def _from_payload(payload: dict) -> ProbeResult:
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        return ProbeResult("offline", "no audio or video stream", None, None, None, None, None, None)
    tags = (audio or {}).get("tags") or {}
    language = tags.get("language") or tags.get("LANGUAGE")
    bitrate = None
    fmt = payload.get("format") or {}
    raw_bitrate = fmt.get("bit_rate")
    if raw_bitrate not in (None, "N/A"):
        try:
            bitrate = int(raw_bitrate)
        except (TypeError, ValueError):
            bitrate = None
    width = video.get("width") if video else None
    height = video.get("height") if video else None
    return ProbeResult(
        status="online",
        detail=None,
        width=int(width) if isinstance(width, int) else None,
        height=int(height) if isinstance(height, int) else None,
        video_codec=(video.get("codec_name") if video else None),
        audio_codec=(audio.get("codec_name") if audio else None),
        audio_language=language,
        bitrate=bitrate,
        video_profile=(video.get("profile") if video else None) or None,
        has_b_frames=_b_frames(video),
    )
