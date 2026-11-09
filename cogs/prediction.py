"""Pokemon prediction and auto-detection"""
import json
import os
import re
import discord
import asyncio
from discord.ext import commands, tasks
from utils import (
    format_pokemon_prediction,
    get_image_url_from_message,
    normalize_pokemon_name,
    get_pokemon_with_variants,
    is_rare_pokemon,
    load_pokemon_data
)
from config import POKETWO_USER_ID, PREDICTION_CONFIDENCE
from guild_cache import GuildCache

# ---------------------------------------------------------------------------
# Components V2 support (discord.py 2.6+)
# V2 messages have no embeds/content; text and images live in a component tree
# (Container -> Section / TextDisplay / MediaGallery / Thumbnail ...).
# Helpers are duck-typed on purpose so they also work on older discord.py.
# ---------------------------------------------------------------------------
_IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|gif|webp)(?:[?#].*)?$", re.IGNORECASE)
_IMAGE_URL_RE = re.compile(r'https?://[^\s<>"]+?\.(?:png|jpg|jpeg|gif|webp)', re.IGNORECASE)

SPAWN_TITLE = "A wild pokémon has appeared!"
NEW_SPAWN_TITLE = "A new wild pokémon has appeared!"


def _component_texts(components) -> list:
    """Text of every TextDisplay in a component tree."""
    texts = []
    for comp in components or []:
        content = getattr(comp, "content", None)
        if isinstance(content, str) and content:
            texts.append(content)
        texts.extend(_component_texts(getattr(comp, "children", None)))
        accessory = getattr(comp, "accessory", None)
        if accessory is not None:
            texts.extend(_component_texts([accessory]))
    return texts


def _component_image_urls(components) -> list:
    """Media URLs (thumbnails, media galleries, section accessories) in a component tree."""
    urls = []
    for comp in components or []:
        url = getattr(getattr(comp, "media", None), "url", None)
        if isinstance(url, str):
            urls.append(url)
        for item in getattr(comp, "items", None) or []:
            url = getattr(getattr(item, "media", None), "url", None)
            if isinstance(url, str):
                urls.append(url)
        urls.extend(_component_image_urls(getattr(comp, "children", None)))
        accessory = getattr(comp, "accessory", None)
        if accessory is not None:
            urls.extend(_component_image_urls([accessory]))
    return urls


def _is_spawn_message(message) -> bool:
    """True for a Poketwo 'A wild pokémon has appeared!' message (embed or V2)."""
    if message.embeds:
        title = message.embeds[0].title
        if title and (title == SPAWN_TITLE or title.endswith(NEW_SPAWN_TITLE)):
            return True

    for text in _component_texts(getattr(message, "components", None)):
        for line in text.splitlines():
            # drop markdown heading/quote markers and bold/italic wrappers
            line = re.sub(r"^[#\-\s*_>]+|[\s*_]+$", "", line)
            if line == SPAWN_TITLE or line.endswith(NEW_SPAWN_TITLE):
                return True
    return False


# Hardcoded channel ID where any image will be auto-predicted
AUTO_PREDICT_CHANNEL_ID = 1453015934393651272

# How often the in-memory prediction counter is flushed to Mongo.
# At ~5 predictions/sec a per-prediction write is way too much load —
# batching into one $inc every interval keeps the DB essentially idle
# while still surviving restarts (see _flush_prediction_stats below).
PREDICTION_STATS_FLUSH_SECONDS = 600

# ---------------------------------------------------------------------------
# Constants – all 18 types and 9 main regions (lowercase, canonical)
# ---------------------------------------------------------------------------
ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice",
    "fighting", "poison", "ground", "flying", "psychic", "bug",
    "rock", "ghost", "dragon", "dark", "steel", "fairy"
]

ALL_REGIONS = [
    "kanto", "johto", "hoenn", "sinnoh", "unova",
    "kalos", "alola", "galar", "paldea", "kitakami", "unknown", "hisui", "pokopia"
]

SAFE_MENTIONS = discord.AllowedMentions(
    everyone=True,
    roles=True,
    users=True   # keep @user mentions for hunters/collectors
)

# Wild Poketwo spawns: suppress all mentions so roles/users aren't pinged
# by the bot's own message (Poketwo already pinged the channel).
NO_MENTIONS = discord.AllowedMentions.none()

# ---------------------------------------------------------------------------
# Best names loader (cached at module level — zero repeated I/O)
# ---------------------------------------------------------------------------
_BEST_NAMES: dict = {}

def _load_best_names() -> dict:
    global _BEST_NAMES
    if _BEST_NAMES:
        return _BEST_NAMES
    path = os.path.join("data", "best_names.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            _BEST_NAMES = json.load(f)
    except Exception as e:
        print(f"[BEST_NAMES] Could not load {path}: {e}")
        _BEST_NAMES = {}
    return _BEST_NAMES


def get_best_name(pokemon_name: str) -> str | None:
    """Return the best/shortest name for a Pokemon, or None if not in map."""
    names = _load_best_names()
    return names.get(pokemon_name)


# ---------------------------------------------------------------------------
# Type & Region lookup — loaded from data/typeandregions.csv
# Structure: {pokemon_name_lower: {"types": ["fire", "flying"], "region": "kanto"}}
# ---------------------------------------------------------------------------
_TYPE_REGION_DATA: dict = {}

def _load_type_region_data() -> dict:
    global _TYPE_REGION_DATA
    if _TYPE_REGION_DATA:
        return _TYPE_REGION_DATA

    path = os.path.join("data", "typeandregions.csv")
    try:
        import csv
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("name", "").strip()
                if not name:
                    continue
                types = []
                if row.get("type1", "").strip():
                    types.append(row["type1"].strip().lower())
                if row.get("type2", "").strip():
                    types.append(row["type2"].strip().lower())
                region = row.get("region", "").strip().lower()
                _TYPE_REGION_DATA[name.lower()] = {
                    "types": types,
                    "region": region,
                }
        print(f"[TYPE_REGION] Loaded {len(_TYPE_REGION_DATA)} entries from {path}")
    except Exception as e:
        print(f"[TYPE_REGION] Could not load {path}: {e}")

    return _TYPE_REGION_DATA


def get_pokemon_types(pokemon_name: str) -> list[str]:
    """Return list of lowercase type strings for a Pokemon name."""
    data = _load_type_region_data()
    entry = data.get(pokemon_name.lower())
    return entry["types"] if entry else []


def get_pokemon_region(pokemon_name: str) -> list[str]:
    """Return list with the lowercase region string for a Pokemon name."""
    data = _load_type_region_data()
    entry = data.get(pokemon_name.lower())
    if not entry or not entry.get("region"):
        return []
    return [entry["region"]]


# ---------------------------------------------------------------------------
# Main cog
# ---------------------------------------------------------------------------
class Prediction(commands.Cog):
    """Pokemon prediction commands and auto-detection"""

    def __init__(self, bot):
        self.bot = bot
        self.pokemon_data = load_pokemon_data()
        self.gcache = GuildCache(bot.db)   # in-memory TTL cache — replaces per-spawn DB queries
        bot.db.gcache = self.gcache        # lets DB mutation methods auto-invalidate cache
        self._bg_tasks: set = set()        # tracks fire-and-forget tasks so they're not GC'd early
        _load_best_names()        # warm cache on startup
        _load_type_region_data()  # warm cache on startup
        print(f"[AUTO-PREDICT] Channel ID set to: {AUTO_PREDICT_CHANNEL_ID}")

        # All-time prediction total (survives restarts — see p!stats).
        # predictions_since_flush is the in-memory buffer written to per
        # prediction; total_predictions_persisted is the last value read
        # from / written to Mongo. Both live on the bot so other cogs
        # (Help) can read them without importing this cog.
        if not hasattr(bot, 'predictions_since_flush'):
            bot.predictions_since_flush = 0
        if not hasattr(bot, 'total_predictions_persisted'):
            bot.total_predictions_persisted = 0
        self._flush_prediction_stats.start()

    def cog_unload(self):
        self._flush_prediction_stats.cancel()

    @tasks.loop(seconds=PREDICTION_STATS_FLUSH_SECONDS)
    async def _flush_prediction_stats(self):
        """Batch-write the buffered prediction count to Mongo."""
        amount = self.bot.predictions_since_flush
        if amount <= 0:
            return
        try:
            await self.bot.db.increment_bot_stat('predictions', amount)
            # Only clear the buffer once the write actually succeeds, so a
            # failed flush just gets retried (and counted) next interval.
            self.bot.predictions_since_flush -= amount
            self.bot.total_predictions_persisted += amount
        except Exception as e:
            print(f"[PREDICTION-STATS] Flush failed (will retry next interval): {e}")

    @_flush_prediction_stats.before_loop
    async def _before_flush_prediction_stats(self):
        await self.bot.wait_until_ready()
        # Prime the persisted total from Mongo once on startup so p!stats
        # is correct immediately, instead of showing 0 until the first flush.
        try:
            self.bot.total_predictions_persisted = await self.bot.db.get_bot_stat('predictions')
        except Exception as e:
            print(f"[PREDICTION-STATS] Could not load persisted total on startup: {e}")

    @property
    def db(self):
        return self.bot.db

    @property
    def predictor(self):
        return self.bot.predictor

    @property
    def http_session(self):
        return self.bot.http_session

    def _create_bg_task(self, coro):
        """Fire-and-forget a coroutine, keeping a reference so it isn't GC'd prematurely.
        Capped at 50 pending tasks — if exceeded the coro is dropped to prevent
        discord.Message objects piling up in memory during heavy incense sessions."""
        if len(self._bg_tasks) >= 50:
            coro.close()  # discard cleanly without an 'never awaited' warning
            return None
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------
    async def extract_image_url(self, message):
        if message.attachments:
            for attachment in message.attachments:
                if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                    return attachment.url

        if message.embeds:
            for embed in message.embeds:
                if embed.image:
                    return embed.image.url
                if embed.thumbnail:
                    return embed.thumbnail.url

        # Components V2 (no embeds): look at media galleries / thumbnails
        media_urls = _component_image_urls(getattr(message, "components", None))
        for url in media_urls:
            if _IMAGE_EXT_RE.search(url):
                return url
        if media_urls:
            return media_urls[0]

        urls = _IMAGE_URL_RE.findall(message.content or "")
        if urls:
            return urls[0]

        # Image links written inside V2 text displays
        for text in _component_texts(getattr(message, "components", None)):
            urls = _IMAGE_URL_RE.findall(text)
            if urls:
                return urls[0]

        url = await get_image_url_from_message(message)
        if url:
            return url

        return None

    # ------------------------------------------------------------------
    # Ping information — ALL gathered in a single batched call
    # ------------------------------------------------------------------

    async def _get_all_ping_data(self, pokemon_name: str, guild_id: int) -> dict:
        """
        Fetch ALL ping data for a spawn.

        Before (no cache):  8 DB queries per spawn × 5 spawns/s = 40 queries/s
        After  (cache hit): 0 DB queries per spawn
        After  (cache miss): 2 queries (AFK snapshot + guild settings)

        Changes vs original:
        - All data flows through GuildCache (TTL-based in-memory).
        - AFKSnapshot replaces 3 separate AFK fetches with 1 (shared across
          all four ping types — each type has its own independent AFK flag).
        - rare_collectors now runs concurrently with regular collectors
          instead of sequentially after them (bug fix).
        - get_only_pings / get_best_name removed from here — they're read
          from the already-cached guild_settings dict by the caller.
        """
        from utils import find_pokemon_by_name
        pokemon = find_pokemon_by_name(pokemon_name, self.pokemon_data)

        types   = get_pokemon_types(pokemon_name)   # in-memory, free
        regions = get_pokemon_region(pokemon_name)  # in-memory, free
        is_rare = pokemon and is_rare_pokemon(pokemon)

        # ── Phase 1: AFK snapshot + guild settings (both cached) ─────────
        # Cache hit  → ~0 ms, no DB queries
        # Cache miss → 2 parallel DB queries instead of the original 4
        afk_snapshot, guild_settings = await asyncio.gather(
            self.gcache.get_afk_snapshot(),
            self.gcache.get_guild_settings(guild_id),
        )

        shiny_afk_set  = afk_snapshot.shiny_afk
        coll_afk_set   = afk_snapshot.collection_afk
        type_afk_set   = afk_snapshot.type_ping_afk
        region_afk_set = afk_snapshot.region_ping_afk

        search_names = [pokemon_name]

        # ── Phase 2: all ping lists concurrently (including reserves) ────────
        async def _get_shiny_hunters():
            raw = await self.gcache.get_shiny_hunters(guild_id, search_names, shiny_afk_set)
            return [
                f"{uid}(AFK)" if is_afk else f"<@{uid}>"
                for uid, is_afk in raw
            ]

        async def _get_collectors():
            regular = await self.gcache.get_collectors(guild_id, search_names, coll_afk_set)
            if is_rare:
                # FIX: rare_collectors now runs inside this coroutine so
                # the outer gather can still run all concurrently
                rare = await self.gcache.get_rare_collectors(guild_id, coll_afk_set)
                seen = set(regular)
                for uid in rare:
                    if uid not in seen:
                        regular.append(uid)
                        seen.add(uid)
            return regular

        # Server-wide admin toggle: even if a user has type/region pings
        # enabled individually, they receive nothing while an admin has
        # disabled it for the whole server. Read straight from the already
        # cached guild_settings dict — no extra DB queries.
        type_pings_enabled   = guild_settings.get('type_pings_enabled', True)
        region_pings_enabled = guild_settings.get('region_pings_enabled', True)

        async def _get_type_pingers():
            if not type_pings_enabled:
                return []
            return await self.gcache.get_type_pingers(guild_id, types, type_afk_set)

        async def _get_region_pingers():
            if not region_pings_enabled:
                return []
            return await self.gcache.get_region_pingers(guild_id, regions, region_afk_set)

        async def _get_reserve_holders():
            return await self.gcache.get_reserve_holders(guild_id, search_names, afk_snapshot.collection_afk)

        hunters, collectors, type_pingers, rgn_pingers, reserve_holders = await asyncio.gather(
            _get_shiny_hunters(),
            _get_collectors(),
            _get_type_pingers(),
            _get_region_pingers(),
            _get_reserve_holders(),
        )

        is_reserved = len(reserve_holders) > 0

        # ── Phase 3: role pings from already-cached guild_settings ────────
        rare_ping     = None
        regional_ping = None

        if pokemon:
            rarity_value = pokemon.get('rarity', '')
            rarities = rarity_value if isinstance(rarity_value, list) else [rarity_value]
            rarities = [r.lower() for r in rarities if r]

            if any(r in ['legendary', 'mythical', 'ultra beast'] for r in rarities):
                rare_role_id = guild_settings.get('rare_role_id')
                if rare_role_id:
                    rare_ping = f"<@&{rare_role_id}>"

            if 'regional' in rarities:
                regional_role_id = guild_settings.get('regional_role_id')
                if regional_role_id:
                    regional_ping = f"<@&{regional_role_id}>"

        return {
            "hunters":        hunters,
            "collectors":     collectors,
            "rare_ping":      rare_ping,
            "regional_ping":  regional_ping,
            "type_pingers":   type_pingers,
            "rgn_pingers":    rgn_pingers,
            "reserve_holders": reserve_holders,
            "is_reserved":    is_reserved,
        }

    # ------------------------------------------------------------------
    # Output formatting
    # ------------------------------------------------------------------
    async def build_prediction_output(
        self,
        name: str,
        confidence: str,
        guild_id: int,
        *,
        show_best_name: bool = False,
        show_catch_command: bool = False,
        _preloaded_ping_data: dict = None,
    ) -> str:
        """
        Gather ALL ping data in a single batched call and build the output string.

        Pass `_preloaded_ping_data` to skip a second fetch when the caller
        already has it (e.g. from the only_pings gate in _run_prediction).

        Output order:
            <name>: <confidence>%
            `<@716390085896962058> c <name>`  ← only if catch_command enabled
            Best Name(s): <n>          ← only if best_name enabled
            Rare Ping: <@&role>         ← only if applicable
            Regional Ping: <@&role>    ← only if applicable
            Shiny Hunters: @...
            Collectors: @...
            Type Pings: @...
            Region Pings: @...
        """
        ping_data = _preloaded_ping_data if _preloaded_ping_data is not None \
                    else await self._get_all_ping_data(name, guild_id)

        lines = [format_pokemon_prediction(name, confidence)]

        if show_catch_command:
            lines.append(f"`<@716390085896962058> c {name.lower()}`\n```<@716390085896962058> c {name.lower()}```")

        if show_best_name:
            best = get_best_name(name)
            if best:
                lines.append(f"Best Name(s): {best}")

        # If reserved: only show reserve pings, suppress everything else
        if ping_data["is_reserved"]:
            reserve_mentions = " ".join([f"<@{uid}>" for uid in ping_data["reserve_holders"]])
            lines.append(f"Reserve Pings: {reserve_mentions}")
            lines.append("⚠️ **This Pokémon is reserved — please do not catch it!**")
            return "\n".join(lines)

        if ping_data["rare_ping"]:
            lines.append(f"Rare Ping: {ping_data['rare_ping']}")
        if ping_data["regional_ping"]:
            lines.append(f"Regional Ping: {ping_data['regional_ping']}")
        if ping_data["hunters"]:
            lines.append(f"Shiny Hunters: {' '.join(ping_data['hunters'])}")
        if ping_data["collectors"]:
            collector_mentions = " ".join([f"<@{uid}>" for uid in ping_data["collectors"]])
            lines.append(f"Collectors: {collector_mentions}")
        if ping_data["type_pingers"]:
            type_mentions = " ".join([f"<@{uid}>" for uid in ping_data["type_pingers"]])
            lines.append(f"Type Pings: {type_mentions}")
        if ping_data["rgn_pingers"]:
            rgn_mentions = " ".join([f"<@{uid}>" for uid in ping_data["rgn_pingers"]])
            lines.append(f"Region Pings: {rgn_mentions}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Unified prediction runner
    # ------------------------------------------------------------------
    async def _run_prediction(
        self,
        image_url: str,
        message: discord.Message,
        allowed_mentions: discord.AllowedMentions,
        *,
        reply: bool = False,
        only_pings_check: bool = False,
    ) -> None:
        """
        Run a prediction for `image_url` and send the result to `message.channel`.

        Parameters
        ----------
        image_url         : URL of the image to predict.
        message           : The Discord message that triggered the prediction.
                            Used for channel, guild, reply target, and logging.
        allowed_mentions  : Pass NO_MENTIONS for p!predict, SAFE_MENTIONS for auto-predict.
        reply             : If True, reply to `message` instead of sending a new message.
        only_pings_check  : If True, honour the guild's only_pings setting and skip
                            sending if nobody has pings (used by Poketwo spawn detection).
        """
        if self.predictor is None or self.http_session is None:
            return

        try:
            # ── 1. Predict (cache-aware) ──────────────────────────────────
            cache_key = self.predictor._generate_cache_key(image_url)
            cached_result = self.predictor.cache.get(cache_key)

            if cached_result:
                name, confidence, model_used = cached_result
            else:
                name, confidence = await self.predictor.predict(image_url, self.http_session)
                if hasattr(self.bot, 'prediction_count'):
                    self.bot.prediction_count += 1
                # In-memory only — batched to Mongo by _flush_prediction_stats.
                self.bot.predictions_since_flush += 1
                cached_result = self.predictor.cache.get(cache_key)
                model_used = cached_result[2] if cached_result else "unknown"

            if not name or not confidence:
                return

            # ── 2. Guild settings (cached) ────────────────────────────────
            guild_settings = await self.gcache.get_guild_settings(message.guild.id)
            show_best  = guild_settings.get('best_name_enabled', False)
            show_catch = guild_settings.get('catch_command_enabled', False)

            # ── 3. only_pings gate (Poketwo spawns only) ──────────────────
            if only_pings_check:
                only_pings_enabled = guild_settings.get('only_pings', False)
                ping_data = await self._get_all_ping_data(name, message.guild.id)
                if not self.should_send_prediction_from_data(only_pings_enabled, ping_data):
                    # Still do low-confidence + secondary logging even if suppressed
                    self._maybe_log_low_confidence(name, confidence, message, image_url)
                    self._create_bg_task(self.log_secondary_model_prediction(
                        name, confidence, model_used, message, image_url
                    ))
                    return
                output = await self.build_prediction_output(
                    name, confidence, message.guild.id,
                    show_best_name=show_best, show_catch_command=show_catch,
                    _preloaded_ping_data=ping_data,
                )
            else:
                output = await self.build_prediction_output(
                    name, confidence, message.guild.id,
                    show_best_name=show_best, show_catch_command=show_catch,
                )

            # ── 4. Send ───────────────────────────────────────────────────
            if reply:
                await message.reply(output, mention_author=False, allowed_mentions=allowed_mentions)
            else:
                await message.channel.send(output, allowed_mentions=allowed_mentions)

            # ── 5. Low-confidence channel + secondary model logging ───────
            self._maybe_log_low_confidence(name, confidence, message, image_url)
            self._create_bg_task(self.log_secondary_model_prediction(
                name, confidence, model_used, message, image_url
            ))

        except ValueError as e:
            error_msg = str(e)
            if "404" in error_msg or "Failed to load image" in error_msg:
                print(f"[PREDICT] Image not accessible: {image_url[:100]}")
            else:
                print(f"[PREDICT] ValueError: {e}")
        except Exception as e:
            if "not loaded" not in str(e):
                print(f"[PREDICT] Error: {e}")
                import traceback
                traceback.print_exc()

    def _maybe_log_low_confidence(
        self,
        name: str,
        confidence: str,
        message: discord.Message,
        image_url: str,
    ) -> None:
        """Fire-and-forget the low-confidence channel log if applicable."""
        try:
            confidence_value = float(str(confidence).rstrip('%'))
        except ValueError:
            return
        if confidence_value < PREDICTION_CONFIDENCE:
            self._create_bg_task(self._send_low_confidence_log(name, confidence, message, image_url))

    async def _send_low_confidence_log(
        self,
        name: str,
        confidence: str,
        message: discord.Message,
        image_url: str,
    ) -> None:
        low_channel_id = await self.db.get_low_prediction_channel()
        if not low_channel_id:
            return
        low_channel = self.bot.get_channel(low_channel_id)
        if not low_channel:
            return

        low_embed = discord.Embed(
            title="Low Confidence Prediction",
            description=(
                f"**Pokemon:** {name}\n"
                f"**Confidence:** {confidence}\n"
                f"**Server:** {message.guild.name}\n"
                f"**Channel:** {message.channel.mention}"
            ),
            color=0xff9900,
        )
        if image_url:
            low_embed.set_thumbnail(url=image_url)

        low_view = discord.ui.View()
        low_view.add_item(discord.ui.Button(
            label="Jump to Message",
            url=message.jump_url,
            emoji="🔗",
            style=discord.ButtonStyle.link,
        ))
        await low_channel.send(embed=low_embed, view=low_view)

    # ------------------------------------------------------------------
    # should_send_prediction
    # ------------------------------------------------------------------
    def should_send_prediction_from_data(
        self,
        only_pings_enabled: bool,
        ping_data: dict,
    ) -> bool:
        """
        Synchronous check — ping_data already fetched, only_pings already known.
        Avoids an extra DB call for get_only_pings().
        Reserved spawns always send (reserve holders need to be notified).
        """
        if not only_pings_enabled:
            return True

        # Reserved pokemon always get sent so reserve holders are notified
        if ping_data.get("is_reserved"):
            return True

        return (
            bool(ping_data.get("hunters"))
            or bool(ping_data.get("collectors"))
            or bool(ping_data.get("rare_ping"))
            or bool(ping_data.get("regional_ping"))
            or bool(ping_data.get("type_pingers"))
            or bool(ping_data.get("rgn_pingers"))
        )

    # ------------------------------------------------------------------
    # Secondary model logging
    # ------------------------------------------------------------------
    async def log_secondary_model_prediction(self, name, confidence, model_used, message, image_url):
        if model_used not in ["secondary", "primary_fallback"]:
            return

        secondary_channel_id = await self.db.get_secondary_model_channel()
        if not secondary_channel_id:
            return

        secondary_channel = self.bot.get_channel(secondary_channel_id)
        if not secondary_channel:
            return

        # Extract everything we need from the message object immediately, then
        # drop the reference so the large discord.Message (with guild/channel
        # state, embeds, attachments) is not retained for the duration of this
        # async function — especially important when the background task queue
        # fills up during heavy incense sessions.
        guild_name    = message.guild.name if message.guild else "Unknown"
        channel_mention = message.channel.mention if message.channel else "Unknown"
        jump_url      = message.jump_url
        del message  # release discord.Message reference ASAP

        try:
            model_label = (
                "Secondary Model (High Confidence)"
                if model_used == "secondary"
                else "Secondary Model Used (Fallback to Primary)"
            )

            embed = discord.Embed(
                title=f"🔬 {model_label}",
                description=(
                    f"**Pokemon:** {name}\n"
                    f"**Confidence:** {confidence}\n"
                    f"**Server:** {guild_name}\n"
                    f"**Channel:** {channel_mention}"
                ),
                color=0x00bfff
            )

            if image_url:
                embed.set_thumbnail(url=image_url)

            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Jump to Message",
                url=jump_url,
                emoji="🔗",
                style=discord.ButtonStyle.link
            ))

            await secondary_channel.send(embed=embed, view=view)
            print(f"[SECONDARY-MODEL] Logged: {name} ({confidence}) - {model_used}")

        except Exception as e:
            print(f"[SECONDARY-MODEL] Failed to log: {e}")

    # ------------------------------------------------------------------
    # p!predict command
    # ------------------------------------------------------------------
    @commands.command(name="predict", aliases=["pred", "p"])
    async def predict_command(self, ctx, *, image_url: str = None):
        """Predict Pokemon from image URL or replied message"""
        if not image_url and ctx.message.reference:
            try:
                replied_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                image_url = await self.extract_image_url(replied_message)
            except discord.NotFound:
                await ctx.reply("Could not find the replied message.", mention_author=False)
                return
            except discord.Forbidden:
                await ctx.reply("I don't have permission to access that message.", mention_author=False)
                return
            except Exception as e:
                await ctx.reply(f"Error fetching replied message: {str(e)[:100]}", mention_author=False)
                return

        if not image_url:
            await ctx.reply(
                "Please provide an image URL after p!predict or reply to a message with an image.",
                mention_author=False
            )
            return

        await self._run_prediction(image_url, ctx.message, NO_MENTIONS, reply=True)

    # ------------------------------------------------------------------
    # on_message listener
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return
        if not message.guild:
            return
        if self.predictor is None:
            return

        # ---- Auto-predict channel ----------------------------------------
        if AUTO_PREDICT_CHANNEL_ID and message.channel.id == AUTO_PREDICT_CHANNEL_ID:
            if not self.predictor.models_initialized:
                return
            image_url = await self.extract_image_url(message)
            if image_url:
                await self._run_prediction(image_url, message, NO_MENTIONS)

        # ---- Poketwo spawn detection in other channels --------------------
        elif message.author.id == POKETWO_USER_ID:
            if not _is_spawn_message(message):
                return
            if not self.predictor.models_initialized:
                return
            image_url = await self.extract_image_url(message)
            if image_url:
                await self._run_prediction(
                    image_url, message, SAFE_MENTIONS, reply=True, only_pings_check=True
                )


async def setup(bot):
    await bot.add_cog(Prediction(bot))
