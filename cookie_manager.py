import os
import time
from threading import Lock

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(SCRIPT_DIR, 'cookies.txt')
COOKIES_BACKUP = os.path.join(SCRIPT_DIR, 'cookies_backup.txt')

file_lock = Lock()


def load_cookies():
    try:
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            cookies = []
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    cookies.append(line)
            return cookies
    except FileNotFoundError:
        return []


def save_cookies(cookies):
    with file_lock:
        if os.path.exists(COOKIES_FILE):
            try:
                import shutil
                shutil.copy2(COOKIES_FILE, COOKIES_BACKUP)
            except Exception:
                pass

        with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
            f.write("# Cookies - One per line\n")
            f.write(f"# Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            for cookie in cookies:
                f.write(f"{cookie}\n")


def update_cookie(old_cookie, new_cookie):
    if old_cookie == new_cookie:
        return False

        cookies = load_cookies()
        updated = False

        for i, cookie in enumerate(cookies):
            if cookie == old_cookie:
                cookies[i] = new_cookie
                updated = True
                break

        if updated:

            if os.path.exists(COOKIES_FILE):
                try:
                    import shutil
                    shutil.copy2(COOKIES_FILE, COOKIES_BACKUP)
                except Exception:
                    pass

            with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                f.write("# Cookies\n")
                f.write(
                    f"# Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                for cookie in cookies:
                    f.write(f"{cookie}\n")

            print(f"[COOKIE] Cookie refreshed and saved")
            return True

    return False


def extract_cookie_from_response(response_headers):
    set_cookie = response_headers.get('set-cookie', '')

    if '.ROBLOSECURITY=' in set_cookie:
        for part in set_cookie.split(';'):
            if '.ROBLOSECURITY=' in part:
                cookie_value = part.split('=', 1)[1].strip()
                if cookie_value and cookie_value != 'deleted':
                    return cookie_value

    return None


class CookieRefreshTracker:

    def __init__(self, original_cookie, on_refresh_callback=None):
        self.original_cookie = original_cookie
        self.current_cookie = original_cookie
        self.refresh_count = 0
        self.last_refresh = None
        self.on_refresh = on_refresh_callback

    def check_and_update(self, response_headers):
        new_cookie = extract_cookie_from_response(response_headers)

        if new_cookie and new_cookie != self.current_cookie:
            old_cookie = self.current_cookie
            self.current_cookie = new_cookie
            self.refresh_count += 1
            self.last_refresh = time.time()

            update_cookie(old_cookie, new_cookie)

            if self.on_refresh:
                self.on_refresh(new_cookie)

            return True

        return False
