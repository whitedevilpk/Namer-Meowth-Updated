"""EventExtract cog — reply to a Pokétwo pokédex embed and this will monitor it
for edits (e.g. browsing variants via the select menus / buttons) and, once you
press Stop, generate:

  1. eventdata.json            — dex_number, name, other_names, is_variant,
                                  variant_of, rarity
  2. pokemon_cdn_mapping.csv    — name, cdn_number   (from the embed image URL)
  3. typeandregion.csv          — dex_number, name, region, type1, type2

Usage:
  Reply to a Pokétwo embed with  p!extractevent
  Browse through all the variants you want captured (each edit is picked up
  automatically)
  Press the ⏹️ Stop button (or use p!stopextract) when you're done — the bot
  DMs/sends the three files back in the channel.
"""
import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from config import EMBED_COLOR

try:
    # Normal case: eventextract.py lives in the same package as poketools.py
    # (e.g. both under a `cogs` package), so relative import works.
    from .poketools import _resolve_message, _component_texts, _component_image_urls
except ImportError:
    # Fallback for setups where cogs are loaded as top-level modules
    # rather than as a package (no relative import available).
    from poketools import _resolve_message, _component_texts, _component_image_urls

# ------------------------------------------------------------------ #
#  Parsing helpers                                                     #
# ------------------------------------------------------------------ #

# "#666 — Autoromantic Flag Vivillon"  /  "#666 - Name"  /  "#666 – Name"
TITLE_RE = re.compile(r"#(\d+)\s*[-—–]\s*(.+)")

# Any custom emoji <a:name:id> / <:name:id>  or shortcode :name:
_EMOJI_TAG_RE = re.compile(r"<a?:\w+:\d+>")
_SHORTCODE_RE = re.compile(r":\w+:")

# Two regional-indicator symbols in a row = a flag emoji, e.g. 🇬🇧 🇯🇵 🇩🇪
_FLAG_LINE_RE = re.compile(r"^([\U0001F1E6-\U0001F1FF]{2})\s*(.+)$")

# Pull the numeric id out of .../images/50274.png
_CDN_RE = re.compile(r"/(\d+)\.\w+(?:\?.*)?$")

ENGLISH_FLAG = "\U0001F1EC\U0001F1E7"  # 🇬🇧


def _fields_dict(embed: discord.Embed) -> Dict[str, str]:
    return {(f.name or "").strip().lower(): (f.value or "") for f in embed.fields}


def _parse_types(raw: str) -> List[str]:
    types: List[str] = []
    for line in raw.splitlines():
        cleaned = _EMOJI_TAG_RE.sub("", line)
        cleaned = _SHORTCODE_RE.sub("", cleaned)
        cleaned = cleaned.strip()
        if cleaned:
            types.append(cleaned)
    return types


def _parse_other_names(raw: str) -> Dict[str, List[str]]:
    other_names: Dict[str, List[str]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _FLAG_LINE_RE.match(line)
        if not m:
            continue
        flag, name = m.group(1), m.group(2).strip()
        other_names.setdefault(flag, []).append(name)

    # English is usually just the primary title name repeated — drop it in
    # that case. But if there's more than one English line (i.e. an actual
    # alt/nickname alongside the primary), keep all of them.
    english = other_names.get(ENGLISH_FLAG)
    if english is not None and len(english) <= 1:
        del other_names[ENGLISH_FLAG]

    return other_names


def parse_pokemon_embed(embed: discord.Embed) -> Optional[dict]:
    """
    Parse a single Pokétwo pokédex embed into a data dict, or None if the
    embed doesn't look like a pokédex entry (e.g. title doesn't match).
    """
    title = embed.title or ""
    m = TITLE_RE.search(title)
    if not m:
        return None

    dex_number = int(m.group(1))
    name = m.group(2).strip()

    fields = _fields_dict(embed)
    region = fields.get("region", "").strip()
    types = _parse_types(fields.get("types", ""))
    other_names = _parse_other_names(fields.get("names", ""))

    cdn_number = None
    if embed.image and embed.image.url:
        cm = _CDN_RE.search(embed.image.url)
        if cm:
            cdn_number = cm.group(1)

    return {
        "dex_number": dex_number,
        "name": name,
        "other_names": other_names,
        "region": region,
        "type1": types[0] if len(types) > 0 else "",
        "type2": types[1] if len(types) > 1 else "",
        "cdn_number": cdn_number,
    }


# ------------------------------------------------------------------ #
#  Components V2 parsing                                               #
# ------------------------------------------------------------------ #
# Newer Pokétwo messages have no embed: the title, Region / Types / Names
# sections and the image live in a component tree on `message.components`.

# A section label on its own line, e.g. "**Region**", "Types:", "### Names",
# optionally followed by the value on the same line ("**Region:** Kalos").
_V2_LABEL_RE = re.compile(r"^[#*_\s]*(region|types|names)(?=[\s*_:]|$)[\s*_:]*(.*)$", re.IGNORECASE)


def _clean_md(text: str) -> str:
    return text.strip().strip("*_").strip()


def parse_pokemon_components(message: discord.Message) -> Optional[dict]:
    """Parse a Components V2 pokédex message into the same dict as parse_pokemon_embed."""
    components = getattr(message, "components", None)
    texts = _component_texts(components)
    if not texts:
        return None

    lines = [ln for block in texts for ln in block.splitlines()]

    dex_number = name = None
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if dex_number is None:
            tm = TITLE_RE.search(line)
            if tm:
                dex_number = int(tm.group(1))
                name = _clean_md(tm.group(2))
                current = None
                continue
        lm = _V2_LABEL_RE.match(line)
        if lm:
            current = lm.group(1).lower()
            sections.setdefault(current, [])
            rest = lm.group(2).strip()
            if rest:
                sections[current].append(rest)
            continue
        if current is not None:
            sections[current].append(line)

    if dex_number is None:
        return None

    region = " ".join(_clean_md(x) for x in sections.get("region", [])).strip()
    types = _parse_types("\n".join(_clean_md(x) for x in sections.get("types", [])))
    other_names = _parse_other_names("\n".join(sections.get("names", [])))

    cdn_number = None
    for url in _component_image_urls(components):
        cm = _CDN_RE.search(url)
        if cm:
            cdn_number = cm.group(1)
            break

    return {
        "dex_number": dex_number,
        "name": name,
        "other_names": other_names,
        "region": region,
        "type1": types[0] if len(types) > 0 else "",
        "type2": types[1] if len(types) > 1 else "",
        "cdn_number": cdn_number,
    }


def parse_pokemon_message(message: discord.Message) -> Optional[dict]:
    """Old-style embed first, then Components V2."""
    if message.embeds:
        parsed = parse_pokemon_embed(message.embeds[0])
        if parsed is not None:
            return parsed
    return parse_pokemon_components(message)


def _has_parsable_content(message: discord.Message) -> bool:
    return bool(message.embeds or _component_texts(getattr(message, "components", None)))


# ------------------------------------------------------------------ #
#  File builders                                                       #
# ------------------------------------------------------------------ #

def _sorted_entries(entries: Dict[Tuple[int, str], dict]) -> List[dict]:
    return [entries[k] for k in sorted(entries.keys(), key=lambda k: (k[0], k[1].lower()))]


def build_eventdata_json(entries: Dict[Tuple[int, str], dict]) -> io.BytesIO:
    payload = [
        {
            "dex_number": e["dex_number"],
            "name": e["name"],
            "other_names": e["other_names"],
            "is_variant": True,
            "variant_of": None,
            "rarity": "Event",
        }
        for e in _sorted_entries(entries)
    ]
    buf = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.seek(0)
    return buf


def build_cdn_mapping_csv(entries: Dict[Tuple[int, str], dict]) -> io.BytesIO:
    text_buf = io.StringIO()
    writer = csv.writer(text_buf)
    for e in _sorted_entries(entries):
        if not e.get("cdn_number"):
            continue
        writer.writerow([e["name"], e["cdn_number"]])
    buf = io.BytesIO(text_buf.getvalue().encode("utf-8"))
    buf.seek(0)
    return buf


def build_typeandregion_csv(entries: Dict[Tuple[int, str], dict]) -> io.BytesIO:
    text_buf = io.StringIO()
    writer = csv.writer(text_buf)
    writer.writerow(["dex_number", "name", "region", "type1", "type2"])
    for e in _sorted_entries(entries):
        writer.writerow([e["dex_number"], e["name"], e["region"], e["type1"], e["type2"]])
    buf = io.BytesIO(text_buf.getvalue().encode("utf-8"))
    buf.seek(0)
    return buf


def build_best_names_json(entries: Dict[Tuple[int, str], dict]) -> io.BytesIO:
    """
    "Main Name": ""  for every captured entry — left blank intentionally so
    the "best"/common nickname can be filled in by hand afterwards.
    """
    payload = {e["name"]: "" for e in _sorted_entries(entries)}
    buf = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.seek(0)
    return buf


def build_output_files(entries: Dict[Tuple[int, str], dict]) -> List[discord.File]:
    return [
        discord.File(build_eventdata_json(entries), filename="eventdata.json"),
        discord.File(build_cdn_mapping_csv(entries), filename="pokemon_cdn_mapping.csv"),
        discord.File(build_typeandregion_csv(entries), filename="typeandregion.csv"),
        discord.File(build_best_names_json(entries), filename="best_names.json"),
    ]


# ------------------------------------------------------------------ #
#  Session state                                                       #
# ------------------------------------------------------------------ #

@dataclass
class ExtractSession:
    target_message_id: int
    channel: discord.abc.Messageable
    owner_id: int
    jump_url: str
    entries: Dict[Tuple[int, str], dict] = field(default_factory=dict)
    last_key: Optional[Tuple[int, str]] = None
    active: bool = True
    progress_message: Optional[discord.Message] = None
    view: Optional["_StopView"] = None

    def ingest_message(self, message: discord.Message) -> bool:
        """Parse a message (embed or Components V2) and store/update the entry.
        Returns True if a valid pokémon entry was found (new or a re-capture)."""
        parsed = parse_pokemon_message(message)
        if parsed is None:
            return False
        key = (parsed["dex_number"], parsed["name"])
        self.entries[key] = parsed
        self.last_key = key
        return True


def _build_progress_embed(session: ExtractSession) -> discord.Embed:
    embed = discord.Embed(title="📥 Event Extraction — Monitoring", color=EMBED_COLOR)
    embed.add_field(name="Watching", value=f"[Jump to message]({session.jump_url})", inline=False)
    embed.add_field(name="Captured Entries", value=str(len(session.entries)), inline=True)
    if session.last_key is not None:
        last = session.entries[session.last_key]
        embed.add_field(
            name="Last Captured",
            value=f"#{last['dex_number']} — {last['name']}",
            inline=True,
        )
    embed.set_footer(
        text="Browse variants on the watched message — each edit is captured automatically. "
        "Press Stop when you're done."
    )
    return embed


def _build_done_embed(session: ExtractSession) -> discord.Embed:
    embed = discord.Embed(title="✅ Event Extraction — Stopped", color=EMBED_COLOR)
    embed.add_field(name="Watching", value=f"[Jump to message]({session.jump_url})", inline=False)
    embed.add_field(name="Total Captured", value=str(len(session.entries)), inline=True)
    embed.set_footer(text="Files below." if session.entries else "No valid entries were captured.")
    return embed


class _StopView(discord.ui.View):
    def __init__(self, cog: "EventExtract", target_message_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.target_message_id = target_message_id
        self.owner_id = owner_id

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Only the person who started this extraction can stop it.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await self.cog.finalize_session(self.target_message_id, interaction=interaction)


# ------------------------------------------------------------------ #
#  Cog                                                                  #
# ------------------------------------------------------------------ #

class EventExtract(commands.Cog):
    """Watch a Pokétwo embed for edits and export the browsed event pokémon to files."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: Dict[int, ExtractSession] = {}

    # ---------------------------------------------------------------- #
    #  Starting a session                                                #
    # ---------------------------------------------------------------- #

    async def _start_session(self, ctx_or_interaction, target: discord.Message, owner_id: int):
        if target.id in self.sessions and self.sessions[target.id].active:
            msg = "⚠️ Already monitoring that message. Press Stop first if you want to restart."
            if isinstance(ctx_or_interaction, discord.Interaction):
                await ctx_or_interaction.followup.send(msg, ephemeral=True)
            else:
                await ctx_or_interaction.send(msg)
            return

        session = ExtractSession(
            target_message_id=target.id,
            channel=target.channel,
            owner_id=owner_id,
            jump_url=target.jump_url,
        )
        session.ingest_message(target)

        view = _StopView(self, target.id, owner_id)
        session.view = view

        embed = _build_progress_embed(session)
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.followup.send(embed=embed, view=view)
            progress_message = await ctx_or_interaction.original_response()
        else:
            progress_message = await ctx_or_interaction.send(embed=embed, view=view)

        session.progress_message = progress_message
        self.sessions[target.id] = session

    # ---------------------------------------------------------------- #
    #  Prefix command                                                    #
    # ---------------------------------------------------------------- #

    @commands.command(name="extractevent", aliases=["eventextract", "evtx", "startevent"])
    async def extractevent_prefix(self, ctx: commands.Context):
        """
        Reply to a Pokétwo pokédex embed to start monitoring it for edits.
        Every time you browse a variant (select menu / buttons) the new
        data is captured. Press Stop when done to get eventdata.json,
        pokemon_cdn_mapping.csv and typeandregion.csv.

        Usage:
          Reply to a Pokétwo embed with  p!extractevent

        Aliases: p!eventextract, p!evtx, p!startevent
        """
        if not (ctx.message.reference and ctx.message.reference.message_id):
            await ctx.send("❌ Reply to a Pokétwo pokédex embed with this command to start monitoring it.")
            return

        target = await _resolve_message(ctx.channel, ctx.message.reference.message_id, bot=self.bot)
        if target is None:
            await ctx.send("❌ Could not fetch the replied-to message.")
            return
        if not _has_parsable_content(target):
            await ctx.send("❌ That message has no embeds or components to extract from.")
            return

        await self._start_session(ctx, target, ctx.author.id)

    @commands.command(name="stopextract", aliases=["stopeventextract", "evtxstop"])
    async def stopextract_prefix(self, ctx: commands.Context, message_id: Optional[int] = None):
        """
        Manually stop an event extraction session (fallback if the Stop
        button isn't available). If no ID is given and you're replying to
        the watched message, that's used instead.

        Usage:
          p!stopextract                — reply to the watched message
          p!stopextract <message_id>   — stop by message ID
        """
        target_id = message_id
        if target_id is None and ctx.message.reference and ctx.message.reference.message_id:
            target_id = ctx.message.reference.message_id

        if target_id is None or target_id not in self.sessions:
            await ctx.send("❌ No active extraction session found for that message.")
            return

        session = self.sessions[target_id]
        if session.owner_id != ctx.author.id:
            await ctx.send("❌ Only the person who started this extraction can stop it.")
            return

        await self.finalize_session(target_id)

    # ---------------------------------------------------------------- #
    #  Listeners — this is what makes monitoring "active"                #
    # ---------------------------------------------------------------- #

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        session = self.sessions.get(after.id)
        if session is None or not session.active:
            return
        captured = session.ingest_message(after)
        if not captured:
            return

        if session.progress_message is not None:
            try:
                await session.progress_message.edit(embed=_build_progress_embed(session), view=session.view)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        session = self.sessions.get(message.id)
        if session is None or not session.active:
            return
        await self.finalize_session(message.id, reason="the watched message was deleted")

    # ---------------------------------------------------------------- #
    #  Finalizing                                                        #
    # ---------------------------------------------------------------- #

    async def finalize_session(
        self,
        target_message_id: int,
        interaction: Optional[discord.Interaction] = None,
        reason: Optional[str] = None,
    ):
        session = self.sessions.pop(target_message_id, None)
        if session is None or not session.active:
            return
        session.active = False

        if session.view is not None:
            for item in session.view.children:
                item.disabled = True
            if session.progress_message is not None:
                try:
                    await session.progress_message.edit(embed=_build_done_embed(session), view=session.view)
                except discord.HTTPException:
                    pass

        content = f"⏹️ Extraction stopped{f' — {reason}' if reason else ''}."
        if not session.entries:
            content += " No valid pokémon entries were captured."
            if interaction is not None:
                await interaction.followup.send(content)
            else:
                await session.channel.send(content)
            return

        content += f" **{len(session.entries)}** unique entries captured."
        files = build_output_files(session.entries)
        if interaction is not None:
            await interaction.followup.send(content=content, files=files)
        else:
            await session.channel.send(content=content, files=files)


@app_commands.context_menu(name="Start Event Extract")
async def start_event_extract_context_menu(interaction: discord.Interaction, message: discord.Message):
    """Right-click a Pokétwo pokédex embed to start monitoring it for edits."""
    await interaction.response.defer()

    cog: Optional[EventExtract] = interaction.client.get_cog("EventExtract")
    if cog is None:
        await interaction.followup.send("❌ EventExtract cog is not loaded.", ephemeral=True)
        return

    if not _has_parsable_content(message):
        await interaction.followup.send("❌ That message has no embeds or components to extract from.", ephemeral=True)
        return

    await cog._start_session(interaction, message, interaction.user.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(EventExtract(bot))
    bot.tree.add_command(start_event_extract_context_menu)
