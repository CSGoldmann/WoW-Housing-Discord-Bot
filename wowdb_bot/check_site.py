"""Credential-free live check: python -m wowdb_bot.check_site."""
import asyncio
import os
from dataclasses import asdict
import json

from dotenv import load_dotenv
from .bot import ROOT, item_embed
from .site import HousingClient, SiteError


async def run():
    load_dotenv(ROOT / ".env")
    client = HousingClient(os.getenv("WOWDB_TRANSPORT", "http"),
        os.getenv("WOWDB_HEADLESS", "false").lower() == "true", str(ROOT / ".browser-profile"))
    try:
        page = await client.search("grave")
        print(f"Search: {len(page.hits)} results on page 1; next page: {page.has_next}")
        hit = next((h for h in page.hits if h.id == 7582), None)
        if not hit:
            raise SiteError("Expected gravestone was not in the live search; inspect the website.")
        item = await client.item(hit.url)
        print(json.dumps(asdict(item), indent=2, ensure_ascii=False))
        assert len(item_embed(item)) <= 6000
        print("Live search, item parsing, and embed construction passed.")
    except SiteError as exc:
        print(f"LIVE CHECK UNAVAILABLE: {exc}")
        raise SystemExit(1)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
