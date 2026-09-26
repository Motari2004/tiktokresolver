import asyncio
import logging
import re
from playwright.async_api import async_playwright

logger = logging.getLogger("tikresolver.downloader")


async def resolve_tiktok(raw_url: str) -> str | None:
    match = re.search(r"https?://\S+", raw_url)
    if not match:
        return None
    tiktok_url = match.group(0).rstrip("-").strip()

    captured = {"url": None}
    link_found = asyncio.Event()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        async def on_response(response):
            if "d.rapidcdn.app" in response.url and "token=" in response.url:
                if captured["url"] is None:
                    captured["url"] = response.url
                    logger.info("Intercepted (response): %s", response.url)
                    link_found.set()

        page.on("response", on_response)

        try:
            await page.goto("https://snaptik.app/en3", wait_until="domcontentloaded")

            input_box = page.get_by_role(
                "textbox", name="Paste TikTok video link here..."
            )
            await input_box.wait_for(state="visible", timeout=15000)
            await input_box.fill(tiktok_url)

            submit_btn = page.locator("#submit-btn").get_by_text("Download")
            await submit_btn.wait_for(state="visible", timeout=15000)
            await submit_btn.click()

            no_wm_btn = page.get_by_role(
                "button", name="Download (No Watermark)"
            )
            await no_wm_btn.wait_for(state="visible", timeout=30000)

            async def abort_rapidcdn(route):
                if "d.rapidcdn.app" in route.request.url and "token=" in route.request.url:
                    if captured["url"] is None:
                        captured["url"] = route.request.url
                        logger.info("Intercepted (request): %s", route.request.url)
                        link_found.set()
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", abort_rapidcdn)

            try:
                await no_wm_btn.click(timeout=10000)
            except Exception:
                pass

            try:
                await asyncio.wait_for(link_found.wait(), timeout=20)
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for rapidcdn link")
                return None

            logger.info("Final captured link: %s", captured["url"])
            return captured["url"]
        finally:
            await browser.close()