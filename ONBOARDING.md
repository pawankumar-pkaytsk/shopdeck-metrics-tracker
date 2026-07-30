# Shopdeck Metrics Tracker — Onboarding

Everything a new owner needs to run, change, and operate this dashboard **without back-and-forth**.

## 1. What it is
A static React-in-HTML analytics dashboard for the HITS / 1k-5k team.
- **Live:** https://hits-tracker.xyz (also www.hits-tracker.xyz)
- **Repo:** https://github.com/pawankumar-pkaytsk/shopdeck-metrics-tracker (branch `main`)
- **Hosting:** Vercel project `shopdeck-dashboard` (org `team_Nv7Bqm9rCMXGjFA018pplRCW`)
- Sections include Leadership → Bird's Eye View (1k-5k overview, ARR cohort, TvA, churn, platform metrics), Output/ARR, GC/GM views, NPS, Troubleshoot, Marketing Ops.

## 2. Access to request (do these first)
1. **GitHub**: repo collaborator (write) on `pawankumar-pkaytsk/shopdeck-metrics-tracker`.
2. **Vercel**: member of the org that owns project `shopdeck-dashboard` (only needed for manual deploys / domain; CI deploys without it).
3. **Metabase** (`https://metabase.kaip.in`): an **API key** on an account with the 500 GB BigQuery quota (see §7). This is the single most important credential.
4. **Google Sheets**: a service-account with read access to the "Collated" / "HITS 2 Handover" sheets (used by the TvA/cohort views).

The GitHub repo already holds all CI secrets, so **once you have repo access, the nightly refresh + deploy work with no local setup.** Local setup (§6) is only needed to test pipeline changes on your machine.

## 3. Architecture / data flow
```
Metabase cards ──(pipelines/*.py, nightly)──> *.json in repo root
                                                     │
index.html (<script type="__bundler/template"> = JSON-encoded HTML+JSX)
      │  build.mjs compiles inline JSX ──> public/main.js + copies *.json into public/
      ▼
Vercel (buildCommand: node build.mjs, outputDirectory: public) ──> https://hits-tracker.xyz
```
- **Frontend is 100% static.** The browser fetches the pre-generated `*.json` (e.g. `bev_data.json`, `arr_data.json`) at runtime. No backend.
- **`index.html`** holds the entire app inside one `<script type="__bundler/template">` tag whose content is a **JSON-encoded** string of HTML+JSX. You cannot hand-edit it as plain text — edit it with the Python pattern in §5.
- **`build.mjs`** extracts that template, compiles the inline JSX (Babel), writes `public/main.js` + resource chunks, and copies top-level `*.json` and `calls/` into `public/`.

## 4. Data pipelines (`pipelines/*.py`)
Each pipeline fetches Metabase card(s) and writes one `*.json`. They authenticate with the **Metabase API key** (header `x-api-key`) when `METABASE_API_KEY` is set (CI env or local `~/metabase-arr-refresh/.mbcreds`), else fall back to session login.

Key ones:
- `bev_refresh.py` → `bev_data.json` — the big Bird's Eye View builder (channel ARR/spend, ARR cohort, TvA, churn comparison, ARR buckets, platform metrics, Google metrics). **Heaviest pipeline (~15–18 min).** Reads other local `*.json` (ts/scaling/task/etc.) + Metabase + Google Sheets.
- `output_refresh.py` → `arr_data.json` — ARR Output dashboard.
- `ts_refresh.py`, `scaling_refresh.py`, `task_refresh.py`, `golive_refresh.py`, `hit1/hit2_refresh.py`, `gc_view/gm_view/gm_compliance/lt_refresh.py`, `nps_refresh.py`, `markops_refresh.py`, `bucket/callback_sla/calls/hypercare_refresh.py`, `snapshot.py`.

**Metabase cards in use (partial):** ARR daily channel `10469` (day-wise seller spend+ARR, ~287k rows), monthly ARR `7336`, HIT cohort `10453`, ARR cohort matrix `11020`, roles/GC-GM `7753`, HITS targets `11322`, weekly 1k-5k `11115`(HIT1)/`11727`(HIT2)/`11740`(HIT1+HIT2), churn `11771` (1k-5k+revenue), platform metrics `11746`, Google benchmarking `11576`. Column names are snake_case (e.g. `arr_overall`, not `ARR_All__c`).

## 5. How to make a change (edit → deploy)
**UI change** (edit the JSON-encoded template safely):
```python
import json, re
p='index.html'; content=open(p).read()
m=re.search(r'(<script type="__bundler/template">)(.*?)(</script>)', content, re.DOTALL)
html=json.loads(m.group(2).replace(r'<\/','</'))
# ... html.replace(old, new) ...  (assert count==1 first)
open(p,'w').write(content[:m.start(2)] + json.dumps(html).replace('</', r'<\/') + content[m.end(2):])
```
Then `node build.mjs` to verify JSX compiles ("Babel dropped: true" = success).

**Pipeline change:** edit `pipelines/*.py`, `python3 -m py_compile` it, optionally run locally (§6).

**Deploy:** commit + push to `main`. **Deploys happen via the workflows' Vercel step, NOT on git push.** To ship a code-only change, either trigger a refresh run (`gh workflow run refresh.yml`) or deploy manually:
```
node build.mjs && npx vercel --prod --yes --token "$(cat ~/.vc_token)"
```
`hits-tracker.xyz` is auto-aliased to the latest production deploy.

**Two data roots in `bev_data.json`:** `data.cards.*` (TvA/weekly toggle live here) and top-level `data.bev2.*` (the numbered Bird's-Eye sections 1–18 read from here). Put a new numbered-section's data in `bev2`.

## 6. Local setup (only to test pipelines)
- `~/metabase-arr-refresh/.mbcreds` (JSON, gitignored, **outside** the repo): `{ "METABASE_URL": "...", "METABASE_USER_EMAIL": "...", "METABASE_PASSWORD": "...", "METABASE_API_KEY": "mb_..." }`
- Google SA key JSON file (for Sheets-backed views).
- `~/.vc_token` — Vercel token for manual deploys.
- Run: `python3 pipelines/bev_refresh.py` (writes `bev_data.json`). If Sheets are unreachable a no-clobber guard preserves the previous TvA/cohort.

## 7. GitHub Actions (CI) — the automation
Workflows in `.github/workflows/` (all self-deploy to Vercel at the end):
- `refresh.yml` — **nightly 02:30 UTC (08:00 IST)**; runs every pipeline in order (bev last), snapshots, commits `*.json`, deploys. This is the main one.
- `ts_refresh.yml` — every 3h (Troubleshoot + scaling). `gc_refresh.yml`, `gm_daily.yml`, `nps_refresh.yml` — lighter, scoped refreshes.

**Secrets** (Repo → Settings → Secrets → Actions): `METABASE_URL`, `METABASE_USER_EMAIL`, `METABASE_PASSWORD`, `METABASE_API_KEY`, `GOOGLE_SA_KEY`, `VERCEL_TOKEN`. To rotate the Metabase key: `gh secret set METABASE_API_KEY` (hidden input) + update local `.mbcreds`.

## 8. Operational gotchas (learned the hard way)
- **Never overlap two `refresh.yml` runs.** The commit step does `git pull --rebase -X ours`; two concurrent refresh runs race and one clobbers the other's fresh `bev_data.json`. Cancel + wait for full stop before re-triggering; the concurrency guard queues, don't fight it.
- **`continue-on-error: true`** on pipeline steps means a step can **crash but still report "success."** Always check the step log for `Traceback`/`TypeError`, not just the green check.
- **`bev_refresh` writes atomically** (serialize in memory, then write) so a serialization error can't truncate `bev_data.json`. Keep it that way.
- **BigQuery quota**: heavy manual card pulls can exhaust it → cards return HTTP 400. The API-key account (§2.3) has its own 500 GB quota — keep using it.
- **Card 10469 is ~287k rows**; `bev_refresh` fetches it once (cached) and reuses. Don't add duplicate fetches.
- Validate after every deploy by curling `https://hits-tracker.xyz/bev_data.json` and checking the fields you changed, since the app is behind Google login (can't screenshot headless).

## 9. Claude Code skills (`.claude/skills/`)

If the new owner uses Claude Code in this repo, six skills load this knowledge automatically —
they are the working companion to this document:

| Skill | Use it for |
|---|---|
| `metrics-tracker-orientation` | **Start here.** Repo map, architecture, which file/pipeline/card backs each screen. |
| `metrics-tracker-edit-view` | Editing `index.html` (the JSON-encoded bundler template), reusable components, drilldown modals, browser verification. |
| `metrics-tracker-pipeline` | Writing/changing a `pipelines/*_refresh.py`, output conventions, verification discipline. |
| `metrics-tracker-metabase` | Metabase API, the card catalogue, BigQuery dbs/partitions, and the quota workarounds. |
| `metrics-tracker-data-model` | Metric definitions: 1k-5k, HIT1/HIT2/Revenue, Spend/Live, Google-live, golive multiplier, TvA, churn, S/GMV, cohorts, incentives. |
| `metrics-tracker-deploy` | Build, manual + CI deploy, the five workflows, and the git-clobber trap. |

They contain no credentials — only paths and secret *names*.

## 10. Handoff checklist
- [ ] New owner added as GitHub collaborator (write).
- [ ] Metabase API key issued on the 500 GB-quota account; `METABASE_API_KEY` secret rotated to it.
- [ ] Google SA has Sheets access; `GOOGLE_SA_KEY` valid.
- [ ] Vercel org membership (if manual deploys needed).
- [ ] New owner cloned repo, created `~/metabase-arr-refresh/.mbcreds`, ran `python3 pipelines/bev_refresh.py` once successfully.
- [ ] Confirmed a `gh workflow run refresh.yml` completes and redeploys.
