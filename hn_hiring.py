#!/usr/bin/env python3
"""
hn_hiring.py — fetch recent job postings from the HN "Who is hiring?" thread.

Two ways to use it:

  1. On demand (interactive or with --days):
        python3 hn_hiring.py                 # prompts: "How many days back?"
        python3 hn_hiring.py --days 2         # last 2 days, printed to stdout

  2. As a daily cron job that pops a macOS notification for *new* postings:
        python3 hn_hiring.py --days 1 --new-only --notify --quiet

How the thread is resolved:
  It follows the HN user `whoishiring` — reads their submission list and picks
  their newest "Who is hiring?" thread. When they post the next month's thread,
  this script picks it up automatically on the next run. Override the account
  with --user, or pin a specific thread with --item ID.

Delivery options:
  (default)     print to stdout
  --out FILE    also write the full report to FILE
  --notify      pop a macOS desktop notification (summary only) via osascript

Dedup:
  --new-only    only show postings not seen on a previous run. Seen comment IDs
                are stored in --state (default: ~/.hn_hiring_seen.json).
                On a daily cron, this means you only hear about genuinely new posts.

Filtering:
  --visa        exclude postings that explicitly refuse visa sponsorship
                (citizens-only, "no visa sponsorship", etc.). Off by default.

Schedule:
  --install [HH:MM]
                create + register the daily LaunchAgent (default 08:00) and exit.
                Set LAUNCHD_LABEL below first.
  --uninstall   stop and remove the LaunchAgent, then exit.
  --set-schedule HH:MM
                change the daily run time of the LaunchAgent and exit
                (e.g. --set-schedule 07:30). Does not fetch postings.

No third-party packages required — standard library only.
"""

import argparse
import html
import json
import os
import plistlib
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

# --------------------------------------------------------------------------- #
# USER CONFIG — set this before using --set-schedule
# --------------------------------------------------------------------------- #
# Reverse-DNS label of your macOS LaunchAgent. It must match BOTH the plist's
# filename and its <Label> value, i.e. the file must live at
#   ~/Library/LaunchAgents/<LAUNCHD_LABEL>.plist
# Only --set-schedule uses this; the rest of the script works without it.
LAUNCHD_LABEL = "com.example.hnhiring"

HN_USER = "https://hacker-news.firebaseio.com/v0/user/{id}.json"
HN_ITEM_FB = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
ALGOLIA_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
# Output lives next to this script (i.e. in the project folder), so it travels
# with the repo instead of scattering files into the home directory.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE = os.path.join(SCRIPT_DIR, ".hn_hiring_seen.json")
DEFAULT_REPORT = os.path.join(SCRIPT_DIR, "hn_hiring_latest.txt")
# The LaunchAgent that runs this script on a daily schedule; --set-schedule
# rewrites its run time in place. Derived from LAUNCHD_LABEL above.
PLIST_PATH = os.path.expanduser(
    f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist"
)


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hn-hiring-script/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def find_latest_thread(user="whoishiring", scan=30):
    """Follow `user`: read their submissions, return their newest 'Who is hiring?' thread.

    The submission list is newest-first, so the current month's thread is near
    the top. Scanning the first `scan` items covers roughly a year of activity.
    """
    data = _get_json(HN_USER.format(id=user))
    if not data:
        raise RuntimeError(f"HN user '{user}' not found.")
    submitted = data.get("submitted", [])
    for item_id in submitted[:scan]:
        item = _get_json(HN_ITEM_FB.format(id=item_id))
        if not item:
            continue
        title = item.get("title") or ""
        if "who is hiring" in title.lower():
            return str(item_id), title
    raise RuntimeError(
        f"No 'Who is hiring?' thread found in {user}'s last {scan} submissions."
    )


def fetch_thread(item_id):
    # Algolia returns the full comment tree in one request (Firebase would need
    # one request per comment), so use it to pull the postings.
    return _get_json(ALGOLIA_ITEM.format(id=item_id))


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def clean_html(text):
    if not text:
        return ""
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def first_line(text):
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return "(no text)"


def collect_recent_posts(thread, days):
    """Return top-level comments created within the last `days` days, newest last."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    posts = []
    for child in thread.get("children", []):
        ts = child.get("created_at_i")
        text = child.get("text")
        if ts is None or not text:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if dt >= cutoff:
            posts.append({
                "id": child.get("id"),
                "author": child.get("author"),
                "dt": dt,
                "text": clean_html(text),
            })
    posts.sort(key=lambda p: p["dt"])
    return posts


# --------------------------------------------------------------------------- #
# Location filtering (heuristic)
# --------------------------------------------------------------------------- #
# These are matched anywhere in the post (unambiguous location signals).
GLOBAL_TERMS = ["global", "worldwide", "anywhere"]
# Non-US regions/countries/cities. A REMOTE role scoped to one of these (and not
# also US/global) is excluded — e.g. "Remote (EU)" out, "Remote Global" in.
NON_US_REGIONS = [
    "eu", "e.u.", "europe", "european", "emea", "uk", "u.k.", "united kingdom",
    "britain", "london", "apac", "latam", "latin america", "asia",
    "asia-pacific", "africa", "india", "canada", "canadian", "australia",
    "new zealand", "germany", "berlin", "munich", "france", "paris",
    "netherlands", "amsterdam", "spain", "madrid", "barcelona", "portugal",
    "lisbon", "poland", "ireland", "dublin", "switzerland", "sweden",
    "singapore", "japan", "tokyo", "brazil", "argentina", "dubai", "uae",
    "israel", "tel aviv",
]
US_CITIES = [
    "san francisco", "sf bay", "bay area", "new york", "nyc", "brooklyn",
    "los angeles", "seattle", "boston", "cambridge", "austin", "chicago",
    "denver", "boulder", "portland", "atlanta", "palo alto", "mountain view",
    "menlo park", "sunnyvale", "san jose", "santa clara", "miami", "dallas",
    "houston", "philadelphia", "san diego", "chattanooga", "pittsburgh",
    "nashville", "raleigh", "durham", "minneapolis", "salt lake city",
    "phoenix", "san antonio", "detroit", "charlotte", "columbus",
    "kansas city", "st. louis", "st louis", "tampa", "orlando", "las vegas",
    "sacramento", "oakland", "irvine", "reston", "arlington",
]
# State abbreviations are matched ONLY in the first two lines and case-sensitively,
# so prose words like "or"/"in"/"focus" don't false-positive as OR/IN/etc.
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
_STATE_RE = re.compile(r"\b(" + "|".join(sorted(US_STATES)) + r")\b")


def has_remote(text):
    """Does the posting mention remote work at all?"""
    return re.search(r"\bremote\b", text, re.I) is not None


def is_global(text):
    """Global / worldwide / anywhere remote."""
    low = text.lower()
    return any(re.search(r"\b" + re.escape(t) + r"\b", low) for t in GLOBAL_TERMS)


def mentions_non_us_region(text):
    """Does the posting name a specific non-US region/country/city?"""
    low = text.lower()
    return any(re.search(r"\b" + re.escape(t) + r"\b", low) for t in NON_US_REGIONS)


def is_remote(text):
    """For sectioning the report: remote or global counts as a remote role."""
    return has_remote(text) or is_global(text)


def is_us(text):
    """Heuristic: is the posting US-based (city, state, or USA)?"""
    low = text.lower()
    for term in US_CITIES + ["usa", "united states"]:
        if re.search(r"\b" + re.escape(term) + r"\b", low):
            return True
    # Uppercase US / USA / U.S. / U.S.A. — case-sensitive so we don't match "us"
    if re.search(r"\b(US|USA)\b", text) or re.search(r"U\.S\.(A\.)?", text):
        return True
    # "City, ST" — state abbreviations only in the first two lines
    head = " ".join([ln.strip() for ln in text.splitlines() if ln.strip()][:2])
    return bool(_STATE_RE.search(head))


def location_ok(text):
    """Include the posting if it's US-based, global-remote, or unscoped remote.

    Excluded: a remote role scoped to a specific non-US region (e.g. "Remote
    (EU)") with no US or global signal. A bare "REMOTE" with no region is kept.
    """
    if is_us(text) or is_global(text):
        return True
    if has_remote(text):
        return not mentions_non_us_region(text)
    return False


# --------------------------------------------------------------------------- #
# Role filtering (heuristic)
# --------------------------------------------------------------------------- #
# Include a posting only if it mentions an engineer role...
ENGINEER_RE = re.compile(r"\b(engineers?|developers?|swe|programmers?)\b", re.I)
# ...and exclude senior levels I'm not targeting. This matches a level word
# ADJACENT to engineer/developer (e.g. "Staff Software Engineer", "Principal
# Backend Developer") so a stray "our staff" or "principal investigator" doesn't
# wrongly exclude a junior posting.
SENIOR_ENG_RE = re.compile(
    r"\b(principal|staff|distinguished|fellow)\b[\w /,+&.-]{0,30}?\b(engineers?|developers?)\b",
    re.I,
)


def role_ok(text):
    """True if the posting is an engineer role that isn't principal/staff level."""
    if not ENGINEER_RE.search(text):
        return False
    if SENIOR_ENG_RE.search(text):
        return False
    return True


# --------------------------------------------------------------------------- #
# Visa filtering (heuristic, opt-in via --visa)
# --------------------------------------------------------------------------- #
# Postings phrase visa sponsorship inconsistently. Rather than require an
# explicit "yes" (which would drop the many postings that stay silent on visas),
# --visa EXCLUDES only postings that clearly say they will NOT sponsor. Patterns
# below are drawn from real July-2026 thread wording. This is a heuristic: it can
# miss unusual phrasings and, rarely, over-match. The negation patterns anchor on
# "sponsor"/"visa" within a short window so unrelated "no"/"not" clauses don't
# trip them.
_NO_VISA_PATTERNS = [
    # "No visa sponsorship", "no visa relocation sponsorship", typo "no visa sposorship"
    r"\bno\b[\w ,/&'.-]{0,25}\bvisas?\b",
    # "no sponsorship"
    r"\bno\b[\w ,/&'.-]{0,15}\bsponsor\w*\b",
    # "not able to sponsor", "unable to do visa sponsorships", "cannot provide visa
    # sponsorship", "can't offer relocation or visa sponsorship", "don't sponsor visas"
    r"\b(?:not able|unable|cannot|can'?t|won'?t|do(?:es)?\s*n'?t|do not|does not)\b"
    r"[\w ,/&'.-]{0,45}\bsponsor\w*\b",
    # "citizens (and legal residents) only"
    r"\bcitizens?\b[\w ,/&'.-]{0,25}\bonly\b",
    # "citizenship required", "work authorization required"
    r"\b(?:citizenship|work authoriz\w*)\b[\w ,/&'.-]{0,20}\brequired\b",
    # "US citizens" / "U.S. citizen" stated as a field or requirement
    r"\bu\.?\s?s\.?a?\.?\s+citizens?\b",
    # "U.S. person status" (common on gov/defense/ITAR roles)
    r"\bu\.?\s?s\.?\s+persons?\b",
]
_NO_VISA_RE = [re.compile(p, re.I) for p in _NO_VISA_PATTERNS]


def refuses_visa_sponsorship(text):
    """True if the posting explicitly states it will NOT sponsor a visa."""
    return any(r.search(text) for r in _NO_VISA_RE)


# --------------------------------------------------------------------------- #
# Dedup state
# --------------------------------------------------------------------------- #
def load_seen(path):
    try:
        with open(path) as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def save_seen(path, seen):
    with open(path, "w") as f:
        json.dump(sorted(seen), f)


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def _render_post(p):
    stamp = p["dt"].strftime("%Y-%m-%d %H:%M UTC")
    return [
        f"\n### {stamp} — {p['author']}",
        f"https://news.ycombinator.com/item?id={p['id']}",
        "",
        p["text"],
        "-" * 78,
    ]


def format_report(title, posts, days, exclusion_note=None):
    # Split into Remote vs Non-remote (US onsite); a Remote+US post counts as Remote.
    remote = sorted((p for p in posts if is_remote(p["text"])),
                    key=lambda p: p["dt"], reverse=True)
    onsite = sorted((p for p in posts if not is_remote(p["text"])),
                    key=lambda p: p["dt"], reverse=True)

    lines = [title, f"Postings from the last {days} day(s): {len(posts)} shown"]
    if exclusion_note:
        lines.append(exclusion_note)

    for name, group in (("REMOTE ROLES", remote), ("NON-REMOTE (US ONSITE) ROLES", onsite)):
        lines.append("\n" + "=" * 78)
        lines.append(f"{name} ({len(group)})")
        lines.append("=" * 78)
        if not group:
            lines.append("(none)")
        for p in group:
            lines.extend(_render_post(p))

    if not posts:
        lines.append("\n(No new postings in this window.)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# macOS desktop notification
# --------------------------------------------------------------------------- #
def notify_macos(title, message):
    r"""Pop a modal dialog window (osascript `display dialog`).

    We use `display dialog` rather than `display notification`: dialogs are
    plain windows that always appear and ignore Notification Center
    permissions / Focus, whereas notification banners can be silently
    suppressed by those settings.

    ensure_ascii=False: AppleScript understands \" \\ \n \t but NOT \uXXXX,
    so pass literal unicode (em-dashes, etc.) rather than escaped code points.
    `giving up after 300` auto-dismisses the window after 5 min so unattended
    daily runs don't stack up modal windows forever.
    """
    script = (
        f"display dialog {json.dumps(message, ensure_ascii=False)} "
        f"with title {json.dumps(title, ensure_ascii=False)} "
        f'buttons {{"OK"}} default button "OK" giving up after 300'
    )
    subprocess.run(["osascript", "-e", script], check=True)


def notification_summary(posts, report_path):
    """Notifications truncate long bodies, so summarize: count + first companies."""
    names = [first_line(p["text"]).split("|")[0].strip()[:40] for p in posts]
    head = ", ".join(names[:3])
    if len(names) > 3:
        head += f", +{len(names) - 3} more"
    return f"{head}\nDetails: {report_path}"


# --------------------------------------------------------------------------- #
# Schedule management (--set-schedule)
# --------------------------------------------------------------------------- #
def _parse_hhmm(hhmm):
    """Parse 'HH:MM' (24-hour) -> (hour, minute), or None if invalid."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", hhmm or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _reload_agent():
    """Reboot the LaunchAgent so launchd picks up plist changes. Exit code."""
    # bootout may fail if the agent isn't currently loaded — that's fine,
    # bootstrap still (re)loads it.
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", PLIST_PATH],
                   capture_output=True)
    reload = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", PLIST_PATH],
                            capture_output=True, text=True)
    if reload.returncode != 0:
        print(f"Reloading the agent failed:\n{reload.stderr.strip()}",
              file=sys.stderr)
        return 1
    return 0


def build_launch_agent(hour, minute):
    """Return the LaunchAgent plist dict, built from the current environment.

    Uses sys.executable and this script's path so it's portable across machines.
    """
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            sys.executable, os.path.abspath(__file__),
            "--days", "1", "--new-only", "--notify", "--quiet",
        ],
        "WorkingDirectory": SCRIPT_DIR,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": os.path.join(SCRIPT_DIR, "hnhiring.out.log"),
        "StandardErrorPath": os.path.join(SCRIPT_DIR, "hnhiring.err.log"),
    }


def install_agent(hhmm="08:00"):
    """Create the LaunchAgent plist for this clone and register it. Exit code."""
    if LAUNCHD_LABEL == "com.example.hnhiring":
        print("Set LAUNCHD_LABEL at the top of hn_hiring.py to your own "
              "reverse-DNS label (e.g. com.yourname.hnhiring) before installing.",
              file=sys.stderr)
        return 2
    parsed = _parse_hhmm(hhmm)
    if parsed is None:
        print("--install expects HH:MM (24-hour), e.g. 07:30", file=sys.stderr)
        return 2
    hour, minute = parsed

    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(build_launch_agent(hour, minute), f)
    rc = _reload_agent()
    if rc == 0:
        print(f"Installed and scheduled daily at {hour:02d}:{minute:02d} "
              f"({PLIST_PATH}).")
    return rc


def uninstall_agent():
    """Stop and remove the LaunchAgent. Idempotent. Exit code."""
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", PLIST_PATH],
                   capture_output=True)
    if os.path.exists(PLIST_PATH):
        os.remove(PLIST_PATH)
        print(f"Removed {PLIST_PATH}.")
    else:
        print(f"Nothing to remove ({PLIST_PATH} not found).")
    return 0


def set_schedule(hhmm):
    """Rewrite the LaunchAgent's daily run time to HH:MM and reload it.

    Edits only StartCalendarInterval's Hour/Minute (via plistlib, so the rest of
    the plist is untouched), then reboots the agent so the change takes effect.
    Returns a process exit code.
    """
    parsed = _parse_hhmm(hhmm)
    if parsed is None:
        print("--set-schedule expects HH:MM (24-hour), e.g. 07:30", file=sys.stderr)
        return 2
    hour, minute = parsed

    if not os.path.exists(PLIST_PATH):
        print(f"LaunchAgent not found at {PLIST_PATH}. Run --install first.",
              file=sys.stderr)
        return 2

    with open(PLIST_PATH, "rb") as f:
        pl = plistlib.load(f)
    sci = pl.get("StartCalendarInterval")
    if not isinstance(sci, dict):
        sci = {}
    sci["Hour"], sci["Minute"] = hour, minute
    pl["StartCalendarInterval"] = sci
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(pl, f)

    rc = _reload_agent()
    if rc == 0:
        print(f"Daily run time set to {hour:02d}:{minute:02d} and agent reloaded.")
    return rc


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(description="Recent HN 'Who is hiring?' postings.")
    parser.add_argument("--days", type=int, default=None,
                        help="How many days back to include. If omitted, you'll be prompted.")
    parser.add_argument("--user", default="whoishiring",
                        help="HN account to follow for the thread (default: whoishiring).")
    parser.add_argument("--item", type=str, default=None,
                        help="Pin a specific HN item ID instead of following --user.")
    parser.add_argument("--new-only", action="store_true",
                        help="Only show postings not seen on a previous run (uses --state).")
    parser.add_argument("--state", default=DEFAULT_STATE,
                        help=f"Seen-IDs state file for --new-only (default: {DEFAULT_STATE}).")
    parser.add_argument("--notify", action="store_true",
                        help="Pop a macOS desktop notification with a summary.")
    parser.add_argument("--out", default=None,
                        help=f"Write the full report to this file (default with --notify: {DEFAULT_REPORT}).")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress stdout (useful in cron with --notify).")
    parser.add_argument("--all-locations", action="store_true",
                        help="Disable the US/Remote/Global filter and include every posting.")
    parser.add_argument("--all-roles", action="store_true",
                        help="Disable the engineer-role / no-principal-or-staff filter.")
    parser.add_argument("--visa", action="store_true",
                        help="Exclude postings that explicitly say they won't sponsor a "
                             "visa (citizens-only, 'no visa sponsorship', etc.). Off by "
                             "default; postings silent on visas are kept.")
    parser.add_argument("--set-schedule", metavar="HH:MM", default=None,
                        help="Change the daily run time of the LaunchAgent (24-hour, e.g. "
                             "07:30), reload it, and exit. Does not fetch postings.")
    parser.add_argument("--install", nargs="?", const="08:00", default=None,
                        metavar="HH:MM",
                        help="Create and register the daily LaunchAgent (default 08:00), "
                             "then exit. Set LAUNCHD_LABEL first.")
    parser.add_argument("--uninstall", action="store_true",
                        help="Stop and remove the LaunchAgent, then exit.")
    args = parser.parse_args(argv)

    # Management actions: run and exit before doing any fetching.
    if args.install is not None:
        return install_agent(args.install)
    if args.uninstall:
        return uninstall_agent()
    if args.set_schedule is not None:
        return set_schedule(args.set_schedule)

    days = args.days
    if days is None:
        try:
            days = int(input("How many days in the past should I check? ").strip())
        except (ValueError, EOFError):
            print("Please provide a whole number of days.", file=sys.stderr)
            return 2
    if days < 1:
        print("--days must be >= 1", file=sys.stderr)
        return 2

    # Resolve the thread
    if args.item:
        item_id, title = args.item, None
    else:
        item_id, title = find_latest_thread(args.user)

    thread = fetch_thread(item_id)
    if title is None:
        title = thread.get("title") or f"HN thread {item_id}"

    posts = collect_recent_posts(thread, days)

    # Filter by location (US/Remote/Global), role (engineer, not principal/staff),
    # and — when --visa is set — drop postings that refuse visa sponsorship.
    window = len(posts)
    loc_excluded = role_excluded = visa_excluded = 0
    kept = []
    for p in posts:
        if not args.all_locations and not location_ok(p["text"]):
            loc_excluded += 1
            continue
        if not args.all_roles and not role_ok(p["text"]):
            role_excluded += 1
            continue
        if args.visa and refuses_visa_sponsorship(p["text"]):
            visa_excluded += 1
            continue
        kept.append(p)
    posts = kept

    note_bits = []
    if loc_excluded:
        note_bits.append(f"{loc_excluded} non-US/remote")
    if role_excluded:
        note_bits.append(f"{role_excluded} non-engineer or principal/staff")
    if visa_excluded:
        note_bits.append(f"{visa_excluded} no visa sponsorship")
    exclusion_note = (
        f"({window - len(posts)} of {window} excluded: " + ", ".join(note_bits) + ")"
        if note_bits else None
    )

    # Dedup against previously-seen postings
    if args.new_only:
        seen = load_seen(args.state)
        fresh = [p for p in posts if p["id"] not in seen]
        seen.update(p["id"] for p in posts)  # remember current window for next run
        save_seen(args.state, seen)
        posts = fresh

    report = format_report(title, posts, days, exclusion_note)

    # Decide where the full report lands
    out_path = args.out or (DEFAULT_REPORT if args.notify else None)
    if out_path:
        with open(out_path, "w") as f:
            f.write(report + "\n")

    # Notify — but stay silent if --new-only found nothing new
    if args.notify and not (args.new_only and not posts):
        n = len(posts)
        summary = notification_summary(posts, out_path) if posts else "No new postings."
        notify_macos(f"HN hiring — {n} new posting(s)", summary)

    if not args.quiet:
        print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
