"""Release & Evolve ID manager — one fully self-contained cog.

Drop this file in cogs/ and add 'cogs.release_evolve' to cogs_to_load in main.py.
Nothing else in the bot is touched: the DB helpers, views, modals and commands all
live in this file, so deleting it (and its line in main.py) removes the feature.

Data lives in two collections created on first use inside the bot's existing
Mongo database (bot.db.db):
    helper_release_ids   {user_id, ids: ["123", "456", ...]}
    helper_evolve_ids    {user_id, ids: [{"id": "123", "uses": 2}, ...]}
To wipe the feature completely, drop those two collections.

Commands (prefix + aliases; aliases are editable in ALIASES below)
    release  <count>               hybrid: also /release
    releasepanel / rpanel   releaseadd / radd   releaseremove / rremove
    releaseclear / rclear   releaselist / rlist
    evolve   <count>               hybrid: also /evolve
    evolvepanel / epanel    evolveadd / eadd    evolveremove / eremove  (--once)
    evolveclear / eclear    evolvelist / elist
    idmigrate  (owner only) — copy lists from the old helper bot's database

Adding IDs (releaseadd / evolveadd) works two ways, and both can be combined:
    releaseadd 123 456 789      type the IDs
    (reply to a Pokétwo embed) releaseadd
                                IDs are read straight from the embed, then the message is
                                watched for edits (e.g. flipping pages) and new IDs are added
                                live. Watching stops after EDIT_WATCH_SECS without an edit,
                                when the message is deleted, or when the cog unloads.
"""
import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import EMBED_COLOR

# ─────────────────────────────────────────────────────────────────────
#  Settings (everything you might want to tweak lives here)
# ─────────────────────────────────────────────────────────────────────

POKETWO_ID = 716390085896962058
POKETWO_MENTION = f"<@{POKETWO_ID}>"

RELEASE_COLLECTION = "helper_release_ids"
EVOLVE_COLLECTION = "helper_evolve_ids"

RELEASE_PER_PAGE = 150      # IDs per page in the release list view
EVOLVE_PER_PAGE = 50        # IDs per page in the evolve list view
MAX_COMMAND_CHARS = 1900    # max length of one "<@Poketwo> r 1 2 3…" line (fits a normal 2000-char message)
EDIT_WATCH_SECS = 120       # releaseadd/evolveadd as a reply: stop watching after this long with no edit (each edit resets it)

# Edit / empty a list here if an alias collides with another cog.
ALIASES = {
    "releasepanel": ["rpanel"], "releaseadd": ["radd"], "releaseremove": ["rremove"],
    "releaseclear": ["rclear"], "releaselist": ["rlist"], "release": [],
    "evolvepanel": ["epanel"], "evolveadd": ["eadd"], "evolveremove": ["eremove"],
    "evolveclear": ["eclear"], "evolvelist": ["elist"], "evolve": [],
}

NO_MENTIONS = discord.AllowedMentions.none()
_NO_DB = "❌ Database isn't available right now. Please try again later."


@dataclass(frozen=True)
class _Kind:
    name: str            # "release" / "evolve"
    collection: str
    command_word: str    # what follows the Poketwo mention in the output
    per_page: int
    has_uses: bool       # evolve entries carry a 1x / 2x use counter

    @property
    def label(self) -> str:
        return self.name.capitalize()


RELEASE = _Kind("release", RELEASE_COLLECTION, "r", RELEASE_PER_PAGE, False)
EVOLVE = _Kind("evolve", EVOLVE_COLLECTION, "evolve", EVOLVE_PER_PAGE, True)


# ─────────────────────────────────────────────────────────────────────
#  Small pure helpers
# ─────────────────────────────────────────────────────────────────────

def _parse_ids(tokens) -> tuple[list[str], list[str]]:
    """Split raw tokens into (unique numeric IDs in order, ignored junk)."""
    valid, invalid, seen = [], [], set()
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if not (tok.isascii() and tok.isdigit()):
            invalid.append(tok)
            continue
        if tok not in seen:
            seen.add(tok)
            valid.append(tok)
    return valid, invalid


def _invalid_note(invalid: list[str]) -> str:
    if not invalid:
        return ""
    shown = ", ".join(f"`{t[:20]}`" for t in invalid[:5])
    more = f" (+{len(invalid) - 5} more)" if len(invalid) > 5 else ""
    return f"\n⚠️ Ignored non-numeric: {shown}{more}"


def _split_once_flag(args) -> tuple[list[str], bool]:
    tokens = [a for a in args if a.lower() != "--once"]
    return tokens, len(tokens) != len(args)


def _command_embeds(kind: _Kind, ids: list[str], footer: str) -> list[discord.Embed]:
    """Build the copy-paste embed(s): '<@Poketwo> r 1 2 3'. Long lists are split so
    every embed's command line stays under MAX_COMMAND_CHARS."""
    head = f"{POKETWO_MENTION} {kind.command_word} "
    chunks, cur, cur_len = [], [], len(head)
    for pid in ids:
        if cur and cur_len + len(pid) + 1 > MAX_COMMAND_CHARS:
            chunks.append(cur)
            cur, cur_len = [], len(head)
        cur.append(pid)
        cur_len += len(pid) + 1
    if cur:
        chunks.append(cur)

    embeds = []
    for n, chunk in enumerate(chunks, 1):
        embed = discord.Embed(description=f"```\n{head}{' '.join(chunk)}\n```", color=EMBED_COLOR)
        part = f"Part {n}/{len(chunks)} • " if len(chunks) > 1 else ""
        embed.set_footer(text=part + footer)
        embeds.append(embed)
    return embeds


# Pokétwo list / marketplace / gigantamax embeds all start each row with the ID in backticks:
#   `39556693`  ...   ` 9593`  ...   **`586470`**  ...
_ID_LINE = re.compile(r"^\*{0,2}`\s*(\d+)\s*`\*{0,2}")


def _extract_ids(message: discord.Message) -> list[str]:
    """IDs from a message's embeds, top-to-bottom, de-duplicated.

    Only the first backticked number of each line counts, so stray numbers elsewhere
    on a row (levels, prices…) can never end up on a release list by accident.
    """
    ids, seen = [], set()
    for embed in message.embeds:
        for line in (embed.description or "").splitlines():
            m = _ID_LINE.match(line.strip())
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                ids.append(m.group(1))
    return ids


@dataclass(eq=False)
class _Watch:
    """One live "reply + add" session: a message being watched for edits."""
    kind: "_Kind"
    user_id: int
    uses: int
    source_id: int
    channel: discord.abc.Messageable
    note: str = ""
    status: Optional[discord.Message] = None
    seen: set = field(default_factory=set)      # IDs already read from the message
    added: int = 0                              # new IDs written to the list this session
    total: int = 0                              # list size after the latest write
    edited: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None


async def _reply(ctx: commands.Context, content: Optional[str] = None, *,
                 embed: Optional[discord.Embed] = None,
                 view: Optional[discord.ui.View] = None,
                 ephemeral: bool = False):
    kwargs = {"mention_author": False, "allowed_mentions": NO_MENTIONS}
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if ephemeral and ctx.interaction is not None:   # prefix replies can't be ephemeral
        kwargs["ephemeral"] = True
    return await ctx.reply(content, **kwargs)


async def _isend(interaction: discord.Interaction, content: Optional[str] = None, *,
                 embed: Optional[discord.Embed] = None,
                 view: Optional[discord.ui.View] = None,
                 ephemeral: bool = False):
    kwargs = {"ephemeral": ephemeral, "allowed_mentions": NO_MENTIONS}
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if interaction.response.is_done():
        return await interaction.followup.send(content, **kwargs)
    return await interaction.response.send_message(content, **kwargs)


async def _isend_embeds(interaction: discord.Interaction, embeds: list[discord.Embed]):
    for embed in embeds:
        await _isend(interaction, embed=embed)


# ─────────────────────────────────────────────────────────────────────
#  Views
# ─────────────────────────────────────────────────────────────────────

class _OwnedView(discord.ui.View):
    """View that only its creator can use and that greys out on timeout."""

    def __init__(self, owner_id: int, timeout: float):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ This isn't your panel — run the command yourself.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class _ListView(_OwnedView):
    """Paginated ID list. tabs = [(key, button_label, heading_or_None, ids)]."""

    def __init__(self, owner_id: int, title: str, tabs: list, per_page: int):
        super().__init__(owner_id, timeout=180)
        self.title = title
        self.current = tabs[0][0]
        self.page = 0
        self._ids = {key: ids for key, _, _, ids in tabs}
        self._heading = {key: heading for key, _, heading, _ in tabs}
        self._pages = {
            key: [" ".join(ids[i:i + per_page]) for i in range(0, len(ids), per_page)]
            for key, _, _, ids in tabs
        }
        self._tab_buttons: dict[str, discord.ui.Button] = {}

        if len(tabs) > 1:
            for key, label, _, _ in tabs:
                button = discord.ui.Button(label=label)
                button.callback = self._tab_callback(key)
                self._tab_buttons[key] = button
                self.add_item(button)

        self.prev_btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.primary)
        self.next_btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.primary)
        self.prev_btn.callback = self._prev
        self.next_btn.callback = self._next
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self._sync()

    @property
    def needs_view(self) -> bool:
        return len(self._tab_buttons) > 0 or any(len(p) > 1 for p in self._pages.values())

    def _sync(self):
        pages = self._pages[self.current]
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= len(pages) - 1
        for key, button in self._tab_buttons.items():
            button.style = (discord.ButtonStyle.primary if key == self.current
                            else discord.ButtonStyle.secondary)

    def get_embed(self) -> discord.Embed:
        pages = self._pages[self.current]
        heading = self._heading[self.current]
        title = self.title if heading is None else f"{self.title} - {heading}"
        description = f"```\n{pages[self.page]}\n```" if pages else "```\nNo IDs in this category\n```"
        footer = f"Total: {len(self._ids[self.current])} ID(s)"
        if len(pages) > 1:
            footer += f" • Page {self.page + 1}/{len(pages)}"
        embed = discord.Embed(title=title, description=description, color=EMBED_COLOR)
        embed.set_footer(text=footer)
        return embed

    def _tab_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            self.current, self.page = key, 0
            self._sync()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        return callback

    async def _prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page = min(len(self._pages[self.current]) - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


# ─────────────────────────────────────────────────────────────────────
#  Modals
# ─────────────────────────────────────────────────────────────────────

def _ids_field() -> discord.ui.TextInput:
    return discord.ui.TextInput(
        label="Pokemon IDs (space-separated)", placeholder="123 456 789",
        style=discord.TextStyle.paragraph, required=True, max_length=2000,
    )


class _AddModal(discord.ui.Modal):
    def __init__(self, cog: "ReleaseEvolve", kind: _Kind):
        super().__init__(title=f"Add IDs to {kind.label} List")
        self.cog, self.kind = cog, kind
        self.ids_input = _ids_field()
        self.add_item(self.ids_input)
        self.uses_input = None
        if kind.has_uses:
            self.uses_input = discord.ui.TextInput(
                label="Uses per ID (1 or 2)", placeholder="2", default="2",
                style=discord.TextStyle.short, required=True, max_length=1,
            )
            self.add_item(self.uses_input)

    async def on_submit(self, interaction: discord.Interaction):
        uses = 1
        if self.uses_input is not None:
            if self.uses_input.value not in ("1", "2"):
                await interaction.response.send_message("❌ Uses must be either 1 or 2!", ephemeral=True)
                return
            uses = int(self.uses_input.value)
        text = await self.cog.add(self.kind, interaction.user.id, self.ids_input.value.split(), uses)
        await interaction.response.send_message(text, ephemeral=True)


class _RemoveModal(discord.ui.Modal):
    def __init__(self, cog: "ReleaseEvolve", kind: _Kind):
        super().__init__(title=f"Remove IDs from {kind.label} List")
        self.cog, self.kind = cog, kind
        self.ids_input = _ids_field()
        self.add_item(self.ids_input)
        self.type_input = None
        if kind.has_uses:
            self.type_input = discord.ui.TextInput(
                label="Remove type (all/once)", placeholder="all", default="all",
                style=discord.TextStyle.short, required=True, max_length=10,
            )
            self.add_item(self.type_input)

    async def on_submit(self, interaction: discord.Interaction):
        once = False
        if self.type_input is not None:
            remove_type = self.type_input.value.strip().lower()
            if remove_type not in ("all", "once"):
                await interaction.response.send_message("❌ Remove type must be 'all' or 'once'!", ephemeral=True)
                return
            once = remove_type == "once"
        text = await self.cog.remove(self.kind, interaction.user.id, self.ids_input.value.split(), once)
        await interaction.response.send_message(text, ephemeral=True)


class _TakeModal(discord.ui.Modal):
    def __init__(self, cog: "ReleaseEvolve", kind: _Kind):
        super().__init__(title=f"{kind.label} Pokemon IDs")
        self.cog, self.kind = cog, kind
        self.count_input = discord.ui.TextInput(
            label=f"Number of IDs to {kind.name}", placeholder="7",
            style=discord.TextStyle.short, required=True, max_length=10,
        )
        self.add_item(self.count_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            count = int(self.count_input.value)
        except ValueError:
            await interaction.response.send_message("❌ Count must be a valid number!", ephemeral=True)
            return
        result = await self.cog.take(self.kind, interaction.user.id, count)
        if isinstance(result, str):
            await interaction.response.send_message(result, ephemeral=True)
        else:
            await _isend_embeds(interaction, result)


class _PanelView(_OwnedView):
    """Main management panel (same layout for release and evolve)."""

    def __init__(self, cog: "ReleaseEvolve", kind: _Kind, owner_id: int):
        super().__init__(owner_id, timeout=300)
        self.cog, self.kind = cog, kind
        self.take_button.label = f"🔄 {kind.label} IDs"

    @discord.ui.button(label="➕ Add IDs", style=discord.ButtonStyle.success, row=0)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_AddModal(self.cog, self.kind))

    @discord.ui.button(label="➖ Remove IDs", style=discord.ButtonStyle.danger, row=0)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_RemoveModal(self.cog, self.kind))

    @discord.ui.button(label="📋 View List", style=discord.ButtonStyle.primary, row=0)
    async def list_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = await self.cog.build_list(self.kind, interaction.user.id)
        if isinstance(result, str):
            await interaction.response.send_message(result, ephemeral=True)
            return
        embed, view = result
        await _isend(interaction, embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="🗑️ Clear All", style=discord.ButtonStyle.secondary, row=1)
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        text = await self.cog.clear(self.kind, interaction.user.id)
        await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(label="🔄", style=discord.ButtonStyle.primary, row=1)
    async def take_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_TakeModal(self.cog, self.kind))


# ─────────────────────────────────────────────────────────────────────
#  Cog
# ─────────────────────────────────────────────────────────────────────

class ReleaseEvolve(commands.Cog):
    """Personal Pokemon ID lists for releasing and evolving."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}
        # active edit-watchers: (source_message_id, list name, user_id) -> _Watch
        self._watches: dict[tuple[int, str, int], _Watch] = {}

    def cog_unload(self):
        for watch in list(self._watches.values()):
            if watch.task and not watch.task.done():
                watch.task.cancel()
        self._watches.clear()

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        """Wake any watcher of this message. Raw event so it also fires for messages
        that aren't in the bot's cache; the watcher re-fetches the message itself."""
        for watch in list(self._watches.values()):
            if watch.source_id == payload.message_id:
                watch.edited.set()

    async def cog_load(self):
        for kind in (RELEASE, EVOLVE):
            col = self._col(kind)
            if col is None:
                print(f"⚠️ ReleaseEvolve: database unavailable, {kind.name} lists disabled")
                continue
            try:
                await col.create_index("user_id", unique=True)
            except Exception as e:
                print(f"⚠️ ReleaseEvolve: could not create index on {kind.collection}: {e}")

    # ── DB helpers (all of this cog's persistence is below) ──────────

    def _col(self, kind: _Kind):
        mongo = getattr(getattr(self.bot, "db", None), "db", None)
        return None if mongo is None else mongo[kind.collection]

    def _lock(self, kind: _Kind, user_id: int) -> asyncio.Lock:
        """One lock per (list, user) so quick back-to-back commands can't overwrite each other."""
        return self._locks.setdefault((kind.name, user_id), asyncio.Lock())

    async def _load(self, kind: _Kind, user_id: int) -> list[dict]:
        """Always returns [{'id': str, 'uses': 1|2}, ...] (release entries use 1)."""
        doc = await self._col(kind).find_one({"user_id": user_id})
        raw = doc.get("ids", []) if doc else []
        if not kind.has_uses:
            return [{"id": str(x), "uses": 1} for x in raw]
        return [
            {"id": str(x["id"]), "uses": 1 if x.get("uses") == 1 else 2}
            for x in raw if isinstance(x, dict) and "id" in x
        ]

    async def _save(self, kind: _Kind, user_id: int, items: list[dict]):
        ids = items if kind.has_uses else [x["id"] for x in items]
        await self._col(kind).update_one(
            {"user_id": user_id}, {"$set": {"ids": ids}}, upsert=True
        )

    # ── Operations (return user-facing text, or embeds for take) ─────

    async def add(self, kind: _Kind, user_id: int, tokens, uses: int = 2) -> str:
        if self._col(kind) is None:
            return _NO_DB
        ids, invalid = _parse_ids(tokens)
        note = _invalid_note(invalid)
        if not ids:
            return "❌ Please provide at least one valid numeric ID!" + note

        new, total = await self._add_ids(kind, user_id, ids, uses)
        return self._add_text(kind, len(new), total, uses) + note

    async def _add_ids(self, kind: _Kind, user_id: int, ids: list[str], uses: int = 2) -> tuple[list[str], int]:
        """Write already-validated IDs. Returns (IDs that were actually new, list size afterwards)."""
        async with self._lock(kind, user_id):
            items = await self._load(kind, user_id)
            existing = {x["id"] for x in items}
            new = [i for i in ids if i not in existing]
            if new:
                items.extend({"id": i, "uses": uses if kind.has_uses else 1} for i in new)
                await self._save(kind, user_id, items)
        return new, len(items)

    @staticmethod
    def _add_text(kind: _Kind, added: int, total: int, uses: int) -> str:
        if not added:
            return f"⚠️ No new IDs added (all were duplicates). Total IDs: {total}"
        use_text = ""
        if kind.has_uses:
            use_text = " with 1 use" if uses == 1 else " with 2 uses"
        return f"✅ Added {added} ID(s){use_text} to your {kind.name} list! Total IDs: {total}"

    async def remove(self, kind: _Kind, user_id: int, tokens, once: bool = False) -> str:
        if self._col(kind) is None:
            return _NO_DB
        ids, invalid = _parse_ids(tokens)
        note = _invalid_note(invalid)
        if not ids:
            return "❌ Please provide at least one valid numeric ID!" + note
        targets = set(ids)

        async with self._lock(kind, user_id):
            items = await self._load(kind, user_id)
            if not items:
                return f"❌ Your {kind.name} list is empty!"
            kept, changed = [], 0
            for item in items:
                if item["id"] not in targets:
                    kept.append(item)
                    continue
                changed += 1
                if once and item["uses"] > 1:        # evolve: drop one use, keep the ID
                    kept.append({"id": item["id"], "uses": item["uses"] - 1})
            if changed:
                await self._save(kind, user_id, kept)

        if not changed:
            return f"⚠️ No IDs were removed (not found in your list). Total IDs: {len(kept)}" + note
        what = "use(s)" if (once and kind.has_uses) else "ID(s)"
        return f"✅ Removed {changed} {what} from your {kind.name} list! Remaining IDs: {len(kept)}" + note

    async def clear(self, kind: _Kind, user_id: int) -> str:
        if self._col(kind) is None:
            return _NO_DB
        async with self._lock(kind, user_id):
            items = await self._load(kind, user_id)
            if not items:
                return f"❌ Your {kind.name} list is already empty!"
            await self._save(kind, user_id, [])
        return f"✅ Cleared all {len(items)} ID(s) from your {kind.name} list!"

    async def take(self, kind: _Kind, user_id: int, count: int):
        """Pop `count` IDs (1x entries first). Returns embeds to send, or an error string."""
        if self._col(kind) is None:
            return _NO_DB
        if count <= 0:
            return "❌ Please provide a positive number!"

        async with self._lock(kind, user_id):
            items = await self._load(kind, user_id)
            if not items:
                return f"❌ Your {kind.name} list is empty! Add IDs first with `{kind.name}add`."
            if count > len(items):
                return f"❌ You only have {len(items)} ID(s) available in your {kind.name} list!"

            ordered = [x for x in items if x["uses"] == 1] + [x for x in items if x["uses"] != 1]
            taken, rest = ordered[:count], ordered[count:]
            # a 2x ID that was just used stays on the list with one use left
            rest += [{"id": x["id"], "uses": 1} for x in taken if x["uses"] == 2]
            await self._save(kind, user_id, rest)

        if kind.has_uses:
            once_n = sum(1 for x in taken if x["uses"] == 1)
            twice_n = len(taken) - once_n
            parts = ([f"{once_n} from 1x"] if once_n else []) + ([f"{twice_n} from 2x"] if twice_n else [])
            footer = f"{count} ID(s) used ({', '.join(parts)}) • {len(rest)} remaining"
        else:
            footer = f"{count} ID(s) removed from your {kind.name} list • {len(rest)} remaining"
        return _command_embeds(kind, [x["id"] for x in taken], footer)

    async def build_list(self, kind: _Kind, user_id: int):
        """Returns (embed, view_or_None), or an error string."""
        if self._col(kind) is None:
            return _NO_DB
        items = await self._load(kind, user_id)
        if not items:
            return f"❌ Your {kind.name} list is empty! Add IDs first with `{kind.name}add`."

        if kind.has_uses:
            tabs = [
                ("once", "Once (1x) ⭐", "1x Use (Priority)", [x["id"] for x in items if x["uses"] == 1]),
                ("twice", "Twice (2x)", "2x Uses", [x["id"] for x in items if x["uses"] != 1]),
            ]
        else:
            tabs = [("all", "All", None, [x["id"] for x in items])]
        view = _ListView(user_id, f"📋 Your {kind.label} List", tabs, kind.per_page)
        return view.get_embed(), (view if view.needs_view else None)

    # ── Shared command bodies ────────────────────────────────────────

    async def _send_panel(self, ctx: commands.Context, kind: _Kind):
        extra = ("⭐ **Priority System:** IDs with 1x use are picked first, then 2x use\n"
                 if kind.has_uses else "")
        embed = discord.Embed(
            title=f"🔧 {kind.label} Management Panel",
            description=(
                f"**Welcome to the {kind.label} ID Manager!**\n\n"
                f"Use the buttons below to manage your Pokemon {kind.name} list:\n\n"
                "➕ **Add IDs** - Add new Pokemon IDs to your list\n"
                "➖ **Remove IDs** - Remove IDs from your list\n"
                "📋 **View List** - See all your saved IDs\n"
                "🗑️ **Clear All** - Remove all IDs from your list\n"
                f"🔄 **{kind.label} IDs** - Use IDs to {kind.name} Pokemon\n\n"
                f"{extra}*Panel expires after 5 minutes of inactivity*"
            ),
            color=EMBED_COLOR,
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}",
                         icon_url=ctx.author.display_avatar.url)
        view = _PanelView(self, kind, ctx.author.id)
        view.message = await _reply(ctx, embed=embed, view=view)

    async def _send_add(self, ctx: commands.Context, kind: _Kind, args):
        """Add IDs typed after the command and/or read from a replied-to embed."""
        tokens, once = _split_once_flag(args) if kind.has_uses else (list(args), False)
        uses = 1 if once else 2

        source, error = await self._reply_source(ctx)
        if error:
            await _reply(ctx, error)
            return
        extracted = _extract_ids(source) if source is not None else []

        if not tokens and not extracted:
            if source is not None:
                await _reply(ctx, "❌ I couldn't find any Pokemon IDs in that message's embeds!")
            else:
                await _reply(ctx, f"❌ Give me some IDs (`{kind.name}add 123 456`) "
                                  f"or reply to a Pokétwo embed with `{kind.name}add`.")
            return

        if not extracted:       # plain typed IDs, nothing to watch
            await _reply(ctx, await self.add(kind, ctx.author.id, tokens, uses))
            return

        if self._col(kind) is None:
            await _reply(ctx, _NO_DB)
            return
        ids, invalid = _parse_ids(list(tokens) + extracted)
        new, total = await self._add_ids(kind, ctx.author.id, ids, uses)

        watch = _Watch(kind=kind, user_id=ctx.author.id, uses=uses,
                       source_id=source.id, channel=source.channel,
                       note=_invalid_note(invalid), seen=set(extracted),
                       added=len(new), total=total)
        watch.status = await _reply(ctx, self._watch_text(watch, watching=True))
        self._start_watch(watch)

    # ── reply extraction + edit watching ─────────────────────────────

    async def _reply_source(self, ctx: commands.Context) -> tuple[Optional[discord.Message], Optional[str]]:
        """The message being replied to: (message, None), (None, None) if not a reply,
        or (None, error text) if it couldn't be fetched."""
        ref = ctx.message.reference
        if ref is None or ref.message_id is None:
            return None, None
        if isinstance(ref.resolved, discord.Message):
            return ref.resolved, None
        try:
            return await ctx.channel.fetch_message(ref.message_id), None
        except discord.NotFound:
            return None, "❌ Could not find the replied message!"
        except discord.Forbidden:
            return None, "❌ I don't have permission to read that message!"
        except discord.HTTPException:
            return None, "❌ Couldn't fetch the replied message, please try again."

    @staticmethod
    def _watch_text(watch: _Watch, watching: bool) -> str:
        text = ReleaseEvolve._add_text(watch.kind, watch.added, watch.total, watch.uses)
        text += f"\n📥 Read {len(watch.seen)} ID(s) from the message" + watch.note
        if watching:
            span = f"{EDIT_WATCH_SECS // 60} min" if EDIT_WATCH_SECS >= 60 else f"{EDIT_WATCH_SECS:g}s"
            text += f"\n👀 Watching that message for edits — stops after {span} without one."
        return text

    def _start_watch(self, watch: _Watch):
        key = (watch.source_id, watch.kind.name, watch.user_id)
        old = self._watches.pop(key, None)       # same user re-running on the same message
        if old is not None and old.task and not old.task.done():
            old.task.cancel()
        self._watches[key] = watch
        watch.task = asyncio.create_task(self._run_watch(key, watch))

    async def _run_watch(self, key, watch: _Watch):
        """Wait for edits to the source message; every edit resets the inactivity timer."""
        try:
            while True:
                try:
                    await asyncio.wait_for(watch.edited.wait(), timeout=EDIT_WATCH_SECS)
                except asyncio.TimeoutError:
                    break
                watch.edited.clear()                 # an edit during the work below sets it again

                try:
                    fresh = await watch.channel.fetch_message(watch.source_id)
                except discord.NotFound:
                    break                            # watched message was deleted
                except discord.HTTPException:
                    continue

                new_ids = [i for i in _extract_ids(fresh) if i not in watch.seen]
                if not new_ids:
                    continue
                watch.seen.update(new_ids)

                try:
                    added, total = await self._add_ids(watch.kind, watch.user_id, new_ids, watch.uses)
                except Exception as e:               # DB hiccup: stop cleanly instead of dying silently
                    print(f"⚠️ ReleaseEvolve: edit-watch write failed: {e}")
                    break
                watch.added += len(added)
                watch.total = total
                try:
                    await watch.status.edit(content=self._watch_text(watch, watching=True),
                                            allowed_mentions=NO_MENTIONS)
                except discord.HTTPException:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:                       # never let the task die unnoticed
            print(f"⚠️ ReleaseEvolve: edit-watch crashed: {e!r}")
        finally:
            await self._finish_watch(key, watch)

    async def _finish_watch(self, key, watch: _Watch):
        """Cleanup: drop the registry entry, remove the 'watching' line, free the state."""
        if self._watches.get(key) is watch:
            del self._watches[key]
        if watch.status is not None:
            try:
                await watch.status.edit(content=self._watch_text(watch, watching=False),
                                        allowed_mentions=NO_MENTIONS)
            except Exception:
                pass
        watch.seen.clear()
        watch.status = None
        watch.task = None

    async def _send_remove(self, ctx: commands.Context, kind: _Kind, args):
        tokens, once = _split_once_flag(args) if kind.has_uses else (list(args), False)
        if not tokens:
            await _reply(ctx, "❌ Please provide at least one ID!")
            return
        await _reply(ctx, await self.remove(kind, ctx.author.id, tokens, once))

    async def _send_list(self, ctx: commands.Context, kind: _Kind):
        result = await self.build_list(kind, ctx.author.id)
        if isinstance(result, str):
            await _reply(ctx, result)
            return
        embed, view = result
        message = await _reply(ctx, embed=embed, view=view)
        if view is not None:
            view.message = message

    async def _send_take(self, ctx: commands.Context, kind: _Kind, count: int):
        result = await self.take(kind, ctx.author.id, count)
        if isinstance(result, str):
            await _reply(ctx, result, ephemeral=True)
            return
        for i, embed in enumerate(result):
            if i == 0:
                await _reply(ctx, embed=embed)
            else:
                await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    # ── Release commands ─────────────────────────────────────────────

    @commands.command(name="releasepanel", aliases=ALIASES["releasepanel"])
    async def release_panel(self, ctx: commands.Context):
        """Open the release management panel."""
        await self._send_panel(ctx, RELEASE)

    @commands.command(name="releaseadd", aliases=ALIASES["releaseadd"])
    async def release_add(self, ctx: commands.Context, *ids: str):
        """Add Pokemon IDs to your release list. Usage: releaseadd 123 456 789"""
        await self._send_add(ctx, RELEASE, ids)

    @commands.command(name="releaseremove", aliases=ALIASES["releaseremove"])
    async def release_remove(self, ctx: commands.Context, *ids: str):
        """Remove Pokemon IDs from your release list. Usage: releaseremove 123 456"""
        await self._send_remove(ctx, RELEASE, ids)

    @commands.command(name="releaseclear", aliases=ALIASES["releaseclear"])
    async def release_clear(self, ctx: commands.Context):
        """Clear your whole release list."""
        await _reply(ctx, await self.clear(RELEASE, ctx.author.id))

    @commands.command(name="releaselist", aliases=ALIASES["releaselist"])
    async def release_list(self, ctx: commands.Context):
        """View your release list."""
        await self._send_list(ctx, RELEASE)

    @commands.hybrid_command(name="release", aliases=ALIASES["release"],
                             description="Release Pokemon IDs from your list")
    @app_commands.describe(count="Number of IDs to release")
    async def release_cmd(self, ctx: commands.Context, count: int):
        """Release IDs from your list. Usage: release 7"""
        await self._send_take(ctx, RELEASE, count)

    # ── Evolve commands ──────────────────────────────────────────────

    @commands.command(name="evolvepanel", aliases=ALIASES["evolvepanel"])
    async def evolve_panel(self, ctx: commands.Context):
        """Open the evolve management panel."""
        await self._send_panel(ctx, EVOLVE)

    @commands.command(name="evolveadd", aliases=ALIASES["evolveadd"])
    async def evolve_add(self, ctx: commands.Context, *args: str):
        """Add IDs to your evolve list. Usage: evolveadd 123 456 (2 uses) | evolveadd 123 --once (1 use)"""
        await self._send_add(ctx, EVOLVE, args)

    @commands.command(name="evolveremove", aliases=ALIASES["evolveremove"])
    async def evolve_remove(self, ctx: commands.Context, *args: str):
        """Remove IDs from your evolve list. Usage: evolveremove 123 | evolveremove 123 --once (one use only)"""
        await self._send_remove(ctx, EVOLVE, args)

    @commands.command(name="evolveclear", aliases=ALIASES["evolveclear"])
    async def evolve_clear(self, ctx: commands.Context):
        """Clear your whole evolve list."""
        await _reply(ctx, await self.clear(EVOLVE, ctx.author.id))

    @commands.command(name="evolvelist", aliases=ALIASES["evolvelist"])
    async def evolve_list(self, ctx: commands.Context):
        """View your evolve list (1x / 2x tabs)."""
        await self._send_list(ctx, EVOLVE)

    @commands.hybrid_command(name="evolve", aliases=ALIASES["evolve"],
                             description="Evolve Pokemon IDs from your list")
    @app_commands.describe(count="Number of IDs to evolve")
    async def evolve_cmd(self, ctx: commands.Context, count: int):
        """Evolve IDs from your list (1x IDs first). Usage: evolve 7"""
        await self._send_take(ctx, EVOLVE, count)

    # ── One-off migration from the old helper bot's database ────────

    @commands.command(name="idmigrate", hidden=True)
    @commands.is_owner()
    async def id_migrate(self, ctx: commands.Context, source_db: str = "discord_bot"):
        """Owner only: copy release/evolve lists from the old helper bot's DB.

        Needs the old data to be on the same Mongo cluster as this bot. Users who
        already have a list here are skipped, so it's safe to run more than once.
        Usage: idmigrate [source_db_name]
        """
        client = getattr(getattr(self.bot, "db", None), "client", None)
        if client is None or self._col(RELEASE) is None:
            await _reply(ctx, _NO_DB)
            return

        source = client[source_db]
        counts = {}
        for kind, old_name in ((RELEASE, "release_ids"), (EVOLVE, "evolve_ids")):
            copied = 0
            async for doc in source[old_name].find({}):
                user_id, raw = doc.get("user_id"), doc.get("ids")
                if user_id is None or not isinstance(raw, list):
                    continue
                if kind.has_uses:
                    ids = [{"id": str(x["id"]), "uses": 1 if x.get("uses") == 1 else 2}
                           for x in raw if isinstance(x, dict) and "id" in x]
                else:
                    ids = [str(x) for x in raw]
                result = await self._col(kind).update_one(
                    {"user_id": user_id}, {"$setOnInsert": {"ids": ids}}, upsert=True
                )
                if result.upserted_id is not None:
                    copied += 1
            counts[kind.name] = copied

        text = (f"✅ Migrated **{counts['release']}** release list(s) and "
                f"**{counts['evolve']}** evolve list(s) from `{source_db}`.")
        if not any(counts.values()):
            text += "\nℹ️ Nothing copied — wrong source DB name, different cluster, or already migrated."
        await _reply(ctx, text)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReleaseEvolve(bot))
