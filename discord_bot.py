import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import aiohttp
import os
import re
from dotenv import load_dotenv
from threading import Thread, Lock
from datetime import datetime
import sys
import signal


load_dotenv()

TOKEN = os.getenv('DISCORD_BOT_TOKEN')
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', 0))
PRC_API_KEY = os.getenv('PRC_API_KEY')

# Change this to the Discord user ID that should control slash commands.
OWNER_USER_ID = 1147491063557275708
# CSRP Dyno bot ID (used to detect !ssu/!ssd from logs)
DYNO_BOT_ID = 155149108183695360
SERVER_LOGS_CHANNEL_ID = LOG_CHANNEL_ID

MIN_PLAYER_THRESHOLD = 25
TARGET_PLAYER_COUNT = 36
CHECK_INTERVAL_SECONDS = 60


PRC_API_URL = "https://api.policeroleplay.community/v1/server/players"

EMOJI_FALSE = "<:false:1446899312859807886>"
EMOJI_CHECK = "<:correct:1446898949838606396>"
EMOJI_INFO = "<:info:1446893385905475737>"
EMOJI_WARNING = "<:icons_warning:1446886271547871273>"
EMOJI_MESSAGE = "<:message:1461038498483015814>"
EMOJI_SCALE_UP = "<:up:1461036166965612574>"
EMOJI_SCALE_DOWN = "<:down:1461036192425031730>"


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)


bot_controller = None
session_state = "SSD"
auto_scale_enabled = True
last_player_count = None


def is_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != OWNER_USER_ID:
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


async def get_player_count():
    global last_player_count

    if not PRC_API_KEY:
        return None, "No API key configured"

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Server-Key": PRC_API_KEY}
            async with session.get(PRC_API_URL, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    count = len(data) if isinstance(data, list) else 0
                    last_player_count = count
                    return count, None
                elif resp.status == 401:
                    return None, "Invalid API key"
                elif resp.status == 403:
                    return None, "API access denied"
                else:
                    return None, f"API error: {resp.status}"
    except asyncio.TimeoutError:
        return None, "API timeout"
    except Exception as e:
        return None, str(e)[:50]


class BotController:

    def __init__(self):

        from roblox import RobloxClientMutex
        self._mutex = RobloxClientMutex()

        self.launching = False
        self.active_clients = []
        self.stop_requested = False
        self.lock = Lock()
        self.launch_progress = 0
        self.total_to_launch = 0
        self.last_error = None
        self.start_time = None
        self.afk_thread = None
        self.afk_running = False

    def start_bots(self, callback=None, max_bots=None):
        with self.lock:
            if self.launching:
                return False, "Already launching!"
            self.launching = True
            self.stop_requested = False
            self.launch_progress = 0
            self.last_error = None
            if not self.start_time:
                self.start_time = datetime.now()

        thread = Thread(target=self._run_bots, args=(
            callback, max_bots), daemon=True)
        thread.start()
        return True, "Starting bots..."

    def remove_bots(self, count):
        with self.lock:
            if not self.active_clients:
                return 0

            to_remove = min(count, len(self.active_clients))
            removed = 0

            for _ in range(to_remove):
                if self.active_clients:
                    client, name, session = self.active_clients.pop()
                    try:
                        client.close()
                        removed += 1
                    except Exception:
                        pass

            return removed

    def _run_bots(self, callback=None, max_bots=None):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, script_dir)

            from roblox import Roblox
            from cookie_manager import CookieRefreshTracker
            from match_cookies import validate_all_cookies
            import json
            import time
            import random

            with open(os.path.join(script_dir, 'config.json'), 'r') as f:
                config = json.load(f)

            place_id = config.get('erlc_place_id', '2534724415')
            ps_code = config.get('private_server_code', 'calf')
            stagger_delay = config.get('stagger_delay', 45)
            config_max = config.get('max_accounts', 15)
            minimize_windows = config.get('minimize_windows', True)
            low_priority = config.get('low_priority_mode', True)

            launch_data = f'{{"psCode":"{ps_code}"}}'

            def send_callback(msg):
                if callback:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            callback(msg), bot.loop)
                    except Exception:
                        pass

            send_callback("🔍 Validating cookies...")
            valid_cookies = validate_all_cookies(verbose=False)

            if not valid_cookies:
                self.last_error = "No valid cookies"
                send_callback("❌ No valid cookies!")
                return

            with self.lock:
                running_names = {name for _, name, _ in self.active_clients}
                current_count = len(self.active_clients)

            available = [(c, u, i)
                         for c, u, i in valid_cookies if u not in running_names]

            max_to_use = max_bots if max_bots else config_max
            max_new = max(0, max_to_use - current_count)
            cookies = available[:max_new]

            if not cookies:
                send_callback("ℹ️ No new accounts to launch")
                return

            self.total_to_launch = len(cookies)
            send_callback(f"🚀 Launching {len(cookies)} accounts...")

            for i, (cookie, username, user_id) in enumerate(cookies):
                if self.stop_requested:
                    send_callback(f"⚠️ Cancelled after {i}")
                    break

                try:
                    send_callback(f"🔄 [{i+1}/{len(cookies)}] {username}...")

                    session = Roblox(cookie)
                    session.cookie_tracker = CookieRefreshTracker(cookie)
                    client = session.create_client(
                        place_id, launch_data=launch_data)

                    with self.lock:
                        self.active_clients.append((client, username, session))
                        self.launch_progress = len(self.active_clients)

                    time.sleep(random.randint(20, 30))

                    if low_priority:
                        client.set_low_priority()
                    if minimize_windows:
                        client.minimize()

                    send_callback(
                        f"✅ [{i+1}/{len(cookies)}] {username} joined")

                    if i < len(cookies) - 1 and not self.stop_requested:
                        time.sleep(random.randint(
                            stagger_delay, stagger_delay + 20))

                except Exception as e:
                    self.last_error = f"{username}: {str(e)[:50]}"
                    send_callback(f"⚠️ Failed {username}")

            if not self.stop_requested:
                send_callback(f"🎉 **{len(self.active_clients)} bots active!**")

            self._ensure_afk_running(send_callback)

        except Exception as e:
            self.last_error = str(e)
            if callback:
                try:
                    asyncio.run_coroutine_threadsafe(
                        callback(f"❌ Error: {e}"), bot.loop)
                except Exception:
                    pass
        finally:
            with self.lock:
                self.launching = False

    def _ensure_afk_running(self, send_callback=None):
        with self.lock:
            if self.afk_running:
                return
            self.afk_running = True

        self.afk_thread = Thread(
            target=self._run_anti_afk, args=(send_callback,), daemon=True)
        self.afk_thread.start()

    def _run_anti_afk(self, send_callback=None):
        import time
        import random

        action_count = 0
        last_status = time.time()

        while not self.stop_requested:
            with self.lock:
                clients = self.active_clients[:]

            if not clients:
                break

            disconnected = []
            for client, name, session in clients:
                if self.stop_requested:
                    break
                try:
                    if client.is_window_valid():
                        client.antiafk()
                        action_count += 1
                    else:
                        with self.lock:
                            if (client, name, session) in self.active_clients:
                                self.active_clients.remove(
                                    (client, name, session))
                                disconnected.append(name)
                except Exception:
                    pass

            if disconnected and send_callback:
                send_callback(f"⚠️ Disconnected: {', '.join(disconnected)}")

            if time.time() - last_status > 600:
                with self.lock:
                    count = len(self.active_clients)
                if send_callback and count > 0:
                    send_callback(f"📊 {count} bots active")
                last_status = time.time()

            time.sleep(random.randint(45, 120))

        with self.lock:
            self.afk_running = False

    def stop_bots(self):
        with self.lock:
            if not self.afk_running and not self.active_clients and not self.launching:
                return False, "No bots running"

            self.stop_requested = True
            self.launching = False

            closed = 0
            for client, name, session in self.active_clients:
                try:
                    client.close()
                    closed += 1
                except Exception:
                    pass

            self.active_clients.clear()
            self.launch_progress = 0
            self.start_time = None

        return True, f"Stopped {closed} bots"

    def get_status(self):
        with self.lock:
            uptime = None
            if self.start_time:
                uptime = datetime.now() - self.start_time

            return {
                'running': self.afk_running or len(self.active_clients) > 0,
                'launching': self.launching,
                'count': len(self.active_clients),
                'accounts': [name for _, name, _ in self.active_clients],
                'progress': f"{self.launch_progress}/{self.total_to_launch}" if self.total_to_launch else "0/0",
                'last_error': self.last_error,
                'uptime': str(uptime).split('.')[0] if uptime else None
            }


@bot.event
async def on_ready():
    global bot_controller
    bot_controller = BotController()

    print(f'✅ Bot logged in as {bot.user}')
    print(f'🔒 Owner: {OWNER_USER_ID}')
    print(f'📡 Log Channel: {LOG_CHANNEL_ID}')
    print(f'🔄 Auto-scale: Every {CHECK_INTERVAL_SECONDS}s')

    try:
        synced = await bot.tree.sync()
        print(f'✅ Synced {len(synced)} commands')
    except Exception as e:
        print(f'❌ Sync failed: {e}')

    if not auto_scale_check.is_running():
        auto_scale_check.start()

    await log_to_channel(
        (
            f"**State:** {session_state}\n"
            f"**Auto-scale:** {'ON' if auto_scale_enabled else 'OFF'}\n"
            f"**Check interval:** {CHECK_INTERVAL_SECONDS}s"
        ),
        discord.Color.green(),
        title=f"{EMOJI_CHECK} Session Bot Ready"
    )


@tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
async def auto_scale_check():
    global session_state

    if not auto_scale_enabled:
        return
    if session_state != "SSU":
        return

    count, error = await get_player_count()

    if error:
        print(f"[Auto-scale] API error: {error}")
        return

    if count is None:
        return

    status = bot_controller.get_status()
    current_bots = status['count']

    pending_bots = 0
    if bot_controller.launching:
        pending_bots = bot_controller.total_to_launch - bot_controller.launch_progress
    effective_bots = current_bots + pending_bots

    if count <= MIN_PLAYER_THRESHOLD:

        bots_needed = TARGET_PLAYER_COUNT - count
        bots_to_add = min(bots_needed, 11 - effective_bots)

        if bots_to_add > 0 and not bot_controller.launching:
            await log_to_channel(
                f"{EMOJI_SCALE_UP} **Auto-Scale:** Scaling up +{bots_to_add} (Players: {count} -> {TARGET_PLAYER_COUNT})",
                discord.Color.blue(),
            )

            async def callback(msg):
                await log_to_channel(msg)

            bot_controller.start_bots(
                callback=callback, max_bots=current_bots + bots_to_add)

    elif count > TARGET_PLAYER_COUNT and current_bots > 0:

        excess = count - TARGET_PLAYER_COUNT
        bots_to_remove = min(excess, current_bots)

        if bots_to_remove > 0:
            removed = bot_controller.remove_bots(bots_to_remove)
            await log_to_channel(
                f"{EMOJI_SCALE_DOWN} **Auto-Scale:** Scaling down -{removed} (Players: {count} -> {TARGET_PLAYER_COUNT})",
                discord.Color.blue(),
            )


@auto_scale_check.before_loop
async def before_auto_scale():
    await bot.wait_until_ready()


@bot.event
async def on_message(message):
    global session_state

    if message.author.id != DYNO_BOT_ID:
        return
    if message.channel.id != SERVER_LOGS_CHANNEL_ID:
        return

    for embed in message.embeds:
        if not embed.description:
            continue

        desc = embed.description.lower()
        if "deleted in" not in desc:
            continue

        full_text = str(embed.to_dict()).lower()

        is_ssu = "!ssu" in full_text and "!ssd" not in full_text
        is_ssd = "!ssd" in full_text

        if not is_ssu and not is_ssd:
            continue

        author_match = re.search(r'author[:\s]+(\d{17,19})', full_text)
        author_id = int(author_match.group(1)) if author_match else 0

        if is_ssu and session_state != "SSU":
            session_state = "SSU"
            await log_to_channel(
                f"**State:** SSU Detected\n**Source:** Dyno Log (!ssu)\n**Action:** Starting bots...",
                discord.Color.green(),
                title=f"{EMOJI_CHECK} Session Start"
            )

            async def callback(msg):
                await log_to_channel(msg)
            bot_controller.start_bots(callback=callback)

        elif is_ssd and session_state != "SSD":
            session_state = "SSD"
            success, msg = bot_controller.stop_bots()
            await log_to_channel(
                f"**State:** SSD Detected\n**Source:** Dyno Log (!ssd)\n**Action:** {msg if success else 'No bots running.'}",
                discord.Color.red(),
                title=f"{EMOJI_FALSE} Session End"
            )


async def log_to_channel(message: str, color=discord.Color.blue(), title=None):
    if LOG_CHANNEL_ID:
        channel = bot.get_channel(LOG_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title=title,
                description=message,
                color=color,
                timestamp=datetime.now(),
            )
            try:
                await channel.send(embed=embed)
            except Exception:
                pass


@bot.tree.command(name="start", description="Start Roblox bots")
@is_owner()
@app_commands.describe(count="Number of bots (optional)")
async def start_command(interaction: discord.Interaction, count: int = None):
    global session_state
    await interaction.response.defer()

    if bot_controller and bot_controller.get_status().get('running'):
        await interaction.followup.send("⚠️ Already running!", ephemeral=True)
        return

    session_state = "SSU"
    await interaction.followup.send(f"🚀 Starting... State → SSU")

    await log_to_channel(f"**{interaction.user.display_name}** started bots", discord.Color.green(), title="🚀 Manual Start")

    async def callback(msg):
        await log_to_channel(msg)
    bot_controller.start_bots(callback=callback, max_bots=count)


@bot.tree.command(name="stop", description="Stop all bots")
@is_owner()
async def stop_command(interaction: discord.Interaction):
    global session_state
    session_state = "SSD"
    success, message = bot_controller.stop_bots()

    if success:
        await interaction.response.send_message(f"🛑 {message}. State → SSD")
        await log_to_channel(f"**{interaction.user.display_name}** stopped bots", discord.Color.red(), title="🛑 Manual Stop")
    else:
        await interaction.response.send_message(f"⚠️ {message}", ephemeral=True)


@bot.tree.command(name="status", description="Check status")
@is_owner()
async def status_command(interaction: discord.Interaction):
    status = bot_controller.get_status()
    count, api_error = await get_player_count()

    embed = discord.Embed(
        title="📊 Status",
        color=discord.Color.green(
        ) if status['running'] else discord.Color.greyple(),
        timestamp=datetime.now()
    )

    embed.add_field(name="State", value=f"**{session_state}**", inline=True)
    embed.add_field(
        name="Bots", value=f"{'🟢' if status['running'] else '🔴'} {status['count']}", inline=True)
    embed.add_field(name="Auto-scale",
                    value="✅ ON" if auto_scale_enabled else "❌ OFF", inline=True)

    if count is not None:
        embed.add_field(name="Server Players", value=str(count), inline=True)
    elif api_error:
        embed.add_field(name="Server Players",
                        value=f"⚠️ {api_error}", inline=True)

    if status['uptime']:
        embed.add_field(name="Uptime", value=status['uptime'], inline=True)

    if status['accounts']:
        embed.add_field(name="Accounts", value="\n".join(
            f"`{i}.` {n}" for i, n in enumerate(status['accounts'][:10], 1)), inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="override", description="Override state")
@is_owner()
@app_commands.choices(state=[
    app_commands.Choice(name="SSU - Start bots", value="SSU"),
    app_commands.Choice(name="SSD - Stop bots", value="SSD"),
])
async def override_command(interaction: discord.Interaction, state: str):
    global session_state
    old = session_state
    session_state = state

    if state == "SSU" and not (bot_controller and bot_controller.get_status().get('running')):
        await interaction.response.send_message(f"⚡ Override: {old} → **SSU**. Starting...")
        await log_to_channel(f"**{interaction.user.display_name}** override → SSU", discord.Color.orange(), title="⚡ Override")

        async def callback(msg):
            await log_to_channel(msg)
        bot_controller.start_bots(callback=callback)

    elif state == "SSD":
        success, msg = bot_controller.stop_bots()
        await interaction.response.send_message(f"⚡ Override: {old} → **SSD**. {msg}")
        await log_to_channel(f"**{interaction.user.display_name}** override → SSD\n{msg}", discord.Color.orange(), title="⚡ Override")
    else:
        await interaction.response.send_message(f"State: **{state}** (no change needed)")


@bot.tree.command(name="autoscale", description="Toggle auto-scaling")
@is_owner()
@app_commands.choices(enabled=[
    app_commands.Choice(name="ON", value="on"),
    app_commands.Choice(name="OFF", value="off"),
])
async def autoscale_command(interaction: discord.Interaction, enabled: str):
    global auto_scale_enabled
    auto_scale_enabled = enabled == "on"

    await interaction.response.send_message(f"🔄 Auto-scale: **{'ON' if auto_scale_enabled else 'OFF'}**")
    await log_to_channel(f"**{interaction.user.display_name}** set auto-scale to **{'ON' if auto_scale_enabled else 'OFF'}**", discord.Color.blue())


@bot.tree.command(name="playercount", description="Check server player count")
@is_owner()
async def playercount_command(interaction: discord.Interaction):
    await interaction.response.defer()
    count, error = await get_player_count()

    if error:
        await interaction.followup.send(f"❌ API Error: {error}")
    else:
        status = ""
        if count <= MIN_PLAYER_THRESHOLD:
            status = f" (⚠️ Below {MIN_PLAYER_THRESHOLD} threshold)"
        elif count > TARGET_PLAYER_COUNT:
            status = f" (✅ Above {TARGET_PLAYER_COUNT} target)"
        await interaction.followup.send(f"👥 Server Players: **{count}**{status}")


@bot.tree.command(name="ping", description="Check latency")
@is_owner()
async def ping_command(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 {round(bot.latency * 1000)}ms | State: **{session_state}**")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        pass
    else:
        print(f"Error: {error}")


def signal_handler(signum, frame):
    print("\n⚠️ Shutting down...")
    if bot_controller:
        bot_controller.stop_bots()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


if __name__ == "__main__":
    if not TOKEN:
        print("❌ Missing DISCORD_BOT_TOKEN")
        sys.exit(1)

    print("=" * 50)
    print("🤖 Roblox Bot Controller - Phase 3")
    print("=" * 50)
    print(f"🔒 Owner: {OWNER_USER_ID}")
    print(f"📡 Log: {LOG_CHANNEL_ID}")
    print(f"🔄 Auto-scale: Every {CHECK_INTERVAL_SECONDS}s")
    print(
        f"📊 Thresholds: ≤{MIN_PLAYER_THRESHOLD} add | >{TARGET_PLAYER_COUNT} remove")
    print("=" * 50)

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Invalid token!")
        sys.exit(1)
