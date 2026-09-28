import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser, Playwright

logger = logging.getLogger("ssstik")

_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None


# ------------------------------------------------------------------
# Browser lifecycle
# ------------------------------------------------------------------
async def start_browser():
    global _playwright, _browser
    if _browser is not None:
        return
    logger.info("Starting Playwright browser...")
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    logger.info("Playwright browser ready.")


async def stop_browser():
    global _playwright, _browser
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
    logger.info("Playwright browser stopped.")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
async def _wait_for_stable_text(
    locator,
    timeout: int = 15000,
    interval: int = 400,
) -> str:
    """
    Poll a locator's text_content() until it stops changing.

    SSSTik fills the result heading in stages: it first renders a short
    preview, then replaces it with the full caption. Reading too early
    gives truncated text. This waits until two consecutive reads match.
    """
    elapsed = 0
    last = ""
    stable_count = 0

    while elapsed < timeout:
        try:
            current = (await locator.text_content()) or ""
        except Exception:
            current = ""

        if current and current == last:
            stable_count += 1
            if stable_count >= 2:
                return current
        else:
            stable_count = 0
            last = current

        await asyncio.sleep(interval / 1000)
        elapsed += interval

    return last


async def _get_caption_oembed(tiktok_url: str) -> Optional[str]:
    """
    Fallback: TikTok's public oEmbed endpoint returns the full description.
    Fast, no browser needed, but only returns the post description
    (no overlay text, no hashtags added as stickers).
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(
                "https://www.tiktok.com/oembed",
                params={"url": tiktok_url},
            )
            r.raise_for_status()
            return r.json().get("title")
    except Exception as e:
        logger.warning("oEmbed caption failed: %s", e)
        return None


# ------------------------------------------------------------------
# Caption scraper
# ------------------------------------------------------------------
async def get_caption(tiktok_url: str) -> Optional[str]:
    """Fetch the full caption for a TikTok video. Returns None on failure."""
    if _browser is None:
        raise RuntimeError("Browser not started.")

    context = await _browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    page = await context.new_page()

    caption = None

    try:
        logger.info("SSSTik: loading homepage for %s", tiktok_url)
        await page.goto("https://ssstik.io/", timeout=60000)

        input_field = page.get_by_role("textbox")
        await input_field.wait_for(timeout=30000)
        await input_field.fill(tiktok_url)

        await page.get_by_role("button", name="Download").click()

        # --- Primary target: the result heading (h3#result-title) ---
        heading = page.locator("h3#result-title")
        await heading.wait_for(state="attached", timeout=45000)

        # Wait until the heading text stops changing
        caption = await _wait_for_stable_text(heading, timeout=15000)
        caption = (caption or "").strip()

        # --- Fallback 1: p.maintext if the heading was empty ---
        if not caption:
            maintext = page.locator("p.maintext")
            if await maintext.count() > 0:
                caption = ((await maintext.first.text_content()) or "").strip()
                if caption:
                    logger.info("SSSTik: used p.maintext fallback")

    except Exception as e:
        logger.warning("SSSTik caption scrape failed for %s: %s", tiktok_url, e)
    finally:
        await page.close()
        await context.close()

    # --- Fallback 2: oEmbed if SSSTik gave nothing or something too short ---
    if not caption or len(caption) < 50:
        oembed = await _get_caption_oembed(tiktok_url)
        if oembed and len(oembed) > len(caption or ""):
            logger.info(
                "SSSTik: using oEmbed fallback (ssstik_len=%s, oembed_len=%s)",
                len(caption) if caption else 0,
                len(oembed),
            )
            caption = oembed.strip()

    logger.info("SSSTik: caption extracted (len=%s)", len(caption) if caption else 0)
    return caption or None


# ------------------------------------------------------------------
# FastAPI lifespan
# ------------------------------------------------------------------
@asynccontextmanager
async def ssstik_lifespan():
    await start_browser()
    try:
        yield
    finally:
        await stop_browser()