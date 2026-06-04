import asyncio
from playwright.async_api import async_playwright


async def refresh():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state="/auth/storage_state.json"
            )
            page = await context.new_page()
            await page.goto(
                "https://notebooklm.google.com/",
                wait_until="networkidle",
                timeout=30000
            )
            await context.storage_state(path="/auth/storage_state.json")
            await browser.close()
            print("Session refreshed!")
    except Exception as e:
        print(f"Refresh failed: {e}")


asyncio.run(refresh())
