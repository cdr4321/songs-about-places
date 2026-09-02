#!/usr/bin/env python3
"""
Append approved rows from review/candidates.csv into data/places.json.

Only rows with 'y' in the approved column are used. A row whose place is new
must have latitude and longitude filled in, or it is skipped and reported.
Run scripts/find_new_place_songs.py first.
"""
import csv, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "places.json")
CAND = os.path.join(ROOT, "review", "candidates.csv")


def main():
    if not os.path.exists(CAND):
        print("No review/candidates.csv. Run find_new_place_songs.py first.")
        return 1
    places = json.load(open(DATA, encoding="utf-8"))
    by = {p["p"]: p for p in places}
    rows = [r for r in csv.DictReader(open(CAND, encoding="utf-8"))
            if r["approved"].strip().lower() in ("y", "yes", "1", "true")]
    if not rows:
        print("Nothing approved. Put 'y' in the approved column for rows you want.")
        return 0

    added, skipped = 0, []
    for r in rows:
        place = r["place"].strip()
        if place not in by:
            if not (r["latitude"].strip() and r["longitude"].strip()):
                skipped.append((r["title"], place, "new place with no coordinates"))
                continue
            by[place] = {"p": place, "c": r["place_category"],
                         "lat": float(r["latitude"]), "lon": float(r["longitude"]), "s": []}
            places.append(by[place])
        entry = by[place]
        dup = any(s["t"].lower() == r["title"].lower() and s["a"].lower() == r["artist"].lower()
                  for s in entry["s"])
        if dup:
            skipped.append((r["title"], place, "already present"))
            continue
        entry["s"].append({
            "y": int(r["year"]), "t": r["title"], "a": r["artist"],
            "m": r["mention_type"] or "Direct",
            "ye": int(r["year_end"]) if r.get("year_end", "").strip().isdigit() else None,
            "wk": int(r["weekly_peak"]) if r["weekly_peak"].strip().isdigit() else None,
        })
        added += 1

    for p in places:
        p["s"].sort(key=lambda s: (s["y"], s["t"]))
        p["n"] = len(p["s"])
    places.sort(key=lambda p: -p["n"])
    json.dump(places, open(DATA, "w", encoding="utf-8"),
              separators=(",", ":"), ensure_ascii=False)

    print(f"added {added} songs; dataset now {len(places)} places, "
          f"{sum(p['n'] for p in places)} songs")
    for t, pl, why in skipped:
        print(f"  skipped: {t} -> {pl} ({why})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
