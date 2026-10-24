"""Open a dedicated local browser profile for manual WoWDB verification."""
import asyncio
from .bot import ROOT


async def run():
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(ROOT / ".browser-profile"), headless=False)
        try:
            page = await context.new_page()
            await page.goto("https://housing.wowdb.com/decor/?q=grave", wait_until="domcontentloaded")
            print("In the browser, complete any verification yourself and confirm you can see the results.")
            await asyncio.to_thread(input, "Then press Enter here to save this profile and close the browser: ")
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(run())
