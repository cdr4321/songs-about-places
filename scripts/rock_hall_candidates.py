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
import collections, csv, gzip, os, re, unicodedata
from common import (PLACE_ROWS, REVIEW, SongIndex, dataset_terms, drop_nested, load_queue,
                    load_songs, load_terms, read_csv, save_queue, squash)
from rock_hall import Inductees, cnorm
import weekly

RH = os.path.join(REVIEW, "rock_hall")
EXTRA_COLS = ["suggest", "inductee", "release", "release_type", "credited_as", "musicbrainz"]

# version tags that don't make a different song
VERSION = re.compile(r"\s*[\(\[][^\)\]]*\b(version|vers|mix|mixx|remix|edit|live|mono|stereo|demo|takes?|"
                     r"instrumental|acoustic|reprise|remaster(ed)?|single|alternate|outtake|"
                     r"rehearsal|session|a cappella|extended|dub|radio|lp|45|ep|interlude|"
                     r"intro|outro|bonus|unreleased|early|rough|feature length|silent|talkie)\b"
                     r"[^\)\]]*[\)\]]", re.I)
VERSION_DASH = re.compile(r"\s+[-\u2013]\s+(.*\b(version|mix|remix|edit|live|mono|stereo|demo|takes?|"
                          r"instrumental|acoustic|remaster(ed)?|single|alternate)\b.*)$", re.I)
# promo spots, interviews and the like aren't songs
NOT_SONG = re.compile(r"\b(commentary|call ?out|hook|interview|radio spot|tv spot|jingle|promo|"
                      r"spoken|dialogue|announcement|liner notes|message from)\b", re.I)


def clean(title):
    t = VERSION.sub("", title)
    t = VERSION_DASH.sub("", t)
    return re.sub(r"\s+", " ", t).strip() or title.strip()


def parts(title):
    """A medley track ('Brooklyn Roads / America') holds several songs."""
    t = re.sub(r"^\s*(medley|demo medley|suite)\s*:\s*", "", title, flags=re.I)
    t = re.sub(r"^.*\bsuite\s*:\s*", "", t, flags=re.I)
    return [x.strip() for x in t.split(" / ") if x.strip()]


def song_key(title):
    """Variants of one song share a key: '(Get Your Kicks On) Route 66' = 'Route 66',
    'Tour de France Etape 1' = 'Tour De France', 'Across 110th Street, Part II'
    = 'Across 110th Street'."""
    t = title.replace("\u2019", "'").replace("\u2018", "'")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t)
    t = re.sub(r"\s+[-\u2013]\s+.*$", " ", t)
    t = re.sub(r"\b(parts?|pts?|etape)\.?\s*([ivx]+|\d+)(\s*(&|and)\s*(\d+|[ivx]+))?\b", " ", t)
    t = re.sub(r"\b(intro|interlude|outro|reprise)\b", " ", t)
    t = re.sub(r"[\u2019']\d\d\b", " ", t)        # Harlem '89, Tour de France '03
    t = re.sub(r"^\s*the\s+", "", t.strip())
    return re.sub(r"[^a-z0-9]", "", t.replace("&", "and"))


class Credits:
    """MusicBrainz credit -> the dataset's style: 'ft.' for featuring, 'and'
    between separate acts, '&' kept inside an act's own name (Simon & Garfunkel),
    and the dataset's spelling when it already has the act."""

    def __init__(self, rows, artist_rows, inductee_rows):
        names = ({r["mb_name"] for r in artist_rows if r.get("mb_name")} | {r["artist"] for r in rows}
                 | {r["inductee"] for r in inductee_rows}
                 | {x.strip() for r in inductee_rows for x in r["credit_names"].split(";") if x.strip()})
        self.keep = sorted({self.ascii(n) for n in names if "&" in n}, key=len, reverse=True)
        self.known = {}
        for r in rows:
            self.known.setdefault(cnorm(r["artist"]), r["artist"])
        # one act's own spelling, for a part of a joint credit ('Jay Z' -> 'Jay-Z')
        self.acts = {cnorm(r["inductee"]): r["inductee"] for r in inductee_rows}

    @staticmethod
    def ascii(c):
        for x, y in (("\u2010", "-"), ("\u2011", "-"), ("\u2019", "'"), ("\u2018", "'"),
                     ("\u201c", '"'), ("\u201d", '"')):
            c = c.replace(x, y)
        return c

    def __call__(self, credit):
        c = self.ascii(credit)
        c = re.sub(r"\s+(feat\.?|featuring|ft\.?)\s+", " ft. ", c, flags=re.I)
        for i, name in enumerate(self.keep):
            c = re.sub(re.escape(name), f"\x00{i}\x00", c, flags=re.I)
        c = re.sub(r"\s+(&|x|with|/|\+)\s+", " and ", c)
        c = re.sub(r"\x00(\d+)\x00", lambda m: self.keep[int(m.group(1))], c)
        # 'Jay Z & Jay-Z': the same act credited twice
        parts = re.split(r"(, | and | ft\. )", c)
        out, seen = [], set()
        for k in range(0, len(parts), 2):
            key = re.sub(r"[^a-z0-9]", "", parts[k].lower())
            if key in seen:
                continue
            seen.add(key)
            out += ([parts[k - 1]] if k and out else []) + [self.acts.get(cnorm(parts[k]), parts[k])]
        c = "".join(out)
        return self.known.get(cnorm(c), c)


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

    cols, rows = load_songs()
    dataset_credit = Credits(rows, read_csv(os.path.join(RH, "artists.csv"))[1], inductees.rows)

    # ---- songs by inductees: one per title per inductee credit, earliest release
    songs = {}
    for t in tracks:
        if NOT_SONG.search(t["title"]):
            continue
        for g in rgs.get(t["rg_id"], []):
            credit = t["track_credit"] or g["rg_credit"]
            year = g["first_release_date"][:4]
            who = inductees.in_credit(credit, year)
            note = ""
            if not who:
                if g["note"] and (not t["track_credit"] or t["track_credit"] == g["rg_credit"]):
                    who, note = [g["inductee"]], g["note"]   # side band, judged row by row
                else:
                    continue
            for part in parts(t["title"]):
                title = clean(part)
                key = (song_key(title), tuple(sorted(who)))
                if not key[0]:
                    continue
                s = songs.get(key)
                rank = (year or "9999", title != part, len(title))
                if s is None or rank < s["rank"]:
                    songs[key] = dict(
                        rank=rank, title=title, credit=credit, artist=dataset_credit(credit),
                        year=year, inductee="; ".join(who), side_note=note,
                        release=g["rg_title"], release_type=g["primary_type"] +
                        (" (soundtrack)" if g["secondary_types"] else ""),
                        musicbrainz=f"https://musicbrainz.org/release-group/{g['rg_id']}")

    # ---- places in their titles
    index = SongIndex(rows)
    by_key = collections.defaultdict(list)
    for r in rows:
        by_key[song_key(r["title"])].append(r)
    flagged = [(song_key(r["title"]), set(inductees.in_credit(r["artist"], r["year"])), r["title"])
               for r in rows if r.get("rock_hall_inductee")]
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
        rejected = {(squash(r["title"]), squash(r["artist"]), r["matched_text"]) for r in read_csv(rp)[1]}
    # keyed on the matched term, so a place corrected in the queue survives a rebuild
    old = {(squash(q["title"]), squash(q["artist"]), q["matched_text"]): q for q in load_queue("rock_hall")}

    queue, in_dataset, counts = [], 0, collections.Counter()
    for s in sorted(songs.values(), key=lambda s: (s["inductee"], s["year"], s["title"])):
        # a bare "Richmond" or "Soho" defaults to the act's side of the Atlantic
        o = origin.get(s["inductee"].split("; ")[0], {}).get("origin", "")
        chart = "uk" if o.split(",")[0].strip() in ("UK", "Ireland") else "us"
        found = weekly.places_in(s["title"], terms, blocked, chart, places)
        if not found:
            continue
        mine = set(s["inductee"].split("; "))
        existing = index.rows_for(s["title"], s["artist"]) or [
            t for k, w, t in flagged if k == song_key(s["title"]) and w & mine]
        if existing:
            in_dataset += 1
            continue
        others = sorted({r["artist"] for r in by_key.get(song_key(s["title"]), [])})
        k0 = song_key(s["title"])
        near = sorted({t for k, w, t in flagged if w & mine and min(len(k0), len(k)) >= 5
                       and (k0 in k or k in k0)})
        chains = [f["chain"] for f in found if f["chain"]]
        outer = set(chains) - set(drop_nested(chains))
        for f in found:
            place = f["chain"] or f["short"]
            k = (squash(s["title"]), squash(s["artist"]), f["term"])
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
            if near:
                notes.append("may be the same song as " + ", ".join(f"'{x}'" for x in near) +
                             " already in the dataset")
            if not s["year"]:
                notes.append("no release date on MusicBrainz — fill in year")
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
                                                  "latitude", "longitude", "note", "approved", "suggest")
                            if prev.get(c)})
            queue.append(row)
            counts[s["inductee"]] += 1
    save_queue("rock_hall", queue, EXTRA_COLS)
    print(f"{len(songs)} songs by inductees in the scan; {in_dataset} place-songs already in "
          f"data/songs.csv; {len(queue)} queue rows for {len({(r['title'], r['artist']) for r in queue})} songs")
    for name, n in counts.most_common(15):
        print(f"  {n:4d}  {name}")


if __name__ == "__main__":
    main()
