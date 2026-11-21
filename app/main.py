import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import ensure_dirs, settings
from app.db import init_db
from app.emulation import router as emulation_router
from app.emulation import start_discovery
from app.hub import Hub, MediaMTXClient, MediaMTXError
from app.icons import apple_touch_png, favicon_ico, favicon_svg
from app.mediamtx_config import write_config
from app.worker import worker_loop

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


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
            try:
                hub.advertise_host()
            except MediaMTXError as exc:
                log.info("MediaMTX is not ready for WebRTC host configuration: %s", exc)
            except httpx.HTTPError as exc:
                log.info("MediaMTX is not ready for WebRTC host configuration: %s", exc)
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
    app.include_router(emulation_router)

    @app.get("/channels")
    def channels_page():
        page = WEB_DIR / "channels.html"
        if not page.is_file():
            raise HTTPException(404, "channel manager is not installed")
        return FileResponse(page)

    @app.get("/")
    def home():
        index = WEB_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(404, "guide is not installed")
        return FileResponse(index)

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
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = settings()
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=False)


if __name__ == "__main__":
    main()
