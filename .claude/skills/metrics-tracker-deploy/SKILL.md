---
name: metrics-tracker-deploy
description: Build, ship and operate the Shopdeck Metrics Tracker — node build.mjs, manual Vercel deploy, the five GitHub Actions workflows, secrets, and the git-clobber trap that silently reverts data files. Use when deploying a change, triggering or debugging a refresh run, or when data on the live site looks stale or reverted.
---

# Build, deploy & operations

## Build

```bash
node build.mjs
```
Extracts the `index.html` bundler template, Babel-compiles the inline JSX, writes `public/main.js`
+ resource chunks, and copies root `*.json` and `calls/` into `public/`.

- Success line ends with **`Babel dropped: true`**.
- `GOOGLE_SA_KEY not set — sheet-backed views will be empty` is **normal locally**.
- A JSX error prints a Babel stack trace and writes nothing. Fix before deploying.

## Deploy

Vercel deploys the **local working directory**, not a git ref (`vercel.json` has
`git.deploymentEnabled.main = false`). So a push alone ships nothing.

```bash
node build.mjs && npx --yes vercel@latest --prod --yes --token "$(cat ~/.vc_token)"
```
`hits-tracker.xyz` is auto-aliased to the newest production deploy. Look for
`▲ Aliased  https://hits-tracker.xyz` and `… ready.` in the output.

**This takes longer than 2 minutes** — give the command a ≥7 min timeout or it dies mid-deploy.

Two consequences of "deploys the working dir":
1. You can ship a hotfix without pushing (do push afterwards, or it gets overwritten).
2. If your working tree has a stale/reverted data file, **you will deploy the stale file**. Verify
   the local JSON before deploying.

## Verify after every deploy

The app sits behind Google login, so headless screenshots of prod aren't reliable. Curl the data:

```bash
curl -s "https://hits-tracker.xyz/bev_data.json" | python3 -c "
import json,sys; d=json.load(sys.stdin)
print(type(d['cards']['cohort']).__name__)          # must be dict, not str
print(d['generatedAt'])"
```
Assert the specific fields you changed. For UI changes, verify locally first via
`preview_start { name: 'metrics-public' }` (see `metrics-tracker-edit-view`).

## GitHub Actions

| Workflow | Schedule | Does |
|---|---|---|
| `refresh.yml` | nightly **02:30 UTC / 08:00 IST** | every pipeline in order (`bev_refresh` late, since it reads others' JSON), `snapshot.py`, commit `*.json`, deploy |
| `ts_refresh.yml` | every 3h | Troubleshoot + scaling |
| `gc_refresh.yml` | every 2h, 09:00–21:00 IST | GC assignment light refresh (`gc_view_refresh.py --light`) |
| `gm_daily.yml`, `nps_refresh.yml` | daily / 3-day | scoped refreshes |

All five end with a Vercel deploy. Trigger manually:
```bash
gh workflow run refresh.yml -R pawankumar-pkaytsk/shopdeck-metrics-tracker
```

**Secrets** (repo → Settings → Secrets → Actions): `METABASE_URL`, `METABASE_USER_EMAIL`,
`METABASE_PASSWORD`, `METABASE_API_KEY`, `GOOGLE_SA_KEY`, `VERCEL_TOKEN`.
Local-only, never committed: `~/metabase-arr-refresh/.mbcreds`, the Google SA key JSON, `~/.vc_token`.

Every pipeline step is `continue-on-error: true` — **a step can crash and still show green.**
Grep the step log for `Traceback` / `TypeError` before believing a run succeeded.

## The git-clobber trap (read this before touching a data file)

All five data-writing workflows share one concurrency group:

```yaml
concurrency:
  group: data-write
  cancel-in-progress: false
```

and each commit step does:

```bash
git add -A -- '*.json' snapshots
git commit -m "Refresh data …"
git pull --rebase -X ours origin main    # <-- the trap
git push origin HEAD:main
```

During a **rebase**, `-X ours` means the **upstream** side wins. So on a conflicting `*.json` a
run can discard its own freshly generated data. Symptoms: "data is not synced", numbers reverting
to yesterday, a fix you just deployed disappearing.

**When you hit it locally:**
- `git checkout --theirs <file>` keeps **your commit's** version during a rebase
  (confusing but correct), then `git add <file>` and `GIT_EDITOR=true git rebase --continue`.
- Do **not** use `git rebase -X ours origin/main` to resolve a push rejection on data files — it
  throws away your file. Prefer a plain `git rebase origin/main`, resolve explicitly, and
  re-verify the file content afterwards.
- Safe push loop:
  ```bash
  for i in 1 2 3; do
    git push -q origin HEAD:main && break
    git fetch -q origin main
    git rebase origin/main || { git checkout --theirs <data file>; git add -A; GIT_EDITOR=true git rebase --continue; }
  done
  python3 -c "import json;print(type(json.load(open('bev_data.json'))['cards']['cohort']).__name__)"
  ```
  **Always re-verify the data file after any rebase**, then deploy.

If a concurrent run clobbered a hand-patched file, the durable fix is to make the **pipeline**
correct and re-trigger the workflow, rather than re-patching the JSON by hand.

## Restoring a data file

Every nightly run commits the `*.json`, so git history is your backup:

```bash
for sha in $(git log --format=%H -12 -- bev_data.json); do
  echo "$sha $(git show --format=%ci -s $sha | cut -c1-16) \
    $(git show $sha:bev_data.json | python3 -c "import json,sys;print(type(json.load(sys.stdin)['cards']['cohort']).__name__)")"
done
git show <good-sha>:bev_data.json > /tmp/good.json    # then splice the key you need
```
`snapshots/YYYY-MM-DD/*.json.gz` (180-day retention, read from GitHub raw — excluded from Vercel)
is the other source.

## Local dev server

`.claude/launch.json` defines **`metrics-public`** → `python3 -m http.server 8099 --directory public`.
It serves `public/`, so **run `node build.mjs` first** or you'll test a stale bundle.

## Sizes to keep an eye on

`gc_detail_data.json` ~43 MB, `bev_data.json` ~4 MB, `gc_data.json` ~4.4 MB. Widening a
per-seller detail map can add tens of MB to what the browser downloads — scope such maps to the
sellers a view actually needs (e.g. KAE/KAM-managed rather than "anyone with any role").
