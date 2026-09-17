#!/usr/bin/env python3
"""
Weekly check of the Billboard Hot 100.

Source: utdata/rwd-billboard-data, which commits hot-100-current.csv weekly.
WEEKS_BACK (default 1) sets how many of the latest chart weeks to read.
See weekly.py for what happens to the songs.
"""
import csv, io, os, re, sys, urllib.request
from weekly import process

HOT100 = "https://raw.githubusercontent.com/utdata/rwd-billboard-data/main/data-out/hot-100-current.csv"


def main():
    weeks_back = int(os.environ.get("WEEKS_BACK", "1"))
    raw = urllib.request.urlopen(HOT100, timeout=180).read().decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(raw)))
    weeks = sorted({r["chart_week"] for r in rows})
    recent = set(weeks[-weeks_back:])
    entries = [{"title": r["title"], "artist": re.sub(r"\s+Featuring\s+", " ft. ", r["performer"]), "week": r["chart_week"],
                "peak": r["peak_pos"] if str(r["peak_pos"]).isdigit() else ""}
               for r in rows if r["chart_week"] in recent]
    print(f"Hot 100 week(s) {min(recent)} to {max(recent)}: {len(entries)} entries")
    process("us", entries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
