"""
The part of the weekly check that is the same for every chart.

Given one or more weeks of chart entries it:
  1. refreshes the chart peak of songs already in data/songs.csv (only ever
     improving it; a song's other columns are never touched),
  2. refreshes the peak of songs already waiting in the review queue,
  3. adds songs whose titles name a place to the review queue.

Step 1 goes straight into the dataset: it is a number the chart itself
reports for a song you already approved. Step 3 never does; new songs wait
for a 'y' in the queue.
"""
import os
from common import (CHART_COL, PLACE_ROWS, QUEUE_COLS, SongIndex, better_peak, dataset_terms,
                    drop_nested, extract, gh_output, load_queue, load_songs, load_terms,
                    resolve, same_artist, save_queue, squash, write_csv, SONGS)

LABEL = {"us": "US Hot 100", "uk": "UK Singles Chart"}


def collapse(entries):
    """One record per song: best peak, earliest week in the batch."""
    out = {}
    for e in entries:
        k = (squash(e["title"]), e["artist"].lower())
        cur = out.get(k)
        if cur is None:
            out[k] = dict(e)
        else:
            cur["week"] = min(cur["week"], e["week"])
            if better_peak(cur["peak"], e["peak"]):
                cur["peak"] = e["peak"]
    return list(out.values())


def places_in(title, terms, blocked, chart, places):
    """Place terms in a title, resolved to dataset chains. Adjacent terms that
    together name a known place ("Paris" + "Texas") are merged into it."""
    hits = extract(title, terms, blocked)
    out, i = [], 0
    while i < len(hits):
        info = terms[hits[i]]
        if i + 1 < len(hits):
            pair = f"{info['place'].split(',')[0]}, {terms[hits[i + 1]]['place'].split(',')[0]}"
            chain, lat, lon, note = resolve(pair, info["category"], chart, places)
            if chain and chain.lower().startswith(pair.lower() + ","):
                out.append(dict(term=f"{hits[i]} + {hits[i + 1]}", short=pair, chain=chain,
                                lat=lat, lon=lon, note=note, category=places_cat(chain),
                                mention="Direct"))
                i += 2
                continue
        chain, lat, lon, note = resolve(info["place"], info["category"], chart, places)
        out.append(dict(term=hits[i], short=info["place"], chain=chain, lat=lat, lon=lon,
                        note=note, category=info["category"], mention=info["mention"]))
        i += 1
    return out


_CATS = {}


def places_cat(chain):
    return _CATS.get(chain, "City")


def process(chart, entries):
    col = CHART_COL[chart]
    cols, rows = load_songs()
    index = SongIndex(rows)
    places = {r["place"]: (r["latitude"], r["longitude"]) for r in rows}
    _CATS.update({r["place"]: r["place_category"] for r in rows})
    PLACE_ROWS.clear()
    for r in rows:
        PLACE_ROWS[r["place"]] = PLACE_ROWS.get(r["place"], 0) + 1
    terms, blocked = load_terms()
    terms = {**terms, **dataset_terms(rows, terms)}
    queue = load_queue(chart)

    songs = collapse(entries)
    peak_updates, added = [], []

    for s in songs:
        # 1. already in the dataset
        hits = index.rows_for(s["title"], s["artist"])
        if hits:
            if better_peak(hits[0][col], s["peak"]):
                old = hits[0][col] or "—"
                for r in hits:
                    r[col] = str(s["peak"])
                peak_updates.append(f"{hits[0]['title']} — {hits[0]['artist']}: {old} → {s['peak']}")
            continue

        # 2. already queued
        queued = [q for q in queue if squash(q["title"]) == squash(s["title"])
                  and same_artist(q["artist"], s["artist"])]
        if queued:
            for q in queued:
                if better_peak(q["weekly_peak"], s["peak"]):
                    q["weekly_peak"] = str(s["peak"])
            continue

        # 3. new: does the title name a place?
        found = places_in(s["title"], terms, blocked, chart, places)
        chains = [f["chain"] for f in found if f["chain"]]
        outer = set(chains) - set(drop_nested(chains))
        for f in found:
            nested = f["chain"] in outer
            note = f["note"]
            if not f["chain"]:
                note = ("NEW PLACE — write the full chain (City, State, Country, Continent) "
                        "in place and fill latitude/longitude")
            elif nested:
                note = ("contains another place in this title — pre-marked n; set y only if "
                        "both places are the point of the title")
            row = {"chart": chart, "chart_week": s["week"], "year": s["week"][:4],
                   "title": s["title"], "artist": s["artist"], "weekly_peak": s["peak"] or "",
                   "place": f["chain"] or f["short"], "place_category": f["category"],
                   "mention_type": f["mention"], "matched_text": f["term"],
                   "latitude": f["lat"], "longitude": f["lon"], "note": note,
                   "approved": "n" if nested else ""}
            queue.append(row)
            added.append(row)

    if peak_updates:
        write_csv(SONGS, cols, rows)
    save_queue(chart, queue)

    print(f"{len(songs)} {LABEL[chart]} songs checked")
    print(f"{len(peak_updates)} peak update(s) written to data/songs.csv")
    for p in peak_updates:
        print("   ", p)
    print(f"{len(added)} new candidate row(s) added to review/{chart}_candidates.csv")
    for r in added:
        print(f"    {r['chart_week']}  {r['title'][:40]:40s}  {r['place'][:40]}  {r['note'][:30]}")

    write_issue(chart, added, len(queue))
    gh_output(new=len(added), peaks=len(peak_updates))
    return added, peak_updates


def write_issue(chart, added, queued):
    path = os.environ.get("ISSUE_BODY")
    if not path or not added:
        return
    lines = [f"The weekly {LABEL[chart]} check found **{len(added)}** song title"
             f"{'' if len(added) == 1 else 's'} that may name a place.", ""]
    for r in added[:30]:
        flag = f" — ⚠️ {r['note']}" if r["note"] else ""
        lines.append(f"- **{r['title']}** — {r['artist']} → {r['place']}{flag}")
    if len(added) > 30:
        lines.append(f"- …and {len(added) - 30} more")
    lines += ["", f"`review/{chart}_candidates.csv` now holds {queued} row(s) awaiting a decision.", "",
              "**To publish:**",
              f"1. Open `review/{chart}_candidates.csv` in the GitHub editor",
              "2. Put `y` in `approved` for genuine place-songs, `n` for false positives "
              "(blank rows stay in the queue)",
              "3. For any NEW PLACE row, write the full place chain and fill latitude/longitude",
              "4. Check the year — it defaults to the chart year; use the release year if different",
              "5. Commit. The *Apply approved candidates* workflow moves `y` rows into "
              "`data/songs.csv`, drops `n` rows, and validates the result.",
              "", "The matcher has a real false-positive rate — it cannot tell Ms. Jackson "
              "from Jackson, Mississippi."]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
