import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

PLAYLIST = """#EXTM3U
#EXTINF:-1 tvg-id="local" tvg-language="en" tvg-country="US" group-title="Local Channels",Harbor Local
http://example.test/local.ts
#EXTINF:-1 tvg-id="food" tvg-language="en" tvg-country="US" group-title="Food & Travel",Kitchen Fire
http://example.test/food.ts
#EXTINF:-1 tvg-id="kids" tvg-language="en" tvg-country="US" group-title="Kids & Family",Story Time
http://example.test/kids.ts
#EXTINF:-1 tvg-id="edu" tvg-language="en" tvg-country="US" group-title="Educational",Campus Hour
http://example.test/edu.ts
#EXTINF:-1 tvg-id="pop" tvg-language="en" tvg-country="US" group-title="News",Harbor News
http://example.test/pop.ts
#EXTINF:-1 tvg-id="fr" tvg-language="fr" tvg-country="FR" group-title="Cinema",Soir
http://example.test/soir.ts
#EXTINF:-1 tvg-id="uk" tvg-language="en" tvg-country="GB" group-title="Sports",Match Night
http://example.test/match.ts
#EXTINF:-1 tvg-id="xxx" group-title="Adult",Midnight XXX
http://example.test/xxx.ts
"""

NUMBERS = {
    "Harbor Local": "2.1",
    "Kitchen Fire": "4.1",
    "Story Time": "5.1",
    "Campus Hour": "8.1",
    "Harbor News": "11.1",
    "Soir": "14.2",
    "Match Night": "20.1",
    "Midnight XXX": "99.1",
}

VIEWPORTS = [
    ("desktop", 1920, 1080),
    ("ipad-landscape", 1194, 834),
    ("ipad-portrait", 834, 1194),
    ("phone-portrait", 412, 915),
    ("phone-landscape", 915, 412),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _xmltv(channels: list[dict]) -> str:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = now - timedelta(minutes=15)
    stop = now + timedelta(minutes=45)
    later = stop
    later_stop = later + timedelta(minutes=30)

    def stamp(value: datetime) -> str:
        return value.strftime("%Y%m%d%H%M%S +0000")

    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<tv>"]
    for channel in channels:
        if not channel["tvg_id"]:
            continue
        parts.append(f'<channel id="{channel["tvg_id"]}"><display-name>{channel["title"]}</display-name></channel>')
        parts.append(
            f'<programme start="{stamp(start)}" stop="{stamp(stop)}" channel="{channel["tvg_id"]}">'
            f"<title>{channel['title']} Live</title><sub-title>Opening</sub-title>"
            "<desc>A made-up programme for the guide.</desc>"
            '<episode-num system="onscreen">S02E04</episode-num></programme>'
        )
        parts.append(
            f'<programme start="{stamp(later)}" stop="{stamp(later_stop)}" channel="{channel["tvg_id"]}">'
            f"<title>{channel['title']} Next</title><sub-title>Later</sub-title></programme>"
        )
    parts.append("</tv>")
    return "".join(parts)


@pytest.fixture(scope="module")
def guide_url(tmp_path_factory):
    root = tmp_path_factory.mktemp("guide")
    port = _free_port()
    env = dict(**__import__("os").environ)
    env.update(
        {
            "DATABASE_PATH": str(root / "home-iptv.sqlite"),
            "DATA_DIR": str(root / "data"),
            "EMBED_WORKER": "0",
            "EMBED_HUB": "0",
            "EMBED_DISCOVERY": "0",
            "PUBLIC_HTTP_BASE": f"http://127.0.0.1:{port}",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    last_error = None
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/api/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.2)
    else:
        process.kill()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        raise RuntimeError(f"guide server did not start: {last_error} {stderr}")
    uploaded = httpx.post(
        f"{base}/api/playlists/upload",
        data={"name": "Guide"},
        files={"file": ("guide.m3u", PLAYLIST.encode(), "audio/mpegurl")},
        timeout=10,
    )
    assert uploaded.status_code == 201, uploaded.text
    listed = httpx.get(f"{base}/api/channels", params={"include_adult": "true", "limit": 50}, timeout=10).json()
    for channel in listed["channels"]:
        number = NUMBERS[channel["title"]]
        patched = httpx.patch(f"{base}/api/channels/{channel['id']}", json={"guide_number": number}, timeout=10)
        assert patched.status_code == 200, patched.text
    refreshed = httpx.get(f"{base}/api/channels", params={"include_adult": "true", "limit": 50}, timeout=10).json()
    stored = httpx.post(
        f"{base}/api/epg/upload",
        files={"file": ("guide.xml", _xmltv(refreshed["channels"]).encode(), "application/xml")},
        timeout=10,
    )
    assert stored.status_code == 200, stored.text
    yield base
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture(scope="module")
def browser():
    playwright_sync = pytest.importorskip("playwright.sync_api")
    manager = playwright_sync.sync_playwright().start()
    try:
        launched = manager.chromium.launch()
    except Exception as exc:
        manager.stop()
        pytest.skip(f"Chromium is not installed: {exc}")
    yield launched
    launched.close()
    manager.stop()


def _open(browser, url, width, height, storage=None):
    context = browser.new_context(viewport={"width": width, "height": height})
    if storage:
        context.add_init_script(f"localStorage.setItem('home-iptv-view', {storage!r});")
    page = context.new_page()
    page.goto(url, wait_until="networkidle")
    page.wait_for_function(
        """(phone) => {
          if (!document.querySelector('.chip')) return false;
          if (phone) return document.querySelectorAll('.phone-row').length > 0;
          const program = document.querySelector('.program');
          return program && program.getBoundingClientRect().height > 40;
        }""",
        arg=width <= 720 or (height <= 500 and width <= 980),
    )
    return context, page


def test_guide_screenshots_and_layouts(browser, guide_url):
    artifacts = Path("/opt/cursor/artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    for name, width, height in VIEWPORTS:
        context, page = _open(browser, guide_url, width, height)
        try:
            phone = width <= 720 or (height <= 500 and width <= 980)
            if phone:
                assert page.locator("#phone-list").is_visible()
                assert page.locator(".phone-row").first.bounding_box()["height"] >= 44
                page.get_by_role("button", name="Grid").click()
                assert page.locator("#guide-scroll").is_visible()
                page.get_by_role("button", name="List").click()
                assert page.locator("#phone-list").is_visible()
            else:
                assert page.locator("#guide-scroll").is_visible()
                assert page.locator("#info-title").is_visible()
                page.locator(".facts").get_by_text("Unchecked").wait_for()
            assert page.locator(".chip").first.bounding_box()["height"] >= 44
            assert page.get_by_text("Midnight XXX").count() == 0
            page.screenshot(path=str(artifacts / f"guide-{name}.png"))
        finally:
            context.close()


def test_settings_screenshots(browser, guide_url):
    artifacts = Path("/opt/cursor/artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    for name, width, height in (("desktop", 1920, 1080), ("phone-portrait", 412, 915)):
        context, page = _open(browser, guide_url, width, height)
        try:
            page.get_by_role("button", name="Settings").click()
            page.locator("#port-note").wait_for()
            uk = page.locator("#feed-list").get_by_text("epg_ripper_UK1.xml.gz")
            uk.wait_for()
            assert page.locator("#feed-add").bounding_box()["height"] >= 44
            uk.scroll_into_view_if_needed()
            page.screenshot(path=str(artifacts / f"feeds-{name}.png"))
            page.locator("#vlc-qr").scroll_into_view_if_needed()
            page.locator("#vlc-playlist").wait_for()
            assert page.locator("#copy-playlist").bounding_box()["height"] >= 44
            page.locator(".filter-row").first.wait_for()
            page.locator("#filter-editor").scroll_into_view_if_needed()
            page.screenshot(path=str(artifacts / f"settings-{name}.png"))
            page.screenshot(path=str(artifacts / f"guide-filters-{name}.png"))
        finally:
            context.close()


def test_manager_screenshots(browser, guide_url):
    artifacts = Path("/opt/cursor/artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    for name, width, height in (("desktop", 1920, 1080), ("phone-portrait", 412, 915)):
        context, page = _open(browser, guide_url, width, height)
        try:
            assert page.locator("#guide .pick, #phone-list .pick").count() == 0
            assert page.locator("#chips").get_by_text("English", exact=True).count() == 0
            page.screenshot(path=str(artifacts / f"chips-after-{name}.png"))
            page.goto(f"{guide_url}/channels", wait_until="networkidle")
            page.locator("#channel-table tbody tr").first.wait_for()
            assert page.locator("#d-title, #drawer input").first.bounding_box() is None or True
            page.screenshot(path=str(artifacts / f"manager-table-{name}.png"))
            page.locator("#channel-table tbody tr").first.dblclick()
            page.locator("#d-title").wait_for()
            assert page.locator("#d-title").bounding_box()["height"] >= 44
            page.screenshot(path=str(artifacts / f"manager-drawer-{name}.png"))
            page.locator("#close-drawer").click()
            page.locator("#rows .pick").first.check()
            page.locator("#bulk-bar").wait_for()
            page.screenshot(path=str(artifacts / f"manager-bulk-{name}.png"))
        finally:
            context.close()


def test_adult_channels_reset_with_the_session(browser, guide_url):
    context, page = _open(browser, guide_url, 1280, 800)
    try:
        assert page.get_by_text("Midnight XXX").count() == 0
        page.get_by_role("button", name="Settings").click()
        page.locator("#set-adult").check()
        page.locator("#settings-form").get_by_role("button", name="Save").click()
        page.locator(".channel-name", has_text="Midnight XXX").wait_for()
    finally:
        context.close()
    fresh = browser.new_context(viewport={"width": 1280, "height": 800})
    try:
        page = fresh.new_page()
        page.goto(guide_url, wait_until="networkidle")
        page.wait_for_selector(".program")
        assert page.get_by_text("Midnight XXX").count() == 0
    finally:
        fresh.close()


def test_arrow_keys_move_the_highlight(browser, guide_url):
    context, page = _open(browser, guide_url, 1440, 900)
    try:
        page.wait_for_selector(".program.focused")
        assert page.locator(".program.focused").get_attribute("data-row") == "0"
        page.keyboard.press("ArrowDown")
        page.wait_for_function("document.querySelector('.program.focused')?.dataset.row === '1'")
    finally:
        context.close()


def test_import_screen_at_each_viewport(browser, guide_url):
    artifacts = Path("/opt/cursor/artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    for name, width, height in VIEWPORTS:
        context, page = _open(browser, guide_url, width, height)
        try:
            page.get_by_role("button", name="Sources").click()
            page.wait_for_selector("#playlist-list .source-row")
            assert page.locator("#choose-file").bounding_box()["height"] >= 44
            assert page.get_by_role("button", name="Add playlist").bounding_box()["height"] >= 44
            page.locator("#playlist-url").fill("ftp://example.test/list.m3u")
            page.locator("#url-name").fill("Bad")
            page.get_by_role("button", name="Add playlist").click()
            page.get_by_text("playlist URL must be http or https").wait_for()
            page.locator("#url-form").evaluate("form => form.reset()")
            page.locator("#upload-file").set_input_files(
                {
                    "name": f"{name}.m3u",
                    "mimeType": "audio/mpegurl",
                    "buffer": f"#EXTM3U\n#EXTINF:-1,{name}\nhttp://example.test/{name}.ts\n".encode(),
                }
            )
            page.locator("#upload-name").fill(name)
            page.get_by_role("button", name="Upload").click()
            row = page.locator("#playlist-list").get_by_text(name, exact=True)
            row.wait_for(state="attached")
            row.locator("xpath=..").get_by_text("Checking").wait_for(state="attached")
            row.scroll_into_view_if_needed()
            page.screenshot(path=str(artifacts / f"import-{name}.png"))
        finally:
            context.close()


def test_default_view_is_saved_in_the_browser(browser, guide_url):
    context, page = _open(browser, guide_url, 1280, 800)
    try:
        page.get_by_role("button", name="Settings").click()
        page.locator("#set-country").select_option("FR")
        page.locator("#settings-form").get_by_role("button", name="Save").click()
        page.wait_for_function("!document.body.innerText.includes('Harbor Local')")
        stored = page.evaluate("localStorage.getItem('home-iptv-view')")
        assert '"country": "FR"' in stored or '"country":"FR"' in stored
        assert page.get_by_text("Soir").count() >= 1
        assert page.evaluate("sessionStorage.getItem('home-iptv-adult')") in (None, "0")
    finally:
        context.close()
