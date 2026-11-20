"""Endless local MPEG-TS test patterns. Each HTTP client is one upstream connection."""

import argparse
import logging
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("test-sources")
COUNTS: dict[str, int] = {}
LOCK = threading.Lock()

PATTERNS = {
    "/bars.ts": ("640x360", "440"),
    "/tone.ts": ("640x360", "880"),
    "/adult.ts": ("320x240", "220"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        pattern = PATTERNS.get(self.path)
        if pattern is None:
            self.send_error(404)
            return
        size, frequency = pattern
        with LOCK:
            COUNTS[self.path] = COUNTS.get(self.path, 0) + 1
            count = COUNTS[self.path]
        log.info("upstream connection %s count %s", self.path, count)
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "30",
            "-c:a",
            "aac",
            "-f",
            "mpegts",
            "pipe:1",
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert process.stdout is not None
        try:
            while True:
                chunk = process.stdout.read(188 * 32)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            process.kill()
            process.wait(timeout=3)

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log.info("test sources on http://%s:%s", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
