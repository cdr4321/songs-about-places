#!/usr/bin/env python3
"""
Queue a reader's song suggestion for review.

The map's suggestion form (relayed by worker/submit-song.js) opens a GitHub
issue labelled `user-submission`. The *Queue user submission* workflow runs
this script on that issue. It adds one row to review/user_candidates.csv, the
same kind of review queue the weekly chart checks fill, and writes a short
comment for the issue.

Only the song itself goes into the queue. The reader's free-text note stays in
the issue and never reaches a CSV in the repo.

Reads, from the environment:
  ISSUE_BODY      the issue's body text
  ISSUE_NUMBER    the issue number
  ISSUE_URL       the issue's web address
  COMMENT_FILE    where to write the comment (optional)

The issue body is untrusted text from the public, so it is only ever parsed
here, never run or interpolated into a shell command.
"""
import datetime, os, re, sys
from common import (CATEGORIES, SongIndex, gh_output, load_queue, load_songs, same_artist,
                    save_queue, squash)

# in the order the relay writes them; the heading text is the contract with worker/submit-song.js
HEADINGS = [("title", "Song title"), ("artist", "Artist"), ("kind", "Kind of place"),
            ("year", "Release year"),
            ("notes", "Anything else I should know about this song?")]
FOOTER = "\n---\n_Submitted through the form on the map._"
NO_RESPONSE = "_No response_"


def parse(body):
    """Split the issue body into its fields, or return None if it isn't one of ours.
    Each heading must follow a line break, so a title can't smuggle in a fake heading
    (the relay strips line breaks from the single-line fields)."""
    body = "\n" + body.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    marks, pos = [], 0
    for key, heading in HEADINGS:
        tag = f"\n### {heading}\n"
        i = body.find(tag, pos)
        if i < 0:
            return None
        marks.append((key, i, i + len(tag)))
        pos = i + len(tag)
    out = {}
    for n, (key, _, start) in enumerate(marks):
        end = marks[n + 1][1] if n + 1 < len(marks) else len(body)
        out[key] = body[start:end].strip()
    cut = out["notes"].rfind(FOOTER.strip())
    if cut >= 0:
        out["notes"] = out["notes"][:cut].strip()
    for k, v in out.items():
        # the relay writes "@" as an entity so a submission can't @-mention anyone
        out[k] = "" if v == NO_RESPONSE else v.replace("&#64;", "@")
    return out


def one_line(s):
    return re.sub(r"\s+", " ", s).strip()


def main():
    fields = parse(os.environ.get("ISSUE_BODY", ""))
    if fields is None:
        print("ERROR: the issue body doesn't have the suggestion form's fields; nothing queued.")
        return 1

    problems = []
    title, artist = one_line(fields["title"]), one_line(fields["artist"])
    # a leading = + @ - makes Excel read a cell as a formula
    for name, val in (("title", title), ("artist", artist)):
        if val and val[0] in "=+@-":
            problems.append(f"{name} started with '{val[0]}' (removed; check it)")
    title, artist = title.lstrip("=+@- "), artist.lstrip("=+@- ")
    if not title or not artist:
        print("ERROR: the issue has no title or no artist; nothing queued.")
        return 1

    kind = one_line(fields["kind"])
    if kind not in CATEGORIES:
        problems.append(f"kind of place '{kind[:40]}' isn't one of the site's categories; set place_category")
        kind = ""

    year = one_line(fields["year"])
    if year and not (re.fullmatch(r"\d{4}", year) and 1800 <= int(year) <= 2100):
        problems.append(f"release year '{year[:12]}' isn't a 4-digit year; left blank")
        year = ""

    number, url = os.environ.get("ISSUE_NUMBER", ""), os.environ.get("ISSUE_URL", "")

    cols, rows = load_songs()
    queue = load_queue("user")
    dups = SongIndex(rows).rows_for(title, artist)
    if dups:
        where = "; ".join(sorted({r["place"] for r in dups})[:3])
        problems.append(f"already in data/songs.csv under {where}")
    if any(squash(q["title"]) == squash(title) and same_artist(q["artist"], artist) for q in queue):
        problems.append("already waiting in review/user_candidates.csv")

    note = (f"USER SUBMISSION (#{number}); write the full place chain in place, fill latitude and "
            "longitude, check the year. Any note from the reader is in the issue.")
    if problems:
        note += " Check: " + "; ".join(problems) + "."
    queue.append({"chart": "user", "chart_week": datetime.date.today().isoformat(), "year": year,
                  "title": title, "artist": artist, "weekly_peak": "", "place": "",
                  "place_category": kind, "mention_type": "", "matched_text": "",
                  "latitude": "", "longitude": "", "note": note, "approved": "",
                  "submission": url})
    save_queue("user", queue)
    print(f"queued '{title}' by {artist} from issue #{number}; "
          f"review/user_candidates.csv now holds {len(queue)} row(s)")

    lines = ["Queued in `review/user_candidates.csv` for review.", ""]
    if problems:
        lines += ["**Check:**"] + [f"- {p}" for p in problems] + [""]
    lines += ["**To publish:** open the file, write the full place chain in `place`, fill "
              "`latitude` and `longitude`, check `year`, put `y` in `approved` (or `n` to drop "
              "it) and commit. *Apply approved candidates* then adds the song, flagged as a user "
              "submission."]
    path = os.environ.get("COMMENT_FILE")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    gh_output(queued=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
