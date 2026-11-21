import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import ensure_dirs, settings
from app.db import init_db
from app.emulation import router as emulation_router
from app.emulation import start_discovery
from app.hub import Hub, MediaMTXClient, MediaMTXError
from app.media_api import router as media_router
from app.icons import apple_touch_png, favicon_ico, favicon_svg
from app.mediamtx_config import write_config
from app.worker import worker_loop

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_ASSET_URL = re.compile(r"/static/[^\"']+")


class FreshStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.endswith((".css", ".js", ".html")):
            response.headers["Cache-Control"] = "no-cache"
        return response


def _asset_stamp() -> str:
    latest = 0
    for path in WEB_DIR.rglob("*"):
        if path.is_file() and path.suffix in {".css", ".js"}:
            latest = max(latest, int(path.stat().st_mtime))
    return str(latest)


def _page(path: Path) -> HTMLResponse:
    if not path.is_file():
        raise HTTPException(404, "page is not installed")
    stamp = _asset_stamp()
    html = _ASSET_URL.sub(lambda match: f"{match.group(0)}?v={stamp}", path.read_text(encoding="utf-8"))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


def _advertise_when_ready(stop: threading.Event, hub: Hub) -> None:
    deadline = time.monotonic() + 30
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            hub.mediamtx.ready()
        except MediaMTXError as exc:
            log.info("MediaMTX API is not ready: %s", exc)
            stop.wait(1)
            continue
        try:
            hub.advertise_host()
            hub.apply_playback_profile()
        except MediaMTXError as exc:
            log.info("MediaMTX rejected the WebRTC host: %s", exc)
            stop.wait(1)
            continue
        except httpx.HTTPError as exc:
            log.info("MediaMTX rejected the WebRTC host: %s", exc)
            stop.wait(1)
            continue
        return
    if not stop.is_set():
        log.warning("MediaMTX API did not answer before the app gave up waiting")


def create_app() -> FastAPI:
    cfg = settings()
    hub = Hub(cfg, MediaMTXClient(cfg.mediamtx_api))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ensure_dirs(cfg)
        init_db(cfg.database_path)
        try:
            write_config()
        except OSError as exc:
            log.warning("MediaMTX config was not written: %s", exc)
        stop = threading.Event()
        threads: list[threading.Thread] = []
        if cfg.embed_worker:
            threads.append(
                threading.Thread(target=worker_loop, args=(stop, cfg), name="verify-worker", daemon=True)
            )
        if cfg.embed_hub:
            threads.append(
                threading.Thread(target=_advertise_when_ready, args=(stop, hub), name="mediamtx-advertise", daemon=True)
            )
            threads.append(threading.Thread(target=hub.reap_loop, args=(stop,), name="stream-reaper", daemon=True))
        discovery = start_discovery(cfg, stop) if cfg.embed_discovery else None
        for thread in threads:
            thread.start()
        yield
        stop.set()
        hub.stop_all()
        if discovery is not None:
            discovery.close()
        for thread in threads:
            thread.join(timeout=5)

    app = FastAPI(title="Home IPTV", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.hub = hub
    app.include_router(router)
    app.include_router(media_router)
    app.include_router(emulation_router)

    @app.get("/library")
    def library_page():
        return _page(WEB_DIR / "library.html")

    @app.get("/channels")
    def channels_page():
        return _page(WEB_DIR / "channels.html")

    @app.get("/")
    def home():
        return _page(WEB_DIR / "index.html")

    @app.get("/favicon.svg")
    def favicon_svg_route():
        return Response(favicon_svg(), media_type="image/svg+xml")

    @app.get("/favicon.ico")
    def favicon_ico_route():
        return Response(favicon_ico(), media_type="image/x-icon")

    @app.get("/apple-touch-icon.png")
    def apple_touch_route():
        return Response(apple_touch_png(), media_type="image/png")

    if WEB_DIR.is_dir():
        app.mount("/static", FreshStaticFiles(directory=WEB_DIR), name="static")
    return app


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = settings()
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=False)


if __name__ == "__main__":
    main()
