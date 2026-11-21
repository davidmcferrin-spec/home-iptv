import subprocess
from pathlib import Path

MAX_TRANSCODES = 2
_TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
_IMAGE_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "vobsub", "pgssub", "dvb_subtitle"}
_MP4_AUDIO = {"aac", "mp3", "mp4a"}
_WEBM_AUDIO = {"opus", "vorbis"}
_BROWSER_VIDEO = {"h264", "avc", "vp8", "vp9", "av1"}


def choose_mode(container: str | None, video_codec: str | None, audio_codecs: list[str]) -> str:
    names = {part.strip().lower() for part in (container or "").split(",") if part.strip()}
    video = (video_codec or "").lower()
    audio = [(codec or "").lower() for codec in audio_codecs if codec]
    if names & {"mp4", "m4v", "mov"} and video in {"h264", "avc"} and all(codec in _MP4_AUDIO for codec in audio):
        return "direct"
    if "webm" in names and video in {"vp8", "vp9", "av1"} and all(codec in _WEBM_AUDIO for codec in audio):
        return "direct"
    if video in _BROWSER_VIDEO and all(codec in _MP4_AUDIO or codec in _WEBM_AUDIO for codec in audio):
        return "remux"
    if video in _BROWSER_VIDEO:
        return "audio"
    return "transcode"


def text_subtitle(codec: str | None) -> bool:
    return (codec or "").lower() in _TEXT_SUBS


def image_subtitle(codec: str | None) -> bool:
    return (codec or "").lower() in _IMAGE_SUBS


def hls_command(
    *,
    ffmpeg: str,
    source: str,
    playlist: str,
    segment_pattern: str,
    mode: str,
    start: float,
    audio_index: int,
    burn_subtitle: int | None,
    vaapi_device: str | None,
) -> list[str]:
    # Image subs are burned with libx264. VAAPI overlay is unreliable for PGS and DVD tracks.
    use_vaapi = bool(vaapi_device) and mode == "transcode" and burn_subtitle is None
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if use_vaapi:
        cmd += ["-hwaccel", "vaapi", "-hwaccel_device", vaapi_device, "-hwaccel_output_format", "vaapi"]
    cmd += ["-i", source, "-map", "0:v:0", "-map", f"0:a:{audio_index}?"]
    if burn_subtitle is not None:
        cmd += ["-vf", f"subtitles={source}:si={burn_subtitle}"]
    if mode == "remux":
        cmd += ["-c", "copy"]
    elif mode == "audio":
        cmd += ["-c:v", "copy", "-c:a", "aac", "-ac", "2", "-b:a", "160k"]
    elif use_vaapi:
        cmd += ["-c:v", "h264_vaapi", "-b:v", "4M", "-maxrate", "6M", "-bufsize", "8M", "-c:a", "aac", "-ac", "2", "-b:a", "160k"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-b:a", "160k"]
    cmd += [
        "-f", "hls",
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-hls_playlist_type", "event",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", segment_pattern,
        playlist,
    ]
    return cmd


def vtt_command(ffmpeg: str, source: str, stream_index: int, dest: str) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", source, "-map", f"0:{stream_index}", "-f", "webvtt", dest,
    ]


def detect_accel(dri: Path | None = None, ffmpeg: str = "ffmpeg") -> dict:
    root = dri or Path("/dev/dri")
    if not root.is_dir():
        return {"available": False, "device": None, "reason": "/dev/dri is not mounted"}
    try:
        renders = sorted(path for path in root.iterdir() if path.name.startswith("renderD"))
    except OSError as exc:
        return {"available": False, "device": None, "reason": f"/dev/dri cannot be read: {exc.strerror or exc}"}
    if not renders:
        return {"available": False, "device": None, "reason": "/dev/dri has no render node"}
    device = str(renders[0])
    try:
        probe = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-init_hw_device", f"vaapi=va:{device}",
                "-f", "lavfi", "-i", "nullsrc=s=64x64",
                "-frames:v", "1", "-f", "null", "-",
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "device": device, "reason": f"VAAPI init failed: {exc}"}
    if probe.returncode != 0:
        detail = probe.stderr.decode(errors="replace").strip().splitlines()
        tail = detail[-1] if detail else f"ffmpeg exited {probe.returncode}"
        return {"available": False, "device": device, "reason": f"VAAPI init failed: {tail}"}
    return {"available": True, "device": device, "reason": "Intel Quick Sync via VAAPI"}
