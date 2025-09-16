import os
import re
import asyncio
import contextlib
from typing import List, Dict, Any, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from rapidfuzz import process, fuzz

# -----------------------------
# Config / startup
# -----------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
# Optional allowlist (comma-separated channel IDs). Leave empty to allow everywhere.
ALLOWED_CHANNEL_IDS = set(
    int(x.strip()) for x in os.getenv("ALLOWED_CHANNEL_IDS", "").split(",") if x.strip().isdigit()
)

INTENTS = discord.Intents.default()
INTENTS.message_content = True  # Required to read message text
INTENTS.guilds = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)
TREE = bot.tree

# ArkhamDB cache
CARDS: List[Dict[str, Any]] = []
CARDS_URL = "https://www.arkhamdb.com/api/public/cards?encounter=1"

# Name index for fuzzy matching
NAME_INDEX: Dict[str, List[Dict[str, Any]]] = {}
NAME_KEYS: List[str] = []

# Limits
MAX_CARD_MATCHES = 8  # parity with your Reddit bot
EMBEDS_PER_MESSAGE_LIMIT = 10

# Footer
FOOTER_TEXT = "I am a bot • GitHub: hardingalexh/agent-of-the-king-reddit"

# Regex
CARD_TOKEN_RE = re.compile(r"\[\[(.+?)\]\]")
DECK_URL_RE = re.compile(
    r"(https?://)?(www\.)?arkhamdb\.com/(deck/view|decklist/view)/([^\s\])\)]*)",
    re.IGNORECASE,
)

# -----------------------------
# Utilities
# -----------------------------


def process_text(text: Optional[str]) -> str:
    if not text:
        return ""
    # ArkhamDB formatting -> Discord
    text = text.replace("[[", "**").replace("]]", "**")
    text = text.replace("<b>", "**").replace("</b>", "**")
    text = text.replace("<i>", "_").replace("</i>", "_")
    # Reddit-style "two spaces + newline" isn't needed; Discord uses \n
    return text


def process_symbols(card: Dict[str, Any]) -> str:
    stats = ["Willpower", "Intellect", "Combat", "Agility", "Wild"]
    pieces = []
    for stat in stats:
        key = f"skill_{stat.lower()}"
        if card.get(key):
            pieces.append(f"{stat} ×{card.get(key)}")
    return ", ".join(pieces)


def card_to_embed(card: Dict[str, Any]) -> discord.Embed:
    name = card.get("name", "Unknown")
    url = card.get("url") or ""
    xp = card.get("xp")
    title = f"{name}" + (f" ({xp})" if xp else "")
    embed = discord.Embed(title=title, url=url, description=process_text(card.get("text")))
    # Image
    if card.get("imagesrc"):
        embed.set_image(url=f"https://www.arkhamdb.com{card.get('imagesrc')}")
    # Fields
    line1 = []
    if card.get("faction") != "Mythos" and card.get("faction_name"):
        line1.append(f"Faction: _{card['faction_name']}_")
    if card.get("cost") is not None:
        line1.append(f"Cost: _{card['cost']}_")
    if card.get("type_name"):
        line1.append(f"Type: _{card['type_name']}_")
    if card.get("slot"):
        line1.append(f"Slot: _{card['slot']}_")
    if line1:
        embed.add_field(name="\u200b", value=" • ".join(line1), inline=False)

    if card.get("traits"):
        embed.add_field(name="Traits", value=f"_{card['traits']}_", inline=False)

    icons = process_symbols(card)
    if icons:
        embed.add_field(name="Test Icons", value=icons, inline=False)

    # Health/Sanity or Enemy stats
    if card.get("type_code") == "enemy":
        stats = []
        if card.get("enemy_fight") is not None:
            stats.append(f"Fight: {card['enemy_fight']}")
        if card.get("enemy_evade") is not None:
            stats.append(f"Evade: {card['enemy_evade']}")
        if card.get("health") is not None:
            hp = f"{card['health']}" + (" per investigator" if card.get("health_per_investigator") else "")
            stats.append(f"Health: {hp}")
        if card.get("enemy_damage") is not None:
            stats.append(f"Damage: {card['enemy_damage']}")
        if card.get("enemy_horror") is not None:
            stats.append(f"Horror: {card['enemy_horror']}")
        if card.get("victory") is not None:
            stats.append(f"Victory {card['victory']}")
        if stats:
            embed.add_field(name="Enemy", value=" • ".join(stats), inline=False)
    else:
        if card.get("health") is not None or card.get("sanity") is not None:
            hs = []
            if card.get("health") is not None:
                hs.append(f"Health: {card['health']}")
            if card.get("sanity") is not None:
                hs.append(f"Sanity: {card['sanity']}")
            embed.add_field(name="\u200b", value=" • ".join(hs), inline=False)

    embed.set_footer(text=FOOTER_TEXT)
    return embed


def chunk_embeds(embeds: List[discord.Embed], size: int = EMBEDS_PER_MESSAGE_LIMIT):
    for i in range(0, len(embeds), size):
        yield embeds[i : i + size]


def set_footer_on_all(embeds: List[discord.Embed]) -> None:
    for e in embeds:
        if not e.footer:
            e.set_footer(text=FOOTER_TEXT)


def parse_level_search(term: str):
    """
    Supports 'Card Name (u)' for any upgraded, or '(2)' for exact level.
    Returns (search_term:str, level_filter: Optional[callable])
    """
    m = re.search(r"\((.+?)\)$", term.strip())
    if not m:
        return term.strip().lower(), None
    level = m.group(1).strip()
    search_term = term[: m.span()[0]].strip().lower()

    def level_filter(card: Dict[str, Any]) -> bool:
        xp = card.get("xp", 0) or 0
        if level.lower() == "u":
            return (search_term in card.get("name", "").lower()) and xp > 0
        try:
            n = int(level)
            return (search_term in card.get("name", "").lower()) and xp == n
        except ValueError:
            return search_term in card.get("name", "").lower()

    return search_term, level_filter


def _norm(s: str) -> str:
    # Lowercase and strip non-alphanumerics so "Lucky!" == "lucky"
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def _build_name_index(cards: List[Dict[str, Any]]):
    """Map normalized name -> list of card dicts (all printings)."""
    idx: Dict[str, List[Dict[str, Any]]] = {}
    for c in cards:
        n = _norm(c.get('name') or '')
        if not n:
            continue
        idx.setdefault(n, []).append(c)
    return idx


def _refresh_name_index():
    global NAME_INDEX, NAME_KEYS
    NAME_INDEX = _build_name_index(CARDS)
    NAME_KEYS = list(NAME_INDEX.keys())


def find_matching_cards(queries: List[str]) -> List[Dict[str, Any]]:
    """
    Matching order per token:
    1) Exact name (normalized) -> if no (level), pick lowest XP printing; else include all matching level.
    2) Substring fallback -> one lowest-XP printing per distinct name.
    3) Fuzzy fallback -> best normalized name over NAME_KEYS, threshold 80; pick lowest XP (respect level if provided).
    """
    matches: List[Dict[str, Any]] = []
    seen_codes = set()

    for q in queries:
        q = q.strip()
        if not q:
            continue

        base, level_fn = parse_level_search(q)
        base_norm = _norm(base)

        # --- EXACT NAME PATH (normalized, e.g., "Lucky!" == "lucky") ---
        exacts = [c for c in CARDS if _norm(c.get('name') or '') == base_norm and (not level_fn or level_fn(c))]
        if exacts:
            # No level given -> lowest XP printing only; else include all passing level filter
            picks = [min(exacts, key=lambda c: (c.get('xp') or 0))] if not level_fn else exacts
            for c in picks:
                code = c.get('code')
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    matches.append(c)
            continue  # prefer exact; skip substring for this token

        # --- SUBSTRING FALLBACK (return one lowest-XP per distinct name) ---
        by_name_lowest: Dict[str, Dict[str, Any]] = {}
        for c in CARDS:
            name = (c.get('name') or '')
            if base in name.lower() and (not level_fn or level_fn(c)):
                key = _norm(name)
                cur = by_name_lowest.get(key)
                if cur is None or (c.get('xp') or 0) < (cur.get('xp') or 0):
                    by_name_lowest[key] = c

        if by_name_lowest:
            for c in by_name_lowest.values():
                code = c.get('code')
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    matches.append(c)
            continue

        # --- FUZZY FALLBACK (only if nothing else matched) ---
        if NAME_KEYS:
            best = process.extractOne(base_norm, NAME_KEYS, scorer=fuzz.token_set_ratio)
            if best and best[1] >= 80:  # tweak threshold 75–85 as desired
                best_key = best[0]
                variants = NAME_INDEX.get(best_key, [])
                if level_fn:
                    variants = [c for c in variants if level_fn(c)]
                if variants:
                    pick = min(variants, key=lambda c: (c.get('xp') or 0))
                    code = pick.get('code')
                    if code and code not in seen_codes:
                        seen_codes.add(code)
                        matches.append(pick)

    return matches


def is_big_response(card_count: int, deck_embed_count: int = 0) -> bool:
    # Thread threshold: >3 cards or deck output likely spanning multiple messages
    return card_count > 3 or deck_embed_count > 10


# -----------------------------
# ArkhamDB I/O
# -----------------------------
async def fetch_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(
        url,
        allow_redirects=True,
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"User-Agent": "AgentOfTheKing/1.0"},
    ) as resp:
        resp.raise_for_status()
        # Accept JSON even if Content-Type header is off
        return await resp.json(content_type=None)


async def load_cards():
    global CARDS
    async with aiohttp.ClientSession() as session:
        CARDS = await fetch_json(session, CARDS_URL)
    _refresh_name_index()


async def fetch_deck(deck_url_match: re.Match) -> Dict[str, Any]:
    """
    Accepts a regex match against DECK_URL_RE, returns deck JSON and a type ('deck' | 'decklist') and the id.
    """
    kind = deck_url_match.group(3).lower()  # 'deck/view' or 'decklist/view'
    raw_tail = deck_url_match.group(4)
    deck_id = (raw_tail or "").split("/")[0].split("]")[0].split(")")[0]
    api_url = None
    deck_type = None
    if "deck/view" in kind:
        deck_type = "deck"
        api_url = f"https://arkhamdb.com/api/public/deck/{deck_id}"
    else:
        deck_type = "decklist"
        api_url = f"https://arkhamdb.com/api/public/decklist/{deck_id}"

    async with aiohttp.ClientSession() as session:
        data = await fetch_json(session, api_url)
        return {"type": deck_type, "id": deck_id, "json": data}


def build_deck_embeds(deck: Dict[str, Any]) -> List[discord.Embed]:
    data = deck["json"]
    investigator_code = data.get("investigator_code")
    gators = [c for c in CARDS if c.get("code") == investigator_code]
    gator = gators[0] if gators else {}
    inv_name = gator.get("name", "Investigator")
    deck_name = data.get("name", "")
    version = data.get("version", "")

    header = discord.Embed(
        title=f"{inv_name}: {deck_name} {version}",
        description="",
    )
    # Link back
    if deck["type"] == "deck":
        header.url = f"https://arkhamdb.com/deck/view/{deck['id']}"
    else:
        header.url = f"https://arkhamdb.com/decklist/view/{deck['id']}"
    header.set_footer(text=FOOTER_TEXT)

    embeds = [header]

    # Gather cards used in deck
    slots = data.get("slots", {}) or {}
    deck_cards = [c for c in CARDS if (c.get("code") or "") in slots.keys()]

    # Categories
    categories = ["Asset", "Permanent", "Event", "Skill", "Treachery", "Enemy"]
    for category in categories:
        if category == "Permanent":
            cat_cards = [c for c in deck_cards if c.get("permanent") is True]
        else:
            cat_cards = [
                c
                for c in deck_cards
                if (c.get("type_code", "") == category.lower() and not c.get("permanent"))
            ]
        if not cat_cards:
            continue

        # For assets: group by slot
        if category == "Asset":
            cat_cards.sort(key=lambda e: e.get("slot", "zzzzzz"))

        embed = discord.Embed(title=f"{category}s")
        parts: List[str] = []

        if category == "Asset":
            last_slot = None
            for card in cat_cards:
                qty = slots.get(card.get("code"), 1)
                line = f"{qty} × [{card.get('name','')}]" + (f" ({card.get('xp')})" if card.get("xp") else "")
                line += f" ({card.get('url','')})"
                slot = card.get("slot", "Other")
                if slot != last_slot:
                    parts.append(f"\n**{slot}:**")
                    last_slot = slot
                parts.append(f"- {line}")
        else:
            for card in cat_cards:
                qty = slots.get(card.get("code"), 1)
                line = f"{qty} × [{card.get('name','')}]" + (f" ({card.get('xp')})" if card.get("xp") else "")
                line += f" ({card.get('url','')})"
                parts.append(f"- {line}")

        # Discord field length safety; split across multiple embeds if huge
        text = "\n".join(parts)
        # 4096 char cap for description; if too long, break into chunks of ~1500 safely
        if len(text) <= 3800:
            embed.description = text
            embed.set_footer(text=FOOTER_TEXT)
            embeds.append(embed)
        else:
            chunks = []
            buf = []
            count = 0
            for line in parts:
                if count + len(line) + 1 > 1500:
                    chunks.append("\n".join(buf))
                    buf = []
                    count = 0
                buf.append(line)
                count += len(line) + 1
            if buf:
                chunks.append("\n".join(buf))
            for i, ch in enumerate(chunks, 1):
                e = discord.Embed(title=f"{category}s [{i}/{len(chunks)}]", description=ch)
                e.set_footer(text=FOOTER_TEXT)
                embeds.append(e)

    return embeds


async def send_embeds_in_batches(target: discord.abc.Messageable, embeds: List[discord.Embed]):
    set_footer_on_all(embeds)
    for batch in chunk_embeds(embeds):
        await target.send(embeds=batch)


# -----------------------------
# Bot events / commands
# -----------------------------
@bot.event
async def on_ready():
    # Load cards once on startup
    await load_cards()
    try:
        await TREE.sync()
    except Exception:
        pass
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    # Ignore self/bots
    if message.author.bot:
        return

    # Allowlist (optional)
    if ALLOWED_CHANNEL_IDS and message.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    content = message.content or ""

    # Extract deck URLs (first, since they don't count against "no results" for cards)
    deck_match = DECK_URL_RE.search(content)

    # Extract card searches [[...]]
    card_tokens = CARD_TOKEN_RE.findall(content)

    # Nothing for us to do
    if not deck_match and not card_tokens:
        return

    # Decide target: same channel or a thread
    thread: Optional[discord.Thread] = None

    # Build card embeds
    card_embeds: List[discord.Embed] = []
    if card_tokens:
        matches = find_matching_cards(card_tokens)
        if len(matches) > MAX_CARD_MATCHES:
            await message.reply("Your search returned more than 8 cards, and that's my hand limit. Take 1 horror.")
            return
        if len(matches) == 0 and not deck_match:
            await message.reply("Your search returned no results. Take 1 horror.")
            return
        card_embeds = [card_to_embed(m) for m in matches]

    # Build deck embeds (if any)
    deck_embeds: List[discord.Embed] = []
    if deck_match:
        try:
            deck = await fetch_deck(deck_match)
            deck_embeds = build_deck_embeds(deck)
        except Exception:
            await message.reply("Something went wrong attempting to retrieve your deck from ArkhamDB. Take 1 horror.")
            deck_embeds = []

    make_thread = is_big_response(len(card_embeds), len(deck_embeds))
    target: discord.abc.Messageable = message.channel

    if make_thread and isinstance(message.channel, discord.TextChannel):
        try:
            name_hint = None
            if deck_embeds:
                name_hint = (deck_embeds[0].title or "arkhamdb").strip()[:80]
            elif card_embeds:
                name_hint = (card_embeds[0].title or "arkhamdb").strip()[:80]
            thread = await message.create_thread(name=f"arkhamdb: {name_hint or 'results'}")
            target = thread
        except Exception:
            # Fallback: stay in channel
            target = message.channel

    # Send results
    try:
        if deck_embeds:
            await send_embeds_in_batches(target, deck_embeds)
        if card_embeds:
            await send_embeds_in_batches(target, card_embeds)
        # Light reaction as ACK
        with contextlib.suppress(Exception):
            await message.add_reaction("🃏")
    except Exception as e:
        await message.reply(f"Failed to send response: {e}")


# Optional: simple slash command to reload cards cache
@TREE.command(name="reload_cards", description="Reload ArkhamDB card cache")
async def reload_cards_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await load_cards()
        await interaction.followup.send("Card cache reloaded.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Failed: {e}", ephemeral=True)


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN in environment/.env")
    bot.run(DISCORD_TOKEN)

