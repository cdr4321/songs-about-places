#!/usr/bin/env python3
"""
Build review/rock_hall_candidates.csv from the Rock Hall discography scan.

Reads the track lists the scan committed to review/rock_hall/ (see
rock_hall_scan.py), keeps tracks credited to an inductee (lead, billed partner
or ft. guest, plus side bands listed with a note in inductees.csv), finds
place names in their titles with the same matcher as the weekly checks, and
queues what isn't already in data/songs.csv.

One row per song per place, like the other queues. A song on several releases
is listed once, with the year of its earliest release. Rows already decided
(in the dataset, or in review/rock_hall/rejected.csv) are not queued again;
re-running keeps the decisions already typed into the queue.

    python scripts/rock_hall_candidates.py
"""
import collections, csv, gzip, os, re
from common import (PLACE_ROWS, REVIEW, SongIndex, dataset_terms, drop_nested, load_queue,
                    load_songs, load_terms, read_csv, save_queue, squash)
from rock_hall import Inductees
import weekly

RH = os.path.join(REVIEW, "rock_hall")
EXTRA_COLS = ["inductee", "release", "release_type", "credited_as", "musicbrainz"]

# version tags that don't make a different song
VERSION = re.compile(r"\s*[\(\[][^\)\]]*\b(version|mix|remix|edit|live|mono|stereo|demo|take|"
                     r"instrumental|acoustic|reprise|remaster(ed)?|single|alternate|outtake|"
                     r"rehearsal|session|a cappella|extended|dub|radio)\b[^\)\]]*[\)\]]", re.I)
VERSION_DASH = re.compile(r"\s+-\s+(.*\b(version|mix|remix|edit|live|mono|stereo|demo|take|"
                          r"instrumental|acoustic|remaster(ed)?|single)\b.*)$", re.I)


def clean(title):
    t = VERSION.sub("", title)
    t = VERSION_DASH.sub("", t)
    return re.sub(r"\s+", " ", t).strip() or title.strip()


def dataset_credit(credit):
    """MusicBrainz credit -> the dataset's style ('ft.', 'and')."""
    c = re.sub(r"\s+(feat\.?|featuring|ft\.?)\s+", " ft. ", credit, flags=re.I)
    c = re.sub(r"\s+(&|x|with|and|/|\+)\s+", " and ", c) if " ft. " not in c else \
        " ft. ".join(re.sub(r"\s+(&|x|with|/|\+)\s+", " and ", p) for p in c.split(" ft. "))
    c = c.replace("‐", "-").replace("’", "'")
    return c


def main():
    inductees = Inductees()
    origin = {}
    for r in inductees.rows:
        origin[r["inductee"]] = r
    rgs = collections.defaultdict(list)
    for r in read_csv(os.path.join(RH, "release_groups.csv"))[1]:
        rgs[r["rg_id"]].append(r)
    with gzip.open(os.path.join(RH, "tracks.csv.gz"), "rt", encoding="utf-8", newline="") as fh:
        tracks = [t for t in csv.DictReader(fh) if t.get("title")]

    # ---- songs by inductees
    songs = {}   # (squashed title, credit) -> song
    for t in tracks:
        for g in rgs.get(t["rg_id"], []):
            credit = t["track_credit"] or g["rg_credit"]
            who = inductees.in_credit(credit, g["first_release_date"][:4])
            note = ""
            if not who:
                if g["note"] and (not t["track_credit"] or t["track_credit"] == g["rg_credit"]):
                    who, note = [g["inductee"]], g["note"]   # side band, judged row by row
                else:
                    continue
            title = clean(t["title"])
            key = (squash(title), squash(credit))
            year = g["first_release_date"][:4]
            s = songs.get(key)
            if s is None or (year and (not s["year"] or year < s["year"])):
                songs[key] = s = dict(
                    title=title, credit=credit, artist=dataset_credit(credit), year=year,
                    inductee="; ".join(who), side_note=note,
                    release=g["rg_title"], release_type=g["primary_type"] +
                    (" (soundtrack)" if g["secondary_types"] else ""),
                    musicbrainz=f"https://musicbrainz.org/release-group/{g['rg_id']}",
                    n=(s or {}).get("n", 0))
            s["n"] += 1

    # ---- places in their titles
    cols, rows = load_songs()
    index = SongIndex(rows)
    by_title = collections.defaultdict(list)
    for r in rows:
        by_title[squash(r["title"])].append(r)
    places = {r["place"]: (r["latitude"], r["longitude"]) for r in rows}
    weekly._CATS.update({r["place"]: r["place_category"] for r in rows})
    PLACE_ROWS.clear()
    for r in rows:
        PLACE_ROWS[r["place"]] = PLACE_ROWS.get(r["place"], 0) + 1
    terms, blocked = load_terms()
    terms = {**terms, **dataset_terms(rows, terms)}

    rejected = set()
    rp = os.path.join(RH, "rejected.csv")
    if os.path.exists(rp):
        rejected = {(squash(r["title"]), squash(r["artist"]), r["place"]) for r in read_csv(rp)[1]}
    old = {(squash(q["title"]), squash(q["artist"]), q["place"]): q for q in load_queue("rock_hall")}

    queue, in_dataset, counts = [], 0, collections.Counter()
    for s in sorted(songs.values(), key=lambda s: (s["inductee"], s["year"], s["title"])):
        # a bare "Richmond" or "Soho" defaults to the act's side of the Atlantic
        o = origin.get(s["inductee"].split("; ")[0], {}).get("origin", "")
        chart = "uk" if o.split(",")[0].strip() in ("UK", "Ireland") else "us"
        found = weekly.places_in(s["title"], terms, blocked, chart, places)
        if not found:
            continue
        existing = index.rows_for(s["title"], s["artist"]) or [
            r for r in by_title.get(squash(s["title"]), [])
            if set(inductees.in_credit(r["artist"], r["year"])) & set(s["inductee"].split("; "))]
        if existing:
            in_dataset += 1
            continue
        others = sorted({r["artist"] for r in by_title.get(squash(s["title"]), [])})
        chains = [f["chain"] for f in found if f["chain"]]
        outer = set(chains) - set(drop_nested(chains))
        for f in found:
            place = f["chain"] or f["short"]
            k = (squash(s["title"]), squash(s["artist"]), place)
            if k in rejected:
                continue
            notes = [x for x in (s["side_note"], f["note"]) if x]
            nested = f["chain"] in outer
            if not f["chain"]:
                notes.append("NEW PLACE — write the full chain (City, State, Country, Continent) "
                             "in place and fill latitude/longitude")
            elif nested:
                notes.append("contains another place in this title — pre-marked n; set y only if "
                             "both places are the point of the title")
            if others:
                notes.append("also in the dataset by " + ", ".join(others))
            row = {"chart": "rock_hall", "chart_week": "", "year": s["year"], "title": s["title"],
                   "artist": s["artist"], "weekly_peak": "", "place": place,
                   "place_category": f["category"], "mention_type": f["mention"],
                   "matched_text": f["term"], "latitude": f["lat"], "longitude": f["lon"],
                   "note": "; ".join(notes), "approved": "n" if nested else "",
                   "inductee": s["inductee"], "release": s["release"],
                   "release_type": s["release_type"], "credited_as": s["credit"],
                   "musicbrainz": s["musicbrainz"]}
            prev = old.get(k)
            if prev:   # keep decisions and edits already made in the queue
                row.update({c: prev[c] for c in ("year", "place", "place_category", "mention_type",
                                                  "latitude", "longitude", "approved") if prev.get(c)})
            queue.append(row)
            counts[s["inductee"]] += 1
    save_queue("rock_hall", queue, EXTRA_COLS)
    print(f"{len(songs)} songs by inductees in the scan; {in_dataset} place-songs already in "
          f"data/songs.csv; {len(queue)} queue rows for {len({(r['title'], r['artist']) for r in queue})} songs")
    for name, n in counts.most_common(15):
        print(f"  {n:4d}  {name}")


if __name__ == "__main__":
    main()
