
from roblox import Roblox
from cookie_manager import load_cookies, save_cookies
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def validate_cookie(cookie: str):
    try:
        session = Roblox(cookie)
        username = getattr(session, "name", None)
        user_id = getattr(session, "id", None)
        if username and user_id:
            return True, str(username), int(user_id)
        return False, None, None
    except Exception:
        return False, None, None


def validate_all_cookies(verbose: bool = True):
    cookies = load_cookies()
    valid_cookies = []
    invalid_count = 0

    if verbose:
        print(f"\n[VALIDATOR] Checking {len(cookies)} cookies...")

    for i, cookie in enumerate(cookies, 1):
        is_valid, username, user_id = validate_cookie(cookie)

        if is_valid:
            valid_cookies.append((cookie, username, user_id))
            if verbose:
                print(f"  {i}. {username} (ID: {user_id}) - VALID")
        else:
            invalid_count += 1
            if verbose:
                print(f"  {i}. Cookie #{i} - INVALID")

    if verbose:
        print(
            f"\n[VALIDATOR] Result: {len(valid_cookies)} valid, {invalid_count} invalid")

    return valid_cookies


def clean_cookies(verbose: bool = True) -> dict:
    original = load_cookies()
    original_count = len(original)

    valid_triplets = validate_all_cookies(verbose=verbose)

                                                      
    seen = set()
    cleaned = []
    duplicate_count = 0

    for cookie, username, user_id in valid_triplets:
        if cookie in seen:
            duplicate_count += 1
            continue
        seen.add(cookie)
        cleaned.append((cookie, username, user_id))

    cleaned_cookie_strings = [c[0] for c in cleaned]
    save_cookies(cleaned_cookie_strings)

    valid_unique_count = len(cleaned_cookie_strings)
    invalid_count = max(0, original_count - len(valid_triplets))
    removed_total = original_count - valid_unique_count

    if verbose:
        print("\n[CLEANUP] Complete")
        print(f"  Original:      {original_count}")
        print(f"  Invalid removed: {invalid_count}")
        print(f"  Duplicates removed: {duplicate_count}")
        print(f"  Final saved:   {valid_unique_count}")
        print(f"  Total removed: {removed_total}")

    return {
        "original": original_count,
        "invalid_removed": invalid_count,
        "duplicates_removed": duplicate_count,
        "final": valid_unique_count,
        "removed_total": removed_total,
    }


def print_account_table():
    cookies = load_cookies()

    if not cookies:
        print("No cookies found in cookies.txt")
        return

    print("\n" + "=" * 68)
    print(f"{'#':<4} {'Username':<25} {'User ID':<15} {'Status'}")
    print("=" * 68)

    valid_count = 0
    for i, cookie in enumerate(cookies, 1):
        is_valid, username, user_id = validate_cookie(cookie)
        if is_valid:
            print(f"{i:<4} {username:<25} {user_id:<15} VALID")
            valid_count += 1
        else:
            print(f"{i:<4} {'--':<25} {'--':<15} INVALID")

    print("=" * 68)
    print(
        f"\nTotal: {valid_count} valid, {len(cookies) - valid_count} invalid")


if __name__ == "__main__":
    print(
        """
╔═══════════════════════════════════════════════════════╗
║           Cookie Validator                            ║
║   Removes invalid + duplicate cookies                 ║
╚═══════════════════════════════════════════════════════╝
"""
    )

                                                           
    clean_cookies(verbose=True)
