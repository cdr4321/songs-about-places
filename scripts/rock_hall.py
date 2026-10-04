"""
Who counts as a Rock & Roll Hall of Fame inductee in an artist credit.

review/rock_hall/inductees.csv lists every inductee (performers, early
influences and musical-excellence inductees) with the other names their
records are credited under. A credit counts when an inductee is its lead
act, a billed partner ("Marvin Gaye and Tammi Terrell", "Paul McCartney and
Wings") or a featured guest ("Drake ft. Jay-Z"). Names must match whole:
"Jon Bon Jovi" is not Bon Jovi, "The Guess Who" is not The Who, "Celine
Dion" is not Dion. `credit_years` keeps namesakes apart: the Rock Hall's
Jimmie Rodgers died in 1933, so a 1958 "Jimmie Rodgers" record is the pop
singer of the same name.
"""
import csv, os, re, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDUCTEES = os.path.join(ROOT, "review", "rock_hall", "inductees.csv")

SEP = r"(?:\s+(?:ft|feat|featuring|with|and|x|vs|versus|presents|meets)\s+|\s*[,/;+]\s*)"


def cnorm(s):
    """Credit text reduced to lowercase words; commas and slashes kept as separators."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"(?<=\w)/(?=\w)", "", s)          # AC/DC
    s = re.sub(r"[.'’\"]", "", s)            # B.B., R.E.M., Guns N' Roses
    s = re.sub(r"[^a-z0-9,/;+ ]", " ", s)         # hyphens etc. become spaces
    s = re.sub(r"\s*([,/;+])\s*", r" \1 ", s)
    return re.sub(r"\s+", " ", s).strip()


def _variant(name):
    return re.sub(r"^the ", "", cnorm(name))


class Inductees:
    def __init__(self, path=INDUCTEES):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            self.rows = list(csv.DictReader(fh))
        self.pats = []
        self.years = {}
        for r in self.rows:
            lo, _, hi = (r.get("credit_years") or "").partition("-")
            self.years[r["inductee"]] = (int(lo) if lo.strip() else 0, int(hi) if hi.strip() else 9999)
            names = [r["inductee"]] + [x.strip() for x in r.get("credit_names", "").split(";") if x.strip()]
            for v in sorted({_variant(n) for n in names if _variant(n)}, key=len, reverse=True):
                body = r"(?:\s+|\s*,\s*)".join(map(re.escape, re.findall(r"[a-z0-9]+", v)))
                # "Hank Williams, Jr." is the son, not the inductee
                self.pats.append((r["inductee"], re.compile(
                    rf"(?:^|{SEP})(?:the\s+)?{body}(?!\s*,?\s*(?:jr|ii|iii)\b)(?=$|{SEP})")))

    def in_credit(self, artist, year=None):
        """Inductees named in an artist credit, in the order found."""
        c = cnorm(artist)
        found = []
        for name, p in self.pats:
            if name in found or not p.search(c):
                continue
            lo, hi = self.years[name]
            if year and str(year).isdigit() and not lo <= int(year) <= hi:
                continue
            found.append(name)
        return found
