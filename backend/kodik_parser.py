import asyncio
import json
import re
import logging
from pathlib import Path
from playwright.async_api import async_playwright
from .models import StreamUrl

log = logging.getLogger("downloader")

DIAG_DIR = Path(__file__).parent.parent / "diagnostics"

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en-US','en']});
window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
delete navigator.__proto__.webdriver;
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (params) => (
    params.name === 'notifications'
        ? Promise.resolve({state: Notification.permission})
        : originalQuery(params)
);
"""


async def resolve_kodik_streams(iframe_url: str) -> list[StreamUrl]:
    streams: list[StreamUrl] = []
    seen: set[str] = set()
    captured_headers: dict[str, str] = {}
    captured_cookies: dict[str, str] = {}
    all_requests: list[dict] = []

    log.info(f"Launching browser for: {iframe_url[:80]}...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1920,1080",
            ],
        )

        origin = "https://yummyani.me/"
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Referer": origin,
                "Origin": origin,
            },
            viewport={"width": 1920, "height": 1080},
        )

        page = await context.new_page()
        await page.add_init_script(STEALTH_JS)

        def on_request(request):
            url = request.url
            if ".m3u8" in url and "trash" not in url:
                if url not in seen:
                    seen.add(url)
                    quality = extract_quality(url)
                    streams.append(StreamUrl(quality=quality, url=url))
                    try:
                        req_headers = request.headers
                        captured_headers.update(dict(req_headers))
                        log.info(f"Captured request headers: {dict(req_headers)}")
                    except Exception as e:
                        log.warning(f"Failed to capture headers: {e}")
                    log.info(f"Captured stream: {quality} - {url[:100]}...")

        page.on("request", on_request)

        def on_all_requests(request):
            url = request.url
            all_requests.append({
                "url": url[:200],
                "method": request.method,
                "resource_type": request.resource_type,
            })

        page.on("request", on_all_requests)

        async def on_response(response):
            url = response.url
            if ".m3u8" in url and "trash" not in url:
                try:
                    headers = response.headers
                    if "set-cookie" in headers:
                        log.info(f"Response set-cookie: {headers['set-cookie']}")
                        # Parse cookie
                        cookie_parts = headers["set-cookie"].split(";")[0].split("=", 1)
                        if len(cookie_parts) == 2:
                            captured_cookies[cookie_parts[0].strip()] = cookie_parts[1].strip()
                except Exception as e:
                    log.warning(f"Failed to capture response headers: {e}")

        page.on("response", on_response)

        html_page = (
            f'<html><head>'
            f'<base href="{origin}">'
            f'</head><body style="background:black;margin:0;">'
            f'<iframe src="{iframe_url}" '
            f'width="100%" height="100%" frameborder="0">'
            f'</iframe></body></html>'
        )

        log.info("Loading iframe page...")
        await page.set_content(html_page)

        for attempt in range(3):
            await asyncio.sleep(3)
            try:
                kodik_frame = None
                for frame in page.frames:
                    if "kodikplayer" in frame.url:
                        kodik_frame = frame
                        break
                if kodik_frame:
                    confirm = await kodik_frame.query_selector(".pc-confirm")
                    if confirm:
                        log.info("Found age confirmation dialog, clicking...")
                        await confirm.click()
                        await asyncio.sleep(2)
                        break
                    else:
                        log.info("No age dialog found in Kodik frame")
                        break
                else:
                    log.info("Kodik frame not yet loaded, retrying...")
            except Exception as e:
                log.warning(f"Age confirm attempt {attempt+1} failed: {e}")

        for i in range(20):
            try:
                await page.mouse.click(640, 360)
            except Exception:
                pass
            await asyncio.sleep(1)
            if streams:
                log.info(f"Streams found after {i+1}s, waiting for more...")
                await asyncio.sleep(2)
                break

        log.info(f"Capturing cookies from browser context...")

        if not streams:
            await _save_diagnostics(page, iframe_url, all_requests)

        all_cookies = {}
        try:
            cookies_list = await context.cookies()
            for c in cookies_list:
                name = c.get("name", "")
                value = c.get("value", "")
                if name and value:
                    all_cookies[name] = value
        except Exception as e:
            log.warning(f"Failed to capture cookies: {e}")

        # Merge response cookies
        all_cookies.update(captured_cookies)
        log.info(f"Total cookies: {len(all_cookies)}: {list(all_cookies.keys())}")

        log.info(f"Browser closed. Total streams captured: {len(streams)}")
        await browser.close()

    for s in streams:
        s.cookies = all_cookies
        s.headers = {k: v for k, v in captured_headers.items()
                     if k.lower() in ("authorizations", "accepts-controls", "authorization")}

    if not streams:
        log.warning("No streams captured, trying force quality...")
        forced = try_force_quality(seen)
        streams.extend(forced)

    streams.sort(key=lambda s: int(s.quality.replace("p", "")) if s.quality.isdigit() else 0, reverse=True)
    return streams


def extract_quality(url: str) -> str:
    patterns = [
        r"(\d{3,4})\.mp4",
        r"(\d{3,4})p",
        r"/(\d{3,4})/",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1) + "p"
    return "unknown"


def try_force_quality(seen: set[str]) -> list[StreamUrl]:
    result = []
    for url in seen:
        for target in ["720", "1080"]:
            for wrong in ["360", "480"]:
                if wrong in url:
                    forced = url.replace(f"{wrong}.mp4", f"{target}.mp4")
                    if forced not in seen:
                        result.append(StreamUrl(quality=f"{target}p (forced)", url=forced))
                        break
    return result


async def _save_diagnostics(page, iframe_url: str, all_requests: list[dict]):
    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)

        html = await page.content()
        (DIAG_DIR / "kodik_page.html").write_text(html, encoding="utf-8")
        log.info(f"Diagnostics: saved page HTML ({len(html)} bytes)")

        await page.screenshot(path=str(DIAG_DIR / "kodik_screenshot.png"), full_page=True)
        log.info("Diagnostics: saved screenshot")

        (DIAG_DIR / "kodik_requests.json").write_text(
            json.dumps(all_requests, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info(f"Diagnostics: saved {len(all_requests)} network requests")

        frames = page.frames
        for i, frame in enumerate(frames):
            try:
                frame_html = await frame.content()
                (DIAG_DIR / f"kodik_frame_{i}.html").write_text(frame_html, encoding="utf-8")
                log.info(f"Diagnostics: frame {i} url={frame.url[:100]}, html={len(frame_html)} bytes")
            except Exception as e:
                log.warning(f"Diagnostics: failed to dump frame {i}: {e}")

    except Exception as e:
        log.warning(f"Diagnostics failed: {e}")
