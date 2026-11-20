import logging
import threading
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api import router
from app.config import ensure_dirs, settings
from app.db import init_db
from app.emulation import router as emulation_router
from app.emulation import start_discovery
from app.hub import Hub, MediaMTXClient, MediaMTXError
from app.worker import worker_loop

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    cfg = settings()
    hub = Hub(cfg, MediaMTXClient(cfg.mediamtx_api))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ensure_dirs(cfg)
        init_db(cfg.database_path)
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
    return app


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = settings()
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=False)


if __name__ == "__main__":
    main()
