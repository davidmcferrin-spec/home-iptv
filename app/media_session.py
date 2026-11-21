import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from app.media_play import MAX_TRANSCODES, hls_command

_lock = threading.Lock()
_sessions: dict[str, "Session"] = {}


class SessionBusy(Exception):
    pass


class SessionStartError(Exception):
    pass


class Session:
    def __init__(self, session_id: str, process: subprocess.Popen, directory: Path, file_start: float):
        self.id = session_id
        self.process = process
        self.directory = directory
        self.file_start = file_start
        self.last_used = time.monotonic()


def reap(idle_seconds: float = 45) -> None:
    now = time.monotonic()
    with _lock:
        stale = [session.id for session in _sessions.values() if now - session.last_used > idle_seconds]
    for session_id in stale:
        close_session(session_id)


def close_session(session_id: str) -> None:
    with _lock:
        session = _sessions.pop(session_id, None)
    if session is None:
        return
    if session.process.poll() is None:
        session.process.terminate()
        try:
            session.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            session.process.kill()
    shutil.rmtree(session.directory, ignore_errors=True)


def session_dir(session_id: str) -> Path | None:
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return None
        session.last_used = time.monotonic()
        return session.directory


def active_count() -> int:
    with _lock:
        return sum(1 for session in _sessions.values() if session.process.poll() is None)


def open_session(
    *,
    source: str,
    work_root: Path,
    mode: str,
    start: float,
    audio_index: int,
    burn_subtitle: int | None,
    vaapi_device: str | None,
    launch=None,
) -> Session:
    reap()
    if active_count() >= MAX_TRANSCODES:
        raise SessionBusy(f"{MAX_TRANSCODES} transcodes are already running")
    starter = launch or _popen
    session_id = uuid.uuid4().hex
    directory = work_root / session_id
    directory.mkdir(parents=True, exist_ok=True)
    device = vaapi_device
    command = _command(source, mode, start, audio_index, burn_subtitle, device)
    process = starter(command, directory)
    if device and not _wait_ready(process, directory):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        device = None
        command = _command(source, mode, start, audio_index, burn_subtitle, None)
        process = starter(command, directory)
        if not _wait_ready(process, directory):
            shutil.rmtree(directory, ignore_errors=True)
            detail = "ffmpeg did not write a playlist"
            raise SessionStartError(detail)
    elif not _wait_ready(process, directory):
        shutil.rmtree(directory, ignore_errors=True)
        raise SessionStartError("ffmpeg did not write a playlist")
    session = Session(session_id, process, directory, start)
    with _lock:
        _sessions[session_id] = session
    return session


def _command(source, mode, start, audio_index, burn_subtitle, device) -> list[str]:
    return hls_command(
        ffmpeg="ffmpeg",
        source=source,
        playlist="index.m3u8",
        segment_pattern="seg%05d.ts",
        mode=mode,
        start=start,
        audio_index=audio_index,
        burn_subtitle=burn_subtitle,
        vaapi_device=device,
    )


def _popen(command: list[str], directory: Path) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        cwd=directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _wait_ready(process: subprocess.Popen, directory: Path, timeout: float = 12) -> bool:
    deadline = time.monotonic() + timeout
    playlist = directory / "index.m3u8"
    while time.monotonic() < deadline:
        if playlist.is_file() and playlist.stat().st_size > 0:
            return True
        if process.poll() is not None:
            return playlist.is_file() and playlist.stat().st_size > 0
        time.sleep(0.1)
    return playlist.is_file() and playlist.stat().st_size > 0
