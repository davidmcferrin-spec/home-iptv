import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import ensure_dirs, settings
from app.db import init_db
from app.worker import worker_loop


def create_app() -> FastAPI:
    cfg = settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ensure_dirs(cfg)
        init_db(cfg.database_path)
        stop = threading.Event()
        thread = None
        if cfg.embed_worker:
            thread = threading.Thread(target=worker_loop, args=(stop, cfg), name="verify-worker", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread is not None:
            thread.join(timeout=5)

    app = FastAPI(title="Home IPTV", lifespan=lifespan)
    app.state.cfg = cfg
    app.include_router(router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = settings()
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=False)


if __name__ == "__main__":
    main()
