"""
Shared pieces of the weekly pipeline: reading and writing data/songs.csv,
matching place names in titles, recognising songs already in the dataset,
and keeping the review queues.

data/songs.csv is written back with a byte-order mark and CRLF line endings,
exactly as Excel saves it, so a round trip through the pipeline doesn't turn
every line into a diff.
"""
import csv, json, os, re, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SONGS = os.path.join(ROOT, "data", "songs.csv")
TERMS = os.path.join(ROOT, "scripts", "place_terms.json")
REVIEW = os.path.join(ROOT, "review")

CATEGORIES = ["City", "Country", "State/Region", "Continent/Region",
              "Street/Landmark", "Neighborhood/District", "Water/Natural Feature"]
MENTIONS = ["Direct", "Demonym", "Nickname"]
CHART_COL = {"us": "us_weekly", "uk": "uk_weekly"}

QUEUE_COLS = ["chart", "chart_week", "year", "title", "artist", "weekly_peak",
              "place", "place_category", "mention_type", "matched_text",
              "latitude", "longitude", "note", "approved"]


# ---------------------------------------------------------------- CSV I/O
def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames), [dict(x) for x in r]


def write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_songs():
    return read_csv(SONGS)


def queue_path(chart):
    return os.path.join(REVIEW, f"{chart}_candidates.csv")


def load_queue(chart):
    p = queue_path(chart)
    if not os.path.exists(p):
        return []
    return read_csv(p)[1]


def save_queue(chart, rows):
    os.makedirs(REVIEW, exist_ok=True)
    rows.sort(key=lambda r: (r["chart_week"], r["title"], r["place"]))
    write_csv(queue_path(chart), QUEUE_COLS, rows)


def load_terms():
    with open(TERMS, encoding="utf-8") as fh:
        cfg = json.load(fh)
    return cfg["terms"], cfg.get("blocked", [])


# ------------------------------------------------------------ normalising
def ascii_(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


def norm(s):
    s = ascii_(s).lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", s)).strip()


# Credit strings differ between sources for the same record: "Kenny Ball" vs
# "Kenny Ball and his Jazzmen", "Beyonce" vs "Beyonce ft. Kendrick Lamar".
# Reduce to the lead act so the same song isn't reported as new every week.
_TRIM = re.compile(r"\b(featuring|feat|ft|with|x|and his|and her|and the|his|her)\b.*$")
_NOISE = re.compile(r"\b(orch|orchestra|band|trio|quartet|quintet|his|the|jazzmen|"
                    r"group|singers|combo|ensemble)\b")


def lead(artist):
    a = norm(artist).replace("'", "")
    a = _TRIM.sub(" ", a)
    a = _NOISE.sub(" ", a)
    return re.sub(r"[^a-z0-9]", "", a)


def squash(title):
    """Letters and digits only, so 'Born In The U.S.A.' == 'Born In The USA'."""
    return re.sub(r"[^a-z0-9]", "", ascii_(title).lower().replace("&", "and"))


def same_artist(a1, a2):
    l1, l2 = lead(a1), lead(a2)
    if not l1 or not l2:
        # a credit made only of noise words ("The Band") must match exactly
        return re.sub(r"[^a-z0-9]", "", norm(a1)) == re.sub(r"[^a-z0-9]", "", norm(a2))
    return l1 == l2 or l1.startswith(l2) or l2.startswith(l1)


class SongIndex:
    """Find dataset rows for a (title, artist) seen on a chart."""

    def __init__(self, rows):
        self.by = {}
        for r in rows:
            self.by.setdefault(squash(r["title"]), []).append(r)

    def rows_for(self, title, artist):
        return [r for r in self.by.get(squash(title), [])
                if same_artist(r["artist"], artist)]


# ------------------------------------------------------------ matching
TOK = re.compile(r"[a-z0-9']+")
ANNOT = re.compile(r'\((?:from\s+"|live\b|feat\b)[^)]*\)|\([^)]*(?:version|remix|mix|edit)\s*\)', re.I)


def tokens(title):
    return TOK.findall(ANNOT.sub(" ", ascii_(title)).replace("&", " and ").lower())


def depossess(word):
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


UK_ISH = ("United Kingdom", "Ireland, Europe")
BROAD = {"Country", "Continent/Region", "State/Region", "Water/Natural Feature"}


def _segs(name):
    # the term list still spells NYC two ways; the dataset uses "New York, New York"
    name = name.replace("New York City, New York", "New York, New York")
    name = name.replace(", New York City", ", New York, New York")
    return [x.strip().lower() for x in name.split(",") if x.strip()]


def _within(short, chain):
    """Every segment of short appears in chain, in order, starting at the first:
    'London, United Kingdom' is within 'London, England, United Kingdom, Europe'."""
    a, b = _segs(short), _segs(chain)
    if not a or a[0] != b[0]:
        return False
    i = 1
    for seg in a[1:]:
        while i < len(b) and b[i] != seg:
            i += 1
        if i == len(b):
            return False
        i += 1
    return True


PLACE_ROWS = {}   # chain -> number of dataset rows; filled by weekly.process


def resolve(short, category, chart, places):
    """Map a place_terms.json name ('Gainesville, Florida') onto a full chain
    already in the dataset ('Gainesville, Florida, United States, North
    America'). Returns (chain, lat, lon, note); chain is None for a place the
    dataset has never had."""
    hits = sorted((c for c in places if _within(short, c)), key=len)
    note = ""
    if len(_segs(short)) == 1 and category not in BROAD and hits:
        # a bare name like "SoHo" could be London or Manhattan
        note = f"matched on the bare name '{short}' — confirm it's this place"
    if not hits:
        first = _segs(short)[0]
        same = [c for c in places if _segs(c)[0] == first]
        pref = [c for c in same if any(u in c for u in UK_ISH)] if chart == "uk" else \
               [c for c in same if "United States" in c]
        hits = pref or (same if len(same) == 1 and chart == "uk" else [])
        if hits:
            note = (f"term list says '{short}'; matched the dataset's place of the same "
                    "name — confirm it's the same place")
    if len(hits) > 1 and chart == "uk":
        hits = [c for c in hits if any(u in c for u in UK_ISH)] or hits
    if hits:
        c = hits[0]
        lat, lon = places[c]
        if (chart == "uk" and "United States" in c and not note
                and category not in BROAD and PLACE_ROWS.get(c, 0) < 3):
            note = "UK chart but a little-used US place — check it isn't a British namesake"
        return c, lat, lon, note
    return None, "", "", ""


# Places already in the dataset are always matchable, even if the term list
# lacks them (it has no "st louis", "ipanema" or "camden town"). Common words
# that happen to be curated places are left out.
DERIVED_SKIP = {"turkey", "queens", "georgia", "jersey"}


def dataset_terms(rows, terms):
    extra = {}
    for r in rows:
        first = r["place"].split(",")[0]
        k = norm(first).strip("'")
        if k and k not in terms and k not in extra and k not in DERIVED_SKIP:
            extra[k] = {"place": r["place"], "category": r["place_category"], "mention": "Direct"}
    return extra


def drop_nested(chains):
    """'Paris, Texas' names Paris and Texas; keep only Paris."""
    return [c for c in chains if not any(o != c and o.endswith(", " + c) for o in chains)]


# ------------------------------------------------------------ misc
def better_peak(old, new):
    """True if new is a higher chart position (lower number) than old."""
    if not str(new).isdigit():
        return False
    return not str(old).strip().isdigit() or int(new) < int(old)


SMALL = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "vs"}


def title_case(s):
    """Official Charts prints titles in capitals. Title-case them for the
    review queue; acronyms (ABBA, AC/DC) still need fixing by hand."""
    words = s.lower().split(" ")
    out = []
    for i, w in enumerate(words):
        if i and w in SMALL:
            out.append(w)
        else:
            out.append(re.sub(r"^([^a-z]*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), w))
    s = " ".join(out)
    s = re.sub(r"\bFt\b\.?", "ft.", s)
    s = re.sub(r"\bFeat\b\.?", "ft.", s)
    return s


def gh_output(**kv):
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")
