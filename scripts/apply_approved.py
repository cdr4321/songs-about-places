#!/usr/bin/env python3
"""
Move approved rows from review/us_candidates.csv, review/uk_candidates.csv
and review/rock_hall_candidates.csv into data/songs.csv.

  approved = y   -> added to data/songs.csv and removed from the queue
  approved = n   -> removed from the queue, and the term/title pair is added
                  to place_terms.json's blocked list so it isn't proposed again
  blank          -> left in the queue

A y row is held back (and reported) if its place is new and has no
coordinates, or if the song/place pair is already in the dataset. If the song
is already in the dataset under another place, the new row copies that song's
list columns and chart stats so every place row of a song agrees.

Any song added whose artist credit names a Rock & Roll Hall of Fame inductee
(review/rock_hall/inductees.csv) gets rock_hall_inductee = Y, whichever queue
it came from.

Runs automatically when a queue file is committed (see
.github/workflows/apply-approved.yml); it can also be run by hand.
"""
import json, os, sys
from common import (TERMS, REVIEW, norm, CHART_COL, SongIndex, load_queue, load_songs, save_queue,
                    queue_path, read_csv, write_csv, SONGS, better_peak)
from rock_hall import Inductees

YES, NO = {"y", "yes"}, {"n", "no"}


def main():
    cols, rows = load_songs()
    place_cols = {"place", "place_category", "mention_type", "latitude", "longitude"}
    song_cols = [c for c in cols if c not in place_cols | {"year", "title", "artist"}]
    places = {r["place"] for r in rows}
    added, removed, held, rejected, rh_rejected = [], 0, [], [], []
    inductees = Inductees()

    for chart in ("us", "uk", "rock_hall"):
        queue = load_queue(chart)
        keep = []
        for q in queue:
            a = q["approved"].strip().lower()
            if a in NO:
                removed += 1
                if chart == "rock_hall":
                    # not a chart song, so don't block the title for the weekly checks;
                    # remember it so a rerun of the Rock Hall scan doesn't propose it again
                    rh_rejected.append(q)
                    continue
                for term in q.get("matched_text", "").split(" + "):
                    if term.strip():
                        rejected.append([term.strip(), norm(q["title"])])
                continue
            if a not in YES:
                keep.append(q)
                continue
            place = q["place"].strip()
            if not q["year"].strip().isdigit():
                held.append((q, "no year — fill in the release year"))
                keep.append(q)
                continue
            if place not in places and not (q["latitude"].strip() and q["longitude"].strip()):
                held.append((q, "new place with no coordinates"))
                keep.append(q)
                continue
            index = SongIndex(rows)
            same = index.rows_for(q["title"], q["artist"])
            if any(r["place"] == place for r in same):
                held.append((q, "already in data/songs.csv — set approved to n to clear it"))
                keep.append(q)
                continue
            new = {c: "" for c in cols}
            if same:  # song already present under another place
                for c in song_cols:
                    new[c] = same[0][c]
                new.update(year=same[0]["year"], title=same[0]["title"], artist=same[0]["artist"])
            else:
                new.update(year=q["year"].strip(), title=q["title"].strip(), artist=q["artist"].strip())
            col = CHART_COL.get(q.get("chart") or chart)
            if col and better_peak(new[col], q["weekly_peak"].strip()):
                new[col] = q["weekly_peak"].strip()
                for r in same:
                    r[col] = new[col]
            if "rock_hall_inductee" in cols and (
                    chart == "rock_hall" or inductees.in_credit(new["artist"], new["year"])):
                new["rock_hall_inductee"] = "Y"
                for r in same:
                    r["rock_hall_inductee"] = "Y"
            new.update(place=place, place_category=q["place_category"].strip(),
                       mention_type=q["mention_type"].strip() or "Direct",
                       latitude=q["latitude"].strip(), longitude=q["longitude"].strip())
            if place in places:  # known place: always use the dataset's coordinates
                ref = next(r for r in rows if r["place"] == place)
                new.update(latitude=ref["latitude"], longitude=ref["longitude"],
                           place_category=ref["place_category"])
            rows.append(new)
            places.add(place)
            added.append(new)
        if queue or os.path.exists(queue_path(chart)):
            save_queue(chart, keep)

    if added:
        # stable sort on year only: existing rows keep their order, new ones join the end of their year
        rows.sort(key=lambda r: int(r["year"]) if r["year"].isdigit() else 9999)
        write_csv(SONGS, cols, rows)

    if rejected:
        remember_rejections(rejected)
    if rh_rejected:
        path = os.path.join(REVIEW, "rock_hall", "rejected.csv")
        old = read_csv(path)[1] if os.path.exists(path) else []
        cols_r = ["title", "artist", "place", "matched_text", "inductee"]
        write_csv(path, cols_r, old + [{c: q.get(c, "") for c in cols_r} for q in rh_rejected])

    print(f"added {len(added)} row(s); cleared {removed} rejected row(s)")
    for r in added:
        print(f"  + {r['year']} {r['title']} — {r['artist']} -> {r['place']}")
    for q, why in held:
        print(f"  held: {q['title']} -> {q['place']} ({why})")
    return 0


def remember_rejections(pairs):
    """A rejected song can stay on the chart for months; block the term for
    that title so the weekly check doesn't propose it again."""
    with open(TERMS, encoding="utf-8") as fh:
        cfg = json.load(fh)
    blocked = cfg.setdefault("blocked", [])
    new = [p for p in pairs if p not in blocked]
    blocked.extend(x for i, x in enumerate(new) if x not in new[:i])
    if new:
        with open(TERMS, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(cfg, ensure_ascii=False, separators=(",", ":")) + "\n")
        print(f"blocked {len(new)} rejected term/title pair(s) in place_terms.json")


if __name__ == "__main__":
    sys.exit(main())
