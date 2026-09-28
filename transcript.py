import asyncio
import logging
import re

from playwright.async_api import (
    async_playwright,
    TimeoutError as PWTimeout,
    Error as PWError,
)

logger = logging.getLogger("tikresolver.transcript")

BASE_URL = "https://www.transcript365.com/free/tiktok-transcript/"

NAV_TIMEOUT_MS      = 30_000
ELEMENT_TIMEOUT_MS  = 20_000
GENERATE_TIMEOUT_MS = 60_000
POLL_INTERVAL_MS    = 1_000
STABILITY_MS        = 1_500

MAX_ATTEMPTS = 3       # ← bumped from 2 to 3
BACKOFF_SEC  = 3.0


async def fetch_transcript(tiktok_url: str) -> str | None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            logger.info("Transcript attempt %d/%d for %s",
                        attempt, MAX_ATTEMPTS, tiktok_url)
            result = await _fetch_once(tiktok_url)
            if result:
                return result
            logger.warning("Attempt %d produced no transcript", attempt)
        except Exception as e:
            logger.exception("Attempt %d crashed: %s", attempt, e)

        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(BACKOFF_SEC * attempt)

    logger.error("All transcript attempts failed for %s", tiktok_url)
    return None


async def _fetch_once(tiktok_url: str) -> str | None:
    async with async_playwright() as p:
        browser = None
        context = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                permissions=["clipboard-read", "clipboard-write"],
            )
            page = await context.new_page()

            await page.goto(BASE_URL, wait_until="domcontentloaded",
                            timeout=NAV_TIMEOUT_MS)
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except PWTimeout:
                pass

            # fill textbox
            url_box = await _find_textbox(page)
            if url_box is None:
                logger.error("Could not find URL textbox")
                return None
            await url_box.wait_for(state="visible",
                                   timeout=ELEMENT_TIMEOUT_MS)
            await url_box.click()
            await url_box.fill(tiktok_url)
            logger.info("URL pasted")

            # click Generate
            gen_btn = await _find_generate_button(page)
            if gen_btn is None:
                logger.error("Could not find Generate button")
                return None
            await gen_btn.wait_for(state="visible",
                                   timeout=ELEMENT_TIMEOUT_MS)
            await gen_btn.click()
            logger.info("Generate clicked")

            # wait for BOTH transcript text and copy button
            copy_btn = await _wait_for_copy_button(page)
            if copy_btn is None:
                logger.warning("Copy button (or transcript) never ready")
                await _snapshot(page, "no-copy-button")
                return None

            # clear clipboard
            try:
                await page.evaluate("navigator.clipboard.writeText('')")
            except Exception:
                pass

            # click copy
            try:
                await copy_btn.click()
                logger.info("Copy button clicked")
            except Exception as e:
                logger.error("Clicking copy failed: %s", e)
                return None

            # read clipboard, retry a few times if empty
            for read_attempt in range(1, 4):
                await asyncio.sleep(0.5)
                try:
                    text = await page.evaluate(
                        "navigator.clipboard.readText()"
                    )
                except Exception as e:
                    logger.warning("clipboard read %d failed: %s",
                                   read_attempt, e)
                    text = ""

                if text and text.strip():
                    text = text.strip()
                    logger.info("Transcript captured (%d chars)",
                                len(text))
                    return text

                logger.info("clipboard empty (read %d/3), retrying...",
                            read_attempt)
                # try clicking again
                try:
                    await copy_btn.click()
                except Exception:
                    pass

            logger.warning("Clipboard remained empty after 3 reads")
            return None

        except Exception as e:
            logger.exception("Unexpected error in transcript scrape: %s", e)
            return None

        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass


# ------------------------------------------------------------------
# The critical helper — waits for text before returning the button
# ------------------------------------------------------------------
async def _wait_for_copy_button(page):
    """
    Poll until the transcript text is populated AND stable, then
    return the copy button locator (ready to click).

    This is the fix for the "clipboard empty" race: the button can
    appear before the text has streamed in.
    """
    deadline = asyncio.get_event_loop().time() + (GENERATE_TIMEOUT_MS / 1000)
    last_len = 0
    stable_since = None

    transcript_selector = (
        "div.grid.md\\:grid-cols-2 > "
        "div.bg-white.rounded-2xl:nth-of-type(1) > "
        "p.text-\\[\\#2D3436\\].text-sm"
    )

    while asyncio.get_event_loop().time() < deadline:
        try:
            p_node = page.locator(transcript_selector)
            if await p_node.count() > 0:
                raw = await p_node.first.inner_text()
                text_len = len(raw.strip())

                if text_len < 20:
                    await asyncio.sleep(POLL_INTERVAL_MS / 1000)
                    continue

                if text_len != last_len:
                    last_len = text_len
                    stable_since = asyncio.get_event_loop().time()
                    await asyncio.sleep(POLL_INTERVAL_MS / 1000)
                    continue

                if stable_since and (
                    asyncio.get_event_loop().time() - stable_since
                    >= STABILITY_MS / 1000
                ):
                    btn = await _find_copy_button(page)
                    if btn is not None:
                        logger.info("Transcript stable (%d chars), "
                                    "copy ready", text_len)
                        return btn
        except Exception as e:
            logger.debug("wait error: %s", e)

        await asyncio.sleep(POLL_INTERVAL_MS / 1000)

    return None


async def _find_copy_button(page):
    try:
        btn = page.locator(
            "div.grid.md\\:grid-cols-2 > "
            "div.bg-white.rounded-2xl:nth-of-type(1) > "
            "p.text-\\[\\#2D3436\\].text-sm > "
            "button.text-\\[\\#95A5A6\\].hover\\:text-\\[\\#41C97F\\]"
        )
        if await btn.count() > 0 and await btn.first.is_visible():
            return btn.first
    except Exception:
        pass

    try:
        btn = page.locator(
            "div.bg-white.rounded-2xl:nth-of-type(1) "
            "button:has(svg rect[width='14'][height='14'])"
        )
        if await btn.count() > 0 and await btn.first.is_visible():
            return btn.first
    except Exception:
        pass

    try:
        btn = page.locator("button:has(svg rect[width='14'][height='14'])")
        if await btn.count() > 0 and await btn.first.is_visible():
            return btn.first
    except Exception:
        pass

    return None


# ------------------------------------------------------------------
# Textbox / Generate button (unchanged)
# ------------------------------------------------------------------
async def _find_textbox(page):
    try:
        loc = page.get_by_role(
            "textbox",
            name="Paste a public TikTok video URL here...",
        )
        if await loc.count() > 0:
            return loc.first
    except Exception:
        pass

    for placeholder in (
        "Paste a public TikTok video URL here...",
        "Paste a public TikTok video URL",
        "Paste a TikTok",
    ):
        try:
            loc = page.get_by_placeholder(placeholder, exact=False)
            if await loc.count() > 0:
                return loc.first
        except Exception:
            continue

    try:
        loc = page.locator('input[type="url"], input[type="text"], textarea')
        if await loc.count() > 0:
            return loc.first
    except Exception:
        pass

    return None


async def _find_generate_button(page):
    try:
        loc = page.locator(
            'button[type="submit"]:has-text("Generate TikTok Transcript")'
        )
        if await loc.count() > 0:
            return loc.first
    except Exception:
        pass

    try:
        loc = page.get_by_role(
            "button",
            name="Generate TikTok Transcript",
            exact=True,
        )
        if await loc.count() > 0:
            return loc.first
    except Exception:
        pass

    try:
        loc = page.locator('button:has-text("Generate TikTok Transcript")')
        if await loc.count() > 0:
            return loc.first
    except Exception:
        pass

    return None


async def _snapshot(page, name: str):
    try:
        path = f"transcript-{name}.png"
        await page.screenshot(path=path, full_page=False)
        logger.info("Debug screenshot saved to %s", path)
    except Exception:
        pass