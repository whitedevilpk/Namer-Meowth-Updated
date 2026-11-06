"""Reserve system — server-specific Pokémon reservation for admins and allowed roless."""

import math
import re
import traceback
import discord
from discord import app_commands
from discord.ext import commands
from typing import List, Optional
from utils import (
    load_pokemon_data,
    find_all_pokemon_by_name_flexible,
    find_pokemon_by_name_flexible,
    get_pokemon_with_variants,
)
from config import EMBED_COLOR

NO_MENTIONS = discord.AllowedMentions.none()

# Default categories (rare, regional, gigantamax) have moved to default_cats.py.
# Admins can import them into their server via `p!cat defaults`.
# Reserve only works with server-defined categories now.

ITEMS_PER_PAGE = 15  # pokemon per user section on the list embed
REPLY_EMOJI = "<:reply:1503236369126916117>"


# ---------------------------------------------------------------------------
# Pagination view for p!reserve list
# ---------------------------------------------------------------------------
class ReserveListView(discord.ui.View):
    def __init__(self, author_id: int, pages: list[discord.Embed]):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.pages = pages
        self.current = 0
        self.message: discord.Message | None = None
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current <= 0
        self.next_btn.disabled = self.current >= len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This button isn't for you!", ephemeral=True
            )
            return False
        return True

    async def _turn_page(self, interaction: discord.Interaction, delta: int):
        # Views that already timed out have no pages left; always answer the
        # interaction so Discord never shows "interaction failed".
        if not self.pages:
            await interaction.response.defer()
            return
        self.current = max(0, min(len(self.pages) - 1, self.current + delta))
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current], view=self
        )

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.primary)
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._turn_page(interaction, -1)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.primary)
    async def next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._turn_page(interaction, +1)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ):
        # Surface the real traceback in the console instead of failing silently.
        traceback.print_exception(type(error), error, error.__traceback__)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Something went wrong with that button.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Something went wrong with that button.", ephemeral=True
                )
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        self.clear_items()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.message = None
        self.pages = []  # release embed objects from memory


# ---------------------------------------------------------------------------
# Helper to build paginated reserve list embeds
# ---------------------------------------------------------------------------
def build_reserve_list_embeds(
    guild_name: str, reserve_docs: list[dict], member_names: dict[int, str] = None
) -> list[discord.Embed]:
    """
    Build a list of Discord embeds for the reserve list.
    Each 'page' holds up to ITEMS_PER_PAGE pokemon entries across all users.
    First page starts with a summary of all users and their counts.
    Users are shown with their mention as a header, pokemon as bullet lines.

    If `member_names` is provided (a dict of user_id -> display name), the
    summary line for each user also shows their name after the mention
    (e.g. "<@id> (Username) — 3").
    """
    member_names = member_names or {}
    if not reserve_docs:
        embed = discord.Embed(
            title=f"📋 Reserve List — {guild_name}",
            description="No reserves set for this server.",
            color=EMBED_COLOR,
        )
        return [embed]

    # Flatten to (user_id, pokemon_list) pairs, sorted by pokemon count (ascending)
    pairs = [(doc["user_id"], sorted(doc.get("pokemon", []))) for doc in reserve_docs]
    pairs = [(uid, pokes) for uid, pokes in pairs if pokes]
    # Sort by count of pokemon (ascending - fewer reserves first)
    pairs.sort(key=lambda x: len(x[1]))

    if not pairs:
        embed = discord.Embed(
            title=f"📋 Reserve List — {guild_name}",
            description="No reserves set for this server.",
            color=EMBED_COLOR,
        )
        return [embed]

    # Build summary: user mentions (with username) and their counts
    summary_lines = []
    for uid, pokes in pairs:
        count = len(pokes)
        name = member_names.get(uid)
        name_suffix = f" ({name})" if name else ""
        summary_lines.append(f"<@{uid}>{name_suffix} — {count}")
    
    summary_text = "\n".join(summary_lines)

    # Build lines: "## <@uid>" header + "• pokemon" entries
    all_lines: list[tuple[str, bool]] = []  # (text, is_header)
    for uid, pokes in pairs:
        all_lines.append((f"<@{uid}>", True))
        for p in pokes:
            all_lines.append((f"{REPLY_EMOJI} {p}", False))

    # Paginate: at most ITEMS_PER_PAGE *pokemon* lines per page
    # But first page always starts with summary
    pages: list[discord.Embed] = []
    current_lines: list[str] = []
    pokemon_count = 0
    is_first_page = True

    def flush_page(lines, include_summary=False):
        if include_summary:
            content = f"**📊 Reserve Count**\n{summary_text}\n\n─────────────\n" + "\n".join(lines) if lines else summary_text
        else:
            content = "\n".join(lines) if lines else "—"
        
        embed = discord.Embed(
            title=f"📋 Reserve List — {guild_name}",
            description=content,
            color=EMBED_COLOR,
        )
        return embed

    for text, is_header in all_lines:
        if not is_header:
            pokemon_count += 1

        current_lines.append(text)

        if pokemon_count >= ITEMS_PER_PAGE and not is_header:
            pages.append(flush_page(current_lines, include_summary=is_first_page))
            is_first_page = False
            current_lines = []
            pokemon_count = 0

    if current_lines:
        pages.append(flush_page(current_lines, include_summary=is_first_page))

    total = len(pages)
    for i, embed in enumerate(pages):
        embed.set_footer(
            text=f"Page {i + 1}/{total} • {sum(len(d.get('pokemon', [])) for d in reserve_docs)} total reserved"
        )

    return pages


# ---------------------------------------------------------------------------
# Reserve Cog
# ---------------------------------------------------------------------------
class Reserve(commands.Cog):
    """Server-specific Pokémon reserve system."""

    def __init__(self, bot):
        self.bot = bot
        self.pokemon_data = load_pokemon_data()

    @property
    def db(self):
        return self.bot.db

    @property
    def gcache(self):
        # gcache is attached to db by prediction.py on cog init
        return getattr(self.bot.db, "gcache", None)

    # ------------------------------------------------------------------
    # Permission check helper
    # ------------------------------------------------------------------
    async def _has_reserve_permission(self, ctx_or_interaction) -> bool:
        """
        Returns True if the user is an admin/server-owner/bot-owner
        OR has one of the guild's reserve allowed roles.
        """
        if isinstance(ctx_or_interaction, commands.Context):
            user = ctx_or_interaction.author
            guild = ctx_or_interaction.guild
            is_owner = await self.bot.is_owner(user)
        else:
            user = ctx_or_interaction.user
            guild = ctx_or_interaction.guild
            is_owner = await self.bot.is_owner(user)

        if is_owner:
            return True
        if user.id == guild.owner_id:
            return True
        if user.guild_permissions.administrator:
            return True

        # Check allowed roles
        if self.gcache:
            allowed = await self.gcache.get_reserve_allowed_roles(guild.id)
        else:
            allowed = await self.db.get_reserve_allowed_roles(guild.id)

        user_role_ids = {r.id for r in user.roles}
        return bool(user_role_ids & set(allowed))

    # ------------------------------------------------------------------
    # Pokemon resolution helpers
    # ------------------------------------------------------------------
    def _resolve_pokemon_names(self, raw_input: str) -> tuple[list[str], list[str]]:
        """
        Parse a comma-separated pokemon string.
        Supports 'furfrou all' / 'all furfrou' for all variants.
        Returns (valid_names, invalid_names).
        """
        parts = [p.strip() for p in raw_input.split(",") if p.strip()]
        valid, invalid = [], []

        for part in parts:
            low = part.lower()
            is_all = low.endswith(" all") or low.startswith("all ")

            if is_all:
                base = part[4:].strip() if low.startswith("all ") else part[:-4].strip()
                variants = get_pokemon_with_variants(base, self.pokemon_data)
                if variants:
                    valid.extend(variants)
                else:
                    invalid.append(part)
            else:
                # A typed name might match more than one Pokemon (e.g. two
                # event mons sharing the same "other name"), so add all
                # matches, not just the first.
                matches = find_all_pokemon_by_name_flexible(part, self.pokemon_data)
                if matches:
                    for mon in matches:
                        canonical = mon.get("name")
                        if canonical and canonical not in valid:
                            valid.append(canonical)
                else:
                    invalid.append(part)

        return valid, invalid

    async def _resolve_category_pokemon(
        self, guild_id: int, category_key: str
    ) -> tuple[list[str], str]:
        """
        Resolve a category name to a list of pokemon from this server's categories.
        Returns (pokemon_list, source_description).

        Note: default categories (rare, regional, gigantamax) must be imported into
        the server first via `p!cat defaults` before they can be used here.
        """
        cat = await self.db.get_category(guild_id, category_key)
        if cat:
            return cat.get("pokemon", []), f"server category **{category_key}**"
        return [], ""

    async def _extract_user_and_rest(self, text: str):
        """
        Look for a user mention or raw ID at the very START or the very END
        of `text` (the two supported orderings: '@user <pokemon,...>' and
        '<pokemon,...> @user'). A mention/ID anywhere in the *middle* is left
        alone and treated as part of the pokemon/category text.

        Returns (user, remainder) if a mention was found and resolved to a
        real user, else (None, text) with `text` unchanged — meaning no
        mention was present (or it didn't resolve), so the remainder is
        the whole original string.
        """
        text = text.strip()
        mention_token = re.compile(r"<@!?(\d+)>")

        async def resolve(token: str):
            m = mention_token.match(token)
            uid = int(m.group(1)) if m else int(token)
            try:
                return await self.bot.fetch_user(uid)
            except Exception:
                return None

        start_match = re.match(r"^(<@!?\d+>|\d{17,20})\s+([\s\S]+)$", text)
        if start_match:
            user = await resolve(start_match.group(1))
            if user is not None:
                return user, start_match.group(2).strip()

        end_match = re.match(r"^([\s\S]+?)\s+(<@!?\d+>|\d{17,20})$", text)
        if end_match:
            user = await resolve(end_match.group(2))
            if user is not None:
                return user, end_match.group(1).strip()

        return None, text

    def _extract_multiple_users(self, text: str) -> list[int]:
        """
        Extract all user mentions / raw IDs from a string, in the order
        they appear. Used by `switch` and `transfer`, which only take
        user references (no pokemon/category text) as arguments.
        """
        mention_re = re.compile(r"^<@!?(\d+)>$")
        ids: list[int] = []
        for tok in (text or "").split():
            m = mention_re.match(tok)
            if m:
                ids.append(int(m.group(1)))
            elif tok.isdigit() and 17 <= len(tok) <= 20:
                ids.append(int(tok))
        return ids

    def _extract_trailing_users(self, text: str) -> tuple[str, list[int]]:
        """
        Peel up to 2 user mentions/IDs off the END of a string, in order.
        Used by `transfer` so pokemon/category text (which may contain
        multiple words, e.g. "pride vivillon") can precede the user(s).
        Returns (remaining_text, [user_ids]).
        """
        mention_re = re.compile(r"^<@!?(\d+)>$")
        tokens = (text or "").split()
        ids: list[int] = []
        while tokens and len(ids) < 2:
            tok = tokens[-1]
            m = mention_re.match(tok)
            if m:
                ids.insert(0, int(m.group(1)))
                tokens.pop()
            elif tok.isdigit() and 17 <= len(tok) <= 20:
                ids.insert(0, int(tok))
                tokens.pop()
            else:
                break
        return " ".join(tokens), ids

    # ------------------------------------------------------------------
    # Main group
    # ------------------------------------------------------------------
    @commands.group(name="reserve", aliases=["r"], invoke_without_command=True)
    async def reserve_group(self, ctx):
        """Reserve system — see `p!reserve help` for all subcommands."""
        if ctx.invoked_subcommand is None:
            await self._send_help(ctx)

    async def _send_help(self, ctx):
        p = ctx.prefix
        embed = discord.Embed(
            title="💾 Reserve System",
            color=EMBED_COLOR,
            description=(
                "Server-specific Pokémon reservation system — reserve the "
                "Pokémon you want to collect, and view what others have called.\n\n"
                f"📖 **{p}r** *(this command, no arguments)* — shows this menu\n"
                f"Aliases: `{p}reserve` = `{p}r`\n"
                "─────────────────────────"
            ),
        )

        embed.add_field(
            name="📋 Viewing Reserves",
            value=(
                f"**{p}r list**\n"
                f"‣ View everyone's reserves in this server\n"
                f"**{p}r list @user**\n"
                f"‣ View just one user's reserves\n"
                f"**{p}r who <pokemon>**\n"
                f"‣ See who has a specific Pokémon reserved"
            ),
            inline=False,
        )

        embed.add_field(
            name="🎒 Managing Your Own Reserve",
            value=(
                f"**{p}r remove p <pokemon,...>**\n"
                f"‣ Remove Pokémon from your reserves\n"
                f"**{p}r remove cat <category>**\n"
                f"‣ Remove a whole category from your reserves\n"
                f"**{p}r clear**\n"
                f"‣ Clear all of your reserves ⚠️\n"
                f"**{p}r transfer @user**\n"
                f"‣ Move ALL your reserves to another account\n"
                f"**{p}r transfer p/cat <name,...> @user**\n"
                f"‣ Move only matching Pokémon/category to another account"
            ),
            inline=False,
        )

        embed.add_field(
            name="\u200b",
            value="**🔐 Admin & Allowed Roles Only**\n─────────────────────────",
            inline=False,
        )

        embed.add_field(
            name="🛡️ Managing Members' Reserves",
            value=(
                f"**{p}r add p @user <pokemon,...>**\n"
                f"‣ Add Pokémon to a user's reserve *(mention can go first or last)*\n"
                f"**{p}r add cat @user <category>**\n"
                f"‣ Add a category to a user's reserve\n"
                f"**{p}r remove p @user <pokemon,...>**\n"
                f"‣ Remove Pokémon from a user's reserve\n"
                f"**{p}r remove cat @user <category>**\n"
                f"‣ Remove a category from a user's reserve\n"
                f"**{p}r clear @user**\n"
                f"‣ Clear one user's reserves\n"
                f"**{p}r clear --all**\n"
                f"‣ Clear EVERY reserve in the server ⚠️"
            ),
            inline=False,
        )

        embed.add_field(
            name="🔀 Bulk Tools",
            value=(
                f"**{p}r switch @user1 @user2**\n"
                f"‣ Swap two users' entire reserve lists\n"
                f"**{p}r transfer @user1 @user2**\n"
                f"‣ Move ALL of user1's reserves onto user2\n"
                f"**{p}r transfer p/cat <name,...> @user1 @user2**\n"
                f"‣ Move only matching Pokémon/category from user1 to user2"
            ),
            inline=False,
        )

        embed.add_field(
            name="🔧 Allowed Roles Setup",
            value=(
                f"**{p}r allowedroles**\n"
                f"‣ View which roles can use admin reserve commands\n"
                f"**{p}r allowedroles add/remove <@role|id>**\n"
                f"‣ Add or remove a role from that list\n"
                f"**{p}r allowedroles clear**\n"
                f"‣ Remove all allowed roles"
            ),
            inline=False,
        )

        embed.add_field(
            name="💡 Tips",
            value=(
                f"• Aliases: `p`/`poke` = pokemon, `cat` = category, `sw` = switch, `tr`/`t` = transfer\n"
                f"• Use `{p}cat defaults` to import built-in categories (rare, regional, gigantamax)\n"
                f"• Selective transfer only moves what the source *actually has* reserved — "
                f"e.g. if a category has 35 Pokémon but they've only reserved 29, only those 29 move\n"
                f"• Use `{p}help reserve` for full command help"
            ),
            inline=False,
        )

        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=NO_MENTIONS)

    # ------------------------------------------------------------------
    # p!reserve add pokemon <user> <pokemon,...>
    # ------------------------------------------------------------------
    @reserve_group.command(name="add")
    async def reserve_add(
        self, ctx, subtype: str, *, rest: str
    ):
        """
        Add pokemon or category to a user's reserves.
        Subtype: 'pokemon'/'poke'/'p' or 'cat'/'category'
        Supports:
          p!r add p @user <pokemon,...>
          p!r add p <pokemon,...> @user
        """
        if not await self._has_reserve_permission(ctx):
            await ctx.reply(
                "❌ You don't have permission to use reserve commands.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        subtype = subtype.lower()

        # --- Resolve user + pokemon_input from `rest` ---
        # Supports both:
        #   @user <pokemon,...>   (user first)
        #   <pokemon,...> @user   (user last)
        user, pokemon_input = await self._extract_user_and_rest(rest)

        if user is None:
            await ctx.reply(
                f"❌ Could not find that user. Use a @mention or user ID.\n"
                f"Usage: `{ctx.prefix}r add p @user <pokemon,...>` or `{ctx.prefix}r add p <pokemon,...> @user`",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if not pokemon_input:
            await ctx.reply(
                f"❌ Please provide Pokémon names or a category.\n"
                f"Usage: `{ctx.prefix}r add p @user <pokemon,...>` or `{ctx.prefix}r add p <pokemon,...> @user`",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if subtype in ("pokemon", "poke", "p"):
            valid, invalid = self._resolve_pokemon_names(pokemon_input)
            if not valid:
                msg = "❌ No valid Pokémon names found."
                if invalid:
                    msg += f" Invalid: {', '.join(invalid[:10])}"
                await ctx.reply(msg, mention_author=False, allowed_mentions=NO_MENTIONS)
                return

            await self.db.add_pokemon_to_reserve(user.id, ctx.guild.id, valid)

            resp = f"✅ Added {len(valid)} Pokémon to {user.mention}'s reserve"
            if len(valid) <= 10:
                resp += f": {', '.join(valid)}"
            else:
                resp += f": {', '.join(valid[:10])} and {len(valid) - 10} more"
            if invalid:
                resp += f"\n❌ Invalid: {', '.join(invalid[:10])}"
            await ctx.reply(resp, mention_author=False, allowed_mentions=NO_MENTIONS)

        elif subtype in ("cat", "category"):
            # Split by comma to handle multiple categories
            cat_names = [c.strip() for c in pokemon_input.split(",") if c.strip()]
            
            if not cat_names:
                await ctx.reply(
                    f"❌ Please provide at least one category name.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                return
            
            all_pokes = []
            all_sources = []
            not_found = []
            
            for cat_name in cat_names:
                pokes, source = await self._resolve_category_pokemon(
                    ctx.guild.id, cat_name
                )
                if pokes:
                    all_pokes.extend(pokes)
                    all_sources.append(source)
                else:
                    not_found.append(cat_name)
            
            if not all_pokes:
                await ctx.reply(
                    f"❌ No categories found: {', '.join(not_found)}\n"
                    f"Admins can add built-in categories with `{ctx.prefix}cat defaults`.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                return
            
            # Remove duplicates while preserving order
            all_pokes = list(dict.fromkeys(all_pokes))
            
            await self.db.add_pokemon_to_reserve(user.id, ctx.guild.id, all_pokes)
            
            resp = f"✅ Added {len(all_pokes)} Pokémon from {len(all_sources)} categor{'y' if len(all_sources) == 1 else 'ies'} to {user.mention}'s reserve"
            if all_sources:
                resp += f": {', '.join(all_sources)}"
            if not_found:
                resp += f"\n⚠️ Not found: {', '.join(not_found)}"
            
            await ctx.reply(resp, mention_author=False, allowed_mentions=NO_MENTIONS)
        else:
            await ctx.reply(
                f"❌ Unknown subtype `{subtype}`. Use `pokemon` or `cat`.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )

    @reserve_add.error
    async def reserve_add_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"❌ Usage: `{ctx.prefix}r add pokemon|poke|p @user <pokemon,...>` "
                f"or `{ctx.prefix}r add pokemon|poke|p <pokemon,...> @user`\n"
                f"Category: `{ctx.prefix}r add cat|category @user <category>`",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        elif isinstance(error, commands.BadArgument):
            await ctx.reply(
                "❌ Could not find that user. Use a @mention or user ID.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )

    # ------------------------------------------------------------------
    # p!reserve remove pokemon|poke|p <pokemon,...>
    # OR p!reserve remove pokemon|poke|p <@user> <pokemon,...>  (admin only)
    # ------------------------------------------------------------------
    @reserve_group.command(name="remove")
    async def reserve_remove(self, ctx, subtype: str = None, *, pokemon_input: str = None):
        """
        Remove pokemon or category from reserves.
        User can remove from their own: p!r remove p pikachu,meowth
        Admin can remove from others: p!r remove p @user pikachu,meowth
        """
        if subtype is None or pokemon_input is None:
            await ctx.reply(
                f"❌ Usage: `{ctx.prefix}r remove pokemon|poke|p <pokemon,...>` "
                f"(remove from yourself)\n"
                f"or `{ctx.prefix}r remove pokemon|poke|p <@user> <pokemon,...>` (admin only)",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        subtype = subtype.lower()

        # Try to parse if there's a user mention/ID at the start or end of
        # pokemon_input (supports both '@user rare' and 'rare @user').
        mentioned_user, target_pokemon_input = await self._extract_user_and_rest(pokemon_input)

        if mentioned_user is not None:
            # Admin check for removing from other users (mentioning yourself
            # is always allowed, same as removing without any mention).
            if mentioned_user.id != ctx.author.id and not await self._has_reserve_permission(ctx):
                await ctx.reply(
                    "❌ You don't have permission to remove reserves from other users.",
                    mention_author=False,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            target_user = mentioned_user
        else:
            target_user = ctx.author

        if not target_pokemon_input.strip():
            await ctx.reply(
                "❌ Please specify pokemon or category to remove.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if subtype in ("pokemon", "poke", "p"):
            valid, invalid = self._resolve_pokemon_names(target_pokemon_input)
            if not valid:
                msg = "❌ No valid Pokémon names found."
                if invalid:
                    msg += f" Invalid: {', '.join(invalid[:10])}"
                await ctx.reply(msg, mention_author=False, allowed_mentions=NO_MENTIONS)
                return

            modified = await self.db.remove_pokemon_from_reserve(
                target_user.id, ctx.guild.id, valid
            )
            if modified:
                resp = f"✅ Removed {len(valid)} Pokémon from {target_user.mention}'s reserve"
                if len(valid) <= 10:
                    resp += f": {', '.join(valid)}"
                else:
                    resp += f": {', '.join(valid[:10])} and {len(valid) - 10} more"
            else:
                resp = (
                    f"⚠️ No changes — those Pokémon weren't in {target_user.mention}'s reserve."
                )
            if invalid:
                resp += f"\n❌ Invalid: {', '.join(invalid[:10])}"
            await ctx.reply(resp, mention_author=False, allowed_mentions=NO_MENTIONS)

        elif subtype in ("cat", "category"):
            # Split by comma to handle multiple categories
            cat_names = [c.strip() for c in target_pokemon_input.split(",") if c.strip()]
            
            if not cat_names:
                await ctx.reply(
                    f"❌ Please provide at least one category name.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                return
            
            all_pokes = []
            all_sources = []
            not_found = []
            
            for cat_name in cat_names:
                pokes, source = await self._resolve_category_pokemon(
                    ctx.guild.id, cat_name
                )
                if pokes:
                    all_pokes.extend(pokes)
                    all_sources.append(source)
                else:
                    not_found.append(cat_name)
            
            if not all_pokes:
                await ctx.reply(
                    f"❌ No categories found: {', '.join(not_found)}\n"
                    f"Admins can add built-in categories with `{ctx.prefix}cat defaults`.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                return
            
            # Remove duplicates while preserving order
            all_pokes = list(dict.fromkeys(all_pokes))
            
            await self.db.remove_pokemon_from_reserve(target_user.id, ctx.guild.id, all_pokes)
            
            resp = f"✅ Removed {len(all_pokes)} Pokémon from {len(all_sources)} categor{'y' if len(all_sources) == 1 else 'ies'} from {target_user.mention}'s reserve"
            if all_sources:
                resp += f": {', '.join(all_sources)}"
            if not_found:
                resp += f"\n⚠️ Not found: {', '.join(not_found)}"
            
            await ctx.reply(resp, mention_author=False, allowed_mentions=NO_MENTIONS)
        else:
            await ctx.reply(
                f"❌ Unknown subtype `{subtype}`. Use `pokemon|poke|p` or `cat|category`.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )

    @reserve_remove.error
    async def reserve_remove_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"❌ Usage: `{ctx.prefix}r remove pokemon|poke|p <pokemon,...>` (remove from yourself)\n"
                f"or `{ctx.prefix}r remove pokemon|poke|p <@user> <pokemon,...>` (admin only)\n"
                f"or `{ctx.prefix}r remove cat|category <category>`",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        elif isinstance(error, commands.BadArgument):
            await ctx.reply(
                "❌ Could not find that user. Use a @mention or user ID.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )

    # ------------------------------------------------------------------
    # p!reserve clear [user]
    # ------------------------------------------------------------------
    @reserve_group.command(name="clear")
    async def reserve_clear(self, ctx, target: str = None):
        """
        Clear reserves.
        Without argument: clears your own reserves.
        With @user or user ID: if it's yourself, clears your own; if it's someone else, admin only.
        Admin only: p!r clear --all (clears entire server).
        """
        if target is None:
            # User clears their own reserves - no permission needed
            cleared = await self.db.clear_user_reserve(ctx.author.id, ctx.guild.id)
            if cleared:
                await ctx.reply(
                    f"✅ Cleared your reserves in **{ctx.guild.name}**.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            else:
                await ctx.reply(
                    f"⚠️ You had no reserves in this server.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        elif target.lower() == "--all":
            # Admin clears entire server
            if not await self._has_reserve_permission(ctx):
                await ctx.reply(
                    "❌ You don't have permission to clear server reserves.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                return
            
            count = await self.db.clear_all_reserves(ctx.guild.id)
            await ctx.reply(
                f"✅ Cleared all reserves in **{ctx.guild.name}** ({count} user entries removed).",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        else:
            # Check if target is the user themselves
            raw = target.strip("<@!>")
            if not raw.isdigit():
                await ctx.reply(
                    "❌ Invalid user. Use a @mention, user ID, or `--all` for whole server.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                return
            uid = int(raw)
            
            # If user mentions themselves, allow it without permission
            if uid == ctx.author.id:
                cleared = await self.db.clear_user_reserve(uid, ctx.guild.id)
                if cleared:
                    await ctx.reply(
                        f"✅ Cleared your reserves in **{ctx.guild.name}**.",
                        mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                else:
                    await ctx.reply(
                        f"⚠️ You had no reserves in this server.",
                        mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            else:
                # User mentioned someone else - need admin permission
                if not await self._has_reserve_permission(ctx):
                    await ctx.reply(
                        "❌ You don't have permission to clear other users' reserves.",
                        mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                    return
                
                cleared = await self.db.clear_user_reserve(uid, ctx.guild.id)
                if cleared:
                    await ctx.reply(
                        f"✅ Cleared reserves for <@{uid}> in this server.",
                        mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                else:
                    await ctx.reply(
                        f"⚠️ <@{uid}> had no reserves in this server.", mention_author=False, allowed_mentions=NO_MENTIONS
                    )

    # ------------------------------------------------------------------
    # p!reserve switch @user1 @user2   (admin/allowed roles only)
    # ------------------------------------------------------------------
    @reserve_group.command(name="switch", aliases=["sw"])
    async def reserve_switch(self, ctx, *, users: str = None):
        """
        Swap the entire reserve lists of two users.
        Admin/allowed roles only.
        Usage: p!r switch @user1 @user2
        """
        if not await self._has_reserve_permission(ctx):
            await ctx.reply(
                "❌ You don't have permission to switch reserves.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        user_ids = self._extract_multiple_users(users)
        if len(user_ids) < 2:
            await ctx.reply(
                f"❌ Usage: `{ctx.prefix}r switch @user1 @user2`",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        uid1, uid2 = user_ids[0], user_ids[1]
        if uid1 == uid2:
            await ctx.reply(
                "❌ Can't switch a user's reserve with themselves.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        list1 = await self.db.get_user_reserve(uid1, ctx.guild.id)
        list2 = await self.db.get_user_reserve(uid2, ctx.guild.id)

        if not list1 and not list2:
            await ctx.reply(
                f"⚠️ Neither <@{uid1}> nor <@{uid2}> have any reserves in this server.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        # Clear both first so add_pokemon_to_reserve doesn't merge with
        # their own old list, then give each user the other's old list.
        await self.db.clear_user_reserve(uid1, ctx.guild.id)
        await self.db.clear_user_reserve(uid2, ctx.guild.id)

        if list2:
            await self.db.add_pokemon_to_reserve(uid1, ctx.guild.id, list2)
        if list1:
            await self.db.add_pokemon_to_reserve(uid2, ctx.guild.id, list1)

        await ctx.reply(
            f"🔄 Switched reserves — <@{uid1}> ↔ <@{uid2}> "
            f"({len(list1)} ↔ {len(list2)} Pokémon).",
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )

    @reserve_switch.error
    async def reserve_switch_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"❌ Usage: `{ctx.prefix}r switch @user1 @user2`",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )

    # ------------------------------------------------------------------
    # p!reserve transfer @user                          (any member — move ALL of YOUR reserves)
    # p!reserve transfer @user1 @user2                   (admin/allowed roles — move ALL of user1 -> user2)
    # p!reserve transfer p/cat <name,...> @user          (any member — move MATCHING reserves of yours)
    # p!reserve transfer p/cat <name,...> @user1 @user2  (admin/allowed roles — move MATCHING user1 -> user2)
    # ------------------------------------------------------------------
    async def _send_transfer_help(self, ctx):
        p = ctx.prefix
        embed = discord.Embed(
            title="🔀 Reserve Transfer — How To",
            color=EMBED_COLOR,
            description=(
                "Move reserved Pokémon between accounts. Only what the "
                "source *actually has reserved* gets moved.\n"
                "─────────────────────────"
            ),
        )

        embed.add_field(
            name="❓ How do I transfer my reserved Pokémon to my alt?",
            value=f"`{p}r transfer p pikachu,meowth @alt`",
            inline=False,
        )

        embed.add_field(
            name="❓ How do I transfer a reserved category (rare, regional, gigantamax)?",
            value=f"`{p}r transfer cat rare @alt`",
            inline=False,
        )

        embed.add_field(
            name="❓ How do I transfer ALL my reserved Pokémon?",
            value=f"`{p}r transfer @alt`",
            inline=False,
        )

        embed.add_field(
            name="🔐 Admin/Allowed Roles Only",
            value=(
                f"`{p}r transfer @user1 @user2` — move ALL of user1's reserves to user2\n"
                f"`{p}r transfer p/cat <name,...> @user1 @user2` — move only matching ones"
            ),
            inline=False,
        )

        embed.add_field(
            name="💡 Tip",
            value=f"Aliases: `{p}r t` and `{p}r tr` both work the same as `{p}r transfer`.",
            inline=False,
        )

        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=NO_MENTIONS)

    @reserve_group.command(name="transfer", aliases=["tr", "t"])
    async def reserve_transfer(self, ctx, *, rest: str = None):
        """
        Transfer reserves from one user to another (added on top of whatever
        the target already has — moved Pokémon are removed from the source).

        Full transfer (moves everything):
          p!r transfer @user                — moves YOUR reserves to @user
          p!r transfer @user1 @user2         — admin only, moves user1 -> user2

        Selective transfer (only moves Pokémon the source actually has
        reserved that match the given name(s)/category — e.g. if a category
        has 35 Pokémon but the source only reserved 29 of them, only those
        29 move):
          p!r transfer p <pokemon,...> @user
          p!r transfer cat <category> @user
          p!r transfer p <pokemon,...> @user1 @user2      — admin only
          p!r transfer cat <category> @user1 @user2       — admin only
        """
        if not rest:
            await self._send_transfer_help(ctx)
            return

        # --- Detect an optional leading subtype (p/poke/pokemon/cat/category) ---
        parts = rest.split(None, 1)
        first = parts[0].lower()
        subtype: Optional[str] = None
        remainder = rest
        if first in ("p", "poke", "pokemon", "cat", "category"):
            subtype = first
            remainder = parts[1] if len(parts) > 1 else ""

        # --- Peel 1-2 trailing user mentions/IDs off the remainder ---
        name_input, user_ids = self._extract_trailing_users(remainder)

        if not user_ids:
            await ctx.reply(
                f"❌ Could not find a user to transfer to. Use a @mention or user ID at the end.\n"
                f"Usage: `{ctx.prefix}r transfer @user` or `{ctx.prefix}r transfer p|cat <name,...> @user`",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if subtype is not None and not name_input.strip():
            await ctx.reply(
                f"❌ Please provide Pokémon names or a category to transfer.\n"
                f"Usage: `{ctx.prefix}r transfer p <pokemon,...> @user`",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if len(user_ids) == 1:
            # Self-transfer: move ctx.author's reserves to the target user.
            source_id = ctx.author.id
            target_id = user_ids[0]
        else:
            # Admin transfer between two specified users.
            if not await self._has_reserve_permission(ctx):
                await ctx.reply(
                    "❌ You don't have permission to transfer reserves between other users.",
                    mention_author=False,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            source_id, target_id = user_ids[0], user_ids[1]

        if source_id == target_id:
            await ctx.reply(
                "❌ Source and target user can't be the same.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        source_desc = "your" if source_id == ctx.author.id else f"<@{source_id}>'s"
        who_has = "You have" if source_id == ctx.author.id else f"<@{source_id}> has"

        source_list = await self.db.get_user_reserve(source_id, ctx.guild.id)
        if not source_list:
            await ctx.reply(
                f"⚠️ {who_has} no reserves in this server to transfer.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if subtype is None:
            # ---- Full transfer: move everything ----
            await self.db.clear_user_reserve(source_id, ctx.guild.id)
            await self.db.add_pokemon_to_reserve(target_id, ctx.guild.id, source_list)

            await ctx.reply(
                f"✅ Transferred all {len(source_list)} Pokémon from {source_desc} reserve to <@{target_id}>.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        # ---- Selective transfer: only move matching Pokémon ----
        if subtype in ("pokemon", "poke", "p"):
            valid, invalid = self._resolve_pokemon_names(name_input)
            if not valid:
                msg = "❌ No valid Pokémon names found."
                if invalid:
                    msg += f" Invalid: {', '.join(invalid[:10])}"
                await ctx.reply(msg, mention_author=False, allowed_mentions=NO_MENTIONS)
                return
            requested = set(valid)
            source_desc_extra = ""
        else:
            cat_names = [c.strip() for c in name_input.split(",") if c.strip()]
            if not cat_names:
                await ctx.reply(
                    "❌ Please provide at least one category name.",
                    mention_author=False,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            requested = set()
            found_sources = []
            not_found = []
            for cat_name in cat_names:
                pokes, source = await self._resolve_category_pokemon(ctx.guild.id, cat_name)
                if pokes:
                    requested.update(pokes)
                    found_sources.append(source)
                else:
                    not_found.append(cat_name)

            if not requested:
                await ctx.reply(
                    f"❌ No categories found: {', '.join(not_found)}\n"
                    f"Admins can add built-in categories with `{ctx.prefix}cat defaults`.",
                    mention_author=False,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            invalid = not_found
            source_desc_extra = f" from {', '.join(found_sources)}" if found_sources else ""

        to_move = [p for p in source_list if p in requested]

        if not to_move:
            await ctx.reply(
                f"⚠️ None of those matched anything in {source_desc} reserve — nothing was transferred.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        await self.db.remove_pokemon_from_reserve(source_id, ctx.guild.id, to_move)
        await self.db.add_pokemon_to_reserve(target_id, ctx.guild.id, to_move)

        resp = (
            f"✅ Transferred {len(to_move)}/{len(requested)} matching Pokémon{source_desc_extra} "
            f"from {source_desc} reserve to <@{target_id}>"
        )
        if len(to_move) <= 10:
            resp += f": {', '.join(sorted(to_move))}"
        else:
            resp += f": {', '.join(sorted(to_move)[:10])} and {len(to_move) - 10} more"
        if invalid:
            resp += f"\n❌ Not found: {', '.join(invalid[:10])}"

        await ctx.reply(resp, mention_author=False, allowed_mentions=NO_MENTIONS)

    @reserve_transfer.error
    async def reserve_transfer_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await self._send_transfer_help(ctx)


    # ------------------------------------------------------------------
    # p!reserve who <pokemon>
    # ------------------------------------------------------------------
    @staticmethod
    def _build_who_embeds(title: str, user_ids: List[int], color=EMBED_COLOR) -> List[discord.Embed]:
        """Build one or more embeds mentioning every user_id given.

        Splits into multiple embeds if the mention list would exceed a
        single embed description's comfortable size.
        """
        if not user_ids:
            return []

        mentions = [f"<@{uid}>" for uid in user_ids]

        max_chars = 3800
        pages: List[List[str]] = []
        current: List[str] = []
        current_len = 0

        for mention in mentions:
            needed = len(mention) + (2 if current else 0)  # ", " separator
            if current and current_len + needed > max_chars:
                pages.append(current)
                current = [mention]
                current_len = len(mention)
            else:
                current.append(mention)
                current_len += needed

        if current:
            pages.append(current)

        total_pages = len(pages)
        embeds = []
        for i, page in enumerate(pages, start=1):
            embed_title = title if total_pages == 1 else f"{title} ({i}/{total_pages})"
            embed = discord.Embed(
                title=embed_title,
                description=", ".join(page),
                color=color,
            )
            embed.set_footer(text=f"{len(user_ids)} total")
            embeds.append(embed)

        return embeds

    @reserve_group.command(name="who", aliases=["w"])
    async def reserve_who(self, ctx, *, pokemon_name: str):
        """Check who in this server has a Pokémon reserved.

        Examples:
            p!r who Missingno
            p!r w Ralts
        """
        pokemon = find_pokemon_by_name_flexible(pokemon_name, self.pokemon_data)

        if not pokemon or not pokemon.get('name'):
            await ctx.reply(f"❌ Invalid Pokemon name: {pokemon_name}", mention_author=False, allowed_mentions=NO_MENTIONS)
            return

        resolved_name = pokemon['name']

        user_ids = await self.db.get_reserve_holders_for_pokemon(ctx.guild.id, [resolved_name])

        # Reserve AFK piggybacks on the collection AFK flag, so exclude
        # collection-AFK users the same way p!cl who does.
        if user_ids:
            afk_users = set(await self.db.get_collection_afk_users())
            user_ids = [uid for uid in user_ids if uid not in afk_users]

        if not user_ids:
            await ctx.reply(f"No one in this server has **{resolved_name}** reserved.", mention_author=False, allowed_mentions=NO_MENTIONS)
            return

        embeds = self._build_who_embeds(f"💾 Reserved by — {resolved_name}", user_ids)

        await ctx.reply(
            embeds=embeds,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @reserve_who.error
    async def reserve_who_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"❌ Usage: `{ctx.prefix}r who <pokemon>` (e.g. `{ctx.prefix}r who Ralts`)",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )

    # ------------------------------------------------------------------
    # p!reserve list [user]
    # ------------------------------------------------------------------
    @reserve_group.command(name="list")
    async def reserve_list(self, ctx, target: str = None):
        """
        Show reserves for this server or a specific user.
        Without argument: shows all reserves in the server.
        With @user or user ID: shows only that user's reserves.
        """
        if target is None:
            # Show all reserves for the server
            docs = await self.db.get_all_reserves(ctx.guild.id)
            # Filter out empty docs
            docs = [d for d in docs if d.get("pokemon")]
            guild_name = ctx.guild.name
        else:
            # Show reserves for a specific user
            raw = target.strip("<@!>")
            if not raw.isdigit():
                await ctx.reply(
                    "❌ Invalid user. Use a @mention or numeric user ID.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                return
            uid = int(raw)
            pokemon_list = await self.db.get_user_reserve(uid, ctx.guild.id)
            if not pokemon_list:
                await ctx.reply(
                    f"⚠️ <@{uid}> has no reserves in this server.",
                    mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
                return
            # Wrap the pokemon list in a doc structure for build_reserve_list_embeds
            docs = [{"user_id": uid, "pokemon": pokemon_list}]
            guild_name = f"{ctx.guild.name} — <@{uid}>"

        # Resolve display names for the summary line — check cache first,
        # fall back to an API fetch for members not already cached.
        member_names: dict[int, str] = {}
        for uid, _pokes in [(d["user_id"], d.get("pokemon")) for d in docs]:
            member = ctx.guild.get_member(uid)
            if member is None:
                try:
                    member = await ctx.guild.fetch_member(uid)
                except (discord.NotFound, discord.HTTPException):
                    member = None
            if member:
                member_names[uid] = member.display_name

        pages = build_reserve_list_embeds(guild_name, docs, member_names=member_names)

        if len(pages) == 1:
            await ctx.reply(embed=pages[0], mention_author=False, allowed_mentions=NO_MENTIONS)
        else:
            view = ReserveListView(ctx.author.id, pages)
            msg = await ctx.reply(embed=pages[0], view=view, mention_author=False, allowed_mentions=NO_MENTIONS)
            view.message = msg

    # ------------------------------------------------------------------
    # p!reserve allowedroles  (subgroup)
    # ------------------------------------------------------------------
    @reserve_group.group(
        name="allowedroles", aliases=["ar", "roles"], invoke_without_command=True
    )
    async def allowed_roles_group(self, ctx):
        """View or manage roles allowed to use reserve commands."""
        if ctx.invoked_subcommand is None:
            await self._show_allowed_roles(ctx)

    async def _show_allowed_roles(self, ctx):
        # Only admins/owner can view this
        is_admin = ctx.author.guild_permissions.administrator
        is_owner = await self.bot.is_owner(ctx.author)
        is_srv_owner = ctx.author.id == ctx.guild.owner_id
        if not (is_admin or is_owner or is_srv_owner):
            await ctx.reply(
                "❌ You need administrator permissions to view allowed roles.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        role_ids = await self.db.get_reserve_allowed_roles(ctx.guild.id)
        if not role_ids:
            embed = discord.Embed(
                title="🔐 Reserve — Allowed Roles",
                description="No extra roles set. Only admins and the server owner can use reserve commands.\n\n"
                f"Use `{ctx.prefix}r allowedroles add <@role|id>` to add a role.",
                color=EMBED_COLOR,
            )
        else:
            lines = []
            for rid in role_ids:
                role = ctx.guild.get_role(rid)
                lines.append(
                    f"• {role.mention} (`{rid}`)"
                    if role
                    else f"• ~~Unknown role~~ (`{rid}`) — deleted?"
                )
            embed = discord.Embed(
                title="🔐 Reserve — Allowed Roles",
                description="\n".join(lines),
                color=EMBED_COLOR,
            )
            embed.set_footer(
                text=f"{len(role_ids)} role(s) — these can use all reserve commands"
            )
        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=NO_MENTIONS)

    @allowed_roles_group.command(name="add")
    @commands.has_permissions(administrator=True)
    async def allowed_roles_add(self, ctx, *, role_input: str):
        """Add a role to the reserve allowed list. Use @mention or role ID."""
        role = await self._resolve_role(ctx, role_input)
        if role is None:
            await ctx.reply(
                "❌ Could not find that role. Use @mention or role ID.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        await self.db.add_reserve_allowed_role(ctx.guild.id, role.id)
        await ctx.reply(
            f"✅ {role.mention} can now use reserve commands.", mention_author=False, allowed_mentions=NO_MENTIONS
        )

    @allowed_roles_group.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def allowed_roles_remove(self, ctx, *, role_input: str):
        """Remove a role from the reserve allowed list."""
        role = await self._resolve_role(ctx, role_input)
        if role is None:
            await ctx.reply(
                "❌ Could not find that role. Use @mention or role ID.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return
        await self.db.remove_reserve_allowed_role(ctx.guild.id, role.id)
        await ctx.reply(
            f"✅ {role.mention} removed from reserve allowed roles.",
            mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )

    @allowed_roles_group.command(name="clear")
    @commands.has_permissions(administrator=True)
    async def allowed_roles_clear(self, ctx):
        """Remove all allowed roles from the reserve system for this server."""
        await self.db.clear_reserve_allowed_roles(ctx.guild.id)
        await ctx.reply("✅ All reserve allowed roles cleared.", mention_author=False, allowed_mentions=NO_MENTIONS)

    async def _resolve_role(self, ctx, role_input: str) -> Optional[discord.Role]:
        """Resolve a role from a mention string or raw ID."""
        raw = role_input.strip("<@&> ")
        if raw.isdigit():
            return ctx.guild.get_role(int(raw))
        # Try name match
        low = role_input.lower().strip()
        for role in ctx.guild.roles:
            if role.name.lower() == low:
                return role
        return None

    # ------------------------------------------------------------------
    # Error handlers for allowed_roles subcommands
    # ------------------------------------------------------------------
    @allowed_roles_add.error
    @allowed_roles_remove.error
    @allowed_roles_clear.error
    async def allowed_roles_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "❌ You need administrator permissions to manage allowed roles.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"❌ Please provide a role mention or ID.", mention_author=False, allowed_mentions=NO_MENTIONS
            )

    # ------------------------------------------------------------------
    # Global error handler for the reserve group
    # ------------------------------------------------------------------
    @reserve_group.error
    async def reserve_group_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        await ctx.reply(
            f"❌ An error occurred: {str(error)[:200]}", mention_author=False, allowed_mentions=NO_MENTIONS
        )


async def setup(bot):
    await bot.add_cog(Reserve(bot))
