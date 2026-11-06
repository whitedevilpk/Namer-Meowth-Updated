"""Main bot file"""
import os
import discord
import asyncio
import aiohttp
import psutil
import gc
import time
from discord.ext import commands
from discord import app_commands
from database import Database
from predict import Prediction, LAZY_LOAD_MODELS
from config import TOKEN, BOT_PREFIX

NO_MENTIONS = discord.AllowedMentions.none()

# Custom prefix function for case-insensitive, space-tolerant prefixes
def _consume_extra_spaces(content, matched_prefix):
    """Extend the matched prefix to also swallow any extra spaces/tabs
    right after it, so 'p!   sh' is treated the same as 'p!sh'."""
    idx = len(matched_prefix)
    while idx < len(content) and content[idx] in (' ', '\t'):
        idx += 1
    return content[:idx]


def get_prefix(bot, message):
    content = message.content
    content_lower = content.lower()

    # Allow mentioning the bot as a prefix (e.g. @BotName help)
    mention_prefixes = [f'<@{bot.user.id}> ', f'<@!{bot.user.id}> '] if bot.user else []

    for prefix in mention_prefixes:
        if content.startswith(prefix):
            return _consume_extra_spaces(content, prefix)

    for prefix in BOT_PREFIX:
        prefix_lower = prefix.lower()
        if content_lower.startswith(prefix_lower):
            matched = content[:len(prefix)]
            return _consume_extra_spaces(content, matched)

    return BOT_PREFIX

# Bot setup
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
    case_insensitive=True,
    max_messages=1000  # ← FIXED: Was None (unlimited), now limits to 1000 messages
)

# Global instances
bot.db = None
bot.predictor = None
bot.http_session = None

# Memory tracking
bot.process = psutil.Process(os.getpid())
bot.prediction_count = 0


async def initialize_predictor():
    """Create the Prediction object. Whether models load immediately depends
    on the LAZY_LOAD_MODELS flag in predict.py (applied later via maybe_eager_load)."""
    try:
        bot.predictor = Prediction()
        print("✅ Predictor object created")
    except Exception as e:
        print(f"❌ Failed to create predictor: {e}")


async def initialize_database():
    """Initialize MongoDB connection"""
    bot.db = Database()
    success = await bot.db.connect()
    return success


async def initialize_http_session():
    """Initialize aiohttp session"""
    timeout = aiohttp.ClientTimeout(total=10, connect=3)
    connector = aiohttp.TCPConnector(
        limit=50,
        limit_per_host=10,
        keepalive_timeout=30,
        enable_cleanup_closed=True,
        ttl_dns_cache=300,  # cache Discord CDN DNS lookups for 5 minutes
    )

    bot.http_session = aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers={'User-Agent': 'Pokemon-Helper-Bot/1.0'}
    )
    print("✅ HTTP session initialized")


async def memory_monitor():
    """Monitor and log memory usage every 5 minutes (reduced from every 60 seconds)"""
    await asyncio.sleep(10)
    
    last_log_time = 0
    LOG_INTERVAL = 480  # 5 minutes instead of 60 seconds

    while True:
        try:
            current_time = time.time()
            mem_info = bot.process.memory_info()
            mem_mb = mem_info.rss / 1024 / 1024

            models_loaded = bot.predictor and bot.predictor.models_initialized
            model_status = "loaded" if models_loaded else "not loaded"

            # Only log every 5 minutes
            if current_time - last_log_time >= LOG_INTERVAL:
                print(f"[MEMORY] {mem_mb:.1f} MB | Models: {model_status} | Predictions: {bot.prediction_count}")
                last_log_time = current_time

            # Run GC early — 460 MB gives headroom before Railway's 500 MB wall
            if mem_mb > 460:
                print(f"[MEMORY] ⚠️ High usage ({mem_mb:.1f} MB), forcing GC...")
                gc.collect()
                await asyncio.sleep(1)
                new_mem_mb = bot.process.memory_info().rss / 1024 / 1024
                print(f"[MEMORY] After GC: {new_mem_mb:.1f} MB (freed {mem_mb - new_mem_mb:.1f} MB)")

            # Evict stale keys from guild cache every cycle
            if bot.db and hasattr(bot.db, 'gcache') and bot.db.gcache:
                bot.db.gcache.cleanup_expired()

            await asyncio.sleep(60)  # Keep checking every 60 seconds, but log less frequently

        except Exception as e:
            print(f"[MEMORY] Monitor error: {e}")
            await asyncio.sleep(60)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"Bot prefix: {', '.join(BOT_PREFIX)}")

    initial_mem = bot.process.memory_info().rss / 1024 / 1024
    print(f"[MEMORY] Initial: {initial_mem:.1f} MB")

    await initialize_http_session()
    await initialize_predictor()
    await initialize_database()

    # Load models now if LAZY_LOAD_MODELS is False; otherwise this is a no-op
    # and models stay unloaded until p!model load is run.
    await bot.predictor.maybe_eager_load(bot.http_session)

    cogs_to_load = [
        'cogs.collection',
        'cogs.shiny_count',
        'cogs.type_region',
        'cogs.release_evolve',
        'cogs.shiny_hunt',
        'cogs.settings',
        'cogs.incense',
        'cogs.poketools',
        'cogs.hint_solver',
        'cogs.prediction',
        'cogs.category',
        'cogs.captcha',
        'cogs.channelconfig',
        'cogs.starboard_catch',
        'cogs.starboard_egg',
        'cogs.starboard_unbox',
        'cogs.bot_hlp',
        'cogs.model_control',
        'cogs.reserve',
        'cogs.eventextract',
        'cogs.organize',
        'cogs.listgen',
    ]

    try:
        await bot.load_extension('jishaku')
        print("✅ Jishaku loaded")
    except Exception as e:
        print(f"⚠️ Could not load Jishaku: {e}")

    loaded_count = 0
    failed_count = 0

    for cog in cogs_to_load:
        try:
            await bot.load_extension(cog)
            print(f"✅ Loaded {cog}")
            loaded_count += 1
        except Exception as e:
            print(f"❌ Failed to load {cog}: {e}")
            failed_count += 1

    # Sync slash commands with Discord
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"⚠️ Failed to sync slash commands: {e}")

    post_startup_mem = bot.process.memory_info().rss / 1024 / 1024

    print(f"\n{'='*50}")
    print(f"✅ Bot ready!")
    print(f"📊 Loaded {loaded_count}/{len(cogs_to_load)} cogs")
    if failed_count > 0:
        print(f"⚠️ Failed to load {failed_count} cogs")
    print(f"🌐 Serving {len(bot.guilds)} guilds")
    print(f"👥 Serving {sum(g.member_count for g in bot.guilds)} users")
    models_loaded = bot.predictor and bot.predictor.models_initialized
    print(f"💾 RAM at startup: {post_startup_mem:.1f} MB (models {'loaded' if models_loaded else 'not loaded'})")
    if not models_loaded:
        print(f"💡 Use p!model load to load prediction models when starting an incense session")
    print(f"{'='*50}\n")

    asyncio.create_task(memory_monitor())


@bot.event
async def on_message_edit(before, after):
    """Process edited messages as commands"""
    if after.author.bot:
        return

    if before.content == after.content:
        return

    await bot.process_commands(after)


@bot.event
async def on_command_error(ctx, error):
    """Global error handler"""
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.CommandOnCooldown):
        await ctx.reply(f"⏳ This command is on cooldown. Try again in {error.retry_after:.1f}s", mention_author=False, allowed_mentions=NO_MENTIONS)
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ You don't have permission to use this command.", mention_author=False, allowed_mentions=NO_MENTIONS)
        return

    if isinstance(error, commands.BotMissingPermissions):
        await ctx.reply("❌ I don't have the necessary permissions to execute this command.", mention_author=False, allowed_mentions=NO_MENTIONS)
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Missing required argument: `{error.param.name}`\nUse `m!help` for command usage.", mention_author=False, allowed_mentions=NO_MENTIONS)
        return

    if isinstance(error, commands.BadArgument):
        await ctx.reply(f"❌ Invalid argument provided.\nUse `m!help` for command usage.", mention_author=False, allowed_mentions=NO_MENTIONS)
        return

    if isinstance(error, commands.NotOwner):
        await ctx.reply("❌ This command can only be used by the bot owner.", mention_author=False, allowed_mentions=NO_MENTIONS)
        return

    if isinstance(error, commands.NoPrivateMessage):
        await ctx.reply("❌ This command can't be used in DMs. Please use it in a server.", mention_author=False, allowed_mentions=NO_MENTIONS)
        return

    if isinstance(error, commands.CheckFailure):
        # Catches any other failed checks not handled above
        return

    print(f"Unexpected error in command {ctx.command}: {error}")
    await ctx.reply("❌ An unexpected error occurred. Please try again later.", mention_author=False, allowed_mentions=NO_MENTIONS)


# ============================================================================
# DIAGNOSTIC COMMAND - Check memory and cache status
# ============================================================================
@bot.command(name="memcheck")
async def memcheck(ctx):
    """Check bot's memory usage and cache status"""
    try:
        mem_info = bot.process.memory_info()
        mem_mb = mem_info.rss / 1024 / 1024
        
        # Get number of cached messages
        cached_messages = len(bot.cached_messages) if hasattr(bot, 'cached_messages') else 0
        
        # Create response
        embed = discord.Embed(
            title="💾 Memory & Cache Status",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="RAM Usage",
            value=f"{mem_mb:.1f} MB / 500 MB ({(mem_mb/500)*100:.1f}%)",
            inline=False
        )
        
        embed.add_field(
            name="Cached Messages",
            value=f"{cached_messages:,} / 1000 (max)",
            inline=False
        )
        
        embed.add_field(
            name="Predictions Made",
            value=str(bot.prediction_count),
            inline=False
        )
        
        # Add warning if memory is high
        if mem_mb > 400:
            embed.color = discord.Color.red()
            embed.add_field(name="⚠️ WARNING", value="Memory is dangerously high!", inline=False)
        elif mem_mb > 350:
            embed.color = discord.Color.orange()
            embed.add_field(name="⚠️ WARNING", value="Memory usage is elevated", inline=False)
        
        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)
        
    except Exception as e:
        await ctx.send(f"❌ Error: {e}", allowed_mentions=NO_MENTIONS)


async def cleanup():
    """Clean up resources on shutdown"""
    if bot.http_session:
        await bot.http_session.close()

    if bot.db:
        bot.db.close()


def main():
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN environment variable not set")
        return

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Error: Invalid Discord token")
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
    finally:
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(cleanup())
        except Exception:
            pass


if __name__ == "__main__":
    main()
