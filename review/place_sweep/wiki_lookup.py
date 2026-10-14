#!/usr/bin/env python3
"""Temporary helper for the place-name sweep: look each name in lookup_in.txt up
on English Wikipedia (following redirects) and record the article it lands on,
its short description, its coordinates and whether it's a disambiguation page.
Writes lookup_out.csv next to this file. Standard library only.
"""
import csv, json, os, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://en.wikipedia.org/w/api.php"
UA = "songs-about-places-place-sweep/1.0 (https://github.com/cdr4321/songs-about-places)"


def query(titles):
    params = dict(action="query", titles="|".join(titles), redirects="1", format="json",
                  formatversion="2", prop="coordinates|pageprops|description",
                  ppprop="disambiguation", colimit="max")
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # back off and retry
            print("retry", attempt, e)
            time.sleep(5 * (attempt + 1))
    return {}


def main():
    names = [x.strip() for x in open(os.path.join(HERE, "lookup_in.txt"), encoding="utf-8") if x.strip()]
    rows = []
    for i in range(0, len(names), 50):
        batch = names[i:i + 50]
        d = query(batch).get("query", {})
        hop = {}
        for n in d.get("normalized", []):
            hop[n["from"]] = n["to"]
        redir = {r["from"]: r["to"] for r in d.get("redirects", [])}
        pages = {p["title"]: p for p in d.get("pages", [])}
        for name in batch:
            t = hop.get(name, name)
            t = redir.get(t, t)
            p = pages.get(t, {})
            co = (p.get("coordinates") or [{}])[0]
            rows.append(dict(name=name, title=p.get("title", ""), missing="missing" in p or not p,
                             redirected=t != hop.get(name, name),
                             disambiguation="disambiguation" in (p.get("pageprops") or {}),
                             description=p.get("description", ""),
                             lat=co.get("lat", ""), lon=co.get("lon", "")))
        time.sleep(1)
    with open(os.path.join(HERE, "lookup_out.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(len(rows), "names;", sum(1 for r in rows if r["lat"]), "with coordinates")


if __name__ == "__main__":
    main()
