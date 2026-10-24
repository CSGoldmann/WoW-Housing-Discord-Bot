import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from aiohttp import web
from aiohttp.test_utils import TestServer

from wowdb_bot.site import (Blocked, Hit, HousingClient, SearchPage, SiteError,
                            item_url, parse_item, parse_search)
from wowdb_bot.bot import Bot, Results, item_embed

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://housing.wowdb.com/decor/7582/founders-point-gravestone-7582/"


class ParsingTests(unittest.TestCase):
    def test_search_uses_url_id_not_database_id(self):
        result = parse_search((FIXTURES / "search.html").read_text())
        self.assertEqual(result.hits[0].id, 7582)
        self.assertEqual(result.hits[0].name, "Founder's Point Gravestone")
        self.assertEqual(result.hits[1].category, "Furnishings › Tables and Desks")
        self.assertTrue(result.has_next)
        self.assertFalse(parse_search((FIXTURES / "search.html").read_text(), 2).has_next)

    def test_detail_cost_not_budget_or_related_card(self):
        item = parse_item((FIXTURES / "item.html").read_text(), URL)
        self.assertEqual(item.budget, "3")
        self.assertEqual(len(item.sources), 2)
        self.assertIn("Faarden", item.sources[0])
        self.assertIn('"Len"', item.sources[1])
        self.assertIn("Gold 25", item.costs[0])
        self.assertNotIn("9999", str(item))
        self.assertNotIn("Copy", str(item))
        self.assertIn("7424311.png", item.image)

    def test_missing_fields_are_explicit_not_invented(self):
        item = parse_item('<h1>Test</h1><div class="card"><h5>Item Information</h5></div>', URL)
        self.assertEqual(item.costs, ())
        self.assertEqual(item.budget, "Not listed")
        self.assertIsNone(item.image)

    def test_source_types_and_multi_currency(self):
        html = '''<h1>Test</h1><div><h5>Sources</h5>
        <div class="mb-3">Crafting: Inscription</div>
        <div class="mb-3">Vendor: Trader<div class="mt-1 ms-4"><strong>
        <img alt="Gold">2 <img alt="Silver">50 + 10 <a>War Resources</a>
        </strong></div></div></div><div class="card"><h5>Item Information</h5></div>'''
        item = parse_item(html, URL)
        self.assertEqual(item.sources[0], "Crafting: Inscription")
        self.assertIn("Gold 2 Silver 50 + 10 War Resources", item.costs[0])

    def test_blocks_and_template_drift(self):
        for parser in (parse_search, lambda html: parse_item(html, URL)):
            with self.assertRaises(Blocked):
                parser("<title>Just a moment...</title>")
            with self.assertRaises(SiteError):
                parser("<html>different template</html>")
        self.assertEqual(parse_search('<div id="items-grid"></div>').hits, ())
        self.assertEqual(parse_search('<h1>No items found</h1>').hits, ())

    def test_url_validation(self):
        self.assertEqual(item_url("7582"), "https://housing.wowdb.com/decor/7582/")
        self.assertEqual(item_url("<" + URL + ">"), URL)
        for value in ("https://evil.test/decor/7582/", "http://housing.wowdb.com/decor/7582/",
                      "https://housing.wowdb.com@evil.test/decor/7582/", "//evil.test/decor/7582/",
                      "/decor/0/", "/decor/../admin/", "https://housing.wowdb.com:443/decor/7582/"):
            with self.subTest(value=value), self.assertRaises(SiteError):
                item_url(value)

    def test_embed_limits_and_escaping(self):
        item = parse_item((FIXTURES / "item.html").read_text(), URL)
        item = replace(item, name="@everyone *" * 100, sources=("x" * 5000,), costs=("y" * 5000,))
        embed = item_embed(item)
        self.assertLessEqual(len(embed), 6000)
        self.assertLessEqual(len(embed.title), 256)
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))
        self.assertIn("@\u200beveryone", embed.title)


def interaction(owner=1):
    return SimpleNamespace(user=SimpleNamespace(id=owner),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    def page(self, number=1, count=24, more=True):
        return SearchPage(tuple(Hit(i + number * 100, f"Decor {i}", URL) for i in range(count)), number, more)

    async def test_pagination_across_site_pages_and_back(self):
        client = SimpleNamespace(search=AsyncMock(side_effect=[self.page(2, 3, False), self.page()]))
        view = Results(client, 1, "chair", self.page())
        self.assertEqual(len(view.children[0].options), 12)
        self.assertTrue(view.children[1].disabled)
        i = interaction()
        await view.move(i, 1)
        self.assertEqual(view.offset, 12)
        client.search.assert_not_awaited()
        await view.move(i, 1)
        self.assertEqual(view.page.page, 2)
        self.assertEqual(len(view.visible), 3)
        self.assertTrue(view.children[2].disabled)
        await view.move(i, -1)
        self.assertEqual(view.page.page, 1)
        self.assertEqual(view.offset, 12)
        view.stop()

    async def test_failed_page_preserves_state_and_owner(self):
        client = SimpleNamespace(search=AsyncMock(side_effect=SiteError("Unavailable")))
        view = Results(client, 1, "chair", self.page(count=12))
        await view.move(interaction(), 1)
        self.assertEqual(view.page.page, 1)
        self.assertFalse(await view.interaction_check(interaction(2)))
        self.assertTrue(await view.interaction_check(interaction(1)))
        view.stop()

    async def test_timeout_disables_controls(self):
        view = Results(None, 1, "chair", self.page())
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        self.assertTrue(all(c.disabled for c in view.children))
        view.message.edit.assert_awaited_once()
        view.stop()

    async def test_cache_and_blocked_not_cached(self):
        client = HousingClient()
        client._http = AsyncMock(return_value='<div id="items-grid"></div>')
        await client.search("chair")
        await client.search("chair")
        self.assertEqual(client._http.await_count, 1)
        client._http = AsyncMock(return_value='<title>Just a moment...</title>')
        client.last_request = 0
        with self.assertRaises(Blocked):
            await client.search("grave")
        self.assertEqual(len(client.cache), 1)
        await client.close()

    async def test_command_schema_without_discord_login(self):
        bot = Bot(HousingClient())
        group = bot.tree.get_command("decor")
        self.assertEqual({c.name for c in group.commands}, {"item", "search"})
        for command in group.commands:
            self.assertEqual(len(command.parameters), 1)
        await bot.close()

    async def test_http_streams_entire_body_and_retries(self):
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return web.Response(status=429, headers={"Retry-After": "0"})
            response = web.StreamResponse(headers={"Content-Type": "text/html; charset=utf-8"})
            await response.prepare(request)
            await response.write(b'<html><head>')
            await asyncio.sleep(0.01)
            await response.write(b'</head><div id="items-grid"></div></html>')
            await response.write_eof()
            return response
        app = web.Application()
        app.router.add_get("/", handler)
        async with TestServer(app) as server:
            client = HousingClient()
            try:
                html = await client._http(str(server.make_url("/")))
                self.assertIn("items-grid", html)
                self.assertTrue(html.endswith("</html>"))
                self.assertEqual(calls, 2)
            finally:
                await client.close()

    async def test_http_block_does_not_retry(self):
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            return web.Response(status=403)
        app = web.Application()
        app.router.add_get("/", handler)
        async with TestServer(app) as server:
            client = HousingClient()
            try:
                with self.assertRaises(Blocked):
                    await client._http(str(server.make_url("/")))
                self.assertEqual(calls, 1)
            finally:
                await client.close()


if __name__ == "__main__":
    unittest.main()
