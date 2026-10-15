/**
 * Relay for the map's "Get in touch" suggestion form.
 *
 * The map is a static page, so it can't hold a GitHub token. The form posts here
 * instead; this Worker checks the submission and opens an issue labelled
 * `user-submission`, which the "Queue user submission" workflow then turns into a
 * row in review/user_candidates.csv. Setup is in worker/README.md.
 *
 * Settings (Cloudflare dashboard, or worker/wrangler.toml):
 *   GITHUB_TOKEN     secret: a fine-grained token with Issues read/write on the repo, nothing else
 *   GITHUB_REPO      "owner/name"; defaults to cdr4321/songs-about-places
 *   ALLOWED_ORIGINS  comma-separated sites allowed to post; defaults to the two below
 *   RATE_LIMITER     optional rate-limit binding (wrangler.toml sets one up)
 */

const DEFAULT_REPO = "cdr4321/songs-about-places";
const DEFAULT_ORIGINS = "https://cdr4321.github.io,https://www.chrisdallariva.com";

// must match CATEGORIES in scripts/common.py and the selector in index.html
const KINDS = ["City", "Country", "State/Region", "Continent/Region", "Street/Landmark",
  "Neighborhood/District", "Water/Natural Feature"];
const MAX = { title: 200, artist: 200, notes: 1500 };
const MAX_BODY = 8000; // bytes of JSON we are willing to read

// the headings are the contract with scripts/add_user_submission.py
const FOOTER = "\n\n---\n_Submitted through the form on the map._";

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGINS || DEFAULT_ORIGINS).split(",").map((s) => s.trim()).filter(Boolean);
    const originOk = allowed.includes(origin);
    const cors = originOk ? { "Access-Control-Allow-Origin": origin, Vary: "Origin" } : {};
    const reply = (status, data) =>
      new Response(JSON.stringify(data), {
        status,
        headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...cors },
      });

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: originOk ? 204 : 403,
        headers: {
          ...cors,
          "Access-Control-Allow-Methods": "POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type",
          "Access-Control-Max-Age": "86400",
        },
      });
    }
    if (request.method !== "POST") return reply(405, { error: "Method not allowed." });
    // Not a real lock (anyone can fake a header from a script), but it keeps other
    // websites from using this form as an open door.
    if (!originOk) return reply(403, { error: "This form only works from the map." });

    if (env.RATE_LIMITER) {
      const key = request.headers.get("CF-Connecting-IP") || "unknown";
      const { success } = await env.RATE_LIMITER.limit({ key });
      if (!success) return reply(429, { error: "Too many suggestions at once. Please wait a minute and try again." });
    }

    const raw = await request.text();
    if (raw.length > MAX_BODY) return reply(413, { error: "That submission is too long." });
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return reply(400, { error: "That submission couldn't be read." });
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      return reply(400, { error: "That submission couldn't be read." });
    }

    // hidden trap field: people never fill it in, bots usually do. Say thanks and drop it.
    if (typeof data.website === "string" && data.website.trim()) return reply(200, { ok: true });

    const clean = validate(data);
    if (clean.error) return reply(400, { error: clean.error });
    const s = clean.value;

    if (!env.GITHUB_TOKEN) {
      console.error("GITHUB_TOKEN is not set");
      return reply(500, { error: "The form isn't set up yet." });
    }
    const repo = env.GITHUB_REPO || DEFAULT_REPO;
    const issue = {
      title: `Song suggestion: ${s.title} — ${s.artist}`.slice(0, 120),
      body: issueBody(s),
      labels: ["user-submission"],
    };
    let res;
    try {
      res = await fetch(`https://api.github.com/repos/${repo}/issues`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
          "User-Agent": "songs-about-places-submit",
        },
        body: JSON.stringify(issue),
      });
    } catch (e) {
      console.error("GitHub request failed", String(e));
      return reply(502, { error: "Couldn't send that right now. Please try again in a bit." });
    }
    if (!res.ok) {
      // keep GitHub's reason in the Worker log, not in the browser
      console.error("GitHub said", res.status, (await res.text()).slice(0, 300));
      return reply(502, { error: "Couldn't send that right now. Please try again in a bit." });
    }
    return reply(200, { ok: true });
  },
};

/** One line of plain text: no control characters, runs of whitespace collapsed. */
function oneLine(v) {
  return String(v ?? "").replace(/[\u0000-\u001f\u007f\u2028\u2029]/g, " ").replace(/\s+/g, " ").trim();
}

function validate(d) {
  for (const k of ["title", "artist", "kind", "year", "notes"]) {
    if (d[k] != null && typeof d[k] !== "string") return { error: "That submission couldn't be read." };
  }
  const title = oneLine(d.title);
  const artist = oneLine(d.artist);
  const kind = oneLine(d.kind);
  const year = oneLine(d.year);
  const notes = String(d.notes ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f\u2028\u2029]/g, " ")
    .trim();

  if (!title) return { error: "Please enter the song title." };
  if (!artist) return { error: "Please enter the artist." };
  if (title.length > MAX.title) return { error: "That title is too long." };
  if (artist.length > MAX.artist) return { error: "That artist name is too long." };
  if (!KINDS.includes(kind)) return { error: "Please choose a kind of place." };
  if (year && !(/^\d{4}$/.test(year) && +year >= 1800 && +year <= 2100)) {
    return { error: "Please enter the release year as four digits, or leave it blank." };
  }
  if (notes.length > MAX.notes) return { error: "That note is too long." };
  return { value: { title, artist, kind, year, notes } };
}

/**
 * Written the way GitHub issue forms write theirs, so it reads well on the issue page
 * and scripts/add_user_submission.py can split it by heading. "@" is written as an
 * entity so a submission can't @-mention (and notify) anyone from this account.
 * The repo is public, so everything here is public too; the form says so.
 */
function issueBody(s) {
  const f = (v) => (v ? v.replace(/@/g, "&#64;") : "_No response_");
  return [
    "### Song title", "", f(s.title), "",
    "### Artist", "", f(s.artist), "",
    "### Kind of place", "", f(s.kind), "",
    "### Release year", "", f(s.year), "",
    "### Anything else I should know about this song?", "", f(s.notes),
  ].join("\n") + FOOTER;
}
