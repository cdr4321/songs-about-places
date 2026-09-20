#!/usr/bin/env python3
"""
Check data/songs.csv (and data/place_aliases.csv) before the site uses them.

Errors fail the check; warnings are printed but don't. Runs on every commit
that touches data/ (see .github/workflows/validate.yml). Most errors it
catches are things Excel does silently when it saves a CSV.
"""
import collections, csv, io, os, re, sys
from common import CATEGORIES, MENTIONS, ROOT, SONGS

ALIASES = os.path.join(ROOT, "data", "place_aliases.csv")
REQUIRED = ["year", "title", "artist", "place", "place_category", "mention_type",
            "latitude", "longitude", "us_weekly", "us_yearly", "uk_weekly", "user_submission"]
FLAG_COLS = {"grammy_hall_of_fame", "national_recording_registry", "rock_hall", "time",
             "spotify_popular", "standard", "user_submission"}
PLACE_COLS = {"place", "place_category", "mention_type", "latitude", "longitude"}
# titles where two nested places are both the point of the song
NESTED_OK = {("Hollywood's Not America", "Ferras")}
# the site's tooltip labels, in index.html — a new list column needs one there too
KNOWN = set(REQUIRED) | FLAG_COLS | {
    "acclaimed_music", "ascap", "blender", "npr", "riaa", "rolling_stone"}


def main():
    err, warn = [], []
    raw = open(SONGS, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        print(f"ERROR: data/songs.csv is not UTF-8 (byte {e.start}). It was probably saved "
              "from Excel as plain 'CSV'. Re-save it as 'CSV UTF-8 (Comma delimited)'.")
        return 1
    if not raw.startswith(b"\xef\xbb\xbf"):
        warn.append("file has no UTF-8 byte-order mark; Excel may show accented titles "
                    "incorrectly when it next opens it (harmless for the site)")
    rd = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    cols = rd.fieldnames or []
    rows = list(rd)

    for c in REQUIRED:
        if c not in cols:
            err.append(f"missing column: {c}")
    for c in cols:
        if c not in KNOWN:
            err.append(f"unknown column '{c}' — add it to KNOWN here and give it a label in index.html")
    if len(set(cols)) != len(cols):
        err.append("duplicate column names")
    if err:
        return report(err, warn)

    list_cols = [c for c in cols if c not in {"year", "title", "artist"} | PLACE_COLS]
    seen = set()
    place_meta = {}
    pin_reported = set()
    song_meta = {}
    nested = collections.defaultdict(list)

    for i, r in enumerate(rows, start=2):  # line 1 is the header
        at = f"line {i} ({r.get('title', '')!r})"
        if None in r:
            err.append(f"{at}: more fields than columns — a comma in a value needs the value in quotes")
            continue
        if any(v is None for v in r.values()):
            err.append(f"{at}: fewer fields than columns")
            continue
        for c in ("year", "title", "artist", "place", "place_category", "mention_type",
                  "latitude", "longitude"):
            if not r[c].strip():
                err.append(f"{at}: {c} is empty")
        for c, v in r.items():
            if v != v.strip():
                warn.append(f"{at}: {c} has leading/trailing spaces")
            if v and (v[0] in "=+@" or (v[0] == "-" and c not in ("latitude", "longitude"))):
                warn.append(f"{at}: {c} starts with '{v[:1]}' — Excel will treat it as a formula")
        if not re.fullmatch(r"\d{4}", r["year"]) or not 1800 <= int(r["year"]) <= 2100:
            err.append(f"{at}: year '{r['year']}' is not a 4-digit year")
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                err.append(f"{at}: coordinates out of range ({lat}, {lon})")
        except ValueError:
            err.append(f"{at}: latitude/longitude not numbers ('{r['latitude']}', '{r['longitude']}')")
            lat = lon = None
        if r["place_category"] not in CATEGORIES:
            err.append(f"{at}: place_category '{r['place_category']}' not one of {CATEGORIES}")
        if r["mention_type"] not in MENTIONS:
            err.append(f"{at}: mention_type '{r['mention_type']}' not one of {MENTIONS}")
        if re.search(r"[^\x00-\x7f]", r["place"]):
            err.append(f"{at}: place has accented characters ('{r['place']}') — use plain letters")
        for c in list_cols:
            v = r[c].strip()
            if not v:
                continue
            if c in FLAG_COLS:
                if v.lower() != "y":
                    err.append(f"{at}: {c} should be y or blank, not '{v}'")
            elif not re.fullmatch(r"\d+", v):
                err.append(f"{at}: {c} should be a whole number (rank/peak), not '{v}'")
        if not any(r[c].strip() for c in list_cols):
            err.append(f"{at}: no reason to be included — every list/chart column is blank")

        key = (r["title"].lower(), r["artist"].lower())
        if key + (r["place"],) in seen:
            err.append(f"{at}: duplicate of an earlier row (same title, artist and place)")
        seen.add(key + (r["place"],))
        nested[(r["title"], r["artist"])].append(r["place"])

        pm = (r["place_category"], r["latitude"], r["longitude"])
        if r["place"] in place_meta and place_meta[r["place"]][0] != pm and r["place"] not in pin_reported:
            pin_reported.add(r["place"])
            err.append(f"{at}: '{r['place']}' has category/coordinates {pm} but line "
                       f"{place_meta[r['place']][1]} has {place_meta[r['place']][0]} — one place, one pin")
        place_meta.setdefault(r["place"], (pm, i))

        sm = tuple(r[c] for c in ["year"] + list_cols)
        if key in song_meta and song_meta[key][0] != sm:
            warn.append(f"{at}: year or list/chart values differ from line {song_meta[key][1]} "
                        f"for the same song — the tooltip will show the first row's values")
        song_meta.setdefault(key, (sm, i))

    for (t, a), ps in nested.items():
        if (t, a) in NESTED_OK:
            continue
        for p in ps:
            if any(q != p and q.endswith(", " + p) for q in ps):
                warn.append(f"'{t}' ({a}) lists both a place and '{p}', which contains it — "
                            "drop the outer place unless both are the point of the title "
                            "(then add the song to NESTED_OK)")

    if os.path.exists(ALIASES):
        with open(ALIASES, encoding="utf-8-sig", newline="") as fh:
            ar = csv.DictReader(fh)
            if ar.fieldnames != ["place", "aliases"]:
                err.append("data/place_aliases.csv must have exactly the columns place,aliases")

    print(f"{len(rows)} rows, {len(song_meta)} songs, {len(place_meta)} places")
    return report(err, warn)


def report(err, warn, cap=60):
    for w in warn[:cap]:
        print("warning:", w)
    if len(warn) > cap:
        print(f"... and {len(warn) - cap} more warnings")
    for e in err[:cap]:
        print("ERROR:", e)
    if len(err) > cap:
        print(f"... and {len(err) - cap} more errors")
    print(f"\n{len(err)} error(s), {len(warn)} warning(s)")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
