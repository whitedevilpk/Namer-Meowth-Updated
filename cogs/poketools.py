"""PokeTools cog — spawn rates, shiny rates, and message utilities"""
import csv
import io
import math
import re
import datetime
import discord
import aiohttp
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict
from utils import find_pokemon_by_name_flexible, load_pokemon_data, normalize_pokemon_name
from config import EMBED_COLOR

NO_MENTIONS = discord.AllowedMentions.none()


# ------------------------------------------------------------------ #
#  Components V2 helpers                                               #
# ------------------------------------------------------------------ #
# Messages sent with the IS_COMPONENTS_V2 flag have NO embeds and NO content.
# Their text lives in a component tree (Container -> Section -> TextDisplay ...)
# on `message.components`. Requires discord.py 2.6+.

def _component_texts(components) -> list[str]:
    """Recursively collect the text of every TextDisplay in a component tree."""
    texts: list[str] = []
    for comp in components or []:
        content = getattr(comp, "content", None)
        if isinstance(content, str) and content:
            texts.append(content)
        texts.extend(_component_texts(getattr(comp, "children", None)))
        accessory = getattr(comp, "accessory", None)
        if accessory is not None:
            texts.extend(_component_texts([accessory]))
    return texts


def _is_v2_message(message: discord.Message) -> bool:
    return bool(_component_texts(getattr(message, "components", None)))

# ------------------------------------------------------------------ #
#  Spawn rate data                                                     #
# ------------------------------------------------------------------ #

# Using the non-pinned raw URL so that reloading always pulls the latest revision
SPAWN_RATE_CSV_URL = (
    "https://gist.github.com/WitherredAway/1bc525b05f4cd52555a2a18c331e0cf9"
    "/raw/pokemon_chances.csv"
)

# Module-level cache so we only fetch once per bot session
_spawn_rate_cache: Optional[Dict[str, dict]] = None

# Shiny rate constants
BASE_SHINY_RATE = 4096
SHINY_CHARM_MULTIPLIER = 1.20


async def fetch_spawn_rates(session: aiohttp.ClientSession = None, force: bool = False) -> Dict[str, dict]:
    global _spawn_rate_cache
    if _spawn_rate_cache is not None and not force:
        return _spawn_rate_cache

    if session is not None:
        async with session.get(SPAWN_RATE_CSV_URL) as response:
            response.raise_for_status()
            text = await response.text(encoding="utf-8")
    else:
        async with aiohttp.ClientSession() as _tmp_session:
            async with _tmp_session.get(SPAWN_RATE_CSV_URL) as response:
                response.raise_for_status()
                text = await response.text(encoding="utf-8")

    reader = csv.DictReader(io.StringIO(text))
    data: Dict[str, dict] = {}
    for row in reader:
        raw_name = row.get("Pokemon", "").strip()
        if not raw_name:
            continue
        key = normalize_pokemon_name(raw_name).lower()
        data[key] = {
            "dex": row.get("Dex", "").strip(),
            "name": raw_name,
            "chance": row.get("Chance", "").strip(),
            "chance_pct": row.get("Chance percentage", "").strip(),
        }

    _spawn_rate_cache = data
    return data


def find_spawn_rate(search_name: str, spawn_data: Dict[str, dict]) -> Optional[dict]:
    normalized = normalize_pokemon_name(search_name).lower()
    return spawn_data.get(normalized)


def find_spawn_rate_family(base_english_name: str, spawn_data: Dict[str, dict]) -> list[dict]:
    """
    Return all spawn-rate entries whose name contains `base_english_name` as a word.
    For example, base_english_name="meowth" matches:
      - "Meowth"
      - "Alolan Meowth"
      - "Galarian Meowth"
    Matches are case-insensitive and require a whole-word boundary so that
    searching "mew" doesn't pull in "Mewtwo".
    Results are sorted: exact match first, then alphabetically.
    """
    needle = normalize_pokemon_name(base_english_name).lower()
    pattern = re.compile(r'\b' + re.escape(needle) + r'\b', re.IGNORECASE)
    matches = [entry for entry in spawn_data.values() if pattern.search(entry["name"])]
    # Sort: exact name first, then alphabetically
    matches.sort(key=lambda e: (e["name"].lower() != needle, e["name"].lower()))
    return matches


# ------------------------------------------------------------------ #
#  Shiny rate math                                                     #
# ------------------------------------------------------------------ #

def shiny_prob(streak: int) -> float:
    """Per-encounter shiny probability at a given streak, no charm."""
    if streak > 0:
        return (1 + math.sqrt(streak) / 7) / BASE_SHINY_RATE
    return 1 / BASE_SHINY_RATE


def shiny_prob_charm(streak: int) -> float:
    """Per-encounter shiny probability at a given streak, with Shiny Charm."""
    return shiny_prob(streak) * SHINY_CHARM_MULTIPLIER


def prob_to_fraction(p: float) -> str:
    return f"1/{round(1 / p)}"


def streak_for_target_pct(target_pct: float, charm: bool) -> int:
    """
    Binary-search for the smallest streak where the per-encounter shiny
    probability meets or exceeds target_pct (0–100).

    Formula: (1 + √streak/7) / 4096  [× 1.20 with charm]
    Returns the streak, or -1 if unreachable within a safe upper bound.
    """
    target = target_pct / 100
    fn = shiny_prob_charm if charm else shiny_prob

    hi = 10_000_000_000
    if fn(hi) < target:
        return -1

    lo = 0
    while lo < hi:
        mid = (lo + hi) // 2
        if fn(mid) >= target:
            hi = mid
        else:
            lo = mid + 1
    return lo


# ------------------------------------------------------------------ #
#  Shiny rate embed builders                                           #
# ------------------------------------------------------------------ #

def _build_shiny_rate_embed(streak: int) -> discord.Embed:
    p_base  = shiny_prob(streak)
    p_charm = shiny_prob_charm(streak)

    embed = discord.Embed(title="✨ Shiny Rate", color=EMBED_COLOR)
    embed.add_field(name="Chain / Streak", value=str(streak), inline=False)
    embed.add_field(
        name="Without Shiny Charm",
        value=f"{prob_to_fraction(p_base)}  ({p_base * 100:.4f}%)",
        inline=False,
    )
    embed.add_field(
        name="With Shiny Charm ✨",
        value=f"{prob_to_fraction(p_charm)}  ({p_charm * 100:.4f}%)",
        inline=False,
    )

    footer = "Formula: 1/4096  ·  Charm ×1.20" if streak == 0 else \
             f"Formula: (1 + √{streak}/7) / 4096  ·  Charm ×1.20"
    embed.set_footer(text=footer)
    return embed


def _build_chain_target_embed(target_pct: float) -> discord.Embed:
    streak_base  = streak_for_target_pct(target_pct, charm=False)
    streak_charm = streak_for_target_pct(target_pct, charm=True)

    def fmt(s: int) -> str:
        return f"Streak **{s:,}**" if s >= 0 else "Unreachable"

    embed = discord.Embed(
        title=f"✨ Streak Needed for {target_pct:.2f}% Per-Encounter Shiny Chance",
        color=EMBED_COLOR,
    )
    embed.add_field(name="Without Shiny Charm", value=fmt(streak_base),  inline=False)
    embed.add_field(name="With Shiny Charm ✨",  value=fmt(streak_charm), inline=False)
    embed.set_footer(text="Streak where your per-encounter shiny chance reaches the target.")
    return embed


def _shiny_usage_embed() -> discord.Embed:
    p_base  = shiny_prob(0)
    p_charm = shiny_prob_charm(0)

    embed = discord.Embed(title="✨ Shiny Rate — Usage", color=EMBED_COLOR)
    embed.description = (
        "**`p!shr [chain] [target%]`**  —  both values are optional\n\n"
        "`p!shr` — show this help + rates at chain 0\n"
        "`p!shr 50` — shiny rates at chain 50\n"
        "`p!shr 89%` — how many encounters for an 89% cumulative shiny chance\n"
        "`p!shr 50 89%` — both at once\n\n"
        "Reply to a Pokétwo Shiny Hunt embed with `p!shr` (optionally adding "
        "a `target%`) and the chain is pulled automatically from the embed."
    )
    embed.add_field(
        name="Chain 0 — No Charm",
        value=f"{prob_to_fraction(p_base)}  ({p_base * 100:.4f}%)",
        inline=False,
    )
    embed.add_field(
        name="Chain 0 — With Charm ✨",
        value=f"{prob_to_fraction(p_charm)}  ({p_charm * 100:.4f}%)",
        inline=False,
    )
    return embed


# ------------------------------------------------------------------ #
#  Time difference helpers                                             #
# ------------------------------------------------------------------ #

async def _resolve_message(
    channel: discord.TextChannel, message_id: int, bot: Optional[commands.Bot] = None
) -> Optional[discord.Message]:
    """
    Fetch a message by ID. Tries the current channel first, then falls back to
    searching every text channel the bot can read across all its guilds.
    Returns None if not found anywhere.
    """
    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.HTTPException):
        pass

    if bot is None:
        return None

    for guild in bot.guilds:
        for ch in guild.text_channels:
            if ch.id == channel.id:
                continue  # already tried
            perms = ch.permissions_for(guild.me)
            if not (perms.read_messages and perms.read_message_history):
                continue
            try:
                return await ch.fetch_message(message_id)
            except (discord.NotFound, discord.HTTPException):
                continue

    return None


async def _resolve_message_fast(
    channel: discord.TextChannel, message_id: int
) -> Optional[discord.Message]:
    """
    Fast resolve: only tries the current channel, doesn't search all guilds.
    Used for timedifference to avoid timeout when messages are from other servers.
    Falls back to snowflake math if message can't be fetched.
    """
    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.HTTPException):
        return None


async def _get_previous_message(
    reference_message: discord.Message,
) -> Optional[discord.Message]:
    """
    Return the message posted immediately before `reference_message`,
    fetched from the channel the message actually lives in.
    """
    async for msg in reference_message.channel.history(before=reference_message, limit=1):
        return msg
    return None


def _build_timediff_embed(msg1: discord.Message, msg2: discord.Message) -> discord.Embed:
    """Build an embed showing the time difference between two messages."""
    earlier, later = (msg1, msg2) if msg1.created_at < msg2.created_at else (msg2, msg1)

    delta = later.created_at - earlier.created_at
    total_seconds = delta.total_seconds()
    total_ms = int(delta.total_seconds() * 1000)

    if total_seconds < 60:
        seconds_str = f"{total_seconds:.3f}s"
    elif total_seconds < 3600:
        minutes = int(total_seconds // 60)
        secs = total_seconds % 60
        seconds_str = f"{minutes}m {secs:.3f}s  ({total_seconds:.3f}s total)"
    else:
        hours = int(total_seconds // 3600)
        remaining = total_seconds % 3600
        minutes = int(remaining // 60)
        secs = remaining % 60
        seconds_str = f"{hours}h {minutes}m {secs:.3f}s  ({total_seconds:.3f}s total)"

    embed = discord.Embed(title="⏱️ Time Difference", color=EMBED_COLOR)
    embed.add_field(
        name="Earlier Message",
        value=(
            f"[Jump]({earlier.jump_url})\n"
            f"ID: `{earlier.id}`\n"
            f"By: {earlier.author.mention}\n"
            f"<t:{int(earlier.created_at.timestamp())}:F>"
        ),
        inline=True,
    )
    embed.add_field(
        name="Later Message",
        value=(
            f"[Jump]({later.jump_url})\n"
            f"ID: `{later.id}`\n"
            f"By: {later.author.mention}\n"
            f"<t:{int(later.created_at.timestamp())}:F>"
        ),
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer
    embed.add_field(name="Seconds",      value=f"`{seconds_str}`",   inline=True)
    embed.add_field(name="Milliseconds", value=f"`{total_ms:,} ms`", inline=True)
    return embed


DISCORD_EPOCH_MS = 1420070400000  # Jan 1, 2015 UTC


def _snowflake_to_datetime(snowflake_id: int) -> datetime.datetime:
    """Extract the UTC creation time encoded in a Discord snowflake ID."""
    ms = (snowflake_id >> 22) + DISCORD_EPOCH_MS
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)


def _build_timediff_embed_snowflake(id1: int, id2: int) -> discord.Embed:
    """
    Build a time-difference embed using only snowflake math — no message fetch needed.
    Used when the bot cannot access the messages directly.
    """
    dt1 = _snowflake_to_datetime(id1)
    dt2 = _snowflake_to_datetime(id2)
    earlier_id, later_id = (id1, id2) if dt1 < dt2 else (id2, id1)
    earlier_dt = _snowflake_to_datetime(earlier_id)
    later_dt   = _snowflake_to_datetime(later_id)

    delta = later_dt - earlier_dt
    total_seconds = delta.total_seconds()
    total_ms = int(total_seconds * 1000)

    if total_seconds < 60:
        seconds_str = f"{total_seconds:.3f}s"
    elif total_seconds < 3600:
        minutes = int(total_seconds // 60)
        secs = total_seconds % 60
        seconds_str = f"{minutes}m {secs:.3f}s  ({total_seconds:.3f}s total)"
    else:
        hours = int(total_seconds // 3600)
        remaining = total_seconds % 3600
        minutes = int(remaining // 60)
        secs = remaining % 60
        seconds_str = f"{hours}h {minutes}m {secs:.3f}s  ({total_seconds:.3f}s total)"

    embed = discord.Embed(title="⏱️ Time Difference", color=EMBED_COLOR)
    embed.add_field(
        name="Earlier Message",
        value=(
            f"ID: `{earlier_id}`\n"
            f"<t:{int(earlier_dt.timestamp())}:F>"
        ),
        inline=True,
    )
    embed.add_field(
        name="Later Message",
        value=(
            f"ID: `{later_id}`\n"
            f"<t:{int(later_dt.timestamp())}:F>"
        ),
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer
    embed.add_field(name="Seconds",      value=f"`{seconds_str}`",   inline=True)
    embed.add_field(name="Milliseconds", value=f"`{total_ms:,} ms`", inline=True)
    embed.set_footer(text="Timestamps derived from snowflake IDs — message content not accessible.")
    return embed


def _build_single_message_date_embed(message_id: int) -> discord.Embed:
    """
    Build an embed showing just the date/time of a single message from its Snowflake ID.
    """
    dt = _snowflake_to_datetime(message_id)
    
    embed = discord.Embed(title="📅 Message Date", color=EMBED_COLOR)
    embed.add_field(
        name="Message ID",
        value=f"`{message_id}`",
        inline=False,
    )
    embed.add_field(
        name="Created At",
        value=f"<t:{int(dt.timestamp())}:F>",
        inline=False,
    )
    embed.set_footer(text="Timestamp derived from snowflake ID.")
    return embed


# ------------------------------------------------------------------ #
#  Pokétwo ObjectID → date helpers                                     #
# ------------------------------------------------------------------ #

def _objectid_to_datetime(object_id: str) -> datetime.datetime:
    """
    Extract the creation timestamp from a MongoDB ObjectID string.
    The first 8 hex characters encode a Unix timestamp (big-endian uint32).
    Returns a timezone-aware UTC datetime.
    """


# ------------------------------------------------------------------ #
#  Color utilities                                                     #
# ------------------------------------------------------------------ #

def _parse_color(color_input: str) -> Optional[tuple[int, int, int]]:
    """
    Parse a color from various formats:
    - Hex: #RRGGBB or RRGGBB (with or without #)
    - RGB: rgb(r, g, b) or r, g, b
    - Color names: red, blue, green, etc.
    Returns (R, G, B) tuple or None if invalid.
    """
    color_input = color_input.strip()
    
    # Try hex format
    if color_input.startswith('#'):
        color_input = color_input[1:]
    if len(color_input) == 6 and all(c in '0123456789abcdefABCDEF' for c in color_input):
        try:
            r = int(color_input[0:2], 16)
            g = int(color_input[2:4], 16)
            b = int(color_input[4:6], 16)
            return (r, g, b)
        except ValueError:
            pass
    
    # Try rgb() format
    if color_input.lower().startswith('rgb(') and color_input.endswith(')'):
        try:
            rgb_str = color_input[4:-1]
            parts = [int(x.strip()) for x in rgb_str.split(',')]
            if len(parts) == 3 and all(0 <= x <= 255 for x in parts):
                return tuple(parts)
        except ValueError:
            pass
    
    # Try comma-separated format
    try:
        parts = [int(x.strip()) for x in color_input.split(',')]
        if len(parts) == 3 and all(0 <= x <= 255 for x in parts):
            return tuple(parts)
    except ValueError:
        pass
    
    # Try color names
    color_names = {
        'red': (255, 0, 0), 'green': (0, 128, 0), 'blue': (0, 0, 255),
        'white': (255, 255, 255), 'black': (0, 0, 0), 'yellow': (255, 255, 0),
        'cyan': (0, 255, 255), 'magenta': (255, 0, 255), 'silver': (192, 192, 192),
        'gray': (128, 128, 128), 'maroon': (128, 0, 0), 'olive': (128, 128, 0),
        'lime': (0, 255, 0), 'aqua': (0, 255, 255), 'teal': (0, 128, 128),
        'navy': (0, 0, 128), 'purple': (128, 0, 128), 'orange': (255, 165, 0),
        'pink': (255, 192, 203), 'brown': (165, 42, 42), 'gold': (255, 215, 0),
    }
    normalized = color_input.lower()
    if normalized in color_names:
        return color_names[normalized]
    
    return None


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB to hex format."""
    return f"#{r:02X}{g:02X}{b:02X}"


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Convert RGB (0-255) to HSL (0-360, 0-100, 0-100)."""
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    max_val = max(r, g, b)
    min_val = min(r, g, b)
    l = (max_val + min_val) / 2
    
    if max_val == min_val:
        h = s = 0
    else:
        d = max_val - min_val
        s = d / (2 - max_val - min_val) if l > 0.5 else d / (max_val + min_val)
        
        if max_val == r:
            h = (g - b) / d + (6 if g < b else 0)
        elif max_val == g:
            h = (b - r) / d + 2
        else:
            h = (r - g) / d + 4
        h /= 6
    
    return (round(h * 360), round(s * 100), round(l * 100))


def _build_color_embed(hex_color: str, rgb: tuple[int, int, int]) -> discord.Embed:
    """Build an embed showing color information."""
    r, g, b = rgb
    h, s, l = _rgb_to_hsl(r, g, b)
    
    # Convert hex to int for Discord color (without #)
    color_int = int(hex_color[1:], 16)
    
    embed = discord.Embed(title="🎨 Color Converter", color=color_int)
    embed.add_field(name="Hex", value=f"`{hex_color.upper()}`", inline=True)
    embed.add_field(name="RGB", value=f"`rgb({r}, {g}, {b})`", inline=True)
    embed.add_field(name="HSL", value=f"`hsl({h}, {s}%, {l}%)`", inline=True)
    embed.add_field(name="Decimal", value=f"`{color_int}`", inline=False)
    embed.set_thumbnail(url=f"https://singlecolorimage.com/get/{hex_color[1:]}/200x200")
    embed.set_footer(text="Color preview shown as thumbnail")
    
    return embed


def _objectid_to_datetime(object_id: str) -> datetime.datetime:
    if len(object_id) < 8:
        raise ValueError(f"ObjectID too short: {object_id!r}")
    unix_ts = int(object_id[:8], 16)
    return datetime.datetime.fromtimestamp(unix_ts, tz=datetime.timezone.utc)


def _extract_objectid_from_embed(message: discord.Message) -> Optional[str]:
    """
    Find a Pokétwo-style 'ID: <objectid>' on a message.  Checks embed footers
    first (old style), then the text of Components V2 messages.
    Returns the ObjectID string, or None if not found.
    """
    for text in _component_texts(getattr(message, "components", None)):
        m = re.search(r"ID:\s*[*_`]*([0-9a-fA-F]{24})", text)
        if m:
            return m.group(1)

    for embed in message.embeds:
        footer_text = embed.footer.text or ""
        # Footer format: "Displaying pokémon XXXXX. ID: <objectid>"
        if "ID:" in footer_text:
            parts = footer_text.split("ID:")
            if len(parts) >= 2:
                oid = parts[-1].strip()
                if oid:
                    return oid
    return None


def _extract_chain_from_embed(message: discord.Message) -> Optional[int]:
    """
    Find a Pokétwo Shiny Hunt "Chain" value and return it as an int.
    Checks embed fields (old style) and Components V2 text.
    Returns None if no chain is found.
    """
    for text in _component_texts(getattr(message, "components", None)):
        m = re.search(r"chain\W{0,6}([\d,]+)", text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                continue

    for embed in message.embeds:
        for field in embed.fields:
            name = (field.name or "").strip().lower()
            if "chain" in name:
                match = re.search(r"[\d,]+", field.value or "")
                if match:
                    try:
                        return int(match.group().replace(",", ""))
                    except ValueError:
                        continue
    return None


def _date_response_text(dt: datetime.datetime) -> str:
    """Return a plain Discord timestamp string for a caught date."""
    unix_ts = int(dt.timestamp())
    return f"<t:{unix_ts}:F>"


# ------------------------------------------------------------------ #
#  Spawn rate embed builder                                            #
# ------------------------------------------------------------------ #

def _build_spawnrate_embed(
    entries: list[dict],
    canonical_name: str,
    searched_name: str,
) -> discord.Embed:
    """
    Build the spawn-rate embed for one or more variants.

    - 1 entry  → classic two-field layout (Spawn Chance + Percentage).
    - 2–8 entries → one field per variant (existing behaviour).
    - 9+ entries  → compact description table to stay under Discord's
                     6 000-character embed limit (e.g. all 28 Unown forms).
    """
    title = f"Spawn Rate — {canonical_name}"
    if searched_name.lower() != canonical_name.lower():
        title += f'  (searched: "{searched_name}")'

    embed = discord.Embed(title=title, color=EMBED_COLOR)
    embed.set_thumbnail(url=f"https://cdn.poketwo.net/images/{entries[0]['dex']}.png")

    if len(entries) == 1:
        entry = entries[0]
        embed.add_field(name="Spawn Chance", value=entry["chance"],     inline=True)
        embed.add_field(name="Percentage",   value=entry["chance_pct"], inline=True)

    elif len(entries) <= 8:
        for entry in entries:
            embed.add_field(
                name=entry["name"],
                value=f"{entry['chance']}  ({entry['chance_pct']})",
                inline=False,
            )

    else:
        # Compact table in description — safe for 28+ Unown forms etc.
        lines = [f"`{'Pokémon':<22} {'Chance':<12} Percentage`"]
        lines.append("`" + "─" * 44 + "`")
        for entry in entries:
            name_col   = entry["name"][:22]
            chance_col = entry["chance"][:12]
            pct_col    = entry["chance_pct"]
            lines.append(f"`{name_col:<22} {chance_col:<12} {pct_col}`")
        embed.description = "\n".join(lines)

    return embed


# ------------------------------------------------------------------ #
#  Cog                                                                 #
# ------------------------------------------------------------------ #

class PokeTools(commands.Cog, name="PokeTools"):
    """Spawn rates, shiny rates, and message utilities for Pokétwo."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pokemon_data = load_pokemon_data()

    # ================================================================ #
    #  Spawn rate                                                        #
    # ================================================================ #

    @app_commands.command(name="spawnrate", description="Show the spawn rate for a Pokémon (shows all regional forms).")
    @app_commands.describe(pokemon="Pokémon name (English, Japanese, or other language)")
    async def spawnrate_slash(self, interaction: discord.Interaction, pokemon: str):
        await interaction.response.defer()

        matched = find_pokemon_by_name_flexible(pokemon, self.pokemon_data)
        canonical_name = matched["name"] if matched else pokemon.strip()

        try:
            spawn_data = await fetch_spawn_rates(session=self.bot.http_session)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to fetch spawn rate data: `{e}`", allowed_mentions=NO_MENTIONS)
            return

        entries = find_spawn_rate_family(canonical_name, spawn_data)
        # If the family search found nothing and we translated a name, also try the raw input
        if not entries and canonical_name.lower() != pokemon.strip().lower():
            entries = find_spawn_rate_family(pokemon.strip(), spawn_data)

        if not entries:
            await interaction.followup.send(
                f"❌ No spawn rate data found for **{canonical_name}**. It may not spawn in the wild.", allowed_mentions=NO_MENTIONS
            )
            return

        embed = _build_spawnrate_embed(entries, canonical_name, pokemon.strip())
        await interaction.followup.send(embed=embed, allowed_mentions=NO_MENTIONS)

    @commands.command(name="spawnrate", aliases=["sr"])
    async def spawnrate_prefix(self, ctx: commands.Context, *, pokemon_name: str = None):
        """Show spawn rates for a Pokémon and all its regional forms. Usage: p!sr <pokemon>"""
        if not pokemon_name:
            await ctx.send("Please provide a Pokémon name. Example: `p!sr meowth`", allowed_mentions=NO_MENTIONS)
            return

        async with ctx.typing():
            matched = find_pokemon_by_name_flexible(pokemon_name, self.pokemon_data)
            canonical_name = matched["name"] if matched else pokemon_name.strip()

            try:
                spawn_data = await fetch_spawn_rates(session=self.bot.http_session)
            except Exception as e:
                await ctx.send(f"❌ Failed to fetch spawn rate data: `{e}`\nPlease try again later.", allowed_mentions=NO_MENTIONS)
                return

            entries = find_spawn_rate_family(canonical_name, spawn_data)
            # If the family search found nothing and we translated a name, also try the raw input
            if not entries and canonical_name.lower() != pokemon_name.strip().lower():
                entries = find_spawn_rate_family(pokemon_name.strip(), spawn_data)

            if not entries:
                await ctx.send(
                    f"❌ No spawn rate data found for **{canonical_name}**. It may not spawn in the wild.", allowed_mentions=NO_MENTIONS
                )
                return

            embed = _build_spawnrate_embed(entries, canonical_name, pokemon_name.strip())
            await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    # ================================================================ #
    #  Shiny rate                                                        #
    # ================================================================ #

    @app_commands.command(name="shinyrate", description="Show shiny rates or encounters needed for a target chance.")
    @app_commands.describe(
        chain="Your current catch streak / chain (optional)",
        target="Target cumulative shiny chance as a percentage, e.g. 89 (optional)",
    )
    async def shinyrate_slash(
        self,
        interaction: discord.Interaction,
        chain: Optional[int] = None,
        target: Optional[float] = None,
    ):
        if chain is not None and chain < 0:
            await interaction.response.send_message("❌ Chain cannot be negative.", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        if target is not None and not (0 < target < 100):
            await interaction.response.send_message("❌ Target must be between 0 and 100.", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return

        if chain is None and target is None:
            await interaction.response.send_message(embed=_shiny_usage_embed(), allowed_mentions=NO_MENTIONS)
            return

        embeds = []
        if chain is not None:
            embeds.append(_build_shiny_rate_embed(chain))
        if target is not None:
            embeds.append(_build_chain_target_embed(target))

        await interaction.response.send_message(embeds=embeds, allowed_mentions=NO_MENTIONS)

    @commands.command(name="shinyrate", aliases=["shr"])
    async def shinyrate_prefix(self, ctx: commands.Context, *, args: str = None):
        """
        Show shiny rates or encounters needed for a target cumulative chance.
        Usage: p!shr [chain] [target%]

        Reply to a Pokétwo Shiny Hunt embed with p!shr (with or without a
        target%) to auto-pull your current chain from the embed's "Chain" field.
        """
        chain: Optional[int] = None
        target: Optional[float] = None
        errors = []
        auto_detected = False

        if args:
            for token in args.split():
                if token.endswith("%"):
                    try:
                        val = float(token[:-1])
                        if not (0 < val < 100):
                            errors.append(f"Target `{token}` must be between 0% and 100%.")
                        else:
                            target = val
                    except ValueError:
                        errors.append(f"`{token}` is not a valid percentage.")
                else:
                    try:
                        val = int(token)
                        if val < 0:
                            errors.append("Chain cannot be negative.")
                        else:
                            chain = val
                    except ValueError:
                        errors.append(f"`{token}` is not a valid chain number.")

            if errors:
                await ctx.send("❌ " + "\n❌ ".join(errors), allowed_mentions=NO_MENTIONS)
                return

        # If no explicit chain was typed, try to auto-pull it from a
        # replied-to Pokétwo Shiny Hunt embed's "Chain" field.
        if chain is None and ctx.message.reference and ctx.message.reference.message_id:
            replied_msg = ctx.message.reference.resolved
            if not isinstance(replied_msg, discord.Message):
                replied_msg = await _resolve_message(ctx.channel, ctx.message.reference.message_id, self.bot)
            if replied_msg is not None:
                detected = _extract_chain_from_embed(replied_msg)
                if detected is not None:
                    chain = detected
                    auto_detected = True

        if chain is None and target is None:
            await ctx.send(embed=_shiny_usage_embed(), allowed_mentions=NO_MENTIONS)
            return

        async with ctx.typing():
            embeds = []
            if chain is not None:
                chain_embed = _build_shiny_rate_embed(chain)
                if auto_detected:
                    chain_embed.set_author(name="📎 Chain auto-detected from replied embed")
                embeds.append(chain_embed)
            if target is not None:
                embeds.append(_build_chain_target_embed(target))

            if not embeds:
                embeds.append(_shiny_usage_embed())

            await ctx.send(embeds=embeds, allowed_mentions=NO_MENTIONS)

    # ================================================================ #
    #  Time difference                                                   #
    # ================================================================ #

    async def _resolve_pair(
        self,
        channel: discord.TextChannel,
        id1: Optional[int],
        id2: Optional[int],
        reply_ref: Optional[discord.MessageReference],
    ) -> tuple[Optional[discord.Message], Optional[discord.Message], Optional[str], Optional[tuple[int, int]], Optional[int]]:
        """
        Resolve the two messages to compare.
        Returns (msg_a, msg_b, error_string, snowflake_ids, single_id).

        - On full success: (msg_a, msg_b, None, None, None)
        - On snowflake fallback (IDs given): (None, None, None, (id1, id2), None) or (None, None, None, None, id1)
        - On error: (None, None, error_string, None, None)

        Modes:
          - reply / one id  → target message + the message before it
          - two ids         → use snowflake math only
          - one id only     → use snowflake math only
        """
        # Two explicit IDs — always use snowflake math (no fetching)
        if id1 is not None and id2 is not None:
            return None, None, None, (id1, id2), None

        # One explicit ID — use snowflake math only
        if id1 is not None:
            return None, None, None, None, id1

        # Reply mode → fetch the replied-to message and the one before it (full details)
        if reply_ref is not None:
            target = await _resolve_message(channel, reply_ref.message_id, self.bot)
            if target is None:
                return None, None, "❌ Could not fetch the replied-to message.", None, None
            prev = await _get_previous_message(target)
            if prev is None:
                return None, None, "❌ Could not find a message before the replied-to message.", None, None
            return target, prev, None, None, None

        return None, None, (
            "❌ Please either:\n"
            "• Reply to a message with `p!td`\n"
            "• Provide one message ID: `p!td <id>`\n"
            "• Provide two message IDs: `p!td <id1> <id2>`"
        ), None, None

    @app_commands.command(
        name="timedifference",
        description="Find the time difference between two messages or show a message date.",
    )
    @app_commands.describe(
        message_id="A single message ID (shows date only), or first ID for time diff",
        message_id2="Second message ID — compare directly with message_id",
    )
    async def timedifference_slash(
        self,
        interaction: discord.Interaction,
        message_id: Optional[str] = None,
        message_id2: Optional[str] = None,
    ):
        await interaction.response.defer()

        id1, id2 = None, None
        if message_id is not None:
            try:
                id1 = int(message_id)
            except ValueError:
                await interaction.followup.send("❌ `message_id` must be a valid integer ID.", allowed_mentions=NO_MENTIONS)
                return
        if message_id2 is not None:
            try:
                id2 = int(message_id2)
            except ValueError:
                await interaction.followup.send("❌ `message_id2` must be a valid integer ID.", allowed_mentions=NO_MENTIONS)
                return

        msg_a, msg_b, error, snowflake_ids, single_id = await self._resolve_pair(interaction.channel, id1, id2, reply_ref=None)
        if error:
            await interaction.followup.send(error, allowed_mentions=NO_MENTIONS)
            return

        # Two IDs — show time difference only
        if snowflake_ids:
            await interaction.followup.send(embed=_build_timediff_embed_snowflake(*snowflake_ids), allowed_mentions=NO_MENTIONS)
            return

        # Single ID — show just the date
        if single_id is not None:
            await interaction.followup.send(embed=_build_single_message_date_embed(single_id), allowed_mentions=NO_MENTIONS)
            return

        # Reply mode — show full details
        await interaction.followup.send(embed=_build_timediff_embed(msg_a, msg_b), allowed_mentions=NO_MENTIONS)

    @commands.command(name="timedifference", aliases=["timediff", "td"])
    async def timedifference_prefix(
        self,
        ctx: commands.Context,
        id1: Optional[int] = None,
        id2: Optional[int] = None,
    ):
        """
        Find the time difference between two messages.

        Usage:
          p!td                — reply to a message; shows it + message above with full details
          p!td <id>           — shows the date of that message ID only
          p!td <id1> <id2>    — shows time difference between the two IDs (instant, no fetching)

        Aliases: p!timediff, p!timedifference
        """
        async with ctx.typing():
            reply_ref = ctx.message.reference if ctx.message.reference else None
            msg_a, msg_b, error, snowflake_ids, single_id = await self._resolve_pair(ctx.channel, id1, id2, reply_ref)
            
            if error:
                await ctx.send(error, allowed_mentions=NO_MENTIONS)
                return

            # Two IDs — show time difference only (Snowflake math, instant)
            if snowflake_ids:
                await ctx.send(embed=_build_timediff_embed_snowflake(*snowflake_ids), allowed_mentions=NO_MENTIONS)
                return

            # Single ID — show just the date
            if single_id is not None:
                await ctx.send(embed=_build_single_message_date_embed(single_id), allowed_mentions=NO_MENTIONS)
                return

            # Reply mode — show full details with author, jump link, etc.
            await ctx.send(embed=_build_timediff_embed(msg_a, msg_b), allowed_mentions=NO_MENTIONS)

    # ================================================================ #
    #  Pokétwo caught date (ObjectID to timestamp)                       #
    # ================================================================ #

    def _resolve_date_target(
        self,
        object_id: Optional[str],
        replied_message: Optional[discord.Message],
    ) -> tuple:
        """
        Return (object_id_str, error_str).
        Priority: explicit object_id arg -> reply embed -> error.
        """
        if object_id:
            return object_id.strip(), None

        if replied_message is not None:
            oid = _extract_objectid_from_embed(replied_message)
            if oid:
                return oid, None
            return None, (
                "❌ The replied message has no Pokétwo ObjectID in its embed footer.\n"
                "Make sure you reply to a Pokétwo `p!info` / `p!pokemon` embed."
            )

        return None, (
            "❌ Usage:\n"
            "• Reply to a Pokétwo embed with `p!date`\n"
            "• Or provide the ObjectID directly: `p!date <objectid>`"
        )

    @app_commands.command(
        name="date",
        description="Show the caught date of a Pokémon from its Pokétwo ObjectID.",
    )
    @app_commands.describe(
        object_id="The Pokétwo ObjectID (from the embed footer). Leave blank when replying to a Pokétwo embed.",
    )
    async def date_slash(
        self,
        interaction: discord.Interaction,
        object_id: Optional[str] = None,
    ):
        await interaction.response.defer()

        oid, error = self._resolve_date_target(object_id, None)
        if error:
            await interaction.followup.send(error, allowed_mentions=NO_MENTIONS)
            return

        try:
            dt = _objectid_to_datetime(oid)
        except (ValueError, OverflowError) as e:
            await interaction.followup.send(f"❌ Invalid ObjectID `{oid}`: {e}", allowed_mentions=NO_MENTIONS)
            return

        await interaction.followup.send(_date_response_text(dt), allowed_mentions=NO_MENTIONS)

    @commands.command(name="date", aliases=["caught", "catchdate"])
    async def date_prefix(self, ctx: commands.Context, object_id: Optional[str] = None):
        """
        Show the caught date/time of a Pokémon from its Pokétwo ObjectID.

        Usage:
          p!date <objectid>   — provide the ObjectID directly
          p!date              — reply to a Pokétwo embed; the ObjectID is read from the footer

        Aliases: p!caught, p!catchdate
        """
        async with ctx.typing():
            replied_msg: Optional[discord.Message] = None
            if ctx.message.reference and ctx.message.reference.message_id:
                replied_msg = await _resolve_message(ctx.channel, ctx.message.reference.message_id, self.bot)

            oid, error = self._resolve_date_target(object_id, replied_msg)
            if error:
                await ctx.send(error, allowed_mentions=NO_MENTIONS)
                return

            try:
                dt = _objectid_to_datetime(oid)
            except (ValueError, OverflowError) as e:
                await ctx.send(f"❌ Invalid ObjectID `{oid}`: {e}", allowed_mentions=NO_MENTIONS)
                return

            await ctx.send(_date_response_text(dt), allowed_mentions=NO_MENTIONS)

    # ================================================================ #
    #  Extract IDs                                                       #
    # ================================================================ #

    @staticmethod
    def _extract_ids_from_embed(message: discord.Message) -> list[str]:
        """
        Extract Pokétwo pokémon/listing IDs from a message. Works on both old
        embed descriptions and Components V2 (container / text display) messages.

        Handles all known variants:
          `39556693`　...          — marketplace (no padding)
          ` 9593`　...             — pokémon list (space-padded)
          **`586470`**　...        — bold-wrapped (gigantamax list)
          `278531`　...            — MissingNo / standard list

        Returns IDs in top-to-bottom order as plain strings (no padding).
        """
        blocks: list[str] = [e.description or "" for e in message.embeds]
        blocks.extend(_component_texts(getattr(message, "components", None)))

        ids = []
        for block in blocks:
            for line in block.splitlines():
                # Match the first backtick-wrapped token on the line, optionally inside **
                # (and optionally after a list/quote marker in V2 text).
                # Captures: **`  123`** or `123` or ` 123`
                m = re.match(r"^(?:[-•>]\s+)?\*{0,2}`\s*(\d+)`\*{0,2}", line.strip())
                if m:
                    ids.append(m.group(1))
        return ids

    @app_commands.command(
        name="extractids",
        description="Extract Pokétwo pokémon/listing IDs from an embed.",
    )
    async def extractids_slash(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer()

        try:
            mid = int(message_id)
        except ValueError:
            await interaction.followup.send("❌ `message_id` must be a valid integer ID.", allowed_mentions=NO_MENTIONS)
            return

        msg = await _resolve_message(interaction.channel, mid)
        if msg is None:
            await interaction.followup.send(f"❌ Could not find message `{mid}` in this channel.", allowed_mentions=NO_MENTIONS)
            return

        ids = self._extract_ids_from_embed(msg)
        if not ids:
            await interaction.followup.send("❌ No Pokétwo IDs found in that message.", allowed_mentions=NO_MENTIONS)
            return

        await interaction.followup.send(" ".join(ids), allowed_mentions=NO_MENTIONS)

    @commands.command(name="extract", aliases=["extractids", "e"])
    async def extractids_prefix(self, ctx: commands.Context, message_id: Optional[int] = None):
        """
        Extract Pokétwo pokémon/listing IDs from an embed, space-separated.

        Usage:
          p!extractids          — reply to a Pokétwo embed
          p!extractids <id>     — provide the message ID directly

        Aliases: p!extract, p!eids
        """
        async with ctx.typing():
            # Resolve target message
            if message_id is not None:
                msg = await _resolve_message(ctx.channel, message_id)
                if msg is None:
                    await ctx.send(f"❌ Could not find message `{message_id}` in this channel.", allowed_mentions=NO_MENTIONS)
                    return
            elif ctx.message.reference and ctx.message.reference.message_id:
                msg = await _resolve_message(ctx.channel, ctx.message.reference.message_id)
                if msg is None:
                    await ctx.send("❌ Could not fetch the replied-to message.", allowed_mentions=NO_MENTIONS)
                    return
            else:
                await ctx.send(
                    "❌ Usage:\n"
                    "• Reply to a Pokétwo embed with `p!extractids`\n"
                    "• Or provide the message ID: `p!extractids <id>`", allowed_mentions=NO_MENTIONS
                )
                return

            ids = self._extract_ids_from_embed(msg)
            if not ids:
                await ctx.send("❌ No Pokétwo IDs found in that message.", allowed_mentions=NO_MENTIONS)
                return

            await ctx.send(" ".join(ids), allowed_mentions=NO_MENTIONS)

    # ================================================================ #
    #  Color converter                                                   #
    # ================================================================ #

    @app_commands.command(
        name="color",
        description="Convert and display color information in multiple formats.",
    )
    @app_commands.describe(
        color_value="Color in hex (#FF0000), RGB (255,0,0), or name (red)",
    )
    async def color_slash(
        self,
        interaction: discord.Interaction,
        color_value: str,
    ):
        rgb = _parse_color(color_value)
        if rgb is None:
            await interaction.response.send_message(
                "❌ Invalid color format. Try:\n"
                "• Hex: `#FF0000` or `FF0000`\n"
                "• RGB: `255, 0, 0` or `rgb(255, 0, 0)`\n"
                "• Name: `red`, `blue`, `green`, etc.", allowed_mentions=NO_MENTIONS
            )
            return

        hex_color = _rgb_to_hex(*rgb)
        embed = _build_color_embed(hex_color, rgb)
        await interaction.response.send_message(embed=embed, allowed_mentions=NO_MENTIONS)

    @commands.command(name="color", aliases=["colour", "c"])
    async def color_prefix(self, ctx: commands.Context, *, color_value: str = None):
        """
        Convert and display color information.

        Usage:
          p!color #FF0000        — show color in hex format
          p!color 255, 0, 0      — show color in RGB format
          p!color red            — show color by name
          p!color rgb(255,0,0)   — RGB function format

        Aliases: p!colour, p!c

        Supported color names: red, green, blue, yellow, cyan, magenta, white, black,
        gray, silver, maroon, olive, lime, aqua, teal, navy, purple, orange, pink, brown, gold
        """
        if not color_value:
            await ctx.send(
                "❌ Please provide a color. Examples:\n"
                "`p!color #FF0000`\n"
                "`p!color 255, 0, 0`\n"
                "`p!color red`", allowed_mentions=NO_MENTIONS
            )
            return

        rgb = _parse_color(color_value)
        if rgb is None:
            await ctx.send(
                "❌ Invalid color format. Try:\n"
                "• Hex: `#FF0000` or `FF0000`\n"
                "• RGB: `255, 0, 0` or `rgb(255, 0, 0)`\n"
                "• Name: `red`, `blue`, `green`, etc.", allowed_mentions=NO_MENTIONS
            )
            return

        hex_color = _rgb_to_hex(*rgb)
        embed = _build_color_embed(hex_color, rgb)
        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    # ================================================================ #
    #  Owner utilities                                                   #
    # ================================================================ #

    @commands.command(name="reloadsr")
    @commands.is_owner()
    async def reloadsr(self, ctx: commands.Context):
        """Force-reload the spawn rate data from the gist. Owner only."""
        async with ctx.typing():
            try:
                old_count = len(_spawn_rate_cache) if _spawn_rate_cache else 0
                data = await fetch_spawn_rates(session=self.bot.http_session, force=True)
                new_count = len(data)
            except Exception as e:
                await ctx.send(f"❌ Failed to reload spawn rate data: `{e}`", allowed_mentions=NO_MENTIONS)
                return

        embed = discord.Embed(title="✅ Spawn Rate Data Reloaded", color=EMBED_COLOR)
        embed.add_field(name="Entries before", value=str(old_count), inline=True)
        embed.add_field(name="Entries now",    value=str(new_count), inline=True)
        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)


@app_commands.context_menu(name="Get Caught Date")
async def date_context_menu(interaction: discord.Interaction, message: discord.Message):
    """Right-click a Pokétwo embed to extract the caught date from its ObjectID."""
    await interaction.response.defer()

    oid = _extract_objectid_from_embed(message)
    if not oid:
        await interaction.followup.send(
            "❌ This message has no Pokétwo ObjectID in its embed footer.\n"
            "Make sure you right-click a Pokétwo `p!info` / `p!pokemon` embed.",
            ephemeral=True,
                allowed_mentions=NO_MENTIONS,
        )
        return

    try:
        dt = _objectid_to_datetime(oid)
    except (ValueError, OverflowError) as e:
        await interaction.followup.send(f"❌ Invalid ObjectID `{oid}`: {e}", allowed_mentions=NO_MENTIONS)
        return

    await interaction.followup.send(_date_response_text(dt), allowed_mentions=NO_MENTIONS)


@app_commands.context_menu(name="Extract IDs")
async def extractids_context_menu(interaction: discord.Interaction, message: discord.Message):
    """Right-click a Pokétwo pokémon list or marketplace embed to extract all IDs."""
    await interaction.response.defer()

    ids = PokeTools._extract_ids_from_embed(message)
    if not ids:
        await interaction.followup.send(
            "❌ No Pokétwo IDs found in that message.",
            ephemeral=True,
                allowed_mentions=NO_MENTIONS,
        )
        return

    await interaction.followup.send(" ".join(ids), allowed_mentions=NO_MENTIONS)


async def setup(bot: commands.Bot):
    await bot.add_cog(PokeTools(bot))
    bot.tree.add_command(date_context_menu)
    bot.tree.add_command(extractids_context_menu)
