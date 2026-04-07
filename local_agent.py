import asyncio
import aiohttp
import json
import os
import sys
import time
from glob import glob
from datetime import datetime
from threading import Thread, Lock


from dotenv import load_dotenv
load_dotenv()

# Must point to your backend websocket endpoint (Render or equivalent).
RENDER_URL = os.getenv('RENDER_URL', "wss://YOUR_RENDER_URL_HERE/ws")
# Must match backend WEBSOCKET_SECRET exactly.
WEBSOCKET_SECRET = os.getenv('WEBSOCKET_SECRET', "YOUR_SECRET_HERE")
RECONNECT_DELAY = 10

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOTTER_DIR = os.path.join(SCRIPT_DIR, '..', 'robloxgamebotter')
if os.path.exists(BOTTER_DIR):
    sys.path.insert(0, BOTTER_DIR)
else:
    BOTTER_DIR = SCRIPT_DIR
    sys.path.insert(0, SCRIPT_DIR)


class BotController:

    MAX_CONSECUTIVE_FAILURES = 3
    BLACKLIST_DURATION = 3600
    RESTRICTED_PLACE_BLACKLIST_DURATION = 21600
    JOIN_VERIFY_TOTAL_SECONDS = 90
    JOIN_VERIFY_POLL_SECONDS = 5
    JOIN_VERIFY_SUCCESS_SAMPLES = 2
    ANTI_AFK_JOIN_GRACE_SECONDS = 180
    API_FALSE_DISCONNECT_THRESHOLD = 2
    WINDOW_INVALID_DISCONNECT_THRESHOLD = 3
    AFK_FALLBACK_THRESHOLD = 3

    def __init__(self, send_log_callback, send_status_callback, loop):

        from roblox import RobloxClientMutex
        self._mutex = RobloxClientMutex()

        self.launching = False
        self.active_clients = []
        self.stop_requested = False
        self.lock = Lock()
        self._send_log = send_log_callback
        self._send_status = send_status_callback
        self._loop = loop
        self.afk_running = False
        self._threads = []
        self._cookie_failures = {}
        self._cookie_blacklist = {}
        self._account_health = {}

    def _load_runtime_config(self):
        config_path = os.path.join(BOTTER_DIR, 'config.json')
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _apply_bloxstrap_performance_profile(self, config):
        if not bool(config.get("apply_bloxstrap_performance_profile", True)):
            return

        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if not local_appdata:
            return

        target_fps = int(config.get("target_fps", 30))
        target_fps = max(15, min(target_fps, 60))

        bloxstrap_root = os.path.join(local_appdata, "Bloxstrap")
        settings_paths = [
            os.path.join(
                bloxstrap_root,
                "Modifications",
                "ClientSettings",
                "ClientAppSettings.json",
            )
        ]

        for path in glob(os.path.join(bloxstrap_root, "Versions", "*", "ClientSettings", "ClientAppSettings.json")):
            settings_paths.append(path)

        desired_flags = {

            "DFIntTaskSchedulerTargetFps": str(target_fps),

            "FIntDebugGraphicsQualityLevel": "1",
            "FIntDebugGraphicsMSAASamples": "1",

            "FFlagDebugForceFutureLighting": "False",
            "FFlagDebugForceFutureIsBrightPhase2": "False",
        }

        changed_any = False
        for settings_path in settings_paths:
            existing = {}
            try:
                if os.path.exists(settings_path):
                    with open(settings_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict):
                            existing = loaded
            except Exception:
                existing = {}

            changed = False
            for key, value in desired_flags.items():
                if existing.get(key) != value:
                    existing[key] = value
                    changed = True

            if not changed:
                continue

            try:
                os.makedirs(os.path.dirname(settings_path), exist_ok=True)
                with open(settings_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2)
                changed_any = True
            except Exception as e:
                self.send_log(
                    f"⚠️ Failed to apply profile at {settings_path}: {e}")

        if changed_any:
            self.send_log(
                f"⚙️ Applied Bloxstrap performance profile (FPS cap {target_fps})")
        else:
            self.send_log(
                f"⚙️ Bloxstrap performance profile already active (FPS cap {target_fps})")

    @staticmethod
    def _classify_launch_error(error_msg: str) -> str:
        lowered = (error_msg or "").lower()
        if "773" in lowered or "restricted place" in lowered:
            return "restricted_place"
        if "invalid" in lowered and "cookie" in lowered:
            return "invalid_cookie"
        if "timed out" in lowered or "timeout" in lowered:
            return "timeout"
        if "auth" in lowered:
            return "auth"
        if "window" in lowered:
            return "window"
        return "unknown"

    def _mark_account_joined(self, username):
        self._account_health[username] = {
            "joined_at": time.time(),
            "last_action": 0.0,
            "last_api_check": 0.0,
            "api_false_streak": 0,
            "window_invalid_streak": 0,
            "afk_fail_streak": 0,
        }

    def _clear_account_health(self, username):
        if username in self._account_health:
            del self._account_health[username]

    def _verify_client_join(self, client, username):
        start = time.time()
        success_samples = 0
        while (time.time() - start) < self.JOIN_VERIFY_TOTAL_SECONDS:
            if self.stop_requested:
                return False, "cancelled"

            try:
                if not client.is_window_valid():
                    return False, "window_closed_during_join"
            except Exception:
                pass

            try:
                in_game = client.check_in_game(strict=True)
            except Exception:
                in_game = None

            if in_game is True:
                success_samples += 1
                if success_samples >= self.JOIN_VERIFY_SUCCESS_SAMPLES:
                    return True, "joined"
            else:
                success_samples = 0

            time.sleep(self.JOIN_VERIFY_POLL_SECONDS)

        return False, "join_verification_timeout"

    def _cleanup_dead_threads(self):
        with self.lock:
            alive = []
            for t in self._threads:
                if t.is_alive():
                    alive.append(t)
                else:

                    t.join(timeout=0.1)
            self._threads = alive

    def send_log(self, message):
        print(f"[LOG] {message}")
        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self._send_log(message), loop=self._loop)
            )
        except Exception as e:
            print(f"[LOG ERROR] Failed to send: {e}")

    def push_status(self):
        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self._send_status(), loop=self._loop)
            )
        except Exception:
            pass

    def start_bots(self, max_bots=None):
        with self.lock:
            if self.launching:
                return False
            self.launching = True
            self.stop_requested = False

        self._cleanup_dead_threads()

        thread = Thread(target=self._run_bots, args=(max_bots,), daemon=True)
        thread.start()
        with self.lock:
            self._threads.append(thread)
        return True

    def _run_bots(self, max_bots=None):
        try:
            from roblox import Roblox
            from cookie_manager import CookieRefreshTracker
            from match_cookies import validate_all_cookies
            import random

            config = self._load_runtime_config()
            self._apply_bloxstrap_performance_profile(config)

            place_id = config.get('erlc_place_id', '2534724415')
            ps_code = config.get('private_server_code', 'calf')
            stagger_delay = config.get('stagger_delay', 45)
            config_max = config.get('max_accounts', 15)
            minimize_windows = config.get('minimize_windows', True)
            low_priority = config.get('low_priority_mode', True)

            self.JOIN_VERIFY_TOTAL_SECONDS = int(config.get(
                'join_verify_total_seconds', self.JOIN_VERIFY_TOTAL_SECONDS))
            self.JOIN_VERIFY_POLL_SECONDS = int(config.get(
                'join_verify_poll_seconds', self.JOIN_VERIFY_POLL_SECONDS))
            self.JOIN_VERIFY_SUCCESS_SAMPLES = int(config.get(
                'join_verify_success_samples', self.JOIN_VERIFY_SUCCESS_SAMPLES))

            launch_data = f'{{"psCode":"{ps_code}"}}'

            self.send_log("🔍 Validating cookies...")
            valid_cookies = validate_all_cookies(verbose=False)

            if not valid_cookies:
                self.send_log(
                    "[ERROR] ❌ No valid cookies found! Cannot launch bots.")
                return

            self.send_log(f"✅ Found {len(valid_cookies)} valid cookies")

            with self.lock:
                running_names = {name for _, name, _ in self.active_clients}
                current_count = len(self.active_clients)

            available = [(c, u, i)
                         for c, u, i in valid_cookies if u not in running_names]

            import time as time_module
            current_time = time_module.time()
            non_blacklisted = []
            for c, u, i in available:
                blacklist_until = self._cookie_blacklist.get(u, 0)
                if current_time >= blacklist_until:
                    non_blacklisted.append((c, u, i))
                else:
                    remaining = int(blacklist_until - current_time)
                    self.send_log(f"⏸️ {u} blacklisted for {remaining}s more")
            available = non_blacklisted

            max_to_use = max_bots if max_bots else config_max
            max_new = max(0, max_to_use - current_count)
            cookies = available[:max_new]

            if not cookies:
                self.send_log("ℹ️ No new accounts to launch")
                return

            count_word = 'account' if len(cookies) == 1 else 'accounts'
            self.send_log(f"🚀 Launching {len(cookies)} {count_word}...")

            self.push_status()

            BATCH_SIZE = max(1, int(config.get('launch_batch_size', 2)))

            def launch_single_bot(cookie, username, user_id, batch_offset):

                jitter_delay = batch_offset * random.randint(8, 15)
                if jitter_delay > 0:
                    time.sleep(jitter_delay)

                if self.stop_requested:
                    return None, username, "cancelled"

                try:
                    self.send_log(f"🔄 Launching {username}...")

                    session = Roblox(cookie)
                    session.cookie_tracker = CookieRefreshTracker(cookie)
                    client = session.create_client(
                        place_id, launch_data=launch_data)

                    time.sleep(random.randint(8, 14))

                    if low_priority:
                        client.set_low_priority()

                    joined, join_reason = self._verify_client_join(
                        client, username)
                    if not joined:
                        try:
                            client.close()
                        except Exception:
                            pass

                        if join_reason == "join_verification_timeout":
                            return None, username, "join verification timed out (possible restricted place / 773)"
                        return None, username, f"launch aborted: {join_reason}"

                    if minimize_windows:
                        client.minimize()

                    return client, username, session

                except Exception as e:
                    return None, username, str(e)[:60]

            from concurrent.futures import ThreadPoolExecutor, as_completed

            for batch_start in range(0, len(cookies), BATCH_SIZE):
                if self.stop_requested:
                    self.send_log(f"⚠️ Cancelled")
                    break

                batch = cookies[batch_start:batch_start + BATCH_SIZE]
                batch_num = (batch_start // BATCH_SIZE) + 1
                total_batches = (len(cookies) + BATCH_SIZE - 1) // BATCH_SIZE
                self.send_log(
                    f"📦 Batch {batch_num}/{total_batches}: {len(batch)} accounts")

                with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                    futures = {}
                    for offset, (cookie, username, user_id) in enumerate(batch):
                        future = executor.submit(
                            launch_single_bot, cookie, username, user_id, offset)
                        futures[future] = username

                    for future in as_completed(futures):
                        username = futures[future]
                        result = future.result()

                        if result[2] == "cancelled":
                            continue
                        elif isinstance(result[2], str) and result[0] is None:

                            error_msg = result[2]
                            reason_class = self._classify_launch_error(
                                error_msg)
                            self._cookie_failures[username] = self._cookie_failures.get(
                                username, 0) + 1
                            failures = self._cookie_failures[username]
                            self.send_log(
                                f"⚠️ Failed {username} ({reason_class}, {failures}/{self.MAX_CONSECUTIVE_FAILURES}): {error_msg}")

                            if reason_class == "restricted_place":
                                self._cookie_blacklist[username] = time.time(
                                ) + self.RESTRICTED_PLACE_BLACKLIST_DURATION
                                self.send_log(
                                    f"🚫 {username} blacklisted for {self.RESTRICTED_PLACE_BLACKLIST_DURATION}s (restricted place)")
                            elif failures >= self.MAX_CONSECUTIVE_FAILURES:
                                self._cookie_blacklist[username] = time.time(
                                ) + self.BLACKLIST_DURATION
                                self.send_log(
                                    f"🚫 {username} blacklisted for {self.BLACKLIST_DURATION}s")
                        else:

                            client, username, session = result
                            with self.lock:
                                self.active_clients.append(
                                    (client, username, session))
                            self._mark_account_joined(username)
                            self._cookie_failures[username] = 0
                            self.send_log(f"✅ {username} joined!")

                if batch_start + BATCH_SIZE < len(cookies) and not self.stop_requested:
                    batch_delay = random.randint(
                        stagger_delay, stagger_delay + 15)
                    self.send_log(
                        f"⏳ Waiting {batch_delay}s before next batch...")
                    time.sleep(batch_delay)

            with self.lock:
                total = len(self.active_clients)

            if not self.stop_requested:
                self.send_log(f"🎉 **{total} bots now active!**")

            self._ensure_afk_running()

        except Exception as e:
            self.send_log(f"❌ Error: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            with self.lock:
                self.launching = False

            self.push_status()

    def _ensure_afk_running(self):
        with self.lock:
            if self.afk_running:
                return
            self.afk_running = True

        self._cleanup_dead_threads()

        thread = Thread(target=self._run_anti_afk, daemon=True)
        thread.start()
        with self.lock:
            self._threads.append(thread)

    def _check_disconnect_reason(self, client, name, session):
        reasons = []

        window_valid = False
        try:
            window_valid = client.is_window_valid()
        except:
            pass

        if not window_valid:
            reasons.append("WINDOW_CRASHED")

        process_alive = False
        try:
            if hasattr(client, '_roblox_pid'):
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                STILL_ACTIVE = 259
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, client._roblox_pid
                )
                if handle:
                    exit_code = ctypes.c_ulong()
                    ctypes.windll.kernel32.GetExitCodeProcess(
                        handle, ctypes.byref(exit_code))
                    ctypes.windll.kernel32.CloseHandle(handle)
                    process_alive = exit_code.value == STILL_ACTIVE
        except:
            pass

        if not process_alive:
            reasons.append("PROCESS_DIED")

        in_game = None
        try:
            in_game = client.check_in_game(strict=True)
            if in_game is False:
                reasons.append("KICKED_BY_SERVER")
            elif in_game is None:
                reasons.append("API_UNAVAILABLE")
        except Exception as e:
            reasons.append(f"API_ERROR: {str(e)[:30]}")

        if "KICKED_BY_SERVER" in reasons:
            return "🚫 KICKED BY SERVER (anti-cheat or manual kick)"
        elif "PROCESS_DIED" in reasons:
            return "💥 ROBLOX CRASHED (process terminated)"
        elif "WINDOW_CRASHED" in reasons:
            return "🪟 WINDOW CLOSED (may have crashed)"
        elif in_game:
            return "❓ UNKNOWN (in-game but window invalid)"
        else:
            return f"❓ UNKNOWN: {', '.join(reasons)}"

    def _run_anti_afk(self):
        import random

        config = self._load_runtime_config()
        base_min = int(config.get("anti_afk_min_interval", 60))
        base_max = int(config.get("anti_afk_max_interval", 180))
        if base_max <= base_min:
            base_max = base_min + 10
        health_check_interval = max(
            30, int(config.get("health_check_interval", 120)))
        minimize_windows = bool(config.get("minimize_windows", True))
        join_grace_seconds = max(60, int(config.get(
            "anti_afk_join_grace_seconds", self.ANTI_AFK_JOIN_GRACE_SECONDS)))
        api_false_threshold = max(2, int(config.get(
            "anti_afk_api_false_threshold", self.API_FALSE_DISCONNECT_THRESHOLD)))
        window_invalid_threshold = max(2, int(config.get(
            "anti_afk_window_invalid_threshold", self.WINDOW_INVALID_DISCONNECT_THRESHOLD)))
        afk_fallback_threshold = max(
            2, int(config.get("anti_afk_fallback_threshold", self.AFK_FALLBACK_THRESHOLD)))

        bot_timing_profiles = {}
        last_health_log_at = 0.0

        print("[Anti-AFK] Started - with in-game disconnect detection")

        def remove_disconnected(client, name, session, fallback_reason: str):
            detailed_reason = fallback_reason
            try:
                detailed_reason = self._check_disconnect_reason(
                    client, name, session)
            except Exception:
                detailed_reason = fallback_reason

            with self.lock:
                if (client, name, session) in self.active_clients:
                    self.active_clients.remove((client, name, session))

            try:
                client.close()
            except Exception:
                pass

            self._clear_account_health(name)
            if name in bot_timing_profiles:
                del bot_timing_profiles[name]
            return f"{name} ({detailed_reason})"

        while not self.stop_requested:
            with self.lock:
                clients = self.active_clients[:]

            if not clients:
                print("[Anti-AFK] No clients, stopping loop")
                break

            disconnected = []
            current_time = time.time()

            for client, name, session in clients:
                if self.stop_requested:
                    break

                if name not in bot_timing_profiles:
                    bot_timing_profiles[name] = {
                        'base_delay': random.randint(base_min, base_max),
                        'last_action': 0.0,
                    }

                health = self._account_health.setdefault(name, {
                    "joined_at": current_time,
                    "last_action": 0.0,
                    "last_api_check": 0.0,
                    "api_false_streak": 0,
                    "window_invalid_streak": 0,
                    "afk_fail_streak": 0,
                })

                profile = bot_timing_profiles[name]

                try:

                    if not client.is_window_valid():
                        health["window_invalid_streak"] += 1
                        print(
                            f"[{name}] Window invalid check #{health['window_invalid_streak']}")
                        if health["window_invalid_streak"] >= window_invalid_threshold:
                            disconnected.append(remove_disconnected(
                                client, name, session, "window invalid"))
                        continue

                    health["window_invalid_streak"] = 0

                    needs_api_check = (
                        current_time - health["last_api_check"]) >= health_check_interval
                    within_join_grace = (
                        current_time - health["joined_at"]) < join_grace_seconds

                    if needs_api_check:
                        health["last_api_check"] = current_time
                        if not within_join_grace:
                            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
                            in_game = None
                            with ThreadPoolExecutor(max_workers=1) as executor:
                                future = executor.submit(
                                    client.check_in_game, True)
                                try:
                                    in_game = future.result(timeout=5)
                                except FuturesTimeoutError:
                                    print(
                                        f"[{name}] API check timeout - skipping")

                            if in_game is False:
                                health["api_false_streak"] += 1
                                print(
                                    f"[{name}] Not in game streak #{health['api_false_streak']}")
                                if health["api_false_streak"] >= api_false_threshold:
                                    disconnected.append(remove_disconnected(
                                        client, name, session, "not in game"))
                                    continue
                            elif in_game is True:
                                health["api_false_streak"] = 0

                    next_action_time = profile['last_action'] + \
                        profile['base_delay'] + random.randint(-10, 20)
                    if current_time < next_action_time:
                        continue

                    success = client.antiafk()
                    if success:
                        health["afk_fail_streak"] = 0
                        profile['last_action'] = current_time
                        health["last_action"] = current_time
                    else:
                        health["afk_fail_streak"] += 1
                        if health["afk_fail_streak"] >= afk_fallback_threshold:
                            fallback_ok = False
                            try:
                                fallback_ok = client.antiafk_focused()
                            except Exception:
                                fallback_ok = False

                            if fallback_ok:
                                if minimize_windows:
                                    try:
                                        client.minimize()
                                    except Exception:
                                        pass
                                health["afk_fail_streak"] = 0
                                profile['last_action'] = current_time
                                health["last_action"] = current_time
                                self.send_log(
                                    f"🔧 {name} anti-AFK fallback applied")
                            else:
                                health["afk_fail_streak"] = afk_fallback_threshold
                                print(f"[{name}] Anti-AFK fallback failed")

                except Exception as e:
                    print(f"[{name}] Anti-AFK error: {e}")

            if disconnected:
                self.send_log(f"⚠️ Disconnected: {', '.join(disconnected)}")
            else:
                with self.lock:
                    active_count = len(self.active_clients)
                if active_count > 0 and (current_time - last_health_log_at) >= 180:
                    self.send_log(
                        f"✅ Anti-AFK health: {active_count} bot{'s' if active_count != 1 else ''} active")
                    last_health_log_at = current_time

            time.sleep(5)

        with self.lock:
            self.afk_running = False
        print("[Anti-AFK] Loop ended")

    def remove_bots(self, count):
        with self.lock:
            removed = 0
            removed_names = []
            for _ in range(min(count, len(self.active_clients))):
                if self.active_clients:
                    client, name, _ = self.active_clients.pop()
                    try:
                        client.close()
                        removed += 1
                        removed_names.append(name)
                    except Exception:
                        pass
            for name in removed_names:
                self._clear_account_health(name)
            if removed_names:
                self.send_log(f"🔻 Removed: {', '.join(removed_names)}")
            self.push_status()
            return removed

    def stop_bots(self):
        with self.lock:
            self.stop_requested = True
            self.launching = False
            closed = 0
            names = []
            for client, name, _ in self.active_clients:
                names.append(name)
                try:
                    client.close()
                    closed += 1
                except Exception:
                    pass
            self.active_clients.clear()
            self._account_health.clear()
            if names:
                self.send_log(f"🛑 Stopped: {', '.join(names)}")
            self.push_status()
            return closed

    def get_status(self):
        with self.lock:
            return {
                "running": self.afk_running or len(self.active_clients) > 0,
                "launching": self.launching,
                "count": len(self.active_clients),
                "accounts": [name for _, name, _ in self.active_clients]
            }


class LocalAgent:

    HEARTBEAT_TIMEOUT = 60
    CMD_ID_TTL = 300

    def __init__(self):
        self.ws = None
        self.connected = False
        self.controller = None
        self.loop = None
        self.last_ping_time = 0
        self.processed_cmd_ids = {}

    async def send_log(self, message):
        if self.ws and self.connected:
            try:
                await self.ws.send_json({"type": "log", "message": message})
            except Exception as e:
                print(f"[WS] Failed to send log: {e}")

    async def send_status(self):
        if self.ws and self.connected and self.controller:
            try:
                await self.ws.send_json({
                    "type": "status_update",
                    "status": self.controller.get_status()
                })
            except Exception:
                pass

    async def status_updater(self):
        while True:
            await asyncio.sleep(10)
            if self.connected:
                await self.send_status()

    async def heartbeat_watchdog(self):
        import time
        while self.connected:
            await asyncio.sleep(10)
            if self.last_ping_time > 0:
                time_since_ping = time.time() - self.last_ping_time
                if time_since_ping > self.HEARTBEAT_TIMEOUT:
                    print(
                        f"[WS] No ping for {time_since_ping:.0f}s - forcing reconnect")
                    if self.ws:
                        await self.ws.close()
                    break

    async def handle_command(self, data):
        import time
        cmd = data.get('command')
        cmd_data = data.get('data', {})
        cmd_id = data.get('cmd_id')

        if cmd_id:
            current_time = time.time()

            self.processed_cmd_ids = {
                k: v for k, v in self.processed_cmd_ids.items()
                if current_time - v < self.CMD_ID_TTL
            }

            if cmd_id in self.processed_cmd_ids:
                print(f"[CMD] Duplicate command {cmd_id} ignored")
                return
            self.processed_cmd_ids[cmd_id] = current_time

        print(f"[CMD] Received: {cmd} with data: {cmd_data}")

        if cmd == 'start_bots':
            count = cmd_data.get('count')
            if self.controller.start_bots(max_bots=count):
                await self.send_log("🚀 Starting bots...")

        elif cmd == 'stop_bots':
            closed = self.controller.stop_bots()
            await self.send_log(f"🛑 Stopped {closed} bots")

        elif cmd == 'remove_bots':
            count = cmd_data.get('count', 1)
            removed = self.controller.remove_bots(count)
            await self.send_log(f"🔻 Removed {removed} bots")

        await self.send_status()

    async def connect(self):
        import time
        self.loop = asyncio.get_running_loop()
        self.controller = BotController(
            self.send_log, self.send_status, self.loop)

        asyncio.create_task(self.status_updater())

        while True:
            watchdog_task = None
            try:
                print(f"🔄 Connecting to {RENDER_URL}...")
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(RENDER_URL, heartbeat=30) as ws:
                        self.ws = ws

                        await ws.send_json({"secret": WEBSOCKET_SECRET})

                        self.connected = True
                        self.last_ping_time = time.time()
                        print("✅ Connected to Render!")
                        await self.send_log("🖥️ **PC Agent Connected**")
                        await self.send_status()

                        watchdog_task = asyncio.create_task(
                            self.heartbeat_watchdog())

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)

                                    if data.get('type') == 'ping':
                                        self.last_ping_time = time.time()
                                        await ws.send_json({"type": "pong", "timestamp": data.get('timestamp')})
                                    else:
                                        await self.handle_command(data)
                                except json.JSONDecodeError:
                                    pass
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                print(f"[WS] Error: {ws.exception()}")
                                break

            except aiohttp.ClientError as e:
                print(f"❌ Connection error: {e}")
            except Exception as e:
                print(f"❌ Error: {e}")
                import traceback
                traceback.print_exc()
            finally:

                if watchdog_task:
                    watchdog_task.cancel()
                    try:
                        await watchdog_task
                    except asyncio.CancelledError:
                        pass

            self.connected = False
            self.ws = None
            self.last_ping_time = 0
            print(f"🔄 Reconnecting in {RECONNECT_DELAY}s...")
            await asyncio.sleep(RECONNECT_DELAY)


async def main():
    print("=" * 60)
    print("🖥️ California Session Management - Local Agent")
    print("=" * 60)
    print(f"Connecting to: {RENDER_URL}")
    print("=" * 60)
    print("")

    agent = LocalAgent()

    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        print("\n⚠️ Shutdown signal received...")
        shutdown_event.set()

    import signal
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    async def run_with_shutdown():
        connect_task = asyncio.create_task(agent.connect())
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, pending = await asyncio.wait(
            [connect_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        if shutdown_event.is_set():
            print("🛑 Initiating graceful shutdown...")

            if agent.connected and agent.ws:
                try:
                    await agent.ws.send_json({"type": "log", "message": "🛑 **PC Agent shutting down gracefully**"})
                except Exception:
                    pass

            if agent.controller:
                print("🛑 Stopping all bots...")
                closed = agent.controller.stop_bots()
                print(f"✅ Stopped {closed} bots")

            if agent.ws:
                try:
                    await agent.ws.close()
                except Exception:
                    pass

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            print("✅ Shutdown complete")

    await run_with_shutdown()


if __name__ == "__main__":
    asyncio.run(main())
