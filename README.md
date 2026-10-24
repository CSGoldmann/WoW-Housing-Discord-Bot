# WoWDB Housing Discord Bot

Search World of Warcraft housing decorations directly from Discord using data
from [WoWDB Housing](https://housing.wowdb.com/).

## Features

- Search by decoration name, keyword, or a source indexed by WoWDB.
- Browse matching decorations using dropdown menus and Previous/Next buttons.
- Refine your search without starting a new command.
- Look up a specific decoration by name, decor ID, or WoWDB Housing link.
- View compact item cards with an image, name, decor ID, category, acquisition
  sources, purchase costs, housing budget, and a link to the original entry.
- See multiple sources, vendor-specific prices, and availability notices when listed.

## How to use

The bot must be installed in your Discord server and running. Type `/decor` in
a channel, choose a command, and fill in the field Discord displays.

| Command | Description | Example |
| --- | --- | --- |
| `/decor search` | Search by name, keyword, or source. | `/decor search query:grave` |
| `/decor item` | Look up a decoration by name, decor ID, or link. | `/decor item value:7582` |

### Search for decorations

```text
/decor search query:grave
/decor search query:chair
/decor search query:Faarden
```

1. Run a search and open the dropdown to choose a decoration.
2. Use **Previous** and **Next** to browse more results.
3. Choose **Refine search** to enter another keyword or a more specific name.

Dropdowns show up to 12 decorations at a time. Pagination continues across
WoWDB's result pages. The person who started a search controls its buttons and
dropdown; other members can start their own searches.

Details opened from a dropdown are visible only to the person who selected the
item. Controls expire after three minutes of inactivity; run the search again
to get fresh controls.

### Look up a specific decoration

**By name:**

```text
/decor item value:Founder's Point Gravestone
```

**By decor ID:**

```text
/decor item value:7582
```

**By WoWDB Housing link:**

```text
/decor item value:https://housing.wowdb.com/decor/7582/founders-point-gravestone-7582/
```

A direct lookup posts the item card in the channel. If a name does not resolve
to one clear match, the bot opens the search picker instead.

Use the **Decor ID** on an item card to find the same decoration again. It is
the number in the WoWDB Housing `/decor/ID/` address and can differ from the
underlying WoW inventory item ID.

### Item information

| Field | What it shows |
| --- | --- |
| Name and image | Decoration name and thumbnail, when available. |
| Decor ID | The ID you can use with `/decor item`. |
| Category | Category and subcategory. |
| Acquisition | Listed vendors, quests, crafting, achievements, or other sources. |
| Purchase cost | Listed prices and currencies for each source. |
| Housing budget | The decoration's housing budget cost, separate from its purchase price. |
| WoWDB link | The original page with full details. |

Missing prices and sources are marked **Not listed on WoWDB**. Long details are
shortened to fit Discord; open the WoWDB link for the complete entry.

## Hosting wanted

This repository shares the bot's source code. Downloading it does not install a
running bot in Discord. A host must run the application and provide their own
Discord bot credentials.

I'm looking for someone who can host the bot and help keep it available to the
community. If you can help with hosting or maintenance, please open an issue
in this repository.

## Notes

Item information comes from [WoWDB Housing](https://housing.wowdb.com/). Results
depend on the site's data and availability. If searches fail, check the source
website or try again later.

This is a community project, not an official Blizzard or WoWDB bot. World of
Warcraft and its associated assets belong to their respective owners.
