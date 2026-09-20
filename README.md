# Songs About Places

An interactive map of songs whose titles name a real place. A song is included
if it reached the US Hot 100 or the UK Singles Chart, or appears on a major
list of great songs (Rolling Stone 500, National Recording Registry, Grammy
Hall of Fame and others). The `?` beside each title lists the reasons.

Live at <https://cdr4321.github.io/songs-about-places/>, also embedded on https://www.chrisdallariva.com/songs-about-places.

## Files

```
index.html                          the map (reads the two CSVs below)
data/songs.csv                      the dataset: one row per song per place
data/place_aliases.csv              extra search words for a place ("usa", "windy city")
review/us_candidates.csv            US review queue (written by the weekly check)
review/uk_candidates.csv            UK review queue (written by the weekly check)
scripts/find_new_place_songs.py     weekly US Hot 100 check
scripts/find_new_uk_place_songs.py  weekly UK Singles Chart check
scripts/weekly.py                   the part of the check both charts share
scripts/apply_approved.py           moves approved queue rows into data/songs.csv
scripts/validate_data.py            checks data/songs.csv before the site uses it
scripts/common.py                   matching and CSV helpers
scripts/place_terms.json            place names the matcher looks for, and blocked title/term pairs
```

## data/songs.csv

Edit it in GitHub's web editor or a spreadsheet. If you use Excel, save as
**CSV UTF-8 (Comma delimited)**; plain "CSV" breaks accented titles, and the
validator will reject the file.

| column | what goes in it |
|---|---|
| `year` | release year (chart-debut year if unknown) |
| `title`, `artist` | as credited; featured artists written `ft.` |
| `place` | full chain ending in the continent: `Memphis, Tennessee, United States, North America`. Plain letters only, no accents |
| `place_category` | City, Country, State/Region, Continent/Region, Street/Landmark, Neighborhood/District, Water/Natural Feature |
| `mention_type` | Direct, Demonym or Nickname |
| `latitude`, `longitude` | decimal degrees; every row for a place must use the same pin |
| `us_weekly`, `uk_weekly` | best weekly chart peak |
| `us_yearly` | US year-end rank |
| `acclaimed_music`, `ascap`, `blender`, `npr`, `riaa`, `rolling_stone` | the song's rank on that list |
| `grammy_hall_of_fame`, `national_recording_registry`, `rock_hall`, `time`, `spotify_popular` | `y` if the song is on it |
| `user_submission` | `y` if a reader suggested the song |

A song naming several places has one row per place, and those rows share the
same year and list/chart values. When a title names a place inside another
("Paris, Texas"), keep only the inner place, unless the contrast is the point
of the song ("Hollywood's Not America" keeps both).

Every row needs at least one list or chart value. To add a list, add a column,
give it a label in `REASONS` near the top of the script in `index.html`, and
add it to `KNOWN` in `validate_data.py`.

`validate_data.py` runs on every commit that touches `data/` (Actions tab,
*Validate data*). Its messages name the line and what to fix.

## Weekly checks

| | US | UK |
|---|---|---|
| runs | Wednesdays 14:00 UTC | Saturdays 10:00 UTC |
| source | `utdata/rwd-billboard-data` (`hot-100-current.csv`) | officialcharts.com singles chart page |

Each run does two things:

1. **Updates peaks automatically.** If a song already in `data/songs.csv` has a
   better peak this week (or its first UK peak), the value is written straight
   to the CSV.
2. **Queues new place-songs for review.** New titles that name a place go into
   `review/us_candidates.csv` or `review/uk_candidates.csv`, and an issue lists
   them. Rows stay in the queue until you decide.

`JackDanHollister/UkTop100Scrape` stopped updating in August 2025, so the UK
check reads the chart page itself using that project's parsing (Apache-2.0),
with fixes for the "New"/"RE" badges that were eating words out of titles.
Official Charts prints titles in capitals; the queue title-cases them, but
check acronyms (ABBA, AC/DC). If the page layout changes, the job fails with a
message rather than silently queuing nothing.

To catch up on missed weeks, run the workflow by hand (Actions, *Weekly
place-song check (UK)*, Run workflow) and set **uk_since**, e.g. `2025-08-08`.
The US workflow takes **weeks_back** instead.

## Reviewing the queue

1. Open the queue file in the GitHub editor.
2. Put `y` in `approved` for genuine place-songs and `n` for false positives.
   Leave a row blank to decide later.
3. Read the `note` column:
   - **NEW PLACE**: replace `place` with the full chain and fill in
     `latitude` and `longitude`.
   - **contains another place in this title**: the outer place of a
     "City, State" title, pre-marked `n`.
   - **UK chart but a little-used US place** or **matched on the bare name**:
     make sure it's the right town (Margate, Kent vs Margate, Florida).
4. Check `year`. It defaults to the chart year; change it to the release year
   if they differ.
5. Commit. *Apply approved candidates* runs automatically: `y` rows go into
   `data/songs.csv`; `n` rows leave the queue and are added to the blocked list
   in `place_terms.json` so they aren't proposed again. The result is validated
   before anything is committed.

The matcher has a real false-positive rate: it cannot tell Ms. Jackson from
Jackson, Mississippi. Nothing new goes live without a `y`.
