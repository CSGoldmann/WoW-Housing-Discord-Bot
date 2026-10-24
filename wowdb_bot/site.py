from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import aiohttp
from bs4 import BeautifulSoup, Tag

BASE = "https://housing.wowdb.com"
ITEM_PATH = re.compile(r"^/decor/([1-9]\d*)(?:/[^/?#]+)?/$")


class SiteError(Exception):
    """An actionable error suitable for display to a Discord user."""


class Blocked(SiteError):
    pass


async def read_body(response):
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(65536):
        size += len(chunk)
        if size > 4_000_000:
            raise SiteError("WoWDB's response exceeded the size limit.")
        chunks.append(chunk)
    return b"".join(chunks).decode(response.charset or "utf-8", errors="replace")


def clean(value: str) -> str:
    return " ".join(value.split())


def item_url(value: str) -> str:
    value = value.strip().strip("<>")
    if value.isdecimal() and int(value) > 0:
        return f"{BASE}/decor/{int(value)}/"
    parts = urlsplit(urljoin(BASE, value))
    if (parts.scheme != "https" or parts.netloc != "housing.wowdb.com"
            or not ITEM_PATH.fullmatch(parts.path)):
        raise SiteError("Use a WoWDB Housing decor URL, a decor ID, or an item name.")
    return BASE + parts.path


def guard(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text().lower() if soup.title else ""
    if ("just a moment" in title or "attention required" in title
            or soup.select_one('#challenge-running, #challenge-form')
            or "window._cf_chl_opt" in html):
        raise Blocked("WoWDB is asking for browser verification. Open the WoWDB link, "
                      "or use browser mode described in README.md. No results were fabricated.")
    return soup


@dataclass(frozen=True)
class Hit:
    id: int
    name: str
    url: str
    category: str = "Not listed"


@dataclass(frozen=True)
class SearchPage:
    hits: tuple[Hit, ...]
    page: int
    has_next: bool


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    url: str
    category: str
    sources: tuple[str, ...]
    costs: tuple[str, ...]
    budget: str
    image: str | None = None
    note: str | None = None


def parse_search(html: str, page: int = 1) -> SearchPage:
    soup = guard(html)
    grid = soup.select_one("#items-grid")
    if grid is None:
        # A changed template must not silently become 'no results'.
        if re.search(r"no (?:items|results|decor).*found", soup.get_text(" "), re.I):
            return SearchPage((), page, False)
        raise SiteError("WoWDB's search layout was not recognized. Please use its website for now.")
    hits = {}
    for card in grid.select(".item-card"):
        link = card.select_one(".card-title a[href]")
        if not link:
            continue
        try:
            url = item_url(link["href"])
        except SiteError:
            continue
        decor_id = int(ITEM_PATH.fullmatch(urlsplit(url).path)[1])
        category = " › ".join(clean(a.get_text(" ")) for a in card.select(".category-bar a"))
        name = clean(link.get_text(" "))
        if name:
            hits[decor_id] = Hit(decor_id, name, url, category or "Not listed")
    if grid.select(".item-card") and not hits:
        raise SiteError("WoWDB's result cards changed. Please use the website for now.")
    has_next = False
    for a in soup.select(".pagination a[href]"):
        p = parse_qs(urlsplit(a["href"]).query).get("page", ["0"])[0]
        if p.isdecimal() and int(p) > page:
            has_next = True
    return SearchPage(tuple(hits.values()), page, has_next)


def readable(tag: Tag) -> str:
    # Work on a copy: currency is often represented only by image alt text.
    copy = BeautifulSoup(str(tag), "html.parser")
    for node in copy.select("script, style, .vendor-map-wrapper, .badge, button"):
        node.decompose()
    for image in copy.select("img"):
        image.replace_with(" " + image.get("alt", "") + " ")
    return clean(copy.get_text(" "))


def parse_item(html: str, url: str) -> Item:
    soup = guard(html)
    h1 = soup.select_one("h1")
    info = next((h.find_parent("div", class_="card")
                 for h in soup.select("h5") if "Item Information" in h.get_text()), None)
    if h1 is None or info is None:
        raise SiteError("This decor was not found, or WoWDB's item layout changed.")
    canonical = soup.select_one('link[rel="canonical"]')
    url = item_url(canonical["href"] if canonical and canonical.get("href") else url)
    fields = {}
    for row in info.select(".row"):
        cols = row.find_all("div", recursive=False)
        if len(cols) >= 2:
            fields[clean(cols[0].get_text()).rstrip(":")] = clean(cols[1].get_text(" "))
    category = " › ".join(fields[k] for k in ("Category", "Subcategory") if fields.get(k))
    source_heading = next((h for h in soup.select("h5") if clean(h.get_text()) == "Sources"), None)
    sources, costs = [], []
    if source_heading:
        for block in source_heading.parent.find_all("div", recursive=False):
            # Current detail template: each source is a direct div.mb-3.
            if "mb-3" not in block.get("class", []):
                continue
            price_nodes = block.select(".mt-1.ms-4 strong")
            price = "; ".join(readable(p) for p in price_nodes if readable(p))
            # Strip vendor pricing/maps from the source summary, preserving requirements.
            copy = BeautifulSoup(str(block), "html.parser")
            for node in copy.select(".mt-1.ms-4, .vendor-map-wrapper"):
                node.decompose()
            source = readable(copy)
            if source:
                sources.append(source)
            if price:
                costs.append(f"{source.split(' (')[0]}: {price}")
    image = soup.select_one("img.item-detail-image")
    meta = soup.select_one('meta[property="og:image"]')
    image_url = image.get("src") if image else (meta.get("content") if meta else None)
    if image_url:
        image_url = urljoin(BASE, image_url)
        if urlsplit(image_url).scheme != "https":
            image_url = None
    note = next((clean(x.get_text(" ")) for x in soup.select(".alert")
                 if "not yet available" in x.get_text().lower()), None)
    return Item(int(ITEM_PATH.fullmatch(urlsplit(url).path)[1]), clean(h1.get_text(" ")),
                url, category or "Not listed", tuple(sources), tuple(costs),
                fields.get("Budget Cost", "Not listed"), image_url, note)


class HousingClient:
    """Serialized, paced requests; bounded TTL cache; fixed-host URLs only."""

    def __init__(self, transport="http", headless=False, profile=".browser-profile"):
        if transport not in {"http", "browser"}:
            raise ValueError("WOWDB_TRANSPORT must be http or browser")
        self.transport, self.headless, self.profile = transport, headless, profile
        self.session = None
        self.context = self.playwright = None
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    async def close(self):
        if self.session:
            await self.session.close()
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()

    async def _browser(self, url):
        try:
            from playwright.async_api import async_playwright
            if self.context is None:
                self.playwright = await async_playwright().start()
                self.context = await self.playwright.chromium.launch_persistent_context(
                    self.profile, headless=self.headless)
            page = await self.context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                # No automated challenge solving; a local user may verify manually.
                await page.wait_for_selector("#items-grid, h1", timeout=5000)
                return await page.content()
            finally:
                await page.close()
        except Exception as exc:
            if isinstance(exc, SiteError):
                raise
            raise SiteError("Browser retrieval failed. Install requirements-browser.txt and run "
                            "python -m playwright install chromium; see README for manual verification.") from exc

    async def _http(self, url):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=18),
                headers={"User-Agent": "WoWDBHousingDiscordBot/1.0 (personal decor lookup)",
                         "Accept": "text/html"}, trust_env=True)
        for attempt in range(3):
            try:
                async with self.session.get(url, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        target = urljoin(url, response.headers.get("Location", ""))
                        # Numeric /decor/ID/ may redirect to its slug.
                        return await self._http_redirect(target)
                    if response.status == 404:
                        raise SiteError("That decoration was not found on WoWDB.")
                    if response.status in {401, 403}:
                        raise Blocked("WoWDB blocked automated access. Try browser mode (README.md), "
                                      "or open the item directly on WoWDB.")
                    if response.status == 429 or response.status >= 500:
                        if attempt < 2:
                            delay = response.headers.get("Retry-After", "2")
                            await asyncio.sleep(min(float(delay) if delay.isdecimal() else 2, 6))
                            continue
                        raise SiteError("WoWDB is busy or rate limiting requests. Try again later.")
                    if response.status != 200:
                        raise SiteError(f"WoWDB returned HTTP {response.status}. Try again later.")
                    if response.content_type != "text/html":
                        raise SiteError("WoWDB returned an unexpected response format.")
                    return await read_body(response)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 2:
                    raise SiteError("Could not reach WoWDB. Please try again later.") from exc
                await asyncio.sleep(attempt + 1)

    async def _http_redirect(self, target):
        # One verified item redirect only; don't recurse through arbitrary redirects.
        target = item_url(target)
        async with self.session.get(target, allow_redirects=False) as response:
            if response.status != 200:
                raise SiteError(f"WoWDB item redirect returned HTTP {response.status}; try its full URL.")
            return await read_body(response)

    async def fetch(self, path, params=None):
        url = BASE + path + ("?" + urlencode(params) if params else "")
        async with self.lock:
            cached = self.cache.get(url)
            if cached and time.monotonic() - cached[0] < 300:
                self.cache.move_to_end(url)
                return cached[1]
            await asyncio.sleep(max(0, 1.0 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            html = await (self._browser(url) if self.transport == "browser" else self._http(url))
            guard(html)
            self.cache[url] = (time.monotonic(), html)
            if len(self.cache) > 128:
                self.cache.popitem(last=False)
            return html

    async def search(self, query, page=1):
        query = clean(query)
        if not 2 <= len(query) <= 100 or page < 1:
            raise SiteError("Search must contain 2–100 characters.")
        return parse_search(await self.fetch("/decor/", {"q": query, "page": page}), page)

    async def item(self, value):
        url = item_url(value)
        return parse_item(await self.fetch(urlsplit(url).path), url)
