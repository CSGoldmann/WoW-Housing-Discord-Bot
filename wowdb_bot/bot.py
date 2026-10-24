from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from .site import HousingClient, Item, SearchPage, SiteError

ROOT = Path(__file__).resolve().parent.parent
PAGE_SIZE = 12
log = logging.getLogger(__name__)


def text(value, limit=1024):
    value = discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))
    return value if len(value) <= limit else value[:limit - 1] + "…"


def item_embed(item: Item) -> discord.Embed:
    embed = discord.Embed(title=text(item.name, 256), url=item.url, color=0xB69250)
    embed.add_field(name="Decor ID", value=str(item.id), inline=False)
    embed.add_field(name="Category", value=text(item.category), inline=False)
    embed.add_field(name="Acquisition", value=text("\n".join(item.sources) or "Not listed on WoWDB"), inline=False)
    embed.add_field(name="Purchase cost", value=text("\n".join(item.costs) or "Not listed on WoWDB"), inline=False)
    embed.add_field(name="Housing budget", value=text(item.budget), inline=True)
    embed.add_field(name="WoWDB", value=f"[View decoration]({item.url})", inline=True)
    if item.image:
        embed.set_thumbnail(url=item.image)
    if item.note:
        embed.description = text(item.note, 500)
    embed.set_footer(text=f"WoWDB Housing • Decor ID {item.id} • Details may change")
    return embed


class RefineModal(discord.ui.Modal, title="Refine decor search"):
    query = discord.ui.TextInput(label="Decoration name or keyword", min_length=2, max_length=100)

    def __init__(self, view):
        super().__init__()
        self.results = view
        self.query.default = view.query

    async def on_submit(self, interaction):
        await interaction.response.defer()
        async with self.results.busy:
            try:
                page = await self.results.client.search(str(self.query))
                if not page.hits:
                    await interaction.followup.send("No matching decorations. Try a shorter name.", ephemeral=True)
                    return
                self.results.query = str(self.query)
                self.results.page = page
                self.results.offset = 0
                self.results.rebuild()
                await interaction.edit_original_response(embed=self.results.embed(), view=self.results)
            except SiteError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)


class Results(discord.ui.View):
    def __init__(self, client: HousingClient, owner: int, query: str, page: SearchPage):
        super().__init__(timeout=180)
        self.client, self.owner, self.query, self.page = client, owner, query, page
        self.offset = 0
        self.message = None
        self.busy = asyncio.Lock()
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner:
            await interaction.response.send_message("Run /decor search to open your own search.", ephemeral=True)
            return False
        return True

    @property
    def visible(self):
        return self.page.hits[self.offset:self.offset + PAGE_SIZE]

    def embed(self):
        embed = discord.Embed(title="Housing decor search", color=0xB69250,
            description=f"Search: **{text(self.query, 200)}**\nChoose a decoration below, or refine your search.")
        embed.set_footer(text=f"WoWDB page {self.page.page} • Results {self.offset + 1}–"
                              f"{self.offset + len(self.visible)} on this page • Controls expire after 3 minutes")
        return embed

    def rebuild(self):
        self.clear_items()
        select = discord.ui.Select(placeholder="Choose a decoration…", row=0, options=[
            discord.SelectOption(label=hit.name[:100], value=str(hit.id), description=hit.category[:100])
            for hit in self.visible])
        choices = {str(h.id): h for h in self.visible}
        async def choose(interaction):
            selected = choices.get((interaction.data or {}).get("values", [""])[0])
            await interaction.response.defer(thinking=True, ephemeral=True)
            if selected is None:
                await interaction.followup.send("That selection expired. Please search again.", ephemeral=True)
                return
            try:
                item = await self.client.item(selected.url)
                await interaction.followup.send(embed=item_embed(item), ephemeral=True)
            except SiteError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
        select.callback = choose
        self.add_item(select)
        previous = discord.ui.Button(label="Previous", row=1,
            disabled=self.page.page == 1 and self.offset == 0)
        following = discord.ui.Button(label="Next", row=1,
            disabled=self.offset + PAGE_SIZE >= len(self.page.hits) and not self.page.has_next)
        refine = discord.ui.Button(label="Refine search", style=discord.ButtonStyle.primary, row=1)
        async def prev(interaction):
            await self.move(interaction, -1)
        async def nxt(interaction):
            await self.move(interaction, 1)
        async def refine_search(interaction):
            await interaction.response.send_modal(RefineModal(self))
        previous.callback, following.callback, refine.callback = prev, nxt, refine_search
        self.add_item(previous)
        self.add_item(following)
        self.add_item(refine)

    async def move(self, interaction, direction):
        await interaction.response.defer()
        async with self.busy:
            try:
                offset = self.offset + direction * PAGE_SIZE
                page = self.page
                if offset < 0:
                    if page.page == 1:
                        return
                    page = await self.client.search(self.query, page.page - 1)
                    offset = max(0, ((len(page.hits) - 1) // PAGE_SIZE) * PAGE_SIZE)
                elif offset >= len(page.hits):
                    if not page.has_next:
                        return
                    page = await self.client.search(self.query, page.page + 1)
                    offset = 0
                if not page.hits:
                    await interaction.followup.send("No more results. Please run a new search.", ephemeral=True)
                    return
                self.page, self.offset = page, offset
                self.rebuild()
                await interaction.edit_original_response(embed=self.embed(), view=self)
            except SiteError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        log.error("Result control failed (%s)", type(error).__name__)
        if interaction.response.is_done():
            await interaction.followup.send("Something went wrong. Run /decor search again.", ephemeral=True)
        else:
            await interaction.response.send_message("Something went wrong. Run /decor search again.", ephemeral=True)


class Decor(app_commands.Group):
    def __init__(self, housing):
        super().__init__(name="decor", description="Search WoWDB housing decorations")
        self.housing = housing

    async def show_search(self, interaction, query):
        page = await self.housing.search(query)
        if not page.hits:
            await interaction.followup.send("No matching decorations. Try a shorter name.")
            return
        view = Results(self.housing, interaction.user.id, query, page)
        view.message = await interaction.followup.send(embed=view.embed(), view=view, wait=True)

    @app_commands.command(name="search", description="Find decorations by name, keyword, or source")
    @app_commands.describe(query="For example: grave, chair, or Faarden")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def search(self, interaction: discord.Interaction, query: app_commands.Range[str, 2, 100]):
        await interaction.response.defer(thinking=True)
        try:
            await self.show_search(interaction, query)
        except SiteError as exc:
            await interaction.followup.send(str(exc))

    @app_commands.command(name="item", description="Show a decoration by exact name, decor ID, or WoWDB URL")
    @app_commands.describe(value="Example: Founder's Point Gravestone, 7582, or a housing.wowdb.com/decor/ URL")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def item(self, interaction: discord.Interaction, value: app_commands.Range[str, 1, 300]):
        await interaction.response.defer(thinking=True)
        try:
            value = value.strip().strip("<>")
            if value.isdecimal() or value.startswith(("https://", "http://", "/")):
                result = await self.housing.item(value)
            else:
                page = await self.housing.search(value)
                exact = [h for h in page.hits if h.name.casefold() == value.casefold()]
                if len(exact) == 1:
                    result = await self.housing.item(exact[0].url)
                elif len(page.hits) == 1 and not page.has_next:
                    result = await self.housing.item(page.hits[0].url)
                else:
                    await self.show_search(interaction, value)
                    return
            await interaction.followup.send(embed=item_embed(result))
        except SiteError as exc:
            await interaction.followup.send(str(exc))


class Bot(discord.Client):
    def __init__(self, housing, guild_id=None):
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none())
        self.housing, self.guild_id = housing, guild_id
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(Decor(housing))
        self.tree.on_error = self.command_error

    async def setup_hook(self):
        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        print(f"Logged in as {self.user}. Try /decor search query:grave")

    async def command_error(self, interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.0f} seconds before trying again."
        else:
            # Do not log interaction payloads or tokens.
            log.error("Slash command failed (%s)", type(error).__name__)
            message = "The request failed. Please try again or check the bot console."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def close(self):
        await self.housing.close()
        await super().close()


def main():
    load_dotenv(ROOT / ".env")
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token == "put_your_bot_token_here":
        raise SystemExit("Add your Discord bot token to .env first. See README.md.")
    guild = os.getenv("DISCORD_GUILD_ID", "").strip()
    if guild and (not guild.isdecimal() or int(guild) <= 0):
        raise SystemExit("DISCORD_GUILD_ID must be a numeric server ID or empty.")
    housing = HousingClient(os.getenv("WOWDB_TRANSPORT", "http"),
        os.getenv("WOWDB_HEADLESS", "false").lower() == "true", str(ROOT / ".browser-profile"))
    bot = Bot(housing, int(guild) if guild else None)
    try:
        bot.run(token)
    except discord.LoginFailure:
        raise SystemExit("Discord rejected the token. Reset it in the Developer Portal and update .env.")


if __name__ == "__main__":
    main()
