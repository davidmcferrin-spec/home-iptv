from urllib.parse import urlsplit


def is_loopback(host: str | None) -> bool:
    if not host:
        return True
    name = host.strip("[]").lower()
    return name in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or name.startswith("127.")


def request_hostname(headers_host: str | None) -> str:
    raw = (headers_host or "").strip()
    if raw.startswith("["):
        end = raw.find("]")
        return raw[1:end] if end > 1 else ""
    return raw.split(":", 1)[0]


def playback_base(configured: str, hostname: str, port: int, scheme: str = "http") -> str:
    parsed = urlsplit(configured if "://" in (configured or "") else f"http://{configured or '127.0.0.1'}")
    if hostname and is_loopback(parsed.hostname) and not is_loopback(hostname):
        return f"{scheme}://{hostname}:{port}"
    return (configured or "").rstrip("/")


def rewrite_playback(info: dict, hostname: str, webrtc_base: str, hls_base: str, scheme: str = "http") -> dict:
    updated = dict(info)
    path = info.get("path") or ""
    webrtc = playback_base(webrtc_base, hostname, 8889, scheme)
    hls = playback_base(hls_base, hostname, 8888, scheme)
    if path:
        updated["whep_url"] = f"{webrtc}/{path}/whep"
        updated["llhls_url"] = f"{hls}/{path}/index.m3u8"
    return updated
