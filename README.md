# HN "Who is hiring?" watcher

A small tool that pulls engineering jobs from Hacker News's
monthly **"Who is hiring?"** thread and notifies me about *new* postings once a
day. Built so I don't have to manually scroll the thread, the relevant roles
come to me. Built for MacOS and MacOS notification system.

## What's here

| File | Purpose |
| --- | --- |
| `hn_hiring.py` | The script. Fetches the latest "Who is hiring?" thread, filters postings, and delivers a report. |
| `hn_hiring_latest.txt` | The most recent report (auto-written on each run). |
| `.hn_hiring_seen.json` | Comment IDs already reported, so `--new-only` skips repeats. |
| `hnhiring.out.log` / `hnhiring.err.log` | stdout/stderr from the scheduled runs. |
| `test_hn_hiring.py` | Unit tests for the parsing/filtering logic. |

## Install (do this first)

A fresh clone has **no schedule**. Nothing runs until you install the
LaunchAgent:

1. Set **`LAUNCHD_LABEL`** at the top of `hn_hiring.py` to your own reverse-DNS
   label, e.g. `LAUNCHD_LABEL = "com.yourname.hnhiring"`.
2. Install (defaults to 08:00; pass a time to override):

   ```bash
   python3 hn_hiring.py --install          # daily at 08:00
   python3 hn_hiring.py --install 07:30     # daily at 07:30
   ```

3. Remove it anytime:

   ```bash
   python3 hn_hiring.py --uninstall
   ```

`--install` writes `~/Library/LaunchAgents/<LAUNCHD_LABEL>.plist` and registers
it with launchd, auto-detecting your Python and this clone's location. Only the
schedule commands need `LAUNCHD_LABEL`; fetching/reporting works without it.

## How the daily automation works

Once installed:

1. **launchd** runs the job every day at your chosen time. If the Mac is asleep
   then, launchd runs it once, automatically, on the next wake (built-in
   `StartCalendarInterval` catch-up) so you still get it the first time you
   open your laptop that day. It fires **at most once per day** either way.
2. It runs `hn_hiring.py` with `--days 1 --new-only --notify --quiet`: only
   postings from the last day, only ones not seen before, delivered as a macOS
   desktop notification, no console noise.

**Changing the run time:** don't hand-edit the plist:

```bash
python3 hn_hiring.py --set-schedule 07:30   # 24-hour HH:MM; rewrites the plist and reloads
```

This edits only the schedule's hour/minute and reboots the LaunchAgent so the
new time takes effect immediately.

## Why it's built this way

- **No dependencies**: the script uses only the Python standard library
- **Follows the thread automatically**: it reads the `whoishiring` account's
  submissions and picks the newest "Who is hiring?" thread, so it rolls over to
  next month's thread on its own.
- **Filters to what's relevant**: by default it keeps **engineer roles** (not
  principal/staff level) that are **US-based, global-remote, or unscoped
  remote**, and splits the report into Remote vs. US-onsite sections. A remote
  role scoped to a non-US region (e.g. "Remote (EU)") is dropped.
- **Optional visa filter** (`--visa`, off by default): drops postings that
  *explicitly* refuse sponsorship ("No visa sponsorship", "U.S. citizens only",
  "work authorization required", "not able to sponsor", etc.). It's an
  exclusion, not a requirement: postings that affirmatively sponsor **or** that
  say nothing about visas are kept, since silence on HN usually isn't a "no."
  The patterns are heuristics drawn from real thread wording.
- **Only surfaces new postings**: dedup via `.hn_hiring_seen.json` means the
  daily notification is signal, not the same list every morning.
- **No wrapper, no polling**: a single daily `StartCalendarInterval` fires once
  a day (or once on wake if 08:00 was missed), so nothing extra runs in the
  background. The tradeoff: if that one run fails (e.g. no wifi at that moment),
  the postings are picked up on the next day's run rather than retried the same
  day.

## Running it manually

```bash
python3 hn_hiring.py                          # prompts for days back, prints report
python3 hn_hiring.py --days 2                  # last 2 days to stdout
python3 hn_hiring.py --days 3 --visa           # last 3 days, drop no-sponsorship posts
python3 hn_hiring.py --all-roles --all-locations   # disable the role/location filters
python3 hn_hiring.py --set-schedule 07:30      # change the daily run time, then exit
```

### All options (`--help`)

```
usage: hn_hiring.py [-h] [--days DAYS] [--user USER] [--item ITEM] [--new-only]
                    [--state STATE] [--notify] [--out OUT] [--quiet] [--all-locations]
                    [--all-roles] [--visa] [--set-schedule HH:MM] [--install [HH:MM]]
                    [--uninstall]

Recent HN 'Who is hiring?' postings.

options:
  -h, --help            show this help message and exit
  --days DAYS           How many days back to include. If omitted, you'll be prompted.
  --user USER           HN account to follow for the thread (default: whoishiring).
  --item ITEM           Pin a specific HN item ID instead of following --user.
  --new-only            Only show postings not seen on a previous run (uses --state).
  --state STATE         Seen-IDs state file for --new-only.
  --notify              Pop a macOS desktop notification with a summary.
  --out OUT             Write the full report to this file.
  --quiet               Suppress stdout (useful in cron with --notify).
  --all-locations       Disable the US/Remote/Global filter and include every posting.
  --all-roles           Disable the engineer-role / no-principal-or-staff filter.
  --visa                Exclude postings that explicitly say they won't sponsor a visa
                        (citizens-only, 'no visa sponsorship', etc.). Off by default;
                        postings silent on visas are kept.
  --set-schedule HH:MM  Change the daily run time of the LaunchAgent (24-hour, e.g.
                        07:30), reload it, and exit. Does not fetch postings.
  --install [HH:MM]     Create and register the daily LaunchAgent (default 08:00),
                        then exit. Set LAUNCHD_LABEL first.
  --uninstall           Stop and remove the LaunchAgent, then exit.
```

## Tests

The test module imports the script as `hiring.hn_hiring`, so run it from the
parent directory:

```bash
python3 -m unittest hiring.test_hn_hiring -v
```

Covers the location, role, and visa filtering heuristics (visa cases use real
thread wording) plus `--set-schedule` input validation.
