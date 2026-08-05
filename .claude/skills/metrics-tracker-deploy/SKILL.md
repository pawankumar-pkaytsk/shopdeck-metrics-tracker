---
name: metrics-tracker-deploy
description: Build, ship and operate the Shopdeck Metrics Tracker — node build.mjs, manual Vercel deploy, the five GitHub Actions workflows, secrets, and the git-clobber trap that silently reverts data files. Use when deploying a change, triggering or debugging a refresh run, or when data on the live site looks stale or reverted.
---

# Build, deploy & operations

## Build

```bash
node build.mjs
```
Extracts the `index.html` bundler template, Babel-compiles the inline JSX, writes `public/main.js` +
resource chunks, and copies root `*.json` and `calls/` into `public/`.

- Success line ends with **`Babel dropped: true`**.
- `GOOGLE_SA_KEY not set — sheet-backed views will be empty` is **normal locally**. On Vercel the 4
  sheets are fetched (`sheet spendinputs.json: … rows` etc.), which is why some views only render on
  production.
- A JSX error prints a Babel stack trace and writes nothing. Fix before deploying.

## Deploy

Vercel deploys the **local working directory**, not a git ref (`vercel.json` has
`git.deploymentEnabled.main = false`). So a push alone ships nothing.

```bash
node build.mjs && npx --yes vercel@latest --prod --yes --token "$(cat ~/.vc_token)"
```
`hits-tracker.xyz` is auto-aliased to the newest production deploy. Look for
`▲ Aliased  https://hits-tracker.xyz` and `"readyState": "READY"`.

**This takes longer than 2 minutes** — give the command a ≥7 min timeout or it dies mid-deploy.

Two consequences of "deploys the working dir":
1. You can ship a hotfix without pushing (do push afterwards, or it gets overwritten).
2. If your working tree has a stale/reverted data file, **you will deploy the stale file.** Verify the
   local JSON *before* deploying, and again after any rebase.

## Verify after every deploy — three checks

```bash
# 1. the data that shipped
curl -s https://hits-tracker.xyz/bev_data.json | python3 -c "
import json,sys; d=json.load(sys.stdin)
print(type(d['cards']['cohort']).__name__)          # must be dict, not str
print(d['generatedAt'])"

# 2. the bundle contains your change (remember Babel escapes · and —)
curl -s https://hits-tracker.xyz/main.js | grep -c 'SpendArrTrend'

# 3. the deployed bundle IS the one you verified locally
curl -s https://hits-tracker.xyz/main.js > /tmp/live.js
shasum -a 256 public/main.js /tmp/live.js | awk '{print $1}' | uniq | wc -l   # 1 = identical
```
Assert the specific fields you changed. Check 3 is the one that catches "I deployed from a stale tree".

## GitHub Actions

| Workflow | Schedule | Does |
|---|---|---|
| `refresh.yml` | nightly **02:30 UTC / 08:00 IST** | every pipeline in order (`bev_refresh` late, since it reads others' JSON), `snapshot.py`, commit `*.json`, deploy |
| `ts_refresh.yml` | every 3h | Troubleshoot + scaling |
| `gc_refresh.yml` | every 2h, 09:00–21:00 IST | GC assignment light refresh |
| `gm_daily.yml`, `nps_refresh.yml` | daily / 3-day | scoped refreshes |

All five end with a Vercel deploy. Trigger manually:
```bash
gh workflow run refresh.yml -R pawankumar-pkaytsk/shopdeck-metrics-tracker
```

**Secrets** (repo → Settings → Secrets → Actions): `METABASE_URL`, `METABASE_USER_EMAIL`,
`METABASE_PASSWORD`, `METABASE_API_KEY`, `GOOGLE_SA_KEY`, `VERCEL_TOKEN`.
Local-only, never committed: `~/metabase-arr-refresh/.mbcreds`, the Google SA key JSON, `~/.vc_token`.

Every pipeline step is `continue-on-error: true` — **a step can crash and still show green.** Grep the
step log for `Traceback` / `TypeError` before believing a run succeeded. A quota-failed step often
prints a clean-looking `0/N` line instead of an error.

## The git-clobber trap (read this before touching a data file)

All five data-writing workflows share one concurrency group (`group: data-write`), and each commit
step does:

```bash
git add -A -- '*.json' snapshots
git commit -m "Refresh data …"
git pull --rebase -X ours origin main    # <-- the trap
git push origin HEAD:main
```

During a **rebase**, `-X ours` means the **upstream** side wins. So on a conflicting `*.json` a run
can discard its own freshly generated data. Symptoms: "data is not synced", numbers reverting to
yesterday, a fix you just deployed disappearing.

**This has actually bitten.** A push-retry loop using a blanket `git checkout --theirs .` threw away
three nightlies of `bev_data.json` and dropped the HIT2 count from 42 to 38.

**The rule: on a data-file conflict, keep the NEWER data, then re-verify it.** Don't reach for a
blanket strategy flag — decide per file, and prefer taking upstream's data file and re-applying your
own change on top:

```bash
git fetch origin main
git log --oneline HEAD..origin/main          # what did they actually change?
git show --stat origin/main                  # data files, or code?

# on a data-file conflict during rebase, take upstream's (the nightly's) copy explicitly:
git show origin/main:bev_data.json > bev_data.json
git add bev_data.json
GIT_EDITOR=true git rebase --continue
# then RE-APPLY your data change on top (re-run the pipeline / the injection script)
```

Then always:
```bash
python3 -c "import json;d=json.load(open('bev_data.json'));print(d['generatedAt'])"
```
**Re-verify the data file after any rebase, before deploying.**

If a concurrent run clobbered a hand-patched file, the durable fix is to make the **pipeline** correct
and re-trigger the workflow, rather than re-patching the JSON by hand.

## Restoring a data file

Every nightly run commits the `*.json`, so git history is your backup:

```bash
for sha in $(git log --format=%H -12 -- bev_data.json); do
  echo "$sha $(git show --format=%ci -s $sha | cut -c1-16) \
    $(git show $sha:bev_data.json | python3 -c "import json,sys;print(json.load(sys.stdin)['generatedAt'])")"
done
git show <good-sha>:bev_data.json > /tmp/good.json    # then splice the key you need
```
`snapshots/YYYY-MM-DD/*.json.gz` (180-day retention, read from GitHub raw — excluded from Vercel) is
the other source.

## Patching a data file without a full pipeline run

`bev_refresh.py` is 15–24 min and quota-exposed, so a targeted fix is often a small standalone script
that loads `bev_data.json`, recomputes one key, and writes it back. **Always make the pipeline change
too** — otherwise the next nightly reverts your patch. Pattern used for `bev2.dodTrend` and the
cohort-denominator rebase: identical logic in both places, pipeline first, then the offline injection
so the site updates immediately.

## Long-running commands

Anything over ~10 min gets moved to the background. To wait on it, use an `until` loop in a
backgrounded Bash call rather than chained `sleep`s:
```bash
until ! pgrep -f "google_sellers_refresh" > /dev/null; do sleep 10; done; echo done
```
If a pipeline is stuck in a retry storm, kill it (`pkill -f <name>`), fix the retry logic, and re-run
— don't wait it out. A 4xx must never be retried (see `metrics-tracker-pipeline`).

## Local dev server

`.claude/launch.json` defines **`metrics-public`** → serves `public/`. **Run `node build.mjs` first**
or you'll test a stale bundle. Note the browser pane is frequently hidden, which gives the page zero
layout width — see the verification notes in `metrics-tracker-edit-view`.

## Sizes to keep an eye on

`gc_detail_data.json` ~43 MB, `bev_data.json` ~5 MB, `gc_data.json` ~4.4 MB. Widening a per-seller
detail map can add tens of MB to what the browser downloads — scope such maps to the sellers a view
actually needs.
