
from roblox import Roblox, RobloxClientMutex
from cookie_manager import load_cookies, CookieRefreshTracker
from match_cookies import validate_all_cookies
import time
import random
import json
import signal
import sys
import os


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


mutex = RobloxClientMutex()


with open(os.path.join(SCRIPT_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    config = json.load(f)

active_clients = []


def cleanup_clients(signum=None, frame=None):
    print("\n[INFO] Shutting down all clients...")
    for client in active_clients:
        try:
            client.close()
        except Exception:
            pass
    print("[INFO] All clients closed.")
    sys.exit(0)


signal.signal(signal.SIGINT, cleanup_clients)
signal.signal(signal.SIGTERM, cleanup_clients)


def create_session(cookie):
    try:
        session = Roblox(cookie)

        session.cookie_tracker = CookieRefreshTracker(
            cookie,
            on_refresh_callback=lambda new_cookie: print(
                f"[{session.name}] Cookie refreshed!")
        )
        print(f"[OK] Authenticated as: {session.name}")
        return session
    except Exception as err:
        print(f"[ERROR] Failed to authenticate: {err}")
        return None


def anti_afk_loop(client, account_name, account_index):
    base_min = config.get('anti_afk_min_interval', 45)
    base_max = config.get('anti_afk_max_interval', 180)
    health_check_interval = config.get('health_check_interval', 300)

    account_offset = (account_index * 7) % 30

    account_min = base_min + random.randint(-10, 10)
    account_max = base_max + random.randint(-20, 20)
    account_min = max(30, account_min)
    account_max = max(account_min + 30, account_max)

    print(f"[{account_name}] Anti-AFK started (interval: {account_min}-{account_max}s)")

    time.sleep(account_offset)

    action_count = 0
    last_health_check = time.time()
    consecutive_failures = 0
    consecutive_successes = 0
    current_health_interval = health_check_interval

    while True:
        try:

            if not client.is_window_valid():
                print(f"[{account_name}] Window closed - requesting reconnect")
                return True

            if time.time() - last_health_check > current_health_interval:
                if config.get('enable_health_checks', True):
                    if not client.check_in_game():
                        print(
                            f"[{account_name}] Not in game - requesting reconnect")
                        return True
                    else:
                        consecutive_failures = 0
                        consecutive_successes += 1

                        if consecutive_successes >= 3 and current_health_interval < 600:
                            current_health_interval = min(
                                600, current_health_interval * 2)
                last_health_check = time.time()

            if random.random() < 0.1:
                wait_time = random.randint(account_max, account_max + 120)
            elif random.random() < 0.15:
                wait_time = random.randint(5, 15)
            else:
                wait_time = random.randint(account_min, account_max)

            time.sleep(wait_time)

            success = client.antiafk()
            action_count += 1

            if success:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            print(
                f"[{account_name}] Action #{action_count} {'OK' if success else 'FAIL'}")

        except Exception as err:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                print(f"[{account_name}] Too many failures - requesting reconnect")
                return True
            print(f"[{account_name}] Error: {err}")
            time.sleep(5)

    return False


def run_bot(cookie, place_id, launch_data, account_index):
    auto_reconnect = config.get('auto_reconnect', False)
    max_reconnect_attempts = config.get(
        'max_reconnect_attempts', 5) if auto_reconnect else 1
    reconnect_delay = config.get('reconnect_delay', 60)
    minimize_windows = config.get('minimize_windows', True)
    low_priority = config.get('low_priority_mode', True)

    reconnect_attempts = 0

    while reconnect_attempts < max_reconnect_attempts:
        session = create_session(cookie)
        if not session:
            print(
                f"[Account_{account_index}] Auth failed, waiting before retry...")
            time.sleep(reconnect_delay)
            reconnect_attempts += 1
            continue

        account_name = session.name or f"Account_{account_index}"
        client = None

        try:
            if reconnect_attempts > 0:
                print(
                    f"[{account_name}] Reconnecting (attempt {reconnect_attempts + 1})...")
            else:
                print(f"[{account_name}] Joining game...")

            client = session.create_client(place_id, launch_data=launch_data)
            active_clients.append(client)

            if low_priority:
                client.set_low_priority()

            load_wait = random.randint(20, 35)
            print(f"[{account_name}] Loading ({load_wait}s)...")
            time.sleep(load_wait)

            if minimize_windows:
                client.minimize()
                print(f"[{account_name}] Window minimized for performance")

            needs_reconnect = anti_afk_loop(
                client, account_name, account_index)

            if needs_reconnect:
                print(f"[{account_name}] Disconnect detected, will reconnect...")
                reconnect_attempts += 1

                if client in active_clients:
                    active_clients.remove(client)
                try:
                    client.close()
                except Exception:
                    pass

                wait_time = reconnect_delay + random.randint(0, 30)
                print(f"[{account_name}] Waiting {wait_time}s before reconnect...")
                time.sleep(wait_time)
                continue
            else:
                break

        except Exception as err:
            print(f"[{account_name}] Error: {err}")
            reconnect_attempts += 1
            if reconnect_attempts < max_reconnect_attempts:
                print(f"[{account_name}] Will retry in {reconnect_delay}s...")
                time.sleep(reconnect_delay)
        finally:

            if client and client in active_clients:
                active_clients.remove(client)
                try:
                    client.close()
                except Exception:
                    pass

    if reconnect_attempts >= max_reconnect_attempts:
        print(
            f"[Account_{account_index}] Max reconnect attempts reached, giving up")


def main():
    print('''
╔═══════════════════════════════════════════════════════╗
║           ERLC Anti-AFK Bot                           ║
║   Keeps your accounts active in private servers       ║
║   With automatic cookie refresh & anti-detection      ║
╚═══════════════════════════════════════════════════════╝
''')

    place_id = config.get('erlc_place_id', '2534724415')
    ps_code = config.get('private_server_code', 'calf')
    stagger_delay = config.get('stagger_delay', 45)
    max_accounts = config.get('max_accounts', 15)
    validate_on_start = config.get('validate_cookies', True)

    launch_data = f'{{"psCode":"{ps_code}"}}'

    print(f"[CONFIG] Place ID: {place_id}")
    print(f"[CONFIG] Private Server Code: {ps_code}")
    print(f"[CONFIG] Max accounts: {max_accounts}")
    print(f"[CONFIG] Stagger delay: {stagger_delay}s")

    raw_cookies = load_cookies()

    if not raw_cookies:
        print("\n[ERROR] No cookies found in cookies.txt!")
        print("Add .ROBLOSECURITY cookies (one per line)")
        return

    print(f"\n[INFO] Found {len(raw_cookies)} cookies in file")

    if validate_on_start:
        print("[INFO] Validating cookies before starting...\n")
        valid_cookies = validate_all_cookies(verbose=True)

        if not valid_cookies:
            print("\n[ERROR] No valid cookies found! Please update cookies.txt")
            return

        cookies = [c[0] for c in valid_cookies]
        print(f"\n[INFO] Will use {len(cookies)} valid cookies")
    else:
        cookies = raw_cookies
        print(
            "[INFO] Skipping validation (set 'validate_cookies': true in config to enable)")

    accounts_to_run = cookies[:max_accounts]

    print(f"[INFO] Starting {len(accounts_to_run)} accounts...")
    print("[INFO] Press Ctrl+C to stop all bots\n")

    from threading import Thread

    threads = []
    for i, cookie in enumerate(accounts_to_run):

        if i > 0:
            actual_delay = random.randint(stagger_delay, stagger_delay + 30)
            print(f"[INFO] Waiting {actual_delay}s before next account...")
            time.sleep(actual_delay)

        print(f"[INFO] Launching account {i+1}/{len(accounts_to_run)}...")
        t = Thread(target=run_bot, args=(
            cookie, place_id, launch_data, i+1), daemon=True)
        t.start()
        threads.append(t)

    print(f"\n[INFO] All {len(accounts_to_run)} accounts launched!")
    print("[INFO] Anti-AFK is now running. Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(60)

            print(f"[STATUS] {len(active_clients)} clients active")
    except KeyboardInterrupt:
        cleanup_clients()


if __name__ == "__main__":
    main()
