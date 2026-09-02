# Songs About Places

An interactive map of every song that has charting on Billboard's weekly or yearly chart, 1940–2026, whose titles name a real place.

```
index.html                          the whole site
SETUP.md                            step-by-step setup guide
data/places.json                    places and songs
scripts/find_new_place_songs.py     weekly check for new songs
scripts/apply_approved.py           publishes the rows you approve
scripts/place_terms.json            place vocabulary + your past rejections
.github/workflows/weekly-check.yml  runs the check every Wednesday
review/candidates.csv               the review queue (generated)
```

## Weekly updates

The Action runs each Wednesday and checks **the latest chart week only** — 100
entries, a few seconds. It reports songs whose titles name a place and aren't
already in the data, writes `review/candidates.csv`, and opens an issue.

Set `WEEKS_BACK` to look further back: `WEEKS_BACK=52` for the past year, or a
large number to re-audit the whole archive. Useful after you widen the place
vocabulary, since a new term should be tested against history.

**It never publishes on its own, by design.** The matcher cannot tell Ms.
Jackson from Jackson, Mississippi, or fine china from China, and a place that
has never charted before has no coordinates. Both need a person. Matches credits
on the lead act, so "Kenny Ball" and "Kenny Ball and his Jazzmen" count as the
same record rather than reappearing weekly.

To publish a batch:

1. Open `review/candidates.csv`
2. Put `y` in the `approved` column for the genuine ones
3. Fill in `latitude`/`longitude` for any row flagged `place_is_new`
4. Run `python scripts/apply_approved.py`
5. Commit `data/places.json` — the live map picks it up

### Data source

`utdata/rwd-billboard-data`, which commits `hot-100-current.csv` into the repo
weekly and is current to the day. The `godefroylmb/Billboard` scraper you found
publishes to Kaggle rather than into its own repo, so using it would mean
storing Kaggle API credentials in the Action. Same underlying charts, no secrets.

To switch sources, change `HOT100` at the top of `find_new_place_songs.py`. It
expects columns `chart_week, title, performer, peak_pos`.

## Teaching it about rejections

`place_terms.json` holds a `blocked` list of `[term, title]` pairs, so the scan won't keep resurfacing 
"Sweet Georgia Brown" or "Bohemian Rhapsody". When you reject a candidate, add it there and
the scan will stay quiet about it from then on.

## Notes

- **YouTube links are searches, not video IDs.** Clicking runs a YouTube search
  for the title and artist. Hard-coded IDs would need an API key, and they rot
  when videos are taken down.
- **Attribution is required.** Map tiles are Esri Dark Gray Canvas over
  OpenStreetMap data; the credit in the corner needs to stay.
- **Why not CARTO?** It was the original choice, but CARTO began watermarking
  keyless tiles with "API KEY REQUIRED" in August 2026 and is retiring its
  raster service. Esri's equivalent needs no key. If you'd rather use CARTO,
  get a free key at carto.com/basemaps/apikey and follow the commented block

  near the top of the script in `index.html` — note the tile URLs order their
  coordinates differently ({z}/{x}/{y} vs {z}/{y}/{x}).
- The map needs a network connection — Leaflet, the tiles, and the fonts all
  load from CDNs.
