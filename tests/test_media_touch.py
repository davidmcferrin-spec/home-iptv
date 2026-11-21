import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def library_url(tmp_path_factory):
    if not Path("/media/Movies/Demo (2024).mp4").is_file():
        pytest.skip("sample media is not on /media")
    root = tmp_path_factory.mktemp("library-touch")
    port = _free_port()
    env = dict(**__import__("os").environ)
    env.update({
        "DATABASE_PATH": str(root / "home-iptv.sqlite"),
        "DATA_DIR": str(root / "data"),
        "EMBED_WORKER": "0",
        "EMBED_HUB": "0",
        "EMBED_DISCOVERY": "0",
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/api/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        process.kill()
        raise RuntimeError("library server did not start")
    for name, path, kind in (("TV", "TV", "tv"), ("Movies", "Movies", "movies")):
        created = httpx.post(f"{base}/api/media/libraries", json={"name": name, "path": path, "kind": kind}, timeout=10)
        assert created.status_code == 201, created.text
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        libraries = httpx.get(f"{base}/api/media/libraries", timeout=5).json()["libraries"]
        if libraries and all(row["scan"]["state"] == "idle" and row["scan"]["total"] for row in libraries):
            break
        time.sleep(0.3)
    else:
        process.kill()
        raise RuntimeError("library scan did not finish")
    yield base
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _swipe(page, x, y, dy):
    session = page.context.new_cdp_session(page)
    session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    for step in range(1, 9):
        session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y + dy * step / 8}]})
    session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


def _open(browser, url, width, height, iphone=False):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        has_touch=True,
        is_mobile=True,
        device_scale_factor=2,
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
            if iphone
            else "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        ),
    )
    page = context.new_page()
    page.goto(url + "/library", wait_until="domcontentloaded")
    page.get_by_role("button", name="Example").wait_for()
    return context, page


def test_library_touch_layout_and_ios_playback(library_url):
    playwright_sync = pytest.importorskip("playwright.sync_api")
    manager = playwright_sync.sync_playwright().start()
    artifacts = Path("/opt/cursor/artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        browser = manager.chromium.launch()
    except Exception as exc:
        manager.stop()
        pytest.skip(f"Chromium is not installed: {exc}")
    try:
        for name, width, height, iphone in (
            ("ipad-820x1180", 820, 1180, False),
            ("ipad-1180x820", 1180, 820, False),
            ("iphone-390x844", 390, 844, True),
        ):
            context, page = _open(browser, library_url, width, height, iphone)
            try:
                targets = page.evaluate(
                    """() => [...document.querySelectorAll('#library-tabs button, #open-library-settings')].map((node) => {
                      const box = node.getBoundingClientRect();
                      return {text: node.textContent, h: box.height, w: box.width};
                    })"""
                )
                assert targets and all(item["h"] >= 44 and item["w"] >= 44 for item in targets), targets
                page.screenshot(path=str(artifacts / f"library-touch-{name}.png"))
                page.get_by_role("button", name="Example").click()
                page.get_by_role("button", name="Pilot").wait_for()
                episode = page.locator(".episode").first.bounding_box()
                assert episode["height"] >= 44, episode
                page.screenshot(path=str(artifacts / f"library-touch-show-{name}.png"))
                if iphone:
                    page.mouse.move(200, 500)
                    before = page.evaluate("document.scrollingElement.scrollTop")
                    _swipe(page, 200, 500, -240)
                    page.wait_for_timeout(200)
                    assert page.evaluate("document.scrollingElement.scrollTop") >= before
                page.get_by_role("button", name="Pilot").click()
                page.get_by_role("button", name="Play").click()
                page.locator("#player-video").wait_for()
                page.wait_for_function("document.getElementById('player-video').readyState >= 2", timeout=20000)
                src = page.evaluate("document.getElementById('player-video').currentSrc")
                assert "/raw" in src, src
                full = page.evaluate(
                    """() => {
                      const video = document.getElementById('player-video');
                      let called = false;
                      video.webkitEnterFullscreen = () => { called = true; };
                      document.getElementById('player-full').click();
                      const bar = document.getElementById('player-bar').getBoundingClientRect();
                      const videoBox = video.getBoundingClientRect();
                      return {called, bar: bar.height, videoTop: videoBox.top};
                    }"""
                )
                assert full["called"]
                assert full["bar"] >= 44
                page.screenshot(path=str(artifacts / f"library-touch-player-{name}.png"))
                page.locator("#player-close").click()
            finally:
                context.close()
        context, page = _open(browser, library_url, 390, 844, True)
        try:
            page.get_by_role("button", name="Movies").click()
            page.get_by_role("button", name="Night (2020)").click()
            page.get_by_role("button", name="Play").click()
            page.wait_for_function("document.getElementById('player-video').readyState >= 2", timeout=20000)
            src = page.evaluate("document.getElementById('player-video').currentSrc")
            assert "index.m3u8" in src, src
            assert not src.startswith("blob:"), src
            page.screenshot(path=str(artifacts / "library-touch-ios-hls-iphone-390x844.png"))
        finally:
            context.close()
    finally:
        browser.close()
        manager.stop()
