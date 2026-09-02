#!/usr/bin/env python3
"""
Weekly check for new Billboard Hot 100 songs whose titles name a place.

Writes review/candidates.csv. It does NOT edit the live dataset: the place
matcher has a real false-positive rate ("Ms. Jackson" is a person, "Fine China"
is porcelain, "Bohemian Rhapsody" is a lifestyle), and coordinates for a place
that has never charted before have to be assigned by hand. So this produces a
queue for a human to approve, and approved rows get appended by
scripts/apply_approved.py.

Source: utdata/rwd-billboard-data, which commits hot-100-current.csv weekly.
The Kaggle-based scrapers need credentials; this one is a plain HTTPS fetch.
"""
import csv, io, json, os, re, sys, unicodedata, urllib.request
from datetime import date

HOT100 = "https://raw.githubusercontent.com/utdata/rwd-billboard-data/main/data-out/hot-100-current.csv"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "places.json")
TERMS = os.path.join(ROOT, "scripts", "place_terms.json")
OUTDIR = os.path.join(ROOT, "review")
OUT = os.path.join(OUTDIR, "candidates.csv")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", s)).strip()


# Credit strings differ between sources for the same record: "Kenny Ball" vs
# "Kenny Ball and his Jazzmen", "Elton John" vs "The Elton John Band",
# "Beyonce" vs "Beyonce Featuring Kendrick Lamar". Reduce to the lead act so
# the same song isn't reported as new every week.
_TRIM = re.compile(r"\b(featuring|feat|ft|with|and his|and her|and the|his|her)\b.*$")
_NOISE = re.compile(r"\b(orch|orchestra|band|trio|quartet|quintet|his|the|jazzmen|"
                    r"group|singers|combo|ensemble)\b")


def lead(artist):
    a = norm(artist).replace("'", "")
    a = _TRIM.sub(" ", a)
    a = _NOISE.sub(" ", a)
    return re.sub(r"[^a-z0-9]", "", a)


def squash(title):
    """Letters and digits only, so 'Born In The U.S.A.' == 'Born In The USA'
    and 'Oklahoma Smokeshow' == 'Oklahoma Smoke Show'. Sources punctuate and
    space the same record inconsistently; Billboard itself changed the
    spelling of 'Smoke Show' mid-run."""
    t = unicodedata.normalize("NFKD", str(title)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", t.lower().replace("&", "and"))


def same_song(t1, a1, t2, a2):
    """Same record? Titles compared without punctuation or spacing; credits
    need only share their lead act."""
    if squash(t1) != squash(t2):
        return False
    l1, l2 = lead(a1), lead(a2)
    if not l1 or not l2:
        return True
    return l1 == l2 or l1.startswith(l2) or l2.startswith(l1)


TOK = re.compile(r"[a-z0-9']+")
ANNOT = re.compile(r'\((?:from\s+"|live\b|feat\b)[^)]*\)|\([^)]*(?:version|remix|mix|edit)\s*\)', re.I)


def tokens(title):
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return TOK.findall(ANNOT.sub(" ", t).replace("&", " and ").lower())


def depossess(word):
    """Hollywood's -> Hollywood, Texas' -> Texas. Without this, any possessive
    place name is invisible to the matcher: "Hollywood's Not America" matched
    America but not Hollywood."""
    return re.sub(r"'s$", "", re.sub(r"s'$", "s", word))


def extract(title, terms, blocked):
    """Longest-match place terms in a title, honouring per-title exclusions."""
    T, i, seen, hits = tokens(title), 0, set(), []
    while i < len(T):
        m = None
        for n in (4, 3, 2, 1):
            if i + n <= len(T):
                p = " ".join(T[i:i + n]).strip("'")
                if p in terms:
                    m = (p, n)
                    break
                # retry with possessives removed
                dp = " ".join(depossess(w) for w in T[i:i + n]).strip("'")
                if dp != p and dp in terms:
                    m = (dp, n)
                    break
        if m:
            term = m[0]
            if term not in seen and [term, norm(title)] not in blocked:
                seen.add(term)
                hits.append(term)
            i += m[1]
        else:
            i += 1
    return hits


def main():
    with open(TERMS, encoding="utf-8") as fh:
        cfg = json.load(fh)
    terms, blocked = cfg["terms"], cfg.get("blocked", [])
    with open(DATA, encoding="utf-8") as fh:
        places = json.load(fh)

    known_titles = {}
    for p in places:
        for s in p["s"]:
            known_titles.setdefault(squash(s["t"]), []).append(s["a"])
    known_places = {p["p"] for p in places}
    print(f"dataset: {len(places)} places, {sum(p['n'] for p in places)} songs")

    weeks_back = int(os.environ.get("WEEKS_BACK", "1"))
    print(f"fetching Hot 100 (checking the most recent {weeks_back} week(s))…")
    raw = urllib.request.urlopen(HOT100, timeout=180).read().decode("utf-8", "replace")
    all_rows = list(csv.DictReader(io.StringIO(raw)))
    weeks = sorted({r["chart_week"] for r in all_rows})
    recent = set(weeks[-weeks_back:])
    rows = [r for r in all_rows if r["chart_week"] in recent]
    print(f"chart week(s) {min(recent)} to {max(recent)} — {len(rows)} entries")

    # one record per song, keeping its best peak and earliest chart week
    songs = {}
    for r in rows:
        k = (norm(r["title"]), norm(r["performer"]))
        peak = int(r["peak_pos"]) if str(r["peak_pos"]).isdigit() else None
        cur = songs.get(k)
        if cur is None:
            songs[k] = {"title": r["title"], "artist": r["performer"],
                        "week": r["chart_week"], "peak": peak}
        else:
            if r["chart_week"] < cur["week"]:
                cur["week"] = r["chart_week"]
            if peak and (cur["peak"] is None or peak < cur["peak"]):
                cur["peak"] = peak

    out = []
    for k, s in songs.items():
        sq = squash(s["title"])
        if any(same_song(s["title"], s["artist"], s["title"], a)
               for a in known_titles.get(sq, [])):
            continue
        for term in extract(s["title"], terms, blocked):
            info = terms[term]
            out.append({
                "chart_week": s["week"],
                "year": s["week"][:4],
                "title": s["title"],
                "artist": s["artist"],
                "weekly_peak": s["peak"] or "",
                "place": info["place"],
                "place_category": info["category"],
                "mention_type": info["mention"],
                "matched_text": term,
                "latitude": info.get("lat", ""),
                "longitude": info.get("lon", ""),
                "place_is_new": "" if info["place"] in known_places else "YES — needs coordinates checked",
                "approved": "",
            })

    out.sort(key=lambda r: (r["chart_week"], r["title"]))
    os.makedirs(OUTDIR, exist_ok=True)
    cols = list(out[0].keys()) if out else [
        "chart_week", "year", "title", "artist", "weekly_peak", "place",
        "place_category", "mention_type", "matched_text", "latitude",
        "longitude", "place_is_new", "approved"]
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out)

    print(f"\n{len(out)} candidate rows -> review/candidates.csv")
    for r in out[:25]:
        flag = "  [NEW PLACE]" if r["place_is_new"] else ""
        print(f"  {r['chart_week']}  {r['title'][:42]:42s}  {r['place'][:30]:30s}{flag}")
    if len(out) > 25:
        print(f"  … and {len(out)-25} more")
    print("\nMark rows 'y' in the approved column, then run scripts/apply_approved.py")
    # surfaced in the GitHub Actions summary
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"count={len(out)}\n")


if __name__ == "__main__":
    sys.exit(main())
