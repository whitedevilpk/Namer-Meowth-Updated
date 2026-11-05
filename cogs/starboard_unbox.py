"""Starboard logging for box openings"""
import discord
import re
from datetime import datetime
from discord.ext import commands
from discord import app_commands
from config import POKETWO_USER_ID, EMBED_COLOR, HIGH_IV_THRESHOLD, LOW_IV_THRESHOLD, Emojis
from starboard_utils import (
    get_gender_emoji,
    find_pokemon_image_url,
    format_iv_display,
    create_jump_button_view
)

class StarboardUnbox(commands.Cog):
    """Automatic logging of box openings to starboard channels"""

    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        """Get database from bot"""
        return self.bot.db

    async def get_unboxed_by_user(self, message: discord.Message):
        """Get who opened the box from the reply"""
        if not message.reference:
            return None

        try:
            if message.reference.resolved:
                return message.reference.resolved.author.id

            referenced_message = await message.channel.fetch_message(message.reference.message_id)
            return referenced_message.author.id

        except Exception as e:
            print(f"Error getting unboxed user: {e}")
            return None

    def extract_pokemon_from_text(self, text: str) -> list:
        """Extract Pokemon data from any text using flexible patterns"""
        pokemon_found = []

        # Patterns for Pokemon extraction
        patterns = [
            # Main pattern
            r'<:_:\d+>\s*(?:✨\s*)?Level\s+(\d+)\s+(.+?)\s*<:(male|female|unknown):\d+>\s*\((\d+(?:\.\d+)?)%\)',
            # Alternative pattern
            r'<:_:\d+>\s*(✨\s*)?Level\s+(\d+)\s+(.+?)\s+<:(male|female|unknown):\d+>\s*\((\d+(?:\.\d+)?)%\)',
        ]

        lines = text.split('\n')
        for line in lines:
            line = line.strip()

            # Skip lines that don't contain Pokemon data indicators
            if not ('<:_:' in line and 'Level' in line and '(' in line and '%' in line):
                continue

            # Remove markdown formatting
            clean_line = line.replace('**', '').replace('- ', '').strip()

            # Check for shiny
            is_shiny = '✨' in clean_line

            for pattern in patterns:
                match = re.search(pattern, clean_line)
                if match:
                    # Determine groups based on pattern
                    if len(match.groups()) == 4:
                        level = match.group(1)
                        pokemon_name_with_gender = match.group(2).strip()
                        gender = match.group(3)
                        iv = float(match.group(4))
                    else:
                        if match.group(1):
                            is_shiny = True
                        level = match.group(2)
                        pokemon_name_with_gender = match.group(3).strip()
                        gender = match.group(4)
                        iv = float(match.group(5))

                    # Remove gender emoji from pokemon name
                    pokemon_name = re.sub(r'<:(male|female|unknown):\d+>', '', pokemon_name_with_gender).strip()

                    # Check for gigantamax
                    is_gigantamax = pokemon_name.lower().startswith('gigantamax')

                    pokemon_data = {
                        'pokemon_name': pokemon_name,
                        'level': level,
                        'iv': iv,
                        'is_shiny': is_shiny,
                        'is_gigantamax': is_gigantamax,
                        'gender': gender
                    }

                    pokemon_found.append(pokemon_data)
                    break

        return pokemon_found

    def extract_milestone_pokemon_from_text(self, text: str) -> list:
        """Extract Pokemon data from pokédex milestone reward messages.

        These messages only tell us the Pokemon name (and whether it's shiny) -
        there's no Level or IV info, e.g.:
            "You've earned a **<:_:1242491442174361721> Original Magearna**!"
            "You've earned a **<:_:1242491499279683646> ✨ Original Magearna**!"
        """
        pokemon_found = []

        pattern = r'<:_:\d+>\s*(✨\s*)?([^*<]+?)\*\*'

        for match in re.finditer(pattern, text):
            is_shiny = bool(match.group(1))
            pokemon_name = match.group(2).strip()

            if not pokemon_name:
                continue

            is_gigantamax = pokemon_name.lower().startswith('gigantamax')

            pokemon_found.append({
                'pokemon_name': pokemon_name,
                'level': '???',
                'iv': '???',
                'is_shiny': is_shiny,
                'is_gigantamax': is_gigantamax,
                'gender': None
            })

        return pokemon_found

    def meets_starboard_criteria(self, pokemon_data: dict) -> bool:
        """Whether a Pokemon (from either an unbox or a milestone message) is starboard-worthy"""
        if pokemon_data.get('is_shiny') or pokemon_data.get('is_gigantamax'):
            return True

        try:
            iv_value = float(pokemon_data.get('iv'))
        except (TypeError, ValueError):
            # Unknown IV (e.g. "?" from a milestone reward) can't qualify on IV alone
            return False

        return iv_value >= HIGH_IV_THRESHOLD or iv_value <= LOW_IV_THRESHOLD

    @staticmethod
    def format_iv(iv) -> str:
        """Format an IV value for display, handling the unknown '?' case"""
        if iv == '?' or iv is None:
            return "?"
        return f"{iv}%"

    def parse_poketwo_unbox_message(self, message: discord.Message, unboxed_by_id: int = None) -> list:
        """Parse a Poketwo box opening or pokédex milestone message to extract Pokemon information"""
        if not message.embeds:
            return []

        embed = message.embeds[0]
        pokemon_found = []

        title = embed.title or ""

        # Keywords for box opening messages
        opening_keywords = ['open', 'opening', 'box', 'chest', 'mystery', 'egg', 'eggs', 'bundle', 'puddle', 'rain', 'storm']
        is_opening_message = any(keyword.lower() in title.lower() for keyword in opening_keywords)

        # Keywords for pokédex milestone reward messages (level/IV unknown, name + shiny only)
        milestone_keywords = ['milestone', 'milestones']
        is_milestone_message = any(keyword.lower() in title.lower() for keyword in milestone_keywords)

        if not is_opening_message and not is_milestone_message:
            return []

        if is_milestone_message:
            extractor = self.extract_milestone_pokemon_from_text
            message_type = 'milestone'
        else:
            extractor = self.extract_pokemon_from_text
            message_type = 'unbox'

        # Try to extract Pokemon from description
        if embed.description:
            pokemon_from_desc = extractor(embed.description)
            for pokemon_data in pokemon_from_desc:
                pokemon_data['unboxed_by_id'] = unboxed_by_id
                pokemon_data['message_type'] = message_type
                pokemon_found.append(pokemon_data)

        # Try to extract Pokemon from all fields
        for field in embed.fields:
            if field.value:
                pokemon_from_field = extractor(field.value)
                for pokemon_data in pokemon_from_field:
                    pokemon_data['unboxed_by_id'] = unboxed_by_id
                    pokemon_data['message_type'] = message_type
                    pokemon_found.append(pokemon_data)

        return pokemon_found

    def create_unbox_embed(self, pokemon_data: dict, original_message: discord.Message = None) -> discord.Embed:
        """Create embed for unbox (or, for milestone rewards, a black embed with Level/IV shown as '?')"""
        pokemon_name = pokemon_data['pokemon_name']
        level = pokemon_data['level']
        iv = pokemon_data['iv']
        is_shiny = pokemon_data['is_shiny']
        is_gigantamax = pokemon_data['is_gigantamax']
        gender = pokemon_data.get('gender')
        unboxed_by_id = pokemon_data.get('unboxed_by_id')
        is_milestone = pokemon_data.get('message_type') == 'milestone'

        # Format IV (milestone rewards only ever know "?")
        iv_display = self.format_iv(iv)

        # Get gender emoji
        gender_emoji = get_gender_emoji(gender)

        # Format Pokemon name with gender
        if gender_emoji:
            pokemon_display = f"{pokemon_name} {gender_emoji}"
        else:
            pokemon_display = pokemon_name

        # Get Pokemon image URL
        image_url = find_pokemon_image_url(pokemon_name, is_shiny, gender, is_gigantamax)

        # Milestone rewards get a dedicated black embed since we only know the name + shiny status
        embed_color = 0x000000 if is_milestone else EMBED_COLOR
        # Footer date should reflect when the original message was sent, not when it was checked
        embed_timestamp = original_message.created_at if original_message else datetime.utcnow()
        embed = discord.Embed(color=embed_color, timestamp=embed_timestamp)

        # Determine title based on criteria
        title_parts = []

        if is_shiny:
            title_parts.append("✨ Shiny")

        if is_gigantamax:
            title_parts.append(f"{Emojis.GIGANTAMAX} Gigantamax")

        # Check IV (not applicable for milestone rewards - level/IV are unknown)
        if not is_milestone:
            try:
                iv_value = float(iv)
                if iv_value >= HIGH_IV_THRESHOLD:
                    title_parts.append("📈 High IV")
                elif iv_value <= LOW_IV_THRESHOLD:
                    title_parts.append("📉 Low IV")
            except ValueError:
                pass

        label = "Milestone Reward" if is_milestone else "Rare Unbox"
        if title_parts:
            embed.title = f"{Emojis.GIFTBOX} " + " ".join(title_parts) + f" {label} Detected {Emojis.GIFTBOX}"
        else:
            embed.title = f"{Emojis.GIFTBOX} {label} Detected {Emojis.GIFTBOX}"

        # Description
        base_description = f"**Pokémon:** {pokemon_display}\n**Level:** {level}\n**IV:** {iv_display}"
        if unboxed_by_id:
            embed.description = f"**Unboxed By:** <@{unboxed_by_id}>\n{base_description}"
        else:
            embed.description = base_description

        if image_url:
            embed.set_thumbnail(url=image_url)

        return embed

    async def send_to_starboard_channels(self, guild: discord.Guild, pokemon_list: list, original_message: discord.Message = None):
        """Send unbox data to appropriate starboard channels"""
        settings = await self.db.get_guild_settings(guild.id)

        # Process each Pokemon that meets criteria
        for pokemon_data in pokemon_list:
            is_shiny = pokemon_data['is_shiny']
            is_gigantamax = pokemon_data['is_gigantamax']
            iv = pokemon_data['iv']

            # Check if this Pokemon meets starboard criteria (handles unknown '?' IV from milestones)
            if not self.meets_starboard_criteria(pokemon_data):
                continue

            # Determine which channels to send to
            channels_to_send = []

            # General unbox channel
            unbox_channel_id = settings.get('starboard_unbox_channel_id')
            if unbox_channel_id:
                channels_to_send.append(unbox_channel_id)

            # Shiny channel
            if is_shiny:
                shiny_channel_id = settings.get('starboard_shiny_channel_id')
                if shiny_channel_id and shiny_channel_id not in channels_to_send:
                    channels_to_send.append(shiny_channel_id)

            # Gigantamax channel
            if is_gigantamax:
                gmax_channel_id = settings.get('starboard_gigantamax_channel_id')
                if gmax_channel_id and gmax_channel_id not in channels_to_send:
                    channels_to_send.append(gmax_channel_id)

            # IV channels
            try:
                iv_value = float(iv)

                if iv_value >= HIGH_IV_THRESHOLD:
                    highiv_channel_id = settings.get('starboard_highiv_channel_id')
                    if highiv_channel_id and highiv_channel_id not in channels_to_send:
                        channels_to_send.append(highiv_channel_id)

                elif iv_value <= LOW_IV_THRESHOLD:
                    lowiv_channel_id = settings.get('starboard_lowiv_channel_id')
                    if lowiv_channel_id and lowiv_channel_id not in channels_to_send:
                        channels_to_send.append(lowiv_channel_id)

            except ValueError:
                pass

            # Create embed and view
            embed = self.create_unbox_embed(pokemon_data, original_message)
            view = create_jump_button_view(original_message)

            # Send to all applicable channels
            for channel_id in channels_to_send:
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        await channel.send(embed=embed, view=view)
                    except Exception as e:
                        print(f"Error sending to starboard channel {channel_id}: {e}")

            # Send to global unbox channel if configured
            global_unbox_channel_id = await self.db.get_global_starboard_unbox_channel()
            if global_unbox_channel_id:
                global_channel = self.bot.get_channel(global_unbox_channel_id)
                if global_channel:
                    try:
                        await global_channel.send(embed=embed, view=view)
                    except Exception as e:
                        print(f"Error sending to global starboard channel: {e}")

    async def box_check_context_callback(self, interaction: discord.Interaction, message: discord.Message):
        """Context menu command to check a box opening message and DM results to the user"""
        if message.author.id != POKETWO_USER_ID or not message.embeds:
            await interaction.response.send_message(
                "❌ Please use this on a Pokétwo box opening message!",
                ephemeral=True
            )
            return

        embed = message.embeds[0]
        title = embed.title or ""
        opening_keywords = ['open', 'opening', 'box', 'chest', 'mystery', 'egg', 'eggs', 'bundle', 'puddle', 'rain', 'storm']
        milestone_keywords = ['milestone', 'milestones']
        if not any(kw.lower() in title.lower() for kw in opening_keywords + milestone_keywords):
            await interaction.response.send_message(
                "❌ This doesn't look like a box opening or milestone reward message.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        unboxed_by_id = await self.get_unboxed_by_user(message)
        pokemon_list = self.parse_poketwo_unbox_message(message, unboxed_by_id)

        if not pokemon_list:
            await interaction.followup.send("❌ Couldn't parse any Pokémon from that message.")
            return

        qualifying_pokemon = [p for p in pokemon_list if self.meets_starboard_criteria(p)]

        # --- Build DM content ---
        if not qualifying_pokemon:
            pokemon_summary = []
            for p in pokemon_list:
                ge = get_gender_emoji(p.get('gender'))
                display = f"{p['pokemon_name']} {ge}".strip()
                pokemon_summary.append(f"**{display}** (Lvl {p['level']}, {self.format_iv(p['iv'])})")

            dm_lines = [
                f"📦 **Box Check** — no starboard-worthy Pokémon found.\n",
                "**All Pokémon in box:**",
                *pokemon_summary,
                f"\n_Criteria: Shiny, Gigantamax, IV ≥{HIGH_IV_THRESHOLD}% or ≤{LOW_IV_THRESHOLD}%_"
            ]
            dm_content = "\n".join(dm_lines)

            try:
                await interaction.user.send(dm_content)
                await interaction.followup.send("📬 Results sent to your DMs!")
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ Couldn't DM you. Please enable DMs from server members and try again."
                )
            return

        # Send each qualifying Pokémon as an embed to DMs
        view = create_jump_button_view(message)
        try:
            dm_channel = await interaction.user.create_dm()
            await dm_channel.send(
                f"📦 **Box Check** — {len(qualifying_pokemon)} noteworthy Pokémon found!\n"
                f"[Jump to original message]({message.jump_url})"
            )
            for pokemon_data in qualifying_pokemon:
                embed_to_send = self.create_unbox_embed(pokemon_data, message)
                await dm_channel.send(embed=embed_to_send, view=view)

            await interaction.followup.send(
                f"📬 Results ({len(qualifying_pokemon)} Pokémon) sent to your DMs!"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Couldn't DM you. Please enable DMs from server members and try again."
            )

    @commands.command(name="unboxcheck", aliases=["bcheck", "eventcheck"])
    @commands.has_permissions(administrator=True)
    async def unbox_check_command(self, ctx, *, input_data: str = None):
        """Manually check a Poketwo box opening message and send results to DMs

        Usage:
            p!unboxcheck (reply to a message)
            p!unboxcheck <message_id>

        Works across all servers and sends results directly to your DMs.
        """
        original_message = None
        unboxed_by_id = None

        if input_data is None:
            if ctx.message.reference and ctx.message.reference.resolved:
                original_message = ctx.message.reference.resolved
                unboxed_by_id = await self.get_unboxed_by_user(original_message)
            else:
                await ctx.reply(
                    "Please provide a message ID or reply to a Poketwo box opening message.\n"
                    "Examples:\n"
                    "`p!unboxcheck 123456789012345678` (message ID)\n"
                    "Or reply to a message with just `p!unboxcheck`",
                    mention_author=False
                )
                return
        else:
            if input_data.strip().isdigit():
                message_id = int(input_data.strip())
                try:
                    original_message = None

                    # First, try the current server
                    try:
                        original_message = await ctx.channel.fetch_message(message_id)
                    except discord.NotFound:
                        pass

                    # If not found, search all guilds the bot is in
                    if not original_message:
                        for guild in self.bot.guilds:
                            for channel in guild.text_channels:
                                if channel.permissions_for(guild.me).read_message_history:
                                    try:
                                        original_message = await channel.fetch_message(message_id)
                                        break
                                    except (discord.NotFound, discord.Forbidden):
                                        continue
                            if original_message:
                                break

                    if not original_message:
                        await ctx.reply(f"❌ Could not find message with ID `{message_id}` in any accessible server.", mention_author=False)
                        return

                    if original_message.author.id != POKETWO_USER_ID:
                        await ctx.reply(f"❌ The message with ID `{message_id}` is not from Poketwo.", mention_author=False)
                        return

                    unboxed_by_id = await self.get_unboxed_by_user(original_message)

                except ValueError:
                    await ctx.reply(f"❌ Invalid message ID: `{input_data.strip()}`", mention_author=False)
                    return
                except Exception as e:
                    await ctx.reply(f"❌ Error fetching message: {str(e)}", mention_author=False)
                    return
            else:
                await ctx.reply("❌ Please provide a valid message ID or reply to a message.", mention_author=False)
                return

        # Parse unbox message
        pokemon_list = self.parse_poketwo_unbox_message(original_message, unboxed_by_id)

        if not pokemon_list:
            await ctx.reply("❌ Invalid message format. Please make sure it's a proper Poketwo box opening message.", mention_author=False)
            return

        # Check which Pokemon meet starboard criteria
        qualifying_pokemon = []
        all_pokemon_data = []

        for pokemon_data in pokemon_list:
            all_pokemon_data.append(pokemon_data)

            if self.meets_starboard_criteria(pokemon_data):
                qualifying_pokemon.append(pokemon_data)

        # Send results to DMs
        try:
            dm_channel = await ctx.author.create_dm()

            # Send summary embed
            summary_embed = discord.Embed(
                title="📬 Box Check Results",
                description=f"Found **{len(all_pokemon_data)}** Pokemon total",
                color=EMBED_COLOR,
                timestamp=datetime.utcnow()
            )
            summary_embed.add_field(
                name="✅ Qualifying Pokemon",
                value=f"{len(qualifying_pokemon)} Pokemon meet starboard criteria" if qualifying_pokemon else "None meet criteria",
                inline=False
            )
            summary_embed.add_field(
                name="📊 Criteria",
                value=f"✨ Shiny\n{Emojis.GIGANTAMAX} Gigantamax\n📈 IV ≥ {HIGH_IV_THRESHOLD}%\n📉 IV ≤ {LOW_IV_THRESHOLD}%",
                inline=False
            )

            if original_message.guild:
                summary_embed.add_field(
                    name="📍 Server",
                    value=original_message.guild.name,
                    inline=True
                )

            await dm_channel.send(embed=summary_embed)

            # Send all Pokemon details
            if pokemon_list:
                all_pokemon_embed = discord.Embed(
                    title="📋 All Pokemon Found",
                    color=EMBED_COLOR,
                    timestamp=datetime.utcnow()
                )

                pokemon_details = []
                for pokemon_data in pokemon_list:
                    gender_emoji = get_gender_emoji(pokemon_data.get('gender'))
                    pokemon_display = f"{pokemon_data['pokemon_name']} {gender_emoji}" if gender_emoji else pokemon_data['pokemon_name']

                    shiny_indicator = "✨" if pokemon_data['is_shiny'] else ""
                    gigantamax_indicator = f"{Emojis.GIGANTAMAX}" if pokemon_data['is_gigantamax'] else ""
                    indicators = f" {shiny_indicator} {gigantamax_indicator}".strip()

                    pokemon_details.append(
                        f"**{pokemon_display}**{f' {indicators}' if indicators else ''}\n"
                        f"Level {pokemon_data['level']} • IV: {self.format_iv(pokemon_data['iv'])}"
                    )

                all_pokemon_embed.description = "\n".join(pokemon_details)
                await dm_channel.send(embed=all_pokemon_embed)

            # Send individual embeds for qualifying Pokemon
            if qualifying_pokemon:
                await dm_channel.send("**✅ Qualifying Pokemon Details:**")

                for pokemon_data in qualifying_pokemon:
                    embed = self.create_unbox_embed(pokemon_data, original_message)
                    view = create_jump_button_view(original_message)
                    await dm_channel.send(embed=embed, view=view)

            # Send result message in the server
            if not qualifying_pokemon:
                pokemon_summary = []
                for pokemon_data in all_pokemon_data:
                    gender_emoji = get_gender_emoji(pokemon_data.get('gender'))
                    pokemon_display = f"{pokemon_data['pokemon_name']} {gender_emoji}" if gender_emoji else pokemon_data['pokemon_name']
                    pokemon_summary.append(f"**{pokemon_display}** (Level {pokemon_data['level']}, {self.format_iv(pokemon_data['iv'])})")

                summary_text = "\n".join(pokemon_summary) if pokemon_summary else "No Pokemon found"
                await ctx.reply(
                    f"✅ Results sent to your DMs!\n\n"
                    f"❌ No Pokemon meet starboard criteria.\n"
                    f"**Found Pokemon:**\n{summary_text}\n"
                    f"**Criteria:** Shiny, Gigantamax, or IV ≥{HIGH_IV_THRESHOLD}% or ≤{LOW_IV_THRESHOLD}%",
                    mention_author=False
                )
            else:
                # Send to starboard if in a guild
                if original_message.guild:
                    await self.send_to_starboard_channels(original_message.guild, qualifying_pokemon, original_message)

                # Create summary for server reply
                summary_lines = []
                for pokemon_data in qualifying_pokemon:
                    criteria_met = []
                    if pokemon_data['is_shiny']:
                        criteria_met.append("✨ Shiny")
                    if pokemon_data['is_gigantamax']:
                        criteria_met.append(f"{Emojis.GIGANTAMAX} Gigantamax")
                    try:
                        iv_value = float(pokemon_data['iv'])
                        if iv_value >= HIGH_IV_THRESHOLD:
                            criteria_met.append(f"📈 High IV ({pokemon_data['iv']}%)")
                        elif iv_value <= LOW_IV_THRESHOLD:
                            criteria_met.append(f"📉 Low IV ({pokemon_data['iv']}%)")
                    except (TypeError, ValueError):
                        pass

                    criteria_text = ", ".join(criteria_met)
                    gender_emoji = get_gender_emoji(pokemon_data.get('gender'))
                    pokemon_display = f"{pokemon_data['pokemon_name']} {gender_emoji}" if gender_emoji else pokemon_data['pokemon_name']
                    summary_lines.append(f"**{pokemon_display}** - {criteria_text}")

                summary_text = "\n".join(summary_lines)
                await ctx.reply(
                    f"✅ {len(qualifying_pokemon)} Pokemon sent to your DMs and starboard!\n"
                    f"{summary_text}",
                    mention_author=False
                )

        except discord.Forbidden:
            await ctx.reply("❌ Couldn't send you DMs. Please enable DMs from server members and try again.", mention_author=False)

    @unbox_check_command.error
    async def unbox_check_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("❌ You need administrator permissions to use this command.", mention_author=False)
        else:
            print(f"Unexpected error in unboxcheck: {error}")
            await ctx.reply("❌ An unexpected error occurred. Please try again.", mention_author=False)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for Poketwo box opening messages"""
        if message.author.id != POKETWO_USER_ID:
            return

        # Check if it's a box opening message
        if not message.embeds:
            return

        embed = message.embeds[0]
        title = embed.title or ""

        # Check for opening keywords or pokédex milestone reward keywords
        opening_keywords = ['open', 'opening', 'box', 'chest', 'mystery', 'egg', 'eggs', 'bundle', 'puddle', 'supply', 'crate', 'crates', 'rain', 'storm']
        milestone_keywords = ['milestone', 'milestones']
        is_relevant_message = any(keyword.lower() in title.lower() for keyword in opening_keywords + milestone_keywords)

        if not is_relevant_message:
            return

        # Get unboxed user
        unboxed_by_id = await self.get_unboxed_by_user(message)
        pokemon_list = self.parse_poketwo_unbox_message(message, unboxed_by_id)

        if not pokemon_list:
            return

        # Filter Pokemon that meet criteria (handles unknown '?' IV from milestone rewards)
        qualifying_pokemon = [p for p in pokemon_list if self.meets_starboard_criteria(p)]

        if qualifying_pokemon:
            await self.send_to_starboard_channels(message.guild, qualifying_pokemon, message)

async def setup(bot):
    cog = StarboardUnbox(bot)

    box_check_context_menu = app_commands.ContextMenu(
        name="Box Check",
        callback=cog.box_check_context_callback
    )
    bot.tree.add_command(box_check_context_menu)

    await bot.add_cog(cog)
