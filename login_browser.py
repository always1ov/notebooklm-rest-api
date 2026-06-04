"""
Headed Chromium login helper. Run inside the container with DISPLAY=:99.
Opens NotebookLM with the configured proxy, keeps saving storage state
every 5 seconds so the session is persisted as soon as the user logs in.
"""
import asyncio
import os

from playwright.async_api import async_playwright

_SUPPRESS_FLAGS = [
    "--no-restore-last-session",
    "--no-first-run",
    "--disable-session-crashed-bubble",
    "--disable-infobars",
    "--disable-blink-features=AutomationControlled",
]


async def main():
    storage_path = os.environ.get("NOTEBOOKLM_STORAGE_PATH", "/auth/storage_state.json")
    profile_dir = os.path.join(os.path.dirname(os.path.abspath(storage_path)), "browser_profile")
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    proxy = {"server": proxy_url} if proxy_url else None

    os.makedirs(profile_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(storage_path)), exist_ok=True)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            profile_dir,
            headless=False,
            proxy=proxy,
            args=_SUPPRESS_FLAGS,
        )
        # Close any pages restored from the previous session, open one clean page
        for page in ctx.pages[1:]:
            await page.close()
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://notebooklm.google.com/")

        # Keep running and saving state every 5 s so login is captured immediately
        while True:
            try:
                await asyncio.sleep(5)
                await ctx.storage_state(path=storage_path)
            except Exception:
                break


asyncio.run(main())
