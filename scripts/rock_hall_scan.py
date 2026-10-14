#!/usr/bin/env python3
"""
Pull the studio-album, EP and single track lists of every Rock & Roll Hall of
Fame inductee from MusicBrainz, so place-songs can be found in them.

Runs as a GitHub Action (.github/workflows/rock-hall-scan.yml) in three steps:

  resolve   review/rock_hall/inductees.csv -> artists.csv, release_groups.csv
            Each inductee's Wikipedia page gives its Wikidata item, whose
            MusicBrainz ID (P434) is the artist to scan. Extra entities from
            the `extra` column, and related MusicBrainz artists whose name
            contains the inductee's ("Bob Seger & The Silver Bullet Band"),
            are scanned too. Every album/EP/single release group is listed;
            only those with no secondary type except Soundtrack are kept
            (no live albums, compilations, remixes, mixtapes, demos...).
  scan      one shard of the kept release groups -> tracks_<shard>.csv
            For each release group, the earliest official release's track
            list (fewest tracks on a tie, so deluxe bonus tracks stay out).
  combine   shards -> review/rock_hall/tracks.csv.gz and scan_summary.txt

MusicBrainz allows one request a second per IP, so each process waits
1.1 s between calls and backs off on 503s. Standard library only.
"""
import csv, gzip, glob, json, os, re, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "review", "rock_hall")
OUT = os.environ.get("SCAN_OUT", os.path.join(ROOT, "scan_out"))
UA = "songs-about-places-rock-hall-scan/1.0 ( https://github.com/cdr4321/songs-about-places )"
MB = os.environ.get("MB_ROOT", "https://musicbrainz.org/ws/2")
WP = os.environ.get("WP_API", "https://en.wikipedia.org/w/api.php")
WD = os.environ.get("WD_API", "https://www.wikidata.org/w/api.php")
KEEP_PRIMARY = {"Album", "EP", "Single"}
OK_SECONDARY = {"Soundtrack"}
GAP = float(os.environ.get("MB_GAP", "1.1"))
# stop a shard cleanly before GitHub's 6-hour job limit; unfinished groups are listed as failed
DEADLINE = time.time() + 60 * float(os.environ.get("SCAN_MINUTES", "320"))

_last = [0.0]
log_lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    log_lines.append(s)


def get(url, mb=True, tries=8):
    """GET JSON. MusicBrainz calls are spaced GAP seconds apart."""
    for k in range(tries):
        if mb:
            wait = _last[0] + GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 400:
                raise
            delay = min(120, 2 ** (k + 1))
            log(f"  HTTP {e.code} on {url[:120]} — retry in {delay}s")
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            delay = min(120, 2 ** (k + 1))
            log(f"  {type(e).__name__} on {url[:120]} — retry in {delay}s")
            time.sleep(delay)
    raise RuntimeError(f"gave up on {url}")


def mb(path, **params):
    params["fmt"] = "json"
    return get(f"{MB}/{path}?{urllib.parse.urlencode(params, safe='|+:')}")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("‐", "-")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def credit(ac):
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in ac or [])


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ resolve
def wiki_to_mbids(titles):
    """Wikipedia title -> list of MusicBrainz artist IDs, via Wikidata P434."""
    titles = sorted(set(t for t in titles if t))
    qid = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        d = get(f"{WP}?" + urllib.parse.urlencode({
            "action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
            "redirects": 1, "format": "json", "titles": "|".join(batch)}), mb=False) or {}
        q = d.get("query", {})
        nm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        rd = {n["from"]: n["to"] for n in q.get("redirects", [])}
        page = {p.get("title"): p.get("pageprops", {}).get("wikibase_item")
                for p in q.get("pages", {}).values()}
        for t in batch:
            t1 = nm.get(t, t)
            item = page.get(rd.get(t1, t1))
            if item:
                qid[t] = item
        time.sleep(0.3)
    items = sorted(set(qid.values()))
    mbids = {}
    for i in range(0, len(items), 50):
        d = get(f"{WD}?" + urllib.parse.urlencode({
            "action": "wbgetentities", "ids": "|".join(items[i:i + 50]),
            "props": "claims", "format": "json"}), mb=False) or {}
        for q, ent in d.get("entities", {}).items():
            vals = []
            for c in ent.get("claims", {}).get("P434", []):
                v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
                if v and c.get("rank") != "deprecated":
                    vals.append(v)
            mbids[q] = vals
        time.sleep(0.3)
    return {t: mbids.get(qid.get(t), []) for t in titles}, qid


def search_artist(name):
    q = name.replace("\\", " ").replace('"', '\\"')
    try:
        d = mb("artist", query=f'artist:"{q}"', limit=10) or {}
    except Exception as e:
        log(f"  artist search failed for {name!r}: {e}")
        return []
    return [a for a in d.get("artists", []) if norm(a["name"]) == norm(name)]


def related(mbid):
    d = mb(f"artist/{mbid}", inc="artist-rels") or {}
    rel = []
    for r in d.get("relations", []):
        a = r.get("artist")
        if a:
            rel.append((a["id"], a["name"], r.get("type", ""), a.get("disambiguation", "")))
    return d, rel


def browse_rgs(mbid):
    out, off = [], 0
    while True:
        d = mb("release-group", artist=mbid, type="album|single|ep", inc="artist-credits",
               limit=100, offset=off) or {}
        rgs = d.get("release-groups", [])
        out += rgs
        off += len(rgs)
        if not rgs or off >= d.get("release-group-count", 0):
            return out


def resolve():
    ind = read(os.path.join(DIR, "inductees.csv"))
    wiki_titles = [r["wikipedia"] for r in ind]
    for r in ind:
        for x in filter(None, (s.strip() for s in r["extra"].split(";"))):
            if x.startswith("wiki:"):
                wiki_titles.append(x[5:].split("|")[0].strip())
    w2m, qid = wiki_to_mbids(wiki_titles)
    log(f"Wikipedia/Wikidata: {sum(1 for v in w2m.values() if v)} of {len(w2m)} titles have a MusicBrainz ID")

    artists, rgs = [], []
    seen_rg = {}
    for n, r in enumerate(ind, 1):
        name = r["inductee"]
        log(f"[{n}/{len(ind)}] {name}")
        targets = []  # (mbid, role, how, note)
        mains = w2m.get(r["wikipedia"], [])
        how = f"wikidata {qid.get(r['wikipedia'], '?')}"
        if not mains:
            hits = search_artist(name)
            mains = [hits[0]["id"]] if hits else []
            how = "musicbrainz name search (check)" if hits else "NOT FOUND"
        for m in mains:
            targets.append((m, "main", how, ""))
        rels = []
        main_names = []
        for m in mains:
            d, rl = related(m)
            rels += rl
            main_names.append(d.get("name", ""))
        rel_ids = {x[0] for x in rels}
        # related artists whose name contains the inductee's: billed variants of the act
        keys = {norm(x) for x in main_names + [name] if len(norm(x)) >= 3}
        for rid, rname, rtype, _ in rels:
            nr = norm(rname)
            if rid not in mains and any(re.search(rf"\b{re.escape(k)}\b", nr) for k in keys):
                targets.append((rid, "extra", f"related ({rtype}), name contains inductee", ""))
        for x in filter(None, (s.strip() for s in r["extra"].split(";"))):
            spec, _, note = x.partition("|")
            spec, note = spec.strip(), note.strip()
            if spec.startswith("wiki:"):
                for m in w2m.get(spec[5:], []):
                    targets.append((m, "extra", f"wikidata ({spec[5:]})", note))
                if not w2m.get(spec[5:]):
                    log(f"  extra {spec} has no MusicBrainz ID")
            elif spec.startswith("name:"):
                hits = search_artist(spec[5:])
                pick = [h for h in hits if h["id"] in rel_ids] or hits[:1]
                for h in pick:
                    rel = "related" if h["id"] in rel_ids else "UNRELATED name match (check)"
                    targets.append((h["id"], "extra", f"name search, {rel}", note))
                if not hits:
                    log(f"  extra {spec} not found")
        done = set()
        for mbid, role, how_, note in targets:
            if mbid in done:
                continue
            done.add(mbid)
            try:
                found = browse_rgs(mbid)
            except Exception as e:  # keep going; the summary lists it
                log(f"  release groups failed for {mbid}: {e}")
                found = []
            kept = 0
            mb_name = ""
            for g in found:
                prim = g.get("primary-type") or ""
                sec = g.get("secondary-types") or []
                keep = prim in KEEP_PRIMARY and set(sec) <= OK_SECONDARY
                ac = credit(g.get("artist-credit"))
                for c in g.get("artist-credit") or []:
                    if c.get("artist", {}).get("id") == mbid:
                        mb_name = mb_name or c["artist"]["name"]
                if not keep:
                    continue
                kept += 1
                rgs.append(dict(inductee=name, scanned_mbid=mbid, role=role, note=note,
                                rg_id=g["id"], rg_title=g.get("title", ""), primary_type=prim,
                                secondary_types="; ".join(sec),
                                first_release_date=g.get("first-release-date", ""),
                                rg_credit=ac))
                seen_rg[g["id"]] = 1
            artists.append(dict(inductee=name, year=r["year"], category=r["category"], role=role,
                                mbid=mbid, mb_name=mb_name, how=how_, note=note,
                                release_groups=len(found), kept=kept))
            log(f"  {role} {mbid} {mb_name!r}: {len(found)} release groups, {kept} kept")
        if not targets:
            artists.append(dict(inductee=name, year=r["year"], category=r["category"], role="main",
                                mbid="", mb_name="", how="NOT FOUND", note="", release_groups=0, kept=0))
    write(os.path.join(DIR, "artists.csv"),
          ["inductee", "year", "category", "role", "mbid", "mb_name", "how", "note",
           "release_groups", "kept"], artists)
    write(os.path.join(DIR, "release_groups.csv"),
          ["inductee", "scanned_mbid", "role", "note", "rg_id", "rg_title", "primary_type",
           "secondary_types", "first_release_date", "rg_credit"], rgs)
    log(f"{len(artists)} artists, {len(rgs)} inductee/release-group pairs, {len(seen_rg)} unique release groups")
    with open(os.path.join(OUT, "resolve_log.txt") if os.path.isdir(OUT) else os.devnull, "w") as fh:
        fh.write("\n".join(log_lines))


# --------------------------------------------------------------------- scan
STATUS_RANK = {"Official": 0, "Promotion": 1}


def pick_release(releases, first_date):
    def key(rel):
        d = rel.get("date") or "9999"
        tracks = sum(m.get("track-count", len(m.get("tracks", []))) for m in rel.get("media", []))
        return (STATUS_RANK.get(rel.get("status") or "", 2),
                0 if first_date and d.startswith(first_date[:4]) else 1,
                d.ljust(10, "9"), tracks)
    ok = [r for r in releases if (r.get("status") or "") not in ("Bootleg", "Pseudo-Release", "Withdrawn", "Cancelled")]
    return min(ok, key=key) if ok else None


def scan(shard, of):
    os.makedirs(OUT, exist_ok=True)
    rows = read(os.path.join(DIR, "release_groups.csv"))
    first = {}
    for r in rows:
        first.setdefault(r["rg_id"], r["first_release_date"])
    ids = sorted(first)[shard::of]
    log(f"shard {shard}/{of}: {len(ids)} release groups")
    out, fails = [], []
    for n, rg in enumerate(ids, 1):
        if time.time() > DEADLINE:
            log(f"  time budget used up; {len(ids) - n + 1} release groups left for a rerun")
            fails += ids[n - 1:]
            break
        rels = None
        for lim in (100, 25, 5):
            try:
                d = mb("release", **{"release-group": rg}, inc="recordings+artist-credits",
                       limit=lim)
                rels = (d or {}).get("releases", [])
                break
            except urllib.error.HTTPError:
                continue
            except RuntimeError as e:
                log(f"  {rg}: {e}")
                break
        if rels is None:
            fails.append(rg)
            continue
        rel = pick_release(rels, first[rg])
        if not rel:
            out.append(dict(rg_id=rg, release_id="", release_status="none usable"))
            continue
        for m in rel.get("media", []):
            for t in m.get("tracks", []):
                out.append(dict(rg_id=rg, release_id=rel["id"], release_title=rel.get("title", ""),
                                release_date=rel.get("date", ""), release_status=rel.get("status", ""),
                                release_country=rel.get("country", ""), medium=m.get("position", ""),
                                format=m.get("format", ""), track=t.get("position", ""),
                                title=t.get("title", ""), track_credit=credit(t.get("artist-credit")),
                                recording_id=(t.get("recording") or {}).get("id", "")))
        if n % 100 == 0:
            log(f"  {n}/{len(ids)}")
    cols = ["rg_id", "release_id", "release_title", "release_date", "release_status",
            "release_country", "medium", "format", "track", "title", "track_credit", "recording_id"]
    write(os.path.join(OUT, f"tracks_{shard}.csv"), cols, out)
    with open(os.path.join(OUT, f"failed_{shard}.txt"), "w") as fh:
        fh.write("\n".join(fails))
    log(f"shard {shard}: {len(out)} track rows, {len(fails)} failed release groups")


# ------------------------------------------------------------------ combine
def combine():
    parts = sorted(glob.glob(os.path.join(OUT, "**", "tracks_*.csv"), recursive=True))
    rows, cols = [], None
    for p in parts:
        with open(p, encoding="utf-8", newline="") as fh:
            rd = csv.DictReader(fh)
            cols = cols or rd.fieldnames
            rows += list(rd)
    fails = []
    for p in glob.glob(os.path.join(OUT, "**", "failed_*.txt"), recursive=True):
        fails += [x for x in open(p).read().split() if x]
    rows.sort(key=lambda r: (r["rg_id"], r.get("medium", ""), r.get("track", "").zfill(4)))
    path = os.path.join(DIR, "tracks.csv.gz")
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols or ["rg_id"])
        w.writeheader()
        w.writerows(rows)
    rgs = read(os.path.join(DIR, "release_groups.csv"))
    got = {r["rg_id"] for r in rows if r.get("release_id")}
    want = {r["rg_id"] for r in rgs}
    summary = [f"shards: {len(parts)}",
               f"release groups wanted: {len(want)}",
               f"release groups with a track list: {len(got)}",
               f"release groups that failed (rerun to fill): {len(set(fails))}",
               f"track rows: {len(rows)}"]
    with open(os.path.join(DIR, "scan_summary.txt"), "w") as fh:
        fh.write("\n".join(summary) + "\n\nfailed:\n" + "\n".join(sorted(set(fails))) + "\n")
    print("\n".join(summary))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "resolve":
        os.makedirs(OUT, exist_ok=True)
        resolve()
    elif cmd == "scan":
        scan(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "combine":
        combine()
    else:
        sys.exit("usage: rock_hall_scan.py resolve | scan SHARD OF | combine")
