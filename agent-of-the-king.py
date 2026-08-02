import os
import re
import asyncio
import contextlib
from urllib.parse import quote
from typing import List, Dict, Any, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from rapidfuzz import process, fuzz

from availability_service import AvailabilityStore

# -----------------------------
# Config / startup
# -----------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
AVAILABILITY_WEB_URL = os.getenv("AVAILABILITY_WEB_URL", "http://127.0.0.1:8000/")
AVAILABILITY_EDITOR_WEB_URL = os.getenv("AVAILABILITY_EDITOR_WEB_URL", AVAILABILITY_WEB_URL)
SYNC_GUILD = discord.Object(id=int(DISCORD_GUILD_ID)) if DISCORD_GUILD_ID.isdigit() else None
# Optional allowlist (comma-separated channel IDs). Leave empty to allow everywhere.
ALLOWED_CHANNEL_IDS = set(
    int(x.strip()) for x in os.getenv("ALLOWED_CHANNEL_IDS", "").split(",") if x.strip().isdigit()
)

INTENTS = discord.Intents.default()
INTENTS.message_content = True  # Required to read message text
INTENTS.guilds = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)
TREE = bot.tree
STORE = AvailabilityStore()

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

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
TIME_SLOTS = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 30)]


def build_availability_grid_template() -> str:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    times = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 30)]
    header = "Time | " + " | ".join(days)
    divider = "---- | " + " | ".join(["---"] * len(days))
    rows = ["Availability Grid Template", header, divider]
    for slot in times:
        rows.append(f"{slot} | . | . | . | . | . | . | .")
    return "\n".join(rows)


class AvailabilityModal(discord.ui.Modal):
    def __init__(self) -> None:
        super().__init__(title="Availability Grid")
        grid_template = build_availability_grid_template()
        self.availability_input = discord.ui.TextInput(
            label="7 days × 48 half-hour slots",
            required=True,
            style=discord.TextStyle.paragraph,
            default=grid_template,
            placeholder="Enter your availability grid",
        )
        self.availability_input._value = grid_template
        self.availability_input._underlying.value = grid_template
        self.add_item(self.availability_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        grid_text = self.availability_input.value.strip()
        if not grid_text:
            await interaction.response.send_message("No availability grid was entered.", ephemeral=True)
            return

        lines = [line for line in grid_text.splitlines() if line.strip()]
        row_count = max(0, len(lines) - 2)
        await interaction.response.send_message(
            f"Availability grid received with {row_count} time rows.",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message(
            "Something went wrong while opening the availability form.",
            ephemeral=True,
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
async def sync_app_commands() -> None:
    try:
        if SYNC_GUILD is not None:
            synced = await TREE.sync(guild=SYNC_GUILD)
            print(f"Synced {len(synced)} guild app commands for guild {SYNC_GUILD.id}")
        else:
            synced = await TREE.sync()
            print(f"Synced {len(synced)} global app commands")
    except Exception as exc:
        print(f"Failed to sync app commands: {exc}")


@bot.event
async def on_ready():
    # Load cards once on startup, but do not block slash command registration if it fails.
    try:
        await load_cards()
    except Exception as exc:
        print(f"Failed to load card cache: {exc}")

    await sync_app_commands()
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

@TREE.command(name="availability", description="Open the browser-based availability editor")
async def availability_cmd(interaction: discord.Interaction):
    discord_user_id = str(interaction.user.id)
    display_name = interaction.user.display_name
    STORE.upsert_user_profile(discord_user_id, str(display_name or discord_user_id), source="availability_command")
    base_url = AVAILABILITY_EDITOR_WEB_URL.rstrip("/")
    redirect_url = f"{base_url}/availability/{discord_user_id}?display_name={quote(display_name)}"
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Open Availability Editor", url=redirect_url))
    await interaction.response.send_message(
        "Open your weekly availability editor:",
        view=view,
        ephemeral=True,
    )


def parse_session_length_hours(raw_value: str) -> float:
    value = float(raw_value.strip())
    if value <= 0:
        raise ValueError("Session length must be greater than 0")
    # Availability slots are 30-minute increments.
    half_hour_steps = round(value * 2)
    normalized = half_hour_steps / 2
    if abs(normalized - value) > 1e-9:
        raise ValueError("Session length must be in 0.5-hour increments")
    return normalized


def format_session_length_hours(value: float) -> str:
    return str(int(value)) if abs(value - int(value)) < 1e-9 else f"{value:g}"


class GameRoleSelect(discord.ui.RoleSelect):
    def __init__(self, game_name: str, game_type: str, session_length_hours: float) -> None:
        super().__init__(
            placeholder="Select participant role",
            min_values=1,
            max_values=1,
        )
        self.game_name = game_name
        self.game_type = game_type
        self.session_length_hours = session_length_hours

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        view = self.view
        if not isinstance(view, AddGameRolePickerView):
            await interaction.response.send_message("Role picker state is invalid.", ephemeral=True)
            return

        selected = self.values[0] if self.values else None
        if not isinstance(selected, discord.Role):
            await interaction.response.send_message("Please choose a valid server role.", ephemeral=True)
            return

        role = selected
        if role.is_default() or role.is_bot_managed():
            await interaction.response.send_message(
                "Please choose a non-bot server role.",
                ephemeral=True,
            )
            return

        role_id = str(role.id)
        role_name = role.name

        result = STORE.add_game(
            self.game_name,
            self.game_type,
            self.session_length_hours,
            role_id=role_id,
            role_name=role_name,
        )
        session_label = format_session_length_hours(self.session_length_hours)

        if result == "created":
            message = f"Game added: {self.game_name} [{self.game_type}, {session_label}h] with role <@&{role_id}>."
        elif result == "reactivated":
            message = f"Game reactivated: {self.game_name} [{self.game_type}, {session_label}h] with role <@&{role_id}>."
        elif result == "updated":
            message = f"Game updated: {self.game_name} [{self.game_type}, {session_label}h] now uses role <@&{role_id}>."
        else:
            message = f"Game is already active: {self.game_name} [{self.game_type}, {session_label}h] with role <@&{role_id}>."

        for child in view.children:
            child.disabled = True

        await interaction.response.edit_message(content=message, view=view)


class EditGameRoleSelect(discord.ui.RoleSelect):
    def __init__(
        self,
        current_name: str,
        new_name: str,
        new_game_type: str,
        new_session_length_hours: float,
    ) -> None:
        super().__init__(
            placeholder="Select participant role",
            min_values=1,
            max_values=1,
        )
        self.current_name = current_name
        self.new_name = new_name
        self.new_game_type = new_game_type
        self.new_session_length_hours = new_session_length_hours

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        view = self.view
        if not isinstance(view, EditGameRolePickerView):
            await interaction.response.send_message("Role picker state is invalid.", ephemeral=True)
            return

        selected = self.values[0] if self.values else None
        if not isinstance(selected, discord.Role):
            await interaction.response.send_message("Please choose a valid server role.", ephemeral=True)
            return

        role = selected
        if role.is_default() or role.is_bot_managed():
            await interaction.response.send_message(
                "Please choose a non-bot server role.",
                ephemeral=True,
            )
            return

        role_id = str(role.id)
        role_name = role.name

        result = STORE.update_game(
            self.current_name,
            self.new_name,
            self.new_game_type,
            self.new_session_length_hours,
            role_id=role_id,
            role_name=role_name,
        )
        session_label = format_session_length_hours(self.new_session_length_hours)

        if result == "updated":
            message = f"Game updated: {self.new_name} [{self.new_game_type}, {session_label}h] with role <@&{role_id}>."
        elif result == "name_conflict":
            message = "A game with that name already exists. Please choose a different name."
        else:
            message = "Could not find the game to update. Please refresh /admin list_games and try again."

        for child in view.children:
            child.disabled = True

        await interaction.response.edit_message(content=message, view=view)


class AddGameRolePickerView(discord.ui.View):
    def __init__(self, invoker_id: int, game_name: str, game_type: str, session_length_hours: float) -> None:
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.add_item(
            GameRoleSelect(
                game_name=game_name,
                game_type=game_type,
                session_length_hours=session_length_hours,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the admin who opened this form can submit it.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class EditGameRolePickerView(discord.ui.View):
    def __init__(
        self,
        invoker_id: int,
        current_name: str,
        new_name: str,
        new_game_type: str,
        new_session_length_hours: float,
    ) -> None:
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.add_item(
            EditGameRoleSelect(
                current_name=current_name,
                new_name=new_name,
                new_game_type=new_game_type,
                new_session_length_hours=new_session_length_hours,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the admin who opened this form can submit it.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class AddGameModal(discord.ui.Modal, title="Add Game"):
    game_name = discord.ui.TextInput(
        label="Game name",
        placeholder="The Scarlet Keys Winter '26",
        required=True,
        max_length=100,
    )
    game_type = discord.ui.TextInput(
        label="Game type",
        default="Arkham Horror LCG",
        required=True,
        max_length=100,
    )
    session_length_hours = discord.ui.TextInput(
        label="Session length (hours)",
        default="4",
        required=True,
        max_length=8,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        cleaned_game_name = str(self.game_name).strip()
        cleaned_game_type = str(self.game_type).strip()
        raw_session_length = str(self.session_length_hours).strip()
        if not cleaned_game_name:
            await interaction.response.send_message("Game name cannot be empty.", ephemeral=True)
            return
        if not cleaned_game_type:
            await interaction.response.send_message("Game type cannot be empty.", ephemeral=True)
            return
        try:
            session_length_hours = parse_session_length_hours(raw_session_length)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        view = AddGameRolePickerView(
            interaction.user.id,
            cleaned_game_name,
            cleaned_game_type,
            session_length_hours,
        )
        session_label = format_session_length_hours(session_length_hours)
        await interaction.response.send_message(
            f"Choose the participant role for **{cleaned_game_name}** ({cleaned_game_type}, {session_label}h):",
            view=view,
            ephemeral=True,
        )


class EditGameModal(discord.ui.Modal, title="Edit Game"):
    def __init__(
        self,
        current_name: str,
        current_game_type: str,
        current_session_length_hours: float,
        invoker_id: int,
    ) -> None:
        super().__init__()
        self.current_name = current_name
        self.invoker_id = invoker_id

        self.game_name = discord.ui.TextInput(
            label="Game name",
            default=current_name,
            required=True,
            max_length=100,
        )
        self.game_type = discord.ui.TextInput(
            label="Game type",
            default=current_game_type or "Arkham Horror LCG",
            required=True,
            max_length=100,
        )
        self.session_length_hours = discord.ui.TextInput(
            label="Session length (hours)",
            default=format_session_length_hours(current_session_length_hours),
            required=True,
            max_length=8,
        )
        self.add_item(self.game_name)
        self.add_item(self.game_type)
        self.add_item(self.session_length_hours)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the admin who opened this editor can submit it.", ephemeral=True)
            return

        cleaned_game_name = str(self.game_name).strip()
        cleaned_game_type = str(self.game_type).strip()
        raw_session_length = str(self.session_length_hours).strip()
        if not cleaned_game_name:
            await interaction.response.send_message("Game name cannot be empty.", ephemeral=True)
            return
        if not cleaned_game_type:
            await interaction.response.send_message("Game type cannot be empty.", ephemeral=True)
            return
        try:
            session_length_hours = parse_session_length_hours(raw_session_length)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        view = EditGameRolePickerView(
            invoker_id=self.invoker_id,
            current_name=self.current_name,
            new_name=cleaned_game_name,
            new_game_type=cleaned_game_type,
            new_session_length_hours=session_length_hours,
        )
        session_label = format_session_length_hours(session_length_hours)
        await interaction.response.send_message(
            f"Choose the participant role for **{cleaned_game_name}** ({cleaned_game_type}, {session_label}h):",
            view=view,
            ephemeral=True,
        )


class GameEditButton(discord.ui.Button):
    def __init__(self, invoker_id: int, game: dict[str, str | None]) -> None:
        name = (game.get("name") or "Unknown game").strip()
        game_type = (game.get("game_type") or "Unknown type").strip()
        role_name = (game.get("role_name") or "No role").strip()
        session_length_hours = float(game.get("session_length_hours") or 4)
        session_label = format_session_length_hours(session_length_hours)
        label = f"{name} | {game_type} {session_label}h | {role_name}"
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.secondary,
        )
        self.invoker_id = invoker_id
        self.game_name = name
        self.game_type = game_type
        self.session_length_hours = session_length_hours

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the admin who opened this list can edit these games.", ephemeral=True)
            return

        await interaction.response.send_modal(
            EditGameModal(
                current_name=self.game_name,
                current_game_type=self.game_type,
                current_session_length_hours=self.session_length_hours,
                invoker_id=self.invoker_id,
            )
        )


class GameListView(discord.ui.View):
    def __init__(self, invoker_id: int, games: list[dict[str, str | None]]) -> None:
        super().__init__(timeout=180)
        self.invoker_id = invoker_id

        for game in games[:25]:
            self.add_item(GameEditButton(invoker_id=invoker_id, game=game))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the admin who opened this list can use these buttons.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class HeatmapGameSelect(discord.ui.Select):
    def __init__(self, games: list[dict[str, str | None]]) -> None:
        options: list[discord.SelectOption] = []
        for game in games[:25]:
            game_name = str(game.get("name") or "Unknown game")
            game_type = str(game.get("game_type") or "Unknown type")
            role_name = str(game.get("role_name") or "No role")
            options.append(
                discord.SelectOption(
                    label=f"{game_name} [{game_type}]"[:100],
                    description=f"Role: {role_name}"[:100],
                    value=game_name,
                )
            )

        super().__init__(
            placeholder="Choose a game",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.games = games

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        if not isinstance(self.view, HeatmapGamePickerView):
            await interaction.response.send_message("Heatmap picker state is invalid.", ephemeral=True)
            return

        selected_game_name = self.values[0]
        game = next((item for item in self.games if item.get("name") == selected_game_name), None)
        if not game:
            await interaction.response.send_message("Selected game was not found.", ephemeral=True)
            return

        # Reload the selected game from storage at click-time to avoid stale picker snapshots.
        latest_game = next(
            (item for item in STORE.list_games(active_only=True) if item.get("name") == selected_game_name),
            None,
        )
        if latest_game:
            game = latest_game

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        role_id = str(game.get("role_id") or "")
        if not role_id.isdigit():
            await interaction.response.send_message("Selected game has no valid role configured.", ephemeral=True)
            return

        role = interaction.guild.get_role(int(role_id))
        if not role:
            await interaction.response.send_message("The role configured for this game no longer exists.", ephemeral=True)
            return

        participants_by_id: dict[str, str] = {}
        for member in role.members:
            if member.bot:
                continue
            member_id = str(member.id)
            member_name = str(member.display_name or member.name or member_id).strip() or member_id
            participants_by_id[member_id] = member_name

        role_participants = sorted(participants_by_id.keys())
        participant_users = [
            {
                "id": participant_id,
                "name": participants_by_id[participant_id],
            }
            for participant_id in role_participants
        ]
        STORE.upsert_user_profiles(participant_users, source="heatmap_role_members")
        context_id = STORE.create_heatmap_context(
            game_name=str(game.get("name") or ""),
            participant_user_ids=role_participants,
            session_length_hours=float(game.get("session_length_hours") or 4),
            guild_id=str(interaction.guild.id),
            role_id=role_id,
            participant_users=participant_users,
        )

        session_length_hours = float(game.get("session_length_hours") or 4)
        presumptive_window = STORE.get_best_group_window_for_heatmap_selection(
            role_participants,
            session_length_hours=session_length_hours,
        )

        base_url = AVAILABILITY_WEB_URL.rstrip("/")
        heatmap_url = f"{base_url}/heatmap?context={context_id}"
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Heatmap", url=heatmap_url))

        available_users = set(STORE.list_user_ids())
        participants_with_availability_count = len(set(role_participants) & available_users)
        group_b_count = len(available_users - set(role_participants))
        game_type = str(game.get("game_type") or "Unknown type")
        session_label = format_session_length_hours(session_length_hours)

        if presumptive_window:
            if presumptive_window.get("is_full_match"):
                window_copy = (
                    f"Best presumptive {session_label}-hour participant window (matching initial heatmap selection): "
                    f"{presumptive_window['day']} {presumptive_window['window_label']} "
                    f"(all {presumptive_window['total_members']} members)."
                )
            else:
                window_copy = (
                    f"Best presumptive {session_label}-hour participant window (matching initial heatmap selection): "
                    f"{presumptive_window['day']} {presumptive_window['window_label']} "
                    f"({presumptive_window['matching_count']}/{presumptive_window['total_members']} members)."
                )
        else:
            window_copy = (
                f"No strong shared {session_label}-hour window is available for the initially selected players."
            )

        await interaction.response.send_message(
            (
                f"Heatmap ready for **{selected_game_name}** ({game_type}). "
                f"Game participants with saved availability: {participants_with_availability_count}. "
                f"Additional users with saved availability: {group_b_count}. "
                f"{window_copy}"
            ),
            view=view,
            ephemeral=True,
        )


class HeatmapGamePickerView(discord.ui.View):
    def __init__(self, invoker_id: int, games: list[dict[str, str | None]]) -> None:
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.add_item(HeatmapGameSelect(games=games))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the user who opened this picker can use it.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class RemoveGameSelect(discord.ui.Select):
    def __init__(self, games: list[dict[str, str | None]]) -> None:
        options: list[discord.SelectOption] = []
        for game in games[:25]:
            game_name = str(game.get("name") or "Unknown game")
            game_type = str(game.get("game_type") or "Unknown type")
            role_name = str(game.get("role_name") or "No role")
            options.append(
                discord.SelectOption(
                    label=game_name[:100],
                    description=f"{game_type} | Role: {role_name}"[:100],
                    value=game_name,
                )
            )

        super().__init__(
            placeholder="Choose a game to remove",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        if not isinstance(self.view, RemoveGamePickerView):
            await interaction.response.send_message("Remove-game picker state is invalid.", ephemeral=True)
            return

        selected_game_name = self.values[0]
        confirm_view = RemoveGameConfirmView(
            invoker_id=self.view.invoker_id,
            game_name=selected_game_name,
        )
        await interaction.response.edit_message(
            content=(
                f"Confirm removal of **{selected_game_name}**?\n"
                "This marks the game inactive (it is not deleted)."
            ),
            view=confirm_view,
        )


class RemoveGameConfirmView(discord.ui.View):
    def __init__(self, invoker_id: int, game_name: str) -> None:
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.game_name = game_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the admin who opened this prompt can use it.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Confirm Remove", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            result = STORE.remove_game(self.game_name)
        except ValueError as exc:
            await interaction.response.edit_message(content=str(exc), view=None)
            return

        for child in self.children:
            child.disabled = True

        if result == "deactivated":
            message = f"Game marked inactive: {self.game_name.strip()}"
        elif result == "already_inactive":
            message = f"Game is already inactive: {self.game_name.strip()}"
        else:
            message = f"Game not found: {self.game_name.strip()}"

        await interaction.response.edit_message(content=message, view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Removal cancelled.", view=self)


class RemoveGamePickerView(discord.ui.View):
    def __init__(self, invoker_id: int, games: list[dict[str, str | None]]) -> None:
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.add_item(RemoveGameSelect(games=games))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the admin who opened this prompt can use it.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


def is_server_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False

    member = interaction.user
    if isinstance(member, discord.Member):
        return member.guild_permissions.administrator

    permissions = getattr(interaction, "permissions", None)
    return bool(permissions and permissions.administrator)

admin_group = app_commands.Group(name="admin", description="Admin tools for managing games and availability")

@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@admin_group.command(name="add_game", description="Add a game to the available list")
async def admin_add_game_cmd(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        await interaction.response.send_message("Only server administrators can use this command.", ephemeral=True)
        return

    await interaction.response.send_modal(AddGameModal())

@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@admin_group.command(name="remove_game", description="Remove a game from the available list")
async def admin_remove_game_cmd(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        await interaction.response.send_message("Only server administrators can use this command.", ephemeral=True)
        return

    games = STORE.list_games(active_only=True)
    if not games:
        await interaction.response.send_message("No active games are configured.", ephemeral=True)
        return

    view = RemoveGamePickerView(invoker_id=interaction.user.id, games=games)
    extra_note = "" if len(games) <= 25 else " Showing first 25 games."
    await interaction.response.send_message(
        f"Choose a game to remove.{extra_note}",
        view=view,
        ephemeral=True,
    )

@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@admin_group.command(name="list_games", description="List the currently available games")
async def admin_list_games_cmd(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        await interaction.response.send_message("Only server administrators can use this command.", ephemeral=True)
        return

    games = STORE.list_games(active_only=True)
    if not games:
        await interaction.response.send_message("No active games are configured.", ephemeral=True)
        return

    view = GameListView(invoker_id=interaction.user.id, games=games)
    extra_note = "" if len(games) <= 25 else "\nShowing first 25 games."
    await interaction.response.send_message(
        f"Select a game to edit.{extra_note}",
        view=view,
        ephemeral=True,
    )

TREE.add_command(admin_group)

@TREE.command(name="heatmap", description="Show availability heatmap for a game")
async def heatmap_cmd(interaction: discord.Interaction):
    games = STORE.list_games(active_only=True)
    if not games:
        await interaction.response.send_message("No active games are configured yet.", ephemeral=True)
        return

    view = HeatmapGamePickerView(invoker_id=interaction.user.id, games=games)
    extra_note = "" if len(games) <= 25 else " Showing first 25 games."
    await interaction.response.send_message(
        f"Choose a game for the heatmap.{extra_note}",
        view=view,
        ephemeral=True,
    )

@TREE.command(name="hi", description="Introduce the bot")
async def hi_cmd(interaction: discord.Interaction):
    help_text = (
        "Agent of the King - Arkham LCG helper\n\n"
        "USAGE\n"
        "  /hi\n"
        "  /availability\n"
        "  /heatmap\n"
        "  /reload_cards\n"
        "  /sync_commands\n\n"
        "DESCRIPTION\n"
        "  Discord bot for Arkham Horror LCG card/deck lookup and weekly availability planning.\n\n"
        "COMMANDS\n"
        "  /availability      Open your personal weekly availability editor link.\n"
        "  /heatmap           Open the shared weekly heatmap view.\n"
        "  /reload_cards      Refresh ArkhamDB card cache.\n"
        "  /sync_commands     Force slash-command sync with Discord.\n"
        "  /admin add_game    Admin only: add or reactivate a game.\n"
        "  /admin remove_game Admin only: mark a game inactive.\n"
        "  /admin list_games  Admin only: list active games.\n\n"
        "MESSAGE FEATURES\n"
        "  [[card name]]                     Lookup card by name.\n"
        "  [[Card Name (0)]]                 Filter by exact XP level.\n"
        "  [[Card Name (u)]]                 Prefer upgraded versions.\n"
        "  https://arkhamdb.com/deck/view/... Expand deck into embeds.\n"
        "  https://arkhamdb.com/decklist/...  Expand decklist into embeds.\n\n"
        "EXAMPLES\n"
        "  [[Deduction]]\n"
        "  [[.41 Derringer (0)]]\n"
        "  [[Shrivelling (u)]]\n"
        "  /availability\n"
        "  /heatmap"
    )
    await interaction.response.send_message(f"```\n{help_text}\n```", ephemeral=True)


@TREE.command(name="sync_commands", description="Force a slash-command sync with Discord")
async def sync_commands_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await sync_app_commands()
        await interaction.followup.send(
            "Slash commands were synced. They may take a moment to appear in Discord.",
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(f"Sync failed: {exc}", ephemeral=True)


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

