import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: str
    data_dir: str
    host: str
    port: int
    embed_worker: bool
    embed_hub: bool
    embed_discovery: bool
    verify_concurrency: int
    verify_timeout_seconds: int
    verify_interval_minutes: int
    idle_timeout_seconds: int
    mediamtx_api: str
    mediamtx_rtsp: str
    public_http_base: str
    public_webrtc_base: str
    public_hls_base: str
    webrtc_additional_host: str
    discovery_timeout_seconds: float


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def settings() -> Settings:
    data_dir = os.environ.get("DATA_DIR", "/data")
    database_path = os.environ.get("DATABASE_PATH", "/config/home-iptv.sqlite")
    return Settings(
        database_path=database_path,
        data_dir=data_dir,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=_int("PORT", 9595),
        embed_worker=_bool("EMBED_WORKER", True),
        embed_hub=_bool("EMBED_HUB", True),
        embed_discovery=_bool("EMBED_DISCOVERY", True),
        verify_concurrency=max(1, _int("VERIFY_CONCURRENCY", 4)),
        verify_timeout_seconds=max(3, _int("VERIFY_TIMEOUT_SECONDS", 20)),
        verify_interval_minutes=max(5, _int("VERIFY_INTERVAL_MINUTES", 360)),
        idle_timeout_seconds=max(5, _int("IDLE_TIMEOUT_SECONDS", 60)),
        mediamtx_api=os.environ.get("MEDIAMTX_API", "http://127.0.0.1:9997").rstrip("/"),
        mediamtx_rtsp=os.environ.get("MEDIAMTX_RTSP", "rtsp://127.0.0.1:8554").rstrip("/"),
        public_http_base=os.environ.get("PUBLIC_HTTP_BASE", "http://127.0.0.1:9595").rstrip("/"),
        public_webrtc_base=os.environ.get("PUBLIC_WEBRTC_BASE", "http://127.0.0.1:8889").rstrip("/"),
        public_hls_base=os.environ.get("PUBLIC_HLS_BASE", "http://127.0.0.1:8888").rstrip("/"),
        webrtc_additional_host=os.environ.get("WEBRTC_ADDITIONAL_HOST", "").strip(),
        discovery_timeout_seconds=float(os.environ.get("DISCOVERY_TIMEOUT_SECONDS", "1.5")),
    )


def ensure_dirs(cfg: Settings) -> None:
    Path(cfg.database_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.data_dir, "playlists").mkdir(parents=True, exist_ok=True)
