#!/usr/bin/env python3
"""
Weekly check of the UK Official Singles Chart Top 100.

JackDanHollister/UkTop100Scrape stopped updating in August 2025 and has no
scheduled job, so this fetches the chart page from officialcharts.com itself,
using that scraper's page parsing (Apache-2.0) with our two fixes:

  * the "New" / "RE" badges are separate elements inside the title link. The
    original stripped them with regexes on the joined text, which also ate
    real words ("NEW YORK GROOVE" -> "YORK GROOVE", "REBEL YELL" -> "BEL
    YELL"). Here the badge element is dropped before the text is read.
  * ad rows ("chart-ad", "primis") are skipped, as upstream.

Chart pages are dated by the Friday the chart week starts. By default the
script reads the most recent Friday that has a chart. Environment:
  UK_SINCE=YYYY-MM-DD   read every week from that date to now (backfill)
  UK_WEEKS=N            read the latest N weeks (default 1)
See weekly.py for what happens to the songs.
"""
import os, re, sys, time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from common import title_case
from weekly import process

BASE = "https://www.officialcharts.com/charts/singles-chart/{}/7501/"
HEADERS = {"User-Agent": "songs-about-places weekly check (github.com/cdr4321/songs-about-places)"}
BADGES = {"new", "re", "re-entry", "reentry"}


def clean_text(el):
    parts = [s.strip() for s in el.find_all(string=True) if s.strip()]
    while len(parts) > 1 and parts[0].lower() in BADGES:
        parts.pop(0)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def fetch_week(friday):
    url = BASE.format(friday.strftime("%Y%m%d"))
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")
    out = []
    for item in soup.find_all("div", class_="chart-item"):
        cls = item.get("class", [])
        if "chart-ad" in cls or "primis" in cls:
            continue
        name, artist = item.find("a", class_="chart-name"), item.find("a", class_="chart-artist")
        if not name or not artist:
            continue
        pos = item.find("strong")
        peak_li = item.find("li", class_="peak")
        peak_el = peak_li.find("span", class_="text-brand-cobalt") if peak_li else None
        pos = pos.get_text(strip=True) if pos else ""
        peak = peak_el.get_text(strip=True) if peak_el else ""
        peak = peak if peak.isdigit() else pos
        # a song's peak can't be worse than where it sits this week
        if pos.isdigit() and (not peak.isdigit() or int(pos) < int(peak)):
            peak = pos
        out.append({"title": title_case(clean_text(name)),
                    "artist": title_case(clean_text(artist)),
                    "week": friday.isoformat(), "peak": peak if peak.isdigit() else ""})
    return out


def last_friday(d):
    return d - timedelta(days=(d.weekday() - 4) % 7)


def main():
    today = date.today()
    since = os.environ.get("UK_SINCE", "").strip()
    if since:
        start = last_friday(datetime.strptime(since, "%Y-%m-%d").date())
        fridays = []
        d = start
        while d <= today:
            fridays.append(d)
            d += timedelta(days=7)
    else:
        n = int(os.environ.get("UK_WEEKS", "1"))
        latest = last_friday(today)
        fridays = [latest - timedelta(days=7 * i) for i in range(n + 1)]  # +1 spare in case
        fridays.sort()

    entries, got = [], []
    for f in fridays:
        try:
            wk = fetch_week(f)
        except requests.RequestException as e:
            print(f"  {f}: fetch failed ({e})")
            wk = []
        if len(wk) >= 90:
            entries += wk
            got.append(f)
            print(f"  {f}: {len(wk)} entries")
        else:
            print(f"  {f}: no complete chart ({len(wk)} entries)")
        time.sleep(1.5)

    if not since:
        # default mode: only the latest N weeks that actually exist
        n = int(os.environ.get("UK_WEEKS", "1"))
        keep = set(sorted(got)[-n:])
        entries = [e for e in entries if date.fromisoformat(e["week"]) in keep]
        got = sorted(keep)

    if not got:
        print("No UK chart could be read. The page layout may have changed — "
              "check fetch_week() against a live chart page.")
        return 1
    print(f"UK chart week(s) {got[0]} to {got[-1]}: {len(entries)} entries")
    process("uk", entries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
