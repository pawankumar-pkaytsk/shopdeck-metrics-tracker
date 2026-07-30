---
name: metrics-tracker-orientation
description: Start here for the Shopdeck Metrics Tracker dashboard (hits-tracker.xyz). Repo map, architecture, where each screen's data comes from, and which other skill to use next. Use when asked to change, debug, or extend this dashboard, or when you need to find which file/pipeline/card backs a given view.
---

# Shopdeck Metrics Tracker — orientation

Static React-in-HTML analytics dashboard for the HITS / 1k-5k team.

- **Live:** https://hits-tracker.xyz
- **Repo:** `pawankumar-pkaytsk/shopdeck-metrics-tracker`, branch `main`
- **Hosting:** Vercel project `shopdeck-dashboard`
- **Full handover doc:** `ONBOARDING.md` in the repo root — read it once; it covers access, secrets, CI and the handoff checklist. This skill set is the working companion to it.

## Architecture in one picture

```
Metabase cards ──(pipelines/*.py, nightly)──> *.json in repo root
                                                    │
index.html  (<script type="__bundler/template"> = ONE JSON-encoded HTML+JSX string)
      │  build.mjs  compiles inline JSX ──> public/main.js  + copies *.json into public/
      ▼
Vercel (buildCommand: node build.mjs, outputDirectory: public) ──> hits-tracker.xyz
```

There is **no backend**. The browser fetches pre-generated `*.json` at runtime. Every number
on screen was computed by a pipeline hours earlier.

## Repo map

| Path | What it is |
|---|---|
| `index.html` | The whole app. The JSX lives inside one JSON-encoded `<script type="__bundler/template">` tag — **never hand-edit as plain text**. See `metrics-tracker-edit-view`. |
| `build.mjs` | Extracts the template, Babel-compiles the JSX, writes `public/`. Success line ends `Babel dropped: true`. |
| `pipelines/*.py` | 28 builders, one per data file. See `metrics-tracker-pipeline`. |
| `*.json` (33 files, repo root) | Generated data. Committed to git — the site serves them statically. |
| `.github/workflows/` | 5 workflows: `refresh.yml` (nightly, the main one), `ts_refresh.yml` (3-hourly), `gc_refresh.yml` (2-hourly), `gm_daily.yml`, `nps_refresh.yml`. All deploy to Vercel at the end. |
| `snapshots/` | Dated gzipped archives for time-travel. Excluded from Vercel; read from GitHub raw. |
| `calls/` | Sharded seller call records (`<4-hex-prefix>.json`). |
| `ONBOARDING.md` | Handover / access / operations doc. |

## Which file backs which screen

The app is a role picker → section. Roles: Growth Consultant, Growth Lead, Growth Manager,
Leadership, L&T, HR, KAE.

| Screen | Data file | Pipeline |
|---|---|---|
| Leadership → Bird Eye View (numbered sections 1..18, ARR buckets, churn cohort, platform + Google metrics) | `bev_data.json` | `bev_refresh.py` (heaviest, ~15–24 min) |
| Target vs Achievement, ARR Cohort | `bev_data.json` → `cards.cohort` (incl. `.tva`) | `bev_refresh.py` |
| Cohort Analysis 1k-5k (section 6) | `cohort_1k5k_data.json` | `cohort_1k5k_refresh.py` |
| Cohort Analysis 1k-5k (Google) | `cohort_google_data.json` | `cohort_google_refresh.py` |
| Track Ongoing Projects → Golive Creative Testing | `creative_test_data.json` | `creative_test_refresh.py` |
| Output / ARR | `arr_data.json` | `output_refresh.py` |
| Show any seller details | `gc_detail_data.json` (large, ~43 MB) | `gc_view_refresh.py` |
| GC / GM / L&T role views | `gc_data.json`, `gm_view_data.json`, `lt_data.json` | `gc_view_/gm_view_/lt_refresh.py` |
| KAE compliance | `kae_hits_data.json` | `kae_refresh.py` |
| Spend/Live, scaling | `scaling_data.json` | `scaling_refresh.py` |
| Troubleshoot | `ts_data.json`, `ts_sop_data.json` | `ts_refresh.py`, `ts_sop_refresh.py` |

## Two data roots inside `bev_data.json` — get this right

- `data.cards.*` — the older root. **Target vs Achievement and the ARR cohort live at `cards.cohort`** (with `cards.cohort.tva`).
- `data.bev2.*` — the numbered Bird's-Eye sections (1..18) read from here.

**Put a new numbered section's data in `bev2`.** Getting this wrong renders an empty section.

> Real incident: a loop variable named `cohort` inside `bev_refresh.py` shadowed the module-level
> `cohort` dict, so `cards.cohort` was written as the string `'M0'` and both the ARR Cohort and the
> whole TvA view went blank for two days. Never reuse `cohort`, `churn`, `arr`, `target` as local
> names in that file.

## Working rules for this repo

1. **Verify data, don't trust the render.** After any change, re-read the JSON and assert the
   specific numbers. Pooled ratios (`sum(num)/sum(den)`), never an average of per-row ratios.
2. **Check your own comparison logic before declaring a data bug.** A string mismatch
   (`'Only Creative'` vs `'Creative only'`) once produced a bogus "the column is random" claim.
3. **`continue-on-error: true`** on every CI pipeline step means a step can crash and still show
   green. Grep the log for `Traceback`.
4. **Never overlap two data-writing workflow runs** — see `metrics-tracker-deploy`.
5. Ratios and thresholds are business definitions, not arbitrary. Look them up in
   `metrics-tracker-data-model` before inventing one.

## Next skill

| Task | Skill |
|---|---|
| Change/add a view, section, table, chart | `metrics-tracker-edit-view` |
| Add or change a data pipeline | `metrics-tracker-pipeline` |
| Query Metabase, create/edit a card, hit BigQuery quota | `metrics-tracker-metabase` |
| Look up a metric definition (1k-5k, HIT1/HIT2, Spend/Live, churn, S/GMV, cohorts, incentives) | `metrics-tracker-data-model` |
| Build, deploy, CI, git-clobber troubleshooting | `metrics-tracker-deploy` |
