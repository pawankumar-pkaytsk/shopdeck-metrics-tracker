---
name: metrics-tracker-orientation
description: Start here for the Shopdeck Metrics Tracker dashboard (hits-tracker.xyz). Repo map, architecture, where each screen's data comes from, and which other skill to use next. Use when asked to change, debug, or extend this dashboard, or when you need to find which file/pipeline/card backs a given view.
---

# Shopdeck Metrics Tracker — orientation

Static React-in-HTML analytics dashboard for the HITS / 1k-5k team.

- **Live:** https://hits-tracker.xyz
- **Repo:** `pawankumar-pkaytsk/shopdeck-metrics-tracker`, branch `main`
- **Hosting:** Vercel project `shopdeck-dashboard`
- **Full handover doc:** `HANDOVER.md` in the repo root — read it once; it covers access, secrets,
  CI and the handoff checklist. This skill set is the working companion to it.

## Architecture in one picture

```
Metabase cards ──(pipelines/*.py, nightly)──> *.json in repo root
                                                    │
index.html  (<script type="__bundler/template"> = ONE JSON-encoded HTML+JSX string)
      │  build.mjs  compiles inline JSX ──> public/main.js  + copies *.json into public/
      ▼
Vercel (buildCommand: node build.mjs, outputDirectory: public) ──> hits-tracker.xyz
```

There is **no backend**. The browser fetches pre-generated `*.json` at runtime. Every number on
screen was computed by a pipeline hours earlier. **All computation belongs in the pipeline**, not
the browser — the exceptions are cheap reshaping (bucketing days into ISO weeks, splitting a seller
list into tranches) where the raw rows are already in the JSON.

## Repo map

| Path | What it is |
|---|---|
| `index.html` | The whole app. The JSX lives inside one JSON-encoded `<script type="__bundler/template">` tag — **never hand-edit as plain text**. See `metrics-tracker-edit-view`. |
| `build.mjs` | Extracts the template, Babel-compiles the JSX, writes `public/`. Success line ends `Babel dropped: true`. Also pulls the 4 Google Sheets when `GOOGLE_SA_KEY` is set. |
| `pipelines/*.py` | ~29 builders, one per data file. See `metrics-tracker-pipeline`. |
| `*.json` (34 files, repo root) | Generated data. Committed to git — the site serves them statically. |
| `.github/workflows/` | 5 workflows: `refresh.yml` (nightly, the main one), `ts_refresh.yml` (3-hourly), `gc_refresh.yml` (2-hourly), `gm_daily.yml`, `nps_refresh.yml`. All deploy to Vercel at the end. |
| `snapshots/` | Dated gzipped archives for time-travel. Excluded from Vercel; read from GitHub raw. |
| `calls/` | Sharded seller call records (`<4-hex-prefix>.json`). |
| `HANDOVER.md` | Handover / access / operations doc. |

## Which file backs which screen

The app is a role picker → section. Roles: Growth Consultant, Growth Lead, Growth Manager,
Leadership, L&T, HR, KAE.

| Screen | Data file | Pipeline |
|---|---|---|
| Leadership → Bird's Eye view (numbered sections, ARR buckets, churn cohort, platform + Google metrics) | `bev_data.json` | `bev_refresh.py` (heaviest, ~15–24 min) |
| Target vs Achievement, ARR Cohort | `bev_data.json` → `cards.cohort` (incl. `.tva`) | `bev_refresh.py` |
| Spend & ARR trend (above section 1) | `bev_data.json` → `bev2.dodTrend` | `bev_refresh.py` |
| Google Seller Book (Google tab) | `google_sellers_data.json` | `google_sellers_refresh.py` |
| Cohort Analysis 1k-5k (section 6) | `cohort_1k5k_data.json` | `cohort_1k5k_refresh.py` |
| Cohort Analysis 1k-5k (Google) | `cohort_google_data.json` | `cohort_google_refresh.py` |
| Track Ongoing Projects → Golive Creative Testing | `creative_test_data.json` | `creative_test_refresh.py` |
| Output / ARR | `arr_data.json` | `output_refresh.py` |
| Show any seller details | `gc_detail_data.json` (large, ~43 MB) | `gc_view_refresh.py` |
| GC / GM / L&T role views | `gc_data.json`, `gm_view_data.json`, `lt_data.json` | `gc_view_/gm_view_/lt_refresh.py` |
| KAE compliance | `kae_hits_data.json` | `kae_refresh.py` |
| Spend/Live, scaling | `scaling_data.json` | `scaling_refresh.py` |
| Troubleshoot | `ts_data.json`, `ts_sop_data.json` | `ts_refresh.py`, `ts_sop_refresh.py` |

### Bird's-Eye section inventory (the parts that move most)

| Section | What it is | Data |
|---|---|---|
| *(above 1)* **Spend & ARR trend** | dual-axis line; Meta/Google/Blended × day/ISO-week × GM dropdown × custom From/To | `bev2.dodTrend` |
| **1** | HIT1 → HIT2 conversion cohort | `bev2.hit2Cohort` |
| **2** | ARR Cohort (1k-5k), M0 = HIT month | `bev2.arrCohort` |
| **3 / 3b / 3c** | ARR buckets (collapsed by default, `BevSection collapsible`) | `bev2.arrBuckets*` |
| **3d** | ARR tranches by HIT cohort — re-ranked at every age | `bev2.arrCohort` + `cards.revenueCohort` |
| **3e** | Same table, **rank frozen at M1** (one `freezeAt` prop, same component) | same |
| **4** | Churn cohort (HIT1/HIT2/Revenue) | `bev2.churnCmp` |
| Google tab | Weekly metrics, Google cohort, **Google Seller Book**, bucket KPIs | `bev2.google`, `google_sellers_data.json` |

## Two data roots inside `bev_data.json` — get this right

- `data.cards.*` — the older root. **Target vs Achievement and the ARR cohort live at
  `cards.cohort`** (with `cards.cohort.tva`), and the Revenue ARR cohort at `cards.revenueCohort`.
- `data.bev2.*` — the numbered Bird's-Eye sections read from here.

**Put a new numbered section's data in `bev2`.** Getting this wrong renders an empty section.

> Real incident: a loop variable named `cohort` inside `bev_refresh.py` shadowed the module-level
> `cohort` dict, so `cards.cohort` was written as the string `'M0'` and both the ARR Cohort and the
> whole TvA view went blank for two days. Never reuse `cohort`, `churn`, `arr`, `target` as local
> names in that file — prefix loop locals (`_ccoh`, `_r`).

## Working rules for this repo

1. **Verify data, don't trust the render.** After any change, re-read the JSON and assert the
   specific numbers. Pooled ratios (`sum(num)/sum(den)`), never an average of per-row ratios.
2. **Check your own comparison logic before declaring a data bug.** A string mismatch
   (`'Only Creative'` vs `'Creative only'`) once produced a bogus "the column is random" claim.
3. **BigQuery quota failures are routine, on both databases.** Every remote fetch must fall back to
   a session token (which returns Metabase's *cached* result) when the api-key path 400s — see
   `metrics-tracker-metabase`. A pipeline without that fallback silently zeroes columns.
4. **`continue-on-error: true`** on every CI pipeline step means a step can crash and still show
   green. Grep the log for `Traceback`.
5. **Never overlap two data-writing workflow runs** — see `metrics-tracker-deploy`.
6. Ratios and thresholds are business definitions, not arbitrary. Look them up in
   `metrics-tracker-data-model` before inventing one.
7. **Populations drift within a single day.** The 1k-5k book read 214 → 240 as `ts_data.json`
   refreshed, and the Jul-26 revenue cohort went 181 → 156 in an afternoon. Always state the
   as-of time next to a count, and re-verify after any pull.
8. **Prefer one component with a prop over a second copy.** 3d/3e differ only by `freezeAt`;
   HIT1/HIT2 in the Google Seller Book differ only by a bucket filter. Copies drift.

## Next skill

| Task | Skill |
|---|---|
| Change/add a view, section, table, chart | `metrics-tracker-edit-view` |
| Add or change a data pipeline | `metrics-tracker-pipeline` |
| Query Metabase, create/edit a card, hit BigQuery quota | `metrics-tracker-metabase` |
| Look up a metric definition (1k-5k, HIT1/HIT2, Spend/Live, churn, S/GMV, cohorts, incentives) | `metrics-tracker-data-model` |
| Build, deploy, CI, git-clobber troubleshooting | `metrics-tracker-deploy` |
