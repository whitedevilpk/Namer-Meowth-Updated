"""Help commands"""
import time
import discord
from discord import app_commands
from discord.ext import commands
from config import EMBED_COLOR, BOT_PREFIX

NO_MENTIONS = discord.AllowedMentions.none()


def format_uptime(seconds: float) -> str:
    """Format a duration in seconds as 'X days, Y hours, Z minutes, W seconds'"""
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return ", ".join(parts)

class Help(commands.Cog):
    """Help and information commandss"""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help", aliases=["h"])
    async def help_command(self, ctx, category: str = None):
        """Show help information

        Categories: collection, category, hunt, pings, settings, roles, blacklist, prediction, starboard, helpful, eventextract, incense, captcha, reserve, organize, channels, listgen, ids, owner, all
        """
        prefix = BOT_PREFIX[0]
        is_owner = await self.bot.is_owner(ctx.author)

        if not category:
            embed = discord.Embed(
                title="📚 Poketwo Helper Bot — Help",
                description=f"Use `{prefix}help <category>` for details  •  `{prefix}help all` to see everything",
                color=EMBED_COLOR,
            )
            embed.add_field(name="📦 Collection",       value=f"`{prefix}help collection` — Manage your Pokémon collection",              inline=False)
            embed.add_field(name="🗂️ Category",         value=f"`{prefix}help category` — Bulk collection management with categories",    inline=False)
            embed.add_field(name="✨ Shiny Hunt",        value=f"`{prefix}help hunt` — Set up shiny hunting",                              inline=False)
            embed.add_field(name="🔷 Type & Region",    value=f"`{prefix}help pings` — Get pinged by Pokémon type or region",             inline=False)
            embed.add_field(name="⚙️ Settings",         value=f"`{prefix}help settings` — Toggle features and AFK",                       inline=False)
            embed.add_field(name="🎭 Roles",            value=f"`{prefix}help roles` — Configure rare / regional ping roles",              inline=False)
            embed.add_field(name="🚫 Blacklist",        value=f"`{prefix}help blacklist` — Block a role from using bot commands",         inline=False)
            embed.add_field(name="📺 Channels",         value=f"`{prefix}help channels` — Configure all bot channels (starboard, captcha…)",inline=False)
            embed.add_field(name="🔮 Prediction",       value=f"`{prefix}help prediction` — Manual Pokémon prediction",                   inline=False)
            embed.add_field(name="🔍 Helpful",          value=f"`{prefix}help helpful` — Spawn rates, shiny rates & hint solver",          inline=False)
            embed.add_field(name="📥 Event Extract",    value=f"`{prefix}help eventextract` — Export event Pokémon data from embeds",     inline=False)
            embed.add_field(name="🔥 Incense",          value=f"`{prefix}help incense` — Manage Poketwo incense sessions",                 inline=False)
            embed.add_field(name="🔐 Captcha",          value=f"`{prefix}help captcha` — Captcha alert information",                       inline=False)
            embed.add_field(name="💾 Reserve",          value=f"`{prefix}help reserve` — Server-specific Pokémon reservation system",      inline=False)
            embed.add_field(name="🗂️ Organize",         value=f"`{prefix}help organize` — Claimable-spot events that feed into reserves",  inline=False)
            embed.add_field(name="📝 List Builder",      value=f"`{prefix}help listgen` — Build and export Pokémon name lists",              inline=False)
            embed.add_field(name="🔄 Release & Evolve", value=f"`{prefix}help ids` — Personal ID lists for releasing & evolving", inline=False)
            if is_owner:
                embed.add_field(name="👑 Owner",        value=f"`{prefix}help owner` — Bot owner commands",                               inline=False)
            embed.add_field(name="ℹ️ About",            value=f"`{prefix}about` — Bot information and stats",                             inline=False)
            embed.add_field(name="🏓 Ping",             value=f"`{prefix}ping` — Check bot latency",                                      inline=False)
            embed.set_footer(text=f"Bot Prefix: {', '.join(BOT_PREFIX)}")
            await ctx.reply(embed=embed, mention_author=False, allowed_mentions=NO_MENTIONS)
            return

        category = category.lower()

        # ── Collection ────────────────────────────────────────────────
        if category in ["collection", "cl", "collect"]:
            embed = discord.Embed(
                title="📦 Collection Commands",
                description="Manage your Pokémon collection for this server. Get pinged when Pokémon you collect spawn!",
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}cl add <pokemon>`",
                value=(
                    "Add Pokémon to your collection\n"
                    f"**Aliases:** `{prefix}collection add`\n"
                    f"• `{prefix}cl add Pikachu`\n"
                    f"• `{prefix}cl add Pikachu, Charizard, Mewtwo`\n"
                    f"• `{prefix}cl add Furfrou all` (adds all Furfrou variants)"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}cl remove <pokemon | --sr <rate> | --user <@user>>`",
                value=(
                    "Remove Pokémon from your collection\n"
                    f"• `{prefix}cl remove Pikachu`\n"
                    f"• `{prefix}cl remove --sr 899` (by spawn rate)\n"
                    f"• `{prefix}cl remove --user @someone` (only removes Pokémon you both have)"
                ),
                inline=False,
            )
            embed.add_field(name=f"`{prefix}cl list`",  value="View your collection (paginated)",     inline=False)
            embed.add_field(name=f"`{prefix}cl raw`",   value="View as raw text grouped by SR tier",  inline=False)
            embed.add_field(name=f"`{prefix}cl clear`", value="⚠️ Clear your entire collection",      inline=False)
            embed.add_field(
                name=f"`{prefix}cl who <pokemon>`",
                value=f"See everyone in this server collecting a Pokémon — `{prefix}cl who Eevee`",
                inline=False,
            )
            embed.add_field(
                name="💡 How It Works",
                value=(
                    "• When a Pokémon you collect spawns, you get pinged\n"
                    "• Use cl add `Furfrou all` to add every Furfrou variant explicitly as adding only furfrou pings for furfrou only\n"
                    "• If a name matches more than one Pokémon (e.g. a shared alt/nickname), **all** matches are added, not just the first"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}cl limit set <number>`  *(Admin)*",
                value=(
                    "Cap how many Pokémon a user can hold in their collection for this server\n"
                    f"• `{prefix}cl limit set 50` — set the cap\n"
                    f"• `{prefix}cl limit clear` / `{prefix}cl limit reset` — remove the cap\n"
                    f"• `{prefix}cl limit` (no args) — view the current cap\n"
                    "If a `cl add` would push someone over the cap, only enough Pokémon to reach it are added."
                ),
                inline=False,
            )

        # ── Category ──────────────────────────────────────────────────
        elif category in ["category", "cat", "categories"]:
            embed = discord.Embed(
                title="🗂️ Category Commands",
                description="Bulk collection management with categories. Admins create categories, users subscribe to them.",
                color=EMBED_COLOR,
            )
            embed.add_field(name=f"`{prefix}cat add <categories>`",    value="Add Pokémon from a category to your collection",  inline=False)
            embed.add_field(name=f"`{prefix}cat remove <categories>`", value="Remove Pokémon from a category from your collection", inline=False)
            embed.add_field(name=f"`{prefix}cat list`",                value="View all server categories with Pokémon counts",  inline=False)
            embed.add_field(name=f"`{prefix}cat info <name>`",         value="View Pokémon in a specific category (paginated)", inline=False)
            embed.add_field(
                name="📝 Admin Commands",
                value=(
                    f"`{prefix}cat create <name> <pokemon>` — Create a category\n"
                    f"`{prefix}cat edit <name> <pokemon>` — Replace all Pokémon in a category\n"
                    f"`{prefix}cat addpokemon <name> <pokemon>` — Add to an existing category\n"
                    f"`{prefix}cat removepokemon <name> <pokemon>` — Remove from a category\n"
                    f"`{prefix}cat defaults` — Add built-in category from default list\n"
                    f"`{prefix}cat delete <name>` — Delete a category"
                ),
                inline=False,
            )

        # ── Shiny Hunt ────────────────────────────────────────────────
        elif category in ["hunt", "sh", "shiny"]:
            embed = discord.Embed(
                title="✨ Shiny Hunt Commands",
                description="Get pinged when your hunt target Pokémon spawns!",
                color=EMBED_COLOR,
            )
            embed.add_field(name=f"`{prefix}sh`",               value="Check your current shiny hunt",               inline=False)
            embed.add_field(name=f"`{prefix}sh <pokemon>`",     value="Start hunting a Pokémon (`{prefix}sh Pikachu`)", inline=False)
            embed.add_field(
                name=f"`{prefix}sh remove <pokemon>`",
                value=(
                    "Remove specific variant(s) from your current hunt, keeping the rest\n"
                    f"**Aliases:** `{prefix}sh rm`\n"
                    f"• `{prefix}sh remove Meowth` — if hunting Meowth, Alolan Meowth, Galarian Meowth, this stops the base form only\n"
                    f"• `{prefix}sh remove Meowth, Galarian Meowth` — remove several at once\n"
                    f"• `{prefix}sh remove Furfrou all` — remove every Furfrou variant"
                ),
                inline=False,
            )
            embed.add_field(name=f"`{prefix}sh clear`",         value="Stop hunting (also accepts `none` / `stop`)", inline=False)
            embed.add_field(
                name=f"`{prefix}sh who <pokemon>`",
                value=f"See everyone in this server hunting a Pokémon — `{prefix}sh who Eevee`",
                inline=False,
            )
            embed.add_field(
                name="💡 Note",
                value=(
                    "You can hunt one or more Pokémon (same dex) at a time per server.\n"
                    "If a name matches more than one Pokémon (e.g. a shared alt/nickname), **all** matches are hunted, not just the first."
                ),
                inline=False,
            )

        # ── Settings ──────────────────────────────────────────────────
        elif category in ["settings", "setting", "config", "afk"]:
            embed = discord.Embed(
                title="⚙️ Settings Commands",
                description="Configure bot features for your server and personal preferences.",
                color=EMBED_COLOR,
            )
            embed.add_field(name="👤 User Settings", value="", inline=False)
            embed.add_field(
                name=f"`{prefix}afk`",
                value=(
                    "Toggle pings via interactive buttons — 4 toggles:\n"
                    f"**Aliases:** `{prefix}away`\n"
                    "🟢 Green = Pings ON  •  🔴 Red = Pings OFF\n"
                    "• **ShinyHunt** • **Collection** • **TypePings** • **RegionPings**\n"
                    "*AFK is global across all servers*"
                ),
                inline=False,
            )
            embed.add_field(name="🛠️ Server Settings", value="", inline=False)
            embed.add_field(
                name=f"`{prefix}server-settings`",
                value=(
                    "View all current server settings\n"
                    f"**Aliases:** `{prefix}ss`, `{prefix}ssettings`\n"
                    "Shows: Roles, feature toggles. Use `{prefix}channel settings` for channels."
                ),
                inline=False,
            )
            embed.add_field(name="📝 Admin Commands", value="", inline=False)
            embed.add_field(
                name=f"`{prefix}toggle <feature>`",
                value=(
                    "Toggle server features on/off\n"
                    f"• `{prefix}toggle best_name` — shortest name line in predictions\n"
                    f"• `{prefix}toggle only_pings` — only send predictions when someone has pings\n"
                    f"• `{prefix}toggle catch_command` — catch command line in predictions\n"
                    f"• `{prefix}toggle hint_solver` — automatic hint solving\n"
                    f"• `{prefix}toggle type_pings` — turn type pings on/off server-wide\n"
                    f"• `{prefix}toggle region_pings` — turn region pings on/off server-wide\n"
                    f"Also accessible via `{prefix}only-pings true/false`"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}clear-pings [@user | user_id]`",
                value=(
                    "Clear all ping data for this server\n"
                    f"**Aliases:** `{prefix}clearpings`, `{prefix}resetpings`\n"
                    "• No argument → clears **all users**\n"
                    "• With @user → clears only that user\n"
                    "⚠️ Requires server owner, admin, or bot owner"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}force-afk @user <type> <on|off>`",
                value=(
                    "Forcefully set a user's AFK state on any ping type  **(Owner only)**\n"
                    f"**Aliases:** `{prefix}forceafk`, `{prefix}fafk`\n"
                    f"• `{prefix}force-afk @user all on` — AFK on all 4 types at once\n"
                    f"• `{prefix}force-afk @user all off` — remove AFK on all 4 types\n"
                    f"• `{prefix}force-afk @user collection on`\n"
                    f"• `{prefix}force-afk @user shinyhunt off`\n"
                    f"• `{prefix}force-afk @user typepings on`\n"
                    f"• `{prefix}force-afk @user regionpings off`\n"
                    "Types: `collection` `shinyhunt` `typepings` `regionpings` `all`\n"
                    "*User can still override with their own `p!afk`*"
                ),
                inline=False,
            )
            embed.add_field(
                name="📺 Channel & Role Config",
                value=(
                    f"See `{prefix}help channels` for channel configuration\n"
                    f"See `{prefix}help roles` for role configuration\n"
                    f"See `{prefix}help blacklist` for blocking a role from bot commands"
                ),
                inline=False,
            )

        # ── Roles ─────────────────────────────────────────────────────
        elif category in ["roles", "role"]:
            embed = discord.Embed(
                title="🎭 Role Commands",
                description=(
                    "Configure ping roles for rare and regional Pokémon, and manage which roles can use incense and reserve commands.\n"
                    f"`{prefix}role` shows **all four** role types at a glance."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}role`",
                value=(
                    "Show all configured roles — Rare, Regional, Incense Allowed, Reserve Allowed\n"
                    f"**Aliases:** `{prefix}roles`"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}role rare [@role]`  *(Admin)*",
                value=(
                    "Set role to ping for rare Pokémon (Legendary / Mythical / Ultra Beast)\n"
                    f"**Aliases:** `{prefix}role r`\n"
                    f"• `{prefix}role rare @Rare Hunters` — set the role\n"
                    f"• `{prefix}role rare` (no args) — clear / disable"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}role regional [@role]`  *(Admin)*",
                value=(
                    "Set role to ping for regional Pokémon\n"
                    f"**Aliases:** `{prefix}role reg`\n"
                    f"• `{prefix}role regional @Regionals` — set the role\n"
                    f"• `{prefix}role regional` (no args) — clear / disable"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"🔥 `{prefix}inc allowedroles`  *(Manage Server)*",
                value=(
                    "Manage which roles can use incense pause/resume commands\n"
                    f"• `{prefix}inc allowedroles` / `{prefix}inc ar` — list current roles\n"
                    f"• `{prefix}inc allowedroles add @Role` — add a role\n"
                    f"• `{prefix}inc allowedroles remove @Role` — remove a role\n"
                    f"• `{prefix}inc allowedroles clear` — remove all"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"📌 `{prefix}r allowedroles`  *(Admin)*",
                value=(
                    "Manage which roles can use reserve commands\n"
                    f"• `{prefix}r allowedroles` / `{prefix}r ar` — list current roles\n"
                    f"• `{prefix}r allowedroles add @Role` — add a role\n"
                    f"• `{prefix}r allowedroles remove @Role` — remove a role\n"
                    f"• `{prefix}r allowedroles clear` — remove all"
                ),
                inline=False,
            )

        # ── Blacklist ─────────────────────────────────────────────────
        elif category in ["blacklist", "bl"]:
            embed = discord.Embed(
                title="🚫 Command Blacklist",
                description=(
                    "Block a role from using this bot's Collection, Shiny Hunt, Type/Region Ping, "
                    "and Settings commands in this server — both prefix and slash commands."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}blacklist role add @role`  *(Admin)*",
                value=(
                    "Add a role to the command blacklist\n"
                    f"**Aliases:** `{prefix}bl role add`"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}blacklist role remove @role`  *(Admin)*",
                value="Remove a role from the command blacklist",
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}blacklist role list`  *(Admin)*",
                value="Show every role currently blacklisted",
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}blacklist role clear`  *(Admin)*",
                value="Remove every role from the blacklist",
                inline=False,
            )
            embed.add_field(
                name="💡 Note",
                value=(
                    "Blacklisted members are blocked from `cl`, `sh`, `tp`/`rp`, `afk`, `role`, and other "
                    "Settings commands, but `p!blacklist` itself always stays usable by admins so a mistake can be undone."
                ),
                inline=False,
            )

        # ── Channels ──────────────────────────────────────────────────
        elif category in ["channels", "channel", "ch"]:
            embed = discord.Embed(
                title="📺 Channel Configuration",
                description=(
                    f"All channel settings live under the `{prefix}channel` group.\n"
                    f"Use `{prefix}channel settings` to see every configured channel at a glance."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}channel settings`",
                value="View all configured channels (captcha, starboard, etc.)",
                inline=False,
            )
            embed.add_field(
                name=f"⭐ `{prefix}channel starboard` — Starboard  *(Admin)*",
                value=(
                    f"`{prefix}channel starboard settings` — view starboard channels\n"
                    f"`{prefix}channel starboard all [#ch | none]` — set all at once\n"
                    f"`{prefix}channel starboard catch/egg/unbox [#ch | none]`\n"
                    f"`{prefix}channel starboard shiny/gigantamax/highiv/lowiv [#ch | none]`\n"
                    f"`{prefix}channel starboard missingno/milestone [#ch | none]`\n"
                    "Use `none` instead of `#channel` to clear."
                ),
                inline=False,
            )
            embed.add_field(
                name=f"✨ `{prefix}channel shinycount [#ch]` — Shiny Count Channel  *(Admin)*",
                value=f"Same as `{prefix}sc channel` — sets the channel that auto-renames with the live shiny count.",
                inline=False,
            )
            embed.add_field(
                name=f"🔐 `{prefix}channel captcha [#ch]` — Captcha Alerts  *(Admin)*",
                value=(
                    f"• `{prefix}channel captcha #alerts` — set captcha alert channel\n"
                    f"• `{prefix}channel captcha` (no args) — clear / disable\n"
                    "Users are pinged here when Pokétwo asks them to verify."
                ),
                inline=False,
            )
            embed.add_field(
                name=f"👑 `{prefix}channel lowpred #ch` / `{prefix}channel secondary #ch`  *(Owner)*",
                value=(
                    "Set global channels for low-confidence predictions and secondary model logs."
                ),
                inline=False,
            )
            embed.add_field(
                name="📋 What Gets Logged to Starboard?",
                value=(
                    "• Shiny / Gigantamax / MissingNo catches, hatches, unboxes\n"
                    "• High IV (≥90%) or Low IV (≤10%)\n"
                    "• Milestone catches (100 / 1K / 10K / 100K of a single species)\n"
                    "A Pokémon meeting multiple criteria is sent to multiple channels."
                ),
                inline=False,
            )

        # ── Type & Region Pings ───────────────────────────────────────
        elif category in ["pings", "ping", "typepings", "regionpings", "tp", "rp"]:
            embed = discord.Embed(
                title="🔷 Type & Region Ping Commands",
                description="Get pinged whenever a Pokémon of a specific type or region spawns!",
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}tp`",
                value=(
                    "Open the interactive **Type Pings** menu with toggle buttons\n"
                    f"**Aliases:** `{prefix}typepings`\n"
                    "🟢 Green = enabled  •  ⚫ Grey = disabled\n"
                    "All 18 types available."
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}tp <types>`",
                value=(
                    "Directly toggle one or more types\n"
                    f"• `{prefix}tp bug` • `{prefix}tp bug grass fire`"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}rp`",
                value=(
                    "Open the interactive **Region Pings** menu\n"
                    f"**Aliases:** `{prefix}regionpings`\n"
                    "All 13 regions: Kanto, Johto, Hoenn, Sinnoh, Unova, Kalos, Alola, Galar, Paldea, "
                    "Kitakami, Hisui, Pokopia, Unknown"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}rp <regions>`",
                value=f"`{prefix}rp kanto` • `{prefix}rp kanto johto hoenn`",
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}tp limit` / `{prefix}rp limit`  *(Admin)*",
                value=(
                    "View, set, or clear how many types/regions a single user may enable at once\n"
                    f"• `{prefix}tp limit set 5` — cap each user at 5 enabled types\n"
                    f"• `{prefix}rp limit set 3` — cap each user at 3 enabled regions\n"
                    f"• `{prefix}tp limit clear` / `{prefix}rp limit clear` *(alias `reset`)* — remove the cap\n"
                    f"• `{prefix}tp limit` / `{prefix}rp limit` (no args) — view the current cap\n"
                    "While a limit is set, the \"Enable All\" button stays disabled."
                ),
                inline=False,
            )
            embed.add_field(
                name="🔕 AFK for Type/Region",
                value=f"Use `{prefix}afk` → **TypePings** / **RegionPings** buttons",
                inline=False,
            )
            embed.add_field(
                name="💡 How It Works",
                value=(
                    "• Settings are per-server\n"
                    "• Bot checks types/region on every spawn and mentions you in the prediction"
                ),
                inline=False,
            )

        # ── Prediction ────────────────────────────────────────────────
        elif category in ["prediction", "predict", "pred"]:
            embed = discord.Embed(
                title="🔮 Prediction Commands",
                description="Manually predict Pokémon from images or view auto-detection info",
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}predict <image_url>`",
                value=f"**Aliases:** `{prefix}pred`, `{prefix}p`",
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}predict` (reply to message)",
                value="Reply to a message with an image to predict it",
                inline=False,
            )
            embed.add_field(
                name="🤖 Auto-Detection",
                value=(
                    "The bot automatically predicts Poketwo spawns and shows collectors, "
                    "hunters, type/region pings, and role pings."
                ),
                inline=False,
            )
            embed.add_field(
                name="📊 Dual Model System",
                value=(
                    "• **Primary model** (224×224) — runs on every spawn\n"
                    "• **Secondary model** — runs when primary confidence < 94%\n"
                    "• Primary ≥ 94% → primary used; Secondary ≥ 90% → secondary used; else primary as fallback"
                ),
                inline=False,
            )

        # ── Starboard ─────────────────────────────────────────────────
        elif category in ["starboard", "star", "log"]:
            embed = discord.Embed(
                title="⭐ Starboard",
                description=(
                    f"Starboard channels are configured under `{prefix}channel starboard`.\n"
                    f"See `{prefix}help channels` for the full reference."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name="Quick Reference",
                value=(
                    f"`{prefix}channel starboard settings` — view current starboard channels\n"
                    f"`{prefix}channel starboard all [#ch | none]` — set all at once\n"
                    f"`{prefix}channel starboard catch/egg/unbox [#ch | none]`\n"
                    f"`{prefix}channel starboard shiny/gigantamax/highiv/lowiv [#ch | none]`\n"
                    f"`{prefix}channel starboard missingno/milestone [#ch | none]`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔍 Manual Checking  *(Admin)*",
                value=(
                    f"`{prefix}catchcheck` • `{prefix}eggcheck` • `{prefix}unboxcheck`\n"
                    "Reply to a message, provide a message ID, or provide several IDs at once "
                    f"(e.g. `{prefix}catchcheck 123... 456... 789...`) — sent oldest to newest."
                ),
                inline=False,
            )
            embed.add_field(
                name=f"✨ Shiny Count — `{prefix}sc` / `{prefix}shinycount`",
                value=(
                    f"`{prefix}sc` — view this server's shiny count\n"
                    f"`{prefix}sc edit <count>` — manually set the count  *(Admin)*\n"
                    f"`{prefix}sc channel [#ch | id]` — set/clear the channel that auto-renames with the count  *(Admin)*\n"
                    "Count only increments from real, auto-detected shiny catches — `catchcheck` never affects it."
                ),
                inline=False,
            )
            embed.add_field(
                name="📋 What Gets Logged?",
                value=(
                    "Shiny / Gigantamax / High IV / Low IV / MissingNo / Milestone catches, hatches, unboxes.\n"
                    "A Pokémon meeting multiple criteria is sent to multiple channels."
                ),
                inline=False,
            )

        # ── Helpful ───────────────────────────────────────────────────
        elif category in ["helpful", "util", "utils", "tools"]:
            embed = discord.Embed(
                title="🔍 Helpful Commands",
                description="Useful utility commands for Pokétwo players",
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}spawnrate <pokemon>` / `{prefix}sr <pokemon>`",
                value="Show the wild spawn rate for a Pokémon",
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}shinyrate [chain] [target%]` / `{prefix}shr`",
                value=(
                    "Per-encounter shiny rate at a given chain, or chain needed for a target %\n"
                    f"• `{prefix}shr 50` — rates at chain 50\n"
                    f"• `{prefix}shr 89%` — chain needed for 89%\n"
                    f"• `{prefix}shr 50 89%` — both at once"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}timedifference` / `{prefix}timediff` / `{prefix}td`",
                value=(
                    "Find time between messages, show message date, or compare IDs (instant)\n"
                    f"• `{prefix}td` (reply) — show full details of message + one before it\n"
                    f"• `{prefix}td <id>` — show just the date of that message (instant)\n"
                    f"• `{prefix}td <id1> <id2>` — show time difference only (instant, works cross-server)\n"
                    "Also available as `/timedifference`"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}date` / `{prefix}caught` / `{prefix}catchdate`",
                value=(
                    "Show when a Pokémon was caught from its Pokétwo ObjectID\n"
                    f"• Reply to a Pokétwo `p!info` embed + `{prefix}date` — reads ObjectID from footer automatically\n"
                    f"• `{prefix}date <objectid>` — provide the ObjectID directly\n"
                    "Also available as `/date` and the **Get Caught Date** right-click context menu"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}color <color>` / `{prefix}c`",
                value=(
                    "Convert and display color in multiple formats\n"
                    f"• `{prefix}color #FF0000` — hex format\n"
                    f"• `{prefix}color 255, 0, 0` — RGB format\n"
                    f"• `{prefix}color red` — color name (red, blue, green, etc.)\n"
                    "Shows: hex, RGB, HSL, decimal, and visual preview"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}extractids` / `{prefix}extract` / `{prefix}eids`",
                value=(
                    "Extract all Pokémon / listing IDs from a Pokétwo embed, space-separated\n"
                    f"• Reply to a Pokétwo `p!pokemon` or marketplace embed + `{prefix}extractids`\n"
                    f"• `{prefix}extractids <message_id>` — provide the message ID directly\n"
                    "Also available as `/extractids` and the **Extract IDs** right-click context menu"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔎 Hint Solver (Automatic)",
                value=(
                    "When Pokétwo sends a hint the bot automatically replies with matching Pokémon name(s).\n"
                    "Supports all languages. No command needed."
                ),
                inline=False,
            )

        # ── Event Extract ─────────────────────────────────────────────
        elif category in ["eventextract", "evtx", "eventdata"]:
            embed = discord.Embed(
                title="📥 Event Extract Commands",
                description=(
                    "Reply to a Pokétwo pokédex embed and the bot will watch it for edits — "
                    "browse through variants (select menu / buttons) and each one gets captured "
                    "automatically. Stop when you're done to get exported files."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}extractevent` / `{prefix}eventextract` / `{prefix}evtx` / `{prefix}startevent`",
                value=(
                    "Start monitoring a Pokétwo pokédex embed\n"
                    f"• Reply to a Pokétwo pokédex embed with `{prefix}extractevent`\n"
                    "• Browse variants — every edit to the message is captured automatically\n"
                    "• A progress embed with a ⏹️ Stop button updates live as you go\n"
                    "Also available as the **Start Event Extract** right-click context menu"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}stopextract` / `{prefix}stopeventextract` / `{prefix}evtxstop`",
                value=(
                    "Manually stop an active extraction and get your files\n"
                    "(fallback for if the ⏹️ Stop button ever fails)"
                ),
                inline=False,
            )
            embed.add_field(
                name="📄 Files You Get",
                value=(
                    "• `eventdata.json` — dex number, name, other-language names, rarity\n"
                    "• `pokemon_cdn_mapping.csv` — name → CDN image number\n"
                    "• `typeandregion.csv` — dex number, name, region, type1, type2\n"
                    "• `best_names.json` — name → blank template for you to fill in the best/shortest name"
                ),
                inline=False,
            )
            embed.add_field(
                name="💡 Note",
                value="Only the person who started the extraction can press Stop. Deleting the watched message also auto-finalizes and sends whatever was captured.",
                inline=False,
            )

        # ── Owner ─────────────────────────────────────────────────────
        elif category in ["owner", "admin", "botowner"]:
            if not is_owner:
                await ctx.reply("❌ This category is only available to the bot owner.", mention_author=False, allowed_mentions=NO_MENTIONS)
                return

            embed = discord.Embed(
                title="👑 Owner Commands",
                description="Bot owner only commands for global settings",
                color=0xFFD700,
            )
            embed.add_field(
                name=f"`{prefix}model load` / `{prefix}model unload` / `{prefix}model reload`",
                value=(
                    "Load, unload, or force-re-download the AI prediction models.\n"
                    f"**Aliases:** `{prefix}lm`, `{prefix}um`, `{prefix}rm`"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}model status`",
                value="Show model load state, RAM usage, and prediction stats",
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}reloadsr`",
                value="Force-reload spawn rate data from the remote CSV",
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}channel lowpred #channel`",
                value=(
                    "Set global channel for low-confidence predictions (< 90%)\n"
                    f"**Aliases:** `{prefix}channel low-prediction`"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}channel secondary #channel`",
                value=(
                    "Set global channel for secondary model logs\n"
                    f"**Aliases:** `{prefix}channel secondary-model`"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}channel global-starboard catch/egg/unbox #channel`",
                value="Set global starboard channels (across all servers)",
                inline=False,
            )

        # ── Incense ───────────────────────────────────────────────────
        elif category in ["incense", "inc", "incenses"]:
            embed = discord.Embed(
                title="🔥 Incense Commands",
                description=(
                    "Automatically restricts Poketwo the moment an Incense is purchased "
                    "in a monitored category channel — so your spawns stay exclusive."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name="⚙️ Setup  *(Manage Server)*",
                value=(
                    f"`{prefix}inc toggle` — Enable/disable the incense watcher\n"
                    f"`{prefix}inc cat add SPAWN1 SPAWN2` — Add categories to monitor\n"
                    f"`{prefix}inc cat remove <name>` — Stop monitoring a category\n"
                    f"`{prefix}inc cat list` — View monitored categories & channel counts"
                ),
                inline=False,
            )
            embed.add_field(
                name="⏸️ Pause  *(Allowed Role required)*",
                value=(
                    f"`{prefix}inc pause` / `{prefix}inc p` — Pause **this** channel\n"
                    f"`{prefix}inc pause all` — Pause ALL monitored categories"
                ),
                inline=False,
            )
            embed.add_field(
                name="▶️ Resume  *(Allowed Role required)*",
                value=(
                    f"`{prefix}inc resume` / `{prefix}inc r` — Resume **this** channel\n"
                    f"`{prefix}inc resume all` — Resume ALL paused channels"
                ),
                inline=False,
            )
            embed.add_field(name="📋 Status", value=f"`{prefix}inc list` — View paused and active channels", inline=False)
            embed.add_field(
                name="🔐 Allowed Roles  *(Manage Server)*",
                value=(
                    f"`{prefix}inc allowedroles` — List allowed roles\n"
                    f"`{prefix}inc allowedroles add @Role` — Add a role\n"
                    f"`{prefix}inc allowedroles remove @Role` — Remove a role\n"
                    f"`{prefix}inc allowedroles clear` — Remove all"
                ),
                inline=False,
            )
            embed.add_field(
                name="🤖 How It Works",
                value=(
                    "• Poketwo sends `You purchased an Incense for X shards!`\n"
                    "• Bot instantly restricts Poketwo in that specific channel only\n"
                    f"• Use `{prefix}inc help` for a quick in-chat reference"
                ),
                inline=False,
            )

        # ── Captcha ───────────────────────────────────────────────────
        elif category in ["captcha", "cap", "verify"]:
            embed = discord.Embed(
                title="🔐 Captcha Alerts",
                description=(
                    "Automatically alerts users in a designated channel when Pokétwo asks them to verify. "
                    "Disabled per-server until a captcha channel is configured."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}channel captcha #channel`  *(Admin)*",
                value=(
                    "Set the channel where captcha alerts will be sent\n"
                    f"• `{prefix}channel captcha #alerts` — set alert channel\n"
                    f"• `{prefix}channel captcha` (no args) — clear / disable"
                ),
                inline=False,
            )
            embed.add_field(
                name="🤖 How It Works",
                value=(
                    "• Bot watches every channel for Pokétwo's captcha message\n"
                    "• When detected, pings the flagged user in the alert channel\n"
                    "• Alert includes a **Verify** button linking to their captcha URL\n"
                    "• **5-minute cooldown** per user — won't re-ping within 5 minutes"
                ),
                inline=False,
            )

        # ── Reserve ───────────────────────────────────────────────────
        elif category in ["reserve", "res", "r"]:
            embed = discord.Embed(
                title="💾 Reserve Commands",
                description="Server-specific Pokémon reservation system.",
                color=EMBED_COLOR,
            )
            embed.add_field(name="📋 View",     value=f"`{prefix}r list` • `{prefix}r list @user` • `{prefix}r who <pokemon>` *(see everyone who has it reserved)*", inline=False)
            embed.add_field(name="➕ Remove",   value=f"`{prefix}r remove p <pokemon>` • `{prefix}r remove cat <cat>` • `{prefix}r clear`", inline=False)
            embed.add_field(
                name="🔀 Transfer (to your alt, no permission needed)",
                value=(
                    f"`{prefix}r transfer @alt` — move ALL your reserves\n"
                    f"`{prefix}r transfer p <pokemon,...> @alt` — move matching Pokémon only\n"
                    f"`{prefix}r transfer cat <category> @alt` — move matching category only\n"
                    f"Aliases: `{prefix}r tr`, `{prefix}r t` • run `{prefix}r transfer` alone for examples"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔐 Admin: Add",
                value=f"`{prefix}r add p @user <pokemon>` • `{prefix}r add cat @user <cat>`",
                inline=False,
            )
            embed.add_field(
                name="🔐 Admin: Remove / Clear",
                value=f"`{prefix}r remove p @user <pokemon>` • `{prefix}r clear @user` • `{prefix}r clear --all`",
                inline=False,
            )
            embed.add_field(
                name="🔐 Admin: Switch / Transfer Between Users",
                value=(
                    f"`{prefix}r switch @user1 @user2` — swap their entire reserves\n"
                    f"`{prefix}r transfer @user1 @user2` — move ALL of user1's reserves to user2\n"
                    f"`{prefix}r transfer p/cat <name,...> @user1 @user2` — move matching ones only"
                ),
                inline=False,
            )
            embed.add_field(
                name="🛠️ Admin: Allowed Roles",
                value=f"`{prefix}r allowedroles` • `{prefix}r allowedroles add @role` • `{prefix}r allowedroles remove @role`",
                inline=False,
            )

        # ── Organize ──────────────────────────────────────────────────
        elif category in ["organize", "og", "event"]:
            embed = discord.Embed(
                title="🗂️ Organize Commands",
                description=(
                    "Post a claimable list of spots (Pokémon or categories, with optional prices). "
                    "Anyone can click a button to claim a spot; when the event's done, an admin commits "
                    "every claim straight into that user's reserves."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name="👆 Everyone",
                value="Click a spot's button to claim it • click again to release it",
                inline=False,
            )
            embed.add_field(
                name="📐 Admin: Templates",
                value=(
                    f"`{prefix}og template create <name>` *(spots on the following lines)*\n"
                    f"`{prefix}og template edit <name>` • `{prefix}og template delete <name>`\n"
                    f"`{prefix}og template view <name>` • `{prefix}og template` *(no args — lists all templates)*\n"
                    f"`{prefix}og template setdefault <name>` — used by `{prefix}og start` with no argument"
                ),
                inline=False,
            )
            embed.add_field(
                name="🚀 Admin: Running an Event",
                value=(
                    f"`{prefix}og start [template]` — post the claim embed\n"
                    f"`{prefix}og view` — repost at the bottom of chat, old message's buttons disabled\n"
                    f"`{prefix}og end` — commit every claim to reserves, close the embed\n"
                    f"`{prefix}og cancel` — close without touching reserves"
                ),
                inline=False,
            )
            embed.add_field(
                name="🚫 Admin: Blacklist",
                value=f"`{prefix}og blacklist` • `{prefix}og blacklist add @role` • `{prefix}og blacklist remove @role` • `{prefix}og blacklist clear`",
                inline=False,
            )
            embed.add_field(
                name="🔧 Admin: Manual Spot Management",
                value=(
                    f"`{prefix}og spot` — numbered list of every spot and who holds it\n"
                    f"`{prefix}og spot set <#> <@member>` — assign/replace a spot's claim\n"
                    f"`{prefix}og spot clear <#>` — remove whoever holds a spot, opening it back up"
                ),
                inline=False,
            )
            embed.add_field(
                name="💡 Spot format",
                value=(
                    "One spot per line: `pokemon | <name>` or `category | <name>`, optional `| <price>` at the end.\n"
                    "e.g. `pokemon | Pride Pyroar | 250k pc` — a shared event name like `Pride Vivillon` "
                    f"auto-expands to every Pokémon using it, same as `{prefix}r add`."
                ),
                inline=False,
            )

        # ── All commands ──────────────────────────────────────────────
        elif category in ["listgen", "lg", "listbuilder", "list"]:
            embed = discord.Embed(
                title="📝 List Builder",
                description=(
                    "Build, filter, and export Pokémon name lists with a button-driven UI.\n"
                    f"Start with `{prefix}listgen` (or `{prefix}lg` / `{prefix}listbuilder`).\n"
                    "Reply to a message containing Pokémon names to extract them automatically — "
                    "the bot watches that message for edits for 2 minutes."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name="🚀 Command",
                value=(
                    f"`{prefix}listgen` — open an empty list builder\n"
                    f"`{prefix}listgen` *(as a reply)* — extract names from the replied message"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔘 Main Buttons  *(Row 1)*",
                value=(
                    "**➕ Add** — add Pokémon via up to 3 filter inputs\n"
                    "**🔍 Filter** — keep only matching Pokémon from the current list\n"
                    "**➖ Remove** — remove Pokémon matching a filter\n"
                    "**🗑️ Clear** — wipe the entire list"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔘 Action Buttons  *(Row 2)*",
                value=(
                    "**📄 Format** *(cycles)* — click to cycle: Comma → --n → --evo → Newline\n"
                    "**🔤 Enclose** — wrap each name with custom text before/after\n"
                    "  └ Use `\\s` in the enclose modal where you need a space (Discord trims spaces)\n"
                    "**⚙️ Advanced** — Language, Sort order, Find & Replace, Events toggle\n"
                    "**📤 Send** — post the finished list (auto-paginates if too long)"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔡 Case Dropdown  *(Row 3)*",
                value=(
                    "Always-visible dropdown — change text case instantly:\n"
                    "**As-is** • **UPPER** • **lower** • **Title**\n"
                    "Works correctly with all languages (case is applied after translation)."
                ),
                inline=False,
            )
            embed.add_field(
                name="🔍 Filter Syntax  *(used in Add / Filter / Remove modals)*",
                value=(
                    "`--type <type>` / `--t` — filter by type\n"
                    "`--region <region>` / `--r` — filter by region\n"
                    "`--sr <denom>` / `--spawnrate` — by spawn-rate denominator *(e.g. `--sr 225`)*\n"
                    "`--stage <1|2|3>` — by evolution stage\n"
                    "`--name <name>` / `--n` — exact name; prefix `all` for all forms\n"
                    "`--catchable` / `--notcatchable` — spawn-rate presence\n"
                    "Each cell = independent filter combined with **OR**. "
                    "Multiple flags in one cell = **AND**."
                ),
                inline=False,
            )
            embed.add_field(
                name="📋 Format Options",
                value=(
                    "**Comma** — `Bulbasaur, Ivysaur, Venusaur`\n"
                    "**--n** — `--n Bulbasaur --n Ivysaur --n Venusaur` *(first Pokémon gets the flag too)*\n"
                    "**--evo** — `--evo Bulbasaur --evo Ivysaur --evo Venusaur`\n"
                    "**Newline** — one name per line (supports bullet prefix in Advanced)"
                ),
                inline=False,
            )
            embed.add_field(
                name="⚙️ Advanced Options",
                value=(
                    "**🌐 Language** — English 🇬🇧 • German 🇩🇪 • French 🇫🇷 • Japanese 🇯🇵 • Best Name ⭐\n"
                    "  └ *Best Name picks the first/most recognised name when multiple exist*\n"
                    "**🔀 Sort** — A→Z • Z→A • Longest first • Shortest first • SR high→low • SR low→high\n"
                    "**🔄 Replace** — find & replace text in the final output\n"
                    "**🎉 Events** — toggle inclusion of event Pokémon"
                ),
                inline=False,
            )
            embed.add_field(
                name="⏱️ Timeout",
                value="The UI closes automatically after **2 minutes of inactivity**.",
                inline=False,
            )

        # ── Release & Evolve ──────────────────────────────────────────
        elif category in ["ids", "idlist", "idlists", "release", "evolve", "rel"]:
            embed = discord.Embed(
                title="🔄 Release & Evolve Lists",
                description=(
                    "Keep personal lists of Pokémon IDs, then pull a batch out whenever you need it — "
                    "the bot gives you a ready-to-paste Pokétwo command. Lists are yours alone and work in every server."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name=f"`{prefix}release <count>` / `{prefix}rel <count>`  •  `/release`",
                value=(
                    "Take the first N IDs off your release list and output the Pokétwo release command\n"
                    f"• `{prefix}release 7`\n"
                    "Very long outputs are split into several messages so each one is pasteable."
                ),
                inline=False,
            )
            embed.add_field(
                name=f"`{prefix}evolve <count>` / `{prefix}e <count>`  •  `/evolve`",
                value=(
                    "Take N IDs off your evolve list and output the Pokétwo evolve command\n"
                    f"• `{prefix}evolve 7`\n"
                    "⭐ IDs with **1 use** are picked first, then 2-use IDs. A 2-use ID that gets picked stays on the list with 1 use left."
                ),
                inline=False,
            )
            embed.add_field(
                name="🔧 Panels",
                value=(
                    f"`{prefix}releasepanel` / `{prefix}relp`  •  `{prefix}evolvepanel` / `{prefix}ep`\n"
                    "Button panel to add, remove, view, clear and use IDs. Only you can press its buttons."
                ),
                inline=False,
            )
            embed.add_field(
                name="🔀 Release List",
                value=(
                    f"`{prefix}releaseadd <ids>` / `{prefix}ra` — add IDs\n"
                    f"`{prefix}releaseremove <ids>` / `{prefix}rr` — remove IDs\n"
                    f"`{prefix}releaselist` / `{prefix}rl` — view your list (paginated)\n"
                    f"`{prefix}releaseclear` / `{prefix}rc` — empty the list"
                ),
                inline=False,
            )
            embed.add_field(
                name="🌱 Evolve List",
                value=(
                    f"`{prefix}evolveadd <ids>` / `{prefix}ea` — add IDs with **2 uses**\n"
                    f"`{prefix}evolveadd <ids> --once` — add IDs with **1 use** (priority)\n"
                    f"`{prefix}evolveremove <ids>` / `{prefix}er` — remove IDs completely\n"
                    f"`{prefix}evolveremove <ids> --once` — remove just one use\n"
                    f"`{prefix}evolvelist` / `{prefix}el` — view with 1x / 2x tabs\n"
                    f"`{prefix}evolveclear` / `{prefix}ec` — empty the list"
                ),
                inline=False,
            )
            embed.add_field(
                name="💡 Note",
                value=(
                    f"IDs are numbers separated by spaces (`{prefix}releaseadd 123 456 789`). "
                    "Duplicates and non-numeric text are ignored."
                ),
                inline=False,
            )
            if is_owner:
                embed.add_field(
                    name="👑 Owner",
                    value=f"`{prefix}idmigrate [source_db]` — copy lists from the old helper bot's database (skips users who already have one)",
                    inline=False,
                )

        elif category in ["all", "commands"]:
            embed = discord.Embed(
                title="📚 All Commands",
                description="Complete list of all bot commands",
                color=EMBED_COLOR,
            )
            embed.add_field(
                name="📦 Collection",
                value=(
                    f"`{prefix}cl add` • `{prefix}cl remove` • `{prefix}cl list` • `{prefix}cl raw` • `{prefix}cl clear` • `{prefix}cl who`\n"
                    f"**Admin:** `{prefix}cl limit set <n>` • `{prefix}cl limit clear`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🗂️ Category",
                value=(
                    f"`{prefix}cat add/remove/list/info`\n"
                    f"**Admin:** `{prefix}cat create/edit/delete/addpokemon/removepokemon/defaults`"
                ),
                inline=False,
            )
            embed.add_field(name="✨ Shiny Hunt",     value=f"`{prefix}sh` • `{prefix}sh <pokemon>` • `{prefix}sh remove <pokemon>` • `{prefix}sh clear` • `{prefix}sh who`",                     inline=False)
            embed.add_field(name="🔷 Type & Region",  value=f"`{prefix}tp` • `{prefix}tp <types>` • `{prefix}rp` • `{prefix}rp <regions>`\n**Admin:** `{prefix}tp limit set/clear <n>` • `{prefix}rp limit set/clear <n>`", inline=False)
            embed.add_field(
                name="⚙️ Settings",
                value=(
                    f"`{prefix}afk` • `{prefix}server-settings` • `{prefix}clear-pings [@user]`\n"
                    f"**Admin:** `{prefix}toggle <feature>` • `{prefix}only-pings` • `{prefix}force-afk @user <type> <on|off>`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🎭 Roles  *(Admin)*",
                value=(
                    f"`{prefix}role` — view all configured roles\n"
                    f"`{prefix}role rare [@role]` • `{prefix}role regional [@role]`\n"
                    f"`{prefix}inc allowedroles add/remove/clear @Role` • `{prefix}r allowedroles add/remove/clear @Role`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🚫 Blacklist  *(Admin)*",
                value=f"`{prefix}blacklist role add/remove/list/clear @role`",
                inline=False,
            )
            embed.add_field(
                name="📺 Channels  *(Admin / Owner)*",
                value=(
                    f"`{prefix}channel settings`\n"
                    f"`{prefix}channel starboard all/catch/egg/unbox/shiny/gigantamax/highiv/lowiv/missingno/milestone [#ch | none]`\n"
                    f"`{prefix}channel captcha [#ch]`\n"
                    f"**Owner:** `{prefix}channel lowpred #ch` • `{prefix}channel secondary #ch`\n"
                    f"**Owner:** `{prefix}channel global-starboard catch/egg/unbox #ch`"
                ),
                inline=False,
            )
            embed.add_field(name="🔮 Prediction",     value=f"`{prefix}predict`",                                                              inline=False)
            embed.add_field(
                name="🔍 Helpful",
                value=(
                    f"`{prefix}sr <pokemon>` • `{prefix}shr [chain] [target%]`\n"
                    f"`{prefix}td [id] [id2]` — show date, time diff, or full details (reply)\n"
                    f"`{prefix}color <hex/rgb/name>` — convert and display color formats\n"
                    f"`{prefix}date [objectid]` / `{prefix}caught` — Pokémon caught date from ObjectID  •  **Right-click:** Get Caught Date\n"
                    f"`{prefix}extractids [msg_id]` / `{prefix}eids` — extract IDs from Pokétwo embed  •  **Right-click:** Extract IDs\n"
                    "Hint solver (automatic — no command needed)"
                ),
                inline=False,
            )
            embed.add_field(
                name="📥 Event Extract",
                value=(
                    f"`{prefix}extractevent` / `{prefix}evtx` — watch a replied Pokétwo embed for edits  •  **Right-click:** Start Event Extract\n"
                    f"`{prefix}stopextract` — manually stop an active extraction"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔥 Incense",
                value=(
                    f"`{prefix}inc toggle` • `{prefix}inc cat add/remove/list`\n"
                    f"`{prefix}inc pause [all]` • `{prefix}inc resume [all]` • `{prefix}inc list`\n"
                    f"**Admin:** `{prefix}inc allowedroles` • `{prefix}inc ar add/remove/clear`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔐 Captcha  *(Admin)*",
                value=f"`{prefix}channel captcha [#channel]`",
                inline=False,
            )
            embed.add_field(
                name="💾 Reserve",
                value=(
                    f"`{prefix}r list` • `{prefix}r list @user` • `{prefix}r who <pokemon>`\n"
                    f"`{prefix}r remove p/cat` • `{prefix}r clear` • `{prefix}r transfer @alt` *(or `p/cat <name> @alt`)*\n"
                    f"**Admin:** `{prefix}r add p/cat @user` • `{prefix}r remove p/cat @user` • `{prefix}r clear @user` • `{prefix}r clear --all` • `{prefix}r switch @u1 @u2` • `{prefix}r transfer @u1 @u2`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🗂️ Organize",
                value=(
                    f"Everyone: click buttons to claim/release spots\n"
                    f"**Admin:** `{prefix}og template create/edit/delete/view` (no args to list) • `{prefix}og start [template]` • "
                    f"`{prefix}og view` • `{prefix}og end` • `{prefix}og cancel` • `{prefix}og spot set/clear` • `{prefix}og blacklist add/remove/clear`"
                ),
                inline=False,
            )
            embed.add_field(
                name="📝 List Builder",
                value=(
                    f"`{prefix}listgen` / `{prefix}lg` / `{prefix}listbuilder`\n"
                    "Buttons: ➕ Add · 🔍 Filter · ➖ Remove · 🗑️ Clear · 📄 Format *(cycles)* · 🔤 Enclose · ⚙️ Advanced · 📤 Send\n"
                    "Row 3: 🔡 Case dropdown (always visible)\n"
                    f"Filters: `--type` `--region` `--sr` `--stage` `--name` `--catchable` `--notcatchable`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔄 Release & Evolve",
                value=(
                    f"`{prefix}release <n>` / `{prefix}rel` • `{prefix}evolve <n>` / `{prefix}e` — also `/release` `/evolve`\n"
                    f"`{prefix}releaseadd/remove/list/clear` (`ra rr rl rc`) • `{prefix}releasepanel` (`relp`)\n"
                    f"`{prefix}evolveadd/remove/list/clear` (`ea er el ec`, `--once`) • `{prefix}evolvepanel` (`ep`)"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔍 Starboard Manual Check  *(Admin)*",
                value=f"`{prefix}catchcheck` • `{prefix}eggcheck` • `{prefix}unboxcheck` (supports multiple message IDs)",
                inline=False,
            )
            embed.add_field(
                name="✨ Shiny Count",
                value=f"`{prefix}sc` • `{prefix}sc edit <count>` *(Admin)* • `{prefix}sc channel [#ch]` *(Admin)*",
                inline=False,
            )
            if is_owner:
                embed.add_field(
                    name="👑 Owner",
                    value=(
                        f"`{prefix}loadmodel` • `{prefix}unloadmodel` • `{prefix}reloadmodel`\n"
                        f"`{prefix}modelstatus` • `{prefix}reloadsr`\n"
                        f"`{prefix}channel lowpred #ch` • `{prefix}channel secondary #ch`\n"
                        f"`{prefix}channel global-starboard catch/egg/unbox #ch`"
                    ),
                    inline=False,
                )
            embed.add_field(name="ℹ️ Info", value=f"`{prefix}help` • `{prefix}about` • `{prefix}ping`", inline=False)

        else:
            await ctx.reply(
                f"❌ Unknown category: `{category}`\n"
                f"Available: `collection`, `category`, `hunt`, `pings`, `settings`, `roles`, `blacklist`, `channels`, "
                f"`prediction`, `starboard`, `helpful`, `eventextract`, `incense`, `captcha`, `reserve`, `organize`, `listgen`, `ids`, "
                f"{'`owner`, ' if is_owner else ''}`all`\n"
                f"Use `{prefix}help` to see the main help menu.",
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
            return

        embed.set_footer(text=f"Bot Prefix: {', '.join(BOT_PREFIX)}")
        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=NO_MENTIONS)

    @commands.command(name="about")
    async def about_command(self, ctx):
        """Show bot information and statistics"""
        prefix = BOT_PREFIX[0]

        embed = discord.Embed(
            title="ℹ️ About Pokémon Helper Bot",
            description="A comprehensive Pokémon collection and prediction bot for Poketwo",
            color=EMBED_COLOR,
        )
        embed.add_field(
            name="✨ Key Features",
            value=(
                "• 📦 **Collection Management** — Track and get pinged for Pokémon you collect\n"
                "• 🗂️ **Category System** — Bulk add Pokémon to collection\n"
                "• ✨ **Shiny Hunting** — Get notified when your hunt target spawns\n"
                "• 🔷 **Type & Region Pings** — Get pinged by Pokémon type or region\n"
                "• 🔮 **Dual Model Prediction** — Automatically identifies Poketwo spawns\n"
                "• ⭐ **Starboard Logging** — Log rare catches, hatches, and unboxes\n"
                "• 🔕 **AFK Mode** — Disable pings when you're away\n"
                "• 🏷️ **Best Name** — Optionally show shortest known name per prediction\n"
                "• 📥 **Event Extract** — Export event Pokémon data straight from Pokétwo embeds\n"
                "• 🗂️ **Organize Events** — Claimable-spot embeds that feed straight into reserves\n"
                "• 🔄 **Release & Evolve Lists** — Personal ID lists that output ready-to-paste Pokétwo commands"
            ),
            inline=False,
        )
        embed.add_field(
            name="📊 Statistics",
            value=(
                f"**Servers:** {len(self.bot.guilds)}\n"
                f"**Users:** {sum(g.member_count for g in self.bot.guilds)}\n"
                f"**Commands:** {len(self.bot.commands)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="⚙️ Technical",
            value=(
                f"**Prefix:** {', '.join(BOT_PREFIX)}\n"
                "**Library:** discord.py\n"
                "**Database:** MongoDB\n"
                "**AI Models:** Dual CNN (224×224)"
            ),
            inline=True,
        )
        embed.add_field(
            name="🚀 Getting Started",
            value=f"Use `{prefix}help` to see all available commands and features!",
            inline=False,
        )
        embed.set_footer(text="Made with ❤️ for the Poketwo community")
        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=NO_MENTIONS)

    @commands.command(name="stats")
    async def stats_command(self, ctx):
        """Show bot uptime and prediction stats"""
        # Uptime — derived from process start time, so it needs no separate tracking
        uptime_seconds = time.time() - self.bot.process.create_time()

        # Session-only counter (see ModelControl) — resets on restart.
        session_count = getattr(self.bot, 'prediction_count', 0)

        # All-time total = last value flushed to Mongo + whatever's been
        # predicted since that flush but not yet written (see prediction.py).
        persisted_total = getattr(self.bot, 'total_predictions_persisted', 0)
        unflushed = getattr(self.bot, 'predictions_since_flush', 0)
        all_time_total = persisted_total + unflushed

        embed = discord.Embed(
            title="📊 Bot Stats",
            color=EMBED_COLOR,
        )
        embed.add_field(name="⏱️ Uptime", value=f"`{format_uptime(uptime_seconds)}`", inline=False)
        embed.add_field(name="🔮 Total Predictions", value=f"`{all_time_total:,}`", inline=True)
        embed.add_field(name="📈 This Session", value=f"`{session_count:,}`", inline=True)
        embed.add_field(name="🌐 Servers", value=f"`{len(self.bot.guilds):,}`", inline=True)
        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=NO_MENTIONS)

    @commands.command(name="ping", aliases=["latency", "pong"])
    async def ping_command(self, ctx):
        """Check bot's latency"""
        import time
        api_latency = round(self.bot.latency * 1000)
        start = time.perf_counter()
        message = await ctx.reply("🏓 Pinging...", mention_author=False, allowed_mentions=NO_MENTIONS)
        end = time.perf_counter()
        response_time = round((end - start) * 1000)

        embed = discord.Embed(title="🏓 Pong!", color=EMBED_COLOR)
        embed.add_field(name="API Latency",   value=f"{api_latency}ms",   inline=True)
        embed.add_field(name="Response Time", value=f"{response_time}ms", inline=True)

        if api_latency < 100:
            status = "🟢 Excellent"
        elif api_latency < 200:
            status = "🟡 Good"
        elif api_latency < 300:
            status = "🟠 Fair"
        else:
            status = "🔴 Poor"

        embed.add_field(name="Status", value=status, inline=True)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await message.edit(content=None, embed=embed)

    @commands.command(name="commands", aliases=["cmds"])
    async def commands_command(self, ctx):
        """Quick alias to show all commands"""
        await ctx.invoke(self.help_command, category="all")

    # ------------------------------------------------------------------
    # Slash Commands
    # ------------------------------------------------------------------
    @app_commands.command(name="help", description="Show help information for the bot")
    @app_commands.describe(category="Category: collection, category, hunt, pings, settings, roles, blacklist, channels, prediction, starboard, helpful, eventextract, incense, captcha, reserve, organize, listgen, ids, all")
    async def slash_help(self, interaction: discord.Interaction, category: str = None):
        ctx = await commands.Context.from_interaction(interaction)
        await self.help_command(ctx, category=category)

    @app_commands.command(name="about", description="Show bot information and statistics")
    async def slash_about(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.about_command(ctx)

    @app_commands.command(name="ping", description="Check bot latency")
    async def slash_ping(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.ping_command(ctx)

    @app_commands.command(name="stats", description="Show bot uptime and prediction stats")
    async def slash_stats(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.stats_command(ctx)


async def setup(bot):
    await bot.add_cog(Help(bot))
