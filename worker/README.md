# Suggestion relay (Cloudflare Worker)

The map is a static page, so it can't hold a GitHub token. The "Get in touch"
form posts to this small Worker instead. The Worker checks the submission and
opens a GitHub issue labelled `user-submission`; from there the *Queue user
submission* workflow adds a row to `review/user_candidates.csv`, the same way
the weekly checks queue chart songs.

```
form on the map  ->  this Worker  ->  GitHub issue  ->  review/user_candidates.csv
                     (holds the token)                    (you review it, as usual)
```

The repo is public, so the issue is public: the form says so. It asks for no
email address.

## One-time setup (about 15 minutes)

The screens are described in general terms; Cloudflare and GitHub move their
menus around now and then.

**1. Make sure the label exists.** On GitHub: repo, **Issues**, **Labels**. There
should be one named exactly `user-submission` (create it if not). The workflow
only acts on issues carrying it.

**2. Create a GitHub token for the Worker.** GitHub, **Settings**, **Developer
settings**, **Personal access tokens**, **Fine-grained tokens**, **Generate new
token**.
- Resource owner: `cdr4321`
- Repository access: **Only select repositories**, then `songs-about-places`
- Repository permissions: **Issues: Read and write** (leave everything else
  at "No access"; GitHub adds read-only Metadata on its own)
- Expiration: as long as it allows (up to a year). Put a reminder in your
  calendar a couple of weeks before it ends; see "When the token expires".

Copy the token when GitHub shows it. You can't see it again.

**3. Create the Worker.** Cloudflare dashboard, **Workers & Pages**, **Create**,
**Create Worker**. Name it `songs-about-places-submit` and deploy the starter
code, then **Edit code**, replace everything with the contents of
`worker/submit-song.js`, and **Deploy**.

**4. Add the token as a secret.** In the Worker, **Settings**, **Variables and
Secrets**, **Add**, type **Secret**, name `GITHUB_TOKEN`, value the token from
step 2. Deploy again if it asks.

**5. Point the map at it.** Copy the Worker's address (it looks like
`https://songs-about-places-submit.<your-name>.workers.dev`) and paste it
between the quotes in `index.html`:

```js
const SUBMIT_URL='';
```

Commit that. Until it's set, the button just opens your contact page.

**6. Test it.** Open the map, click **Get in touch**, and send a suggestion
(use a made-up song). Within a minute you should see an issue titled
"Song suggestion: ...", a comment from the bot saying it was queued, and a new
row in `review/user_candidates.csv`. Then close the issue and delete the test
row (or put `n` in `approved` and commit).

## Optional: rate limiting

The code limits each visitor to 3 submissions a minute **if** a rate-limit
binding named `RATE_LIMITER` exists. `wrangler.toml` here defines one, so
deploying from the command line gets it:

```
cd worker
npx wrangler deploy
npx wrangler secret put GITHUB_TOKEN
```

You can instead add the same binding by hand in the dashboard (Worker,
**Settings**, **Bindings**). Without it the Worker still works; the other
protections still apply.

## What protects it

- It accepts requests only from the map's own addresses (`ALLOWED_ORIGINS`).
  That stops other websites from using it, but isn't a lock: someone writing a
  script can fake the header.
- A hidden form field that people never fill in; bots that do are quietly
  dropped.
- Length and format checks on every field, repeated on the server.
- `@` is written as an entity in the issue, so a submission can't notify
  anyone from your account.
- The token can only touch Issues on this one repo.
- GitHub's errors (and the token) never reach the browser; they stay in the
  Worker's logs.

Nothing goes on the map without you: the workflow only queues a row, and a
song is added after you put `y` in `approved` and commit.

## If spam gets through

Delete the issue and the queue row. If it keeps happening, tighten the rate
limit in `wrangler.toml`, or revoke the GitHub token (the form then shows a
"couldn't send" message until you make a new one).

## When the token expires

The form starts showing "Couldn't send that right now". Create a new token
as in step 2 and replace the `GITHUB_TOKEN` secret in the Worker (step 4).
Cloudflare's **Logs** tab for the Worker will show GitHub's reason.

## Changing what the form asks

The fields live in three places that have to agree: the form in `index.html`,
`validate()` and `issueBody()` in `submit-song.js`, and `HEADINGS` in
`scripts/add_user_submission.py`. The kinds of place must match `CATEGORIES`
in `scripts/common.py`.
