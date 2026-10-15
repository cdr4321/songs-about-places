"""
Match a reader's free-text place against what the dataset already knows, and
guess how the song's title mentions it.

Used by add_user_submission.py to pre-fill a suggestion's place, latitude,
longitude, place_category and mention_type, so a suggestion about a place that
is already on the map needs only a quick check. Nothing here is final: the
review queue still needs a `y`, and the note on each row says what was filled
in and why.

How a place is matched, in order:
  1. the dataset's own places: "Los Angeles", "Los Angeles, CA", "Paris, Texas",
     "Memphis Tennessee" (a trailing state or province is understood, with its
     usual abbreviation). The reader's "Kind of place" breaks ties between
     places that share a name (New York the city or the state).
  2. nicknames and aliases: data/place_aliases.csv ("la la land") and the weekly
     checks' term list ("NYC", "Big Apple", "Windy City").
  3. a few abbreviations the term list leaves out on purpose when reading
     titles, because they'd fire on ordinary words ("LA", "DC"). Here a reader
     typed them into a Place box, so there's no such risk.
A name shared by more than one place that the kind can't separate (Paris,
France or Paris, Texas) is reported as ambiguous and not guessed, and so is a
kind that contradicts the dataset (Georgia as a Country, when the dataset only
has the state).

Mention type: Direct if the title contains the place's name; otherwise a
nickname or demonym from the term list or the aliases file; blank if the title
doesn't tell.
"""
import csv, os, re
from common import (ROOT, _spellings, dataset_terms, extract, load_terms, resolve, term_key, tokens)

ALIASES = os.path.join(ROOT, "data", "place_aliases.csv")

US_STATES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California",
    "co": "Colorado", "ct": "Connecticut", "de": "Delaware", "fl": "Florida", "ga": "Georgia",
    "hi": "Hawaii", "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
    "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland", "ma": "Massachusetts",
    "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri", "mt": "Montana",
    "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico",
    "ny": "New York", "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
    "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah", "vt": "Vermont",
    "va": "Virginia", "wa": "Washington", "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
    "dc": "D.C.",
    # Canada
    "ab": "Alberta", "bc": "British Columbia", "mb": "Manitoba", "nb": "New Brunswick",
    "nl": "Newfoundland and Labrador", "ns": "Nova Scotia", "on": "Ontario",
    "pe": "Prince Edward Island", "qc": "Quebec", "sk": "Saskatchewan",
}
# "LA" is Louisiana when it follows a comma ("Lafayette, LA") but Los Angeles on its own,
# so abbreviations are only expanded in a later segment.
STATE_NAMES = {term_key(v) for v in US_STATES.values()}
COUNTRY_WORDS = {"usa": "United States", "us": "United States", "u s a": "United States",
                 "u s": "United States", "united states of america": "United States",
                 "uk": "United Kingdom", "u k": "United Kingdom", "great britain": "United Kingdom",
                 "britain": "United Kingdom"}
# typed whole into the Place box: (what it means, how it looks in a title)
FORM_ALIASES = {"la": ("Los Angeles, California", r"\bLA\b"),
                "dc": ("Washington, D.C.", r"\bDC\b")}
# a place that really is ambiguous is listed, not guessed; stop the list at this many
MAX_CANDIDATES = 5


def one_line(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def variants(seg):
    """Spellings of one place-name segment that count as the same ('St. Louis' = 'Saint Louis')."""
    words = tokens(seg)
    return set(_spellings(words)) if words else set()


def contains(words, seq):
    n = len(seq)
    return n > 0 and any(words[i:i + n] == seq for i in range(len(words) - n + 1))


class Result:
    def __init__(self, typed):
        self.typed = typed
        self.status = "new"        # matched | ambiguous | mismatch | new
        self.chain = ""
        self.lat = self.lon = ""
        self.category = ""
        self.candidates = []
        self.via = ""              # how it matched, for the reviewer
        self.notes = []


class Matcher:
    def __init__(self, rows):
        self.info = {}
        for r in rows:
            self.info.setdefault(r["place"], (r["latitude"], r["longitude"], r["place_category"]))
        self.parts = {c: [variants(s) for s in c.split(", ")] for c in self.info}
        terms, self.blocked = load_terms()
        self.terms = {**terms, **dataset_terms(rows, terms)}
        self.places_ll = {c: (v[0], v[1]) for c, v in self.info.items()}
        self.aliases = {}
        if os.path.exists(ALIASES):
            with open(ALIASES, encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    if r["place"] in self.info:
                        for a in r["aliases"].split(";"):
                            if term_key(a):
                                self.aliases[term_key(a)] = r["place"]

    # ------------------------------------------------------------ the place
    def _later(self, seg):
        """A segment after the first: expand 'TN' and 'UK'."""
        k = term_key(seg)
        if k in US_STATES:
            return US_STATES[k]
        return COUNTRY_WORDS.get(k, seg)

    def _find(self, segs):
        first = variants(segs[0])
        rest = [term_key(self._later(s)) for s in segs[1:]]
        hits = []
        for chain, vs in self.parts.items():
            if not (first & vs[0]):
                continue
            i, ok = 1, True
            for q in rest:
                while i < len(vs) and q not in vs[i]:
                    i += 1
                if i == len(vs):
                    ok = False
                    break
                i += 1
            if ok:
                hits.append(chain)
        return hits

    @staticmethod
    def _trailing_state(text):
        """'Los Angeles California' / 'Memphis TN' -> ['Los Angeles', 'California'] / ['Memphis', 'TN']."""
        words = text.split()
        for k in (3, 2, 1):
            if len(words) > k:
                tail = " ".join(words[-k:])
                if term_key(tail) in STATE_NAMES or (k == 1 and tail.isupper() and tail.lower() in US_STATES):
                    return [" ".join(words[:-k]), tail]
        return None

    def match(self, text, kind=""):
        text = one_line(text)
        res = Result(text)
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if not parts:
            return res
        attempts = [parts]
        if len(parts) == 1 and self._trailing_state(parts[0]):
            attempts.append(self._trailing_state(parts[0]))   # tried second: "West Virginia" is a place
        hits, via = [], ""
        for segs in attempts:
            hits = self._find(segs)
            if hits:
                via = "the dataset's own place"
                break
        key = term_key(text)
        if not hits and key in self.aliases:
            hits, via = [self.aliases[key]], "an alias in data/place_aliases.csv"
        if not hits and key in FORM_ALIASES:
            hits = self._find([s.strip() for s in FORM_ALIASES[key][0].split(",")])
            via = f"the abbreviation '{text}'"
        if not hits and key in self.terms and not self.terms[key].get("ask"):
            t = self.terms[key]
            chain = resolve(t["place"], t["category"], "us", self.places_ll)[0]
            if chain:
                hits, via = [chain], f"the term list ('{text}' = {t['mention'].lower()} for {t['place']})"
        hits = list(dict.fromkeys(hits))

        if kind and hits:
            same = [h for h in hits if self.info[h][2] == kind]
            if same:
                hits = same
            else:
                # e.g. "Georgia" as a Country when the dataset only has the state: don't put
                # the wrong pin in front of the reviewer, list what the dataset has instead
                res.status = "mismatch"
                res.candidates = sorted(hits, key=len)[:MAX_CANDIDATES]
                res.notes.append(f"the reader chose kind '{kind}', but the dataset has this name as "
                                 + " / ".join(f"'{self.info[h][2]}'" for h in res.candidates))
                return res
        if len(hits) == 1:
            res.status, res.chain, res.via = "matched", hits[0], via
            res.lat, res.lon, res.category = self.info[hits[0]]
        elif hits:
            res.status = "ambiguous"
            res.candidates = sorted(hits, key=len)[:MAX_CANDIDATES]
        return res

    # -------------------------------------------------------- the mention
    def mention(self, title, res):
        """(mention type, short reason); ('', reason) when the title doesn't say."""
        T = tokens(title)
        names = (variants(res.chain.split(", ")[0]) if res.chain
                 else variants(res.typed.split(",")[0]))
        if any(contains(T, n.split()) for n in names):
            return "Direct", "the title contains the place's name"
        if not res.chain:
            return "", "the title doesn't contain the place's name"
        found = {}
        for hit in extract(title, self.terms, self.blocked):
            t = self.terms[hit]
            if resolve(t["place"], t["category"], "us", self.places_ll)[0] == res.chain:
                found.setdefault(t["mention"], hit)
        for key, chain in self.aliases.items():
            if chain == res.chain and contains(T, key.split()):
                # an alias the term list also knows keeps the term list's call ("american" is a demonym)
                found.setdefault(self.terms[key]["mention"] if key in self.terms else "Nickname", key)
        for key, (text, pattern) in FORM_ALIASES.items():
            if self._find([s.strip() for s in text.split(",")]) == [res.chain] and re.search(pattern, title):
                found.setdefault("Nickname", re.search(pattern, title).group(0))
        for m in ("Direct", "Nickname", "Demonym"):
            if m in found:
                return m, f"the title has '{found[m]}', a {m.lower()} for this place"
        return "", "the title doesn't name the place in a way the term list knows"
