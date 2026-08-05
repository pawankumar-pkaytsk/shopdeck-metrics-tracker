# Shopdeck Metrics Tracker — handover

Everything needed to take over this project on a new machine. Generated 2026-08-06 from the six
Claude Code skills in `.claude/skills/` (repo @ `b35ba3c3`), so this document and the skills are the
same text — if they ever disagree, the skills are authoritative.

- **Live:** https://hits-tracker.xyz
- **Repo:** `pawankumar-pkaytsk/shopdeck-metrics-tracker`, branch `main`
- **Hosting:** Vercel project `shopdeck-dashboard`

## How to use this

If the new machine runs Claude Code, it needs nothing beyond the repo: `.claude/skills/` ships with
it and the six skills load on demand. This file is for reading start-to-finish once, and for anyone
not using Claude Code.

## Day-one setup

```bash
git clone https://github.com/pawankumar-pkaytsk/shopdeck-metrics-tracker.git
cd shopdeck-metrics-tracker
node build.mjs          # must end: "Babel dropped: true"
```

Three secrets live **outside** the repo and must be recreated by hand — none are in git:

| Path | Contents |
|---|---|
| `~/metabase-arr-refresh/.mbcreds` | `{"METABASE_URL","METABASE_USER_EMAIL","METABASE_PASSWORD","METABASE_API_KEY"}` |
| `~/Downloads/metrics-tracker-automation-*.json` | Google service-account key (Sheets read) |
| `~/.vc_token` | Vercel deploy token |

CI has its own copies as repo secrets: `METABASE_URL`, `METABASE_USER_EMAIL`, `METABASE_PASSWORD`,
`METABASE_API_KEY`, `GOOGLE_SA_KEY`, `VERCEL_TOKEN`.

## The five things that have cost the most time

1. **BigQuery quota, on both databases.** Every remote fetch needs the session-token fallback
   (cached result) when the api-key path 400s, and a 4xx must never be retried. §4.
2. **The git-clobber trap.** `git pull --rebase -X ours` in CI can discard a run's own fresh data;
   a blanket `checkout --theirs` once dropped three nightlies. §6.
3. **`index.html` is one JSON-encoded string.** Decode → replace with `assert count == 1` →
   re-encode. §2.
4. **Drilldown columns need `render`**, not just `get`, or they silently render blank. §2.
5. **`hitsMap` is a HIT1 roster, not a 1k-5k one** — 38 of 42 HIT2 sellers fall out of it. §5.

## Contents

| § | Skill | Covers |
|---|---|---|
| 1 | `metrics-tracker-orientation` | Start here for the Shopdeck Metrics Tracker dashboard (hits-tracker.xyz). Repo map, architecture, where each screen's da |
| 2 | `metrics-tracker-edit-view` | Edit the Shopdeck Metrics Tracker UI in index.html — the app lives inside one JSON-encoded bundler template, so normal t |
| 3 | `metrics-tracker-pipeline` | Write or modify a Shopdeck Metrics Tracker data pipeline (pipelines/*_refresh.py that turns Metabase cards into a commit |
| 4 | `metrics-tracker-metabase` | Query Metabase and BigQuery for the Shopdeck Metrics Tracker — read/create/edit cards via the API, the card catalogue, B |
| 5 | `metrics-tracker-data-model` | Business metric definitions for the Shopdeck Metrics Tracker — 1k-5k, HIT1/HIT2/Revenue buckets, roles (GL/GM/CL), ARR a |
| 6 | `metrics-tracker-deploy` | Build, ship and operate the Shopdeck Metrics Tracker — node build.mjs, manual Vercel deploy, the five GitHub Actions wor |


---

# 1. metrics-tracker-orientation

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

---

# 2. metrics-tracker-edit-view

# Editing the dashboard UI (`index.html`)

The entire app is one JSON-encoded string inside
`<script type="__bundler/template"> … </script>`. You **cannot** use Edit/Write on it directly, and
you cannot grep it usefully as plain text. Always decode → patch → re-encode with Python.

## The canonical edit pattern

```python
import json, re
p = 'index.html'; content = open(p).read()
m = re.search(r'(<script type="__bundler/template">)(.*?)(</script>)', content, re.DOTALL)
html = json.loads(m.group(2).replace(r'<\/', '</'))      # decode

reps = [(old1, new1), (old2, new2)]
for a, b in reps:
    assert html.count(a) == 1, (html.count(a), a[:70])    # ALWAYS assert uniqueness
    html = html.replace(a, b, 1)

open(p, 'w').write(content[:m.start(2)] + json.dumps(html).replace('</', r'<\/') + content[m.end(2):])
```

Then **always**:

```bash
node build.mjs   # success ends with: "Babel dropped: true"
```

A JSX syntax error prints a Babel stack trace and writes nothing — fix before continuing.

### Hard-won rules

- **`assert html.count(old) == 1` on every replacement**, and assert them all *before* writing, so a
  batch fails atomically. A failed assertion means nothing was written — re-read the region with
  `repr()` and fix the anchor.
- **Never slice between two markers you also introduced.** Index-based
  `html[:start] + new + html[end:]` where `end` matched a comment inside *your own newly inserted
  component* corrupted the file. Either (a) do text replacements only, or (b) apply the text
  replacement **first** and insert the new component **second**, or (c) make your new component's
  comments unique.
- **Read the exact anchor before matching it.** Anchors drift as the file changes — a stale anchor
  from an earlier turn is the most common assertion failure. `print(repr(lines[i]))` first.
- **Escaped characters:** Babel escapes `·` → `·` and `—` → `—` in `public/main.js`, so
  grepping the compiled file for those literals fails. Grep the decoded `html` string, or grep for an
  identifier. In the decoded string the characters are literal (`·`, `—`).
- Inside the JSON-encoded source a JS single-quote escape appears as `\'`; match what's actually
  there.
- **`valueOf` is a forbidden prop name.** `function T({ valueOf })` destructures to
  `Object.prototype.valueOf` — always truthy — and the page goes blank. Same risk for `toString`,
  `constructor`. Name it `arrOf`, `pickOf`, etc.
- **Prefer a prop over a second component.** 3d/3e differ only by `freezeAt`; HIT1/HIT2 in the Google
  Seller Book differ only by a bucket filter. Two copies drift within a week.

## Reusable components already in the app

| Component | Signature / use |
|---|---|
| `BevSection` | `{ tone, title, sub, awaiting, collapsible, defaultOpen, children }`. Tones: `blue green amber purple rose teal slate`. When collapsed it does **not** mount children. |
| `FloatingTabBar` | `{ items:[{value,label,icon}], value, onChange, size }`. **Defined outside the template** (design-system chunk), so it won't grep in `index.html` — it is still in scope. Same for `Button`. No colour prop — hand-roll a styled `div` for a coloured pill. |
| `CRModal` | `{ title, rows, cols, onClose }` — the drilldown modal used across Bird's Eye view. |
| `DrillNum` | `{ onClick, children, strong }` — the dashed-underline clickable number. |
| `DataTable` | `{ columns, rows }` |
| `SectionTitle` | `{ title, sub, right }` |
| `Card` | `variant="regular"`, `padding={0}` |
| `Button` | `size`, `variant`, `icon`, `onClick` |
| `StatCard` | `{ label, value, sub, onClick }` |
| `TimeSwitch` | render-prop toggle: `<TimeSwitch modes={[{k,label}]} initial="x">{(mode)=>…}</TimeSwitch>` |
| `exportCSV`, `exportPNG` | CSV / PNG export helpers; mark chrome `className="no-export"` |
| `CohortHeatmap1k5k` | `{ open, src, dataOverride }` — `dataOverride` lets a wrapper own extra toggles |
| `ArrCohortTrancheControls` / `ArrCohortTrancheTable` | sections 3d/3e. Pass `freezeAt="M1"` to freeze the rank at that age |
| `SpendArrTrend` | `{ T }` — dual-axis line chart with channel / granularity / GM / date-range controls |
| `GoogleSellerBook` | `{ open }` — reads `google_sellers_data.json` |

**CSS variables that exist:** `--sd-font-sans --sd-fg-1/2/3 --sd-heading --sd-divider --sd-border
--sd-white --sd-bg-app --sd-surface-2 --sd-stroke --sd-primary --sd-red-700 --sd-green-700
--sd-radius-lg/xl --sd-shadow-regular/elevated`. `--sd-bg-2` does **not** exist (use `--sd-surface-2`).

## Charts — `BChart` will not do a two-metric time series

`BChart` puts **every series on one shared Y scale** and draws an **x-label per category**. For two
metrics of different magnitude over 180 points it is unusable. `SpendArrTrend` is the pattern to copy
for that case: independent left/right axes, `everyN` sparse x-ticks, hover crosshair, `viewBox` SVG.

If you build a chart with a mouse handler, guard against zero layout:

```js
const box = ev.currentTarget.getBoundingClientRect();
if (!box.width) { setHov(null); return; }   // hidden / collapsed container
```

## Drilldown modals — the recurring bug

`CRModal` renders each cell as **`col.render ? col.render(row) : row[col.key]`**.

A column that only has `get` (used for CSV export and sorting) renders **blank** when the row is an
array or the key doesn't exist. This has caused blank Seller/ARR/name columns four separate times.

**Rule: every drilldown column needs a `render`.**

```jsx
const cols = [
  { key: 's',  label: 'Seller ID', get: r => r[0], render: r => <span style={{ font:'400 12px/1 monospace' }}>{r[0]}</span> },
  { key: 'n',  label: 'Seller',    get: r => r[1], render: r => r[1] || '(unnamed)' },
  { key: 'arr',label: 'ARR', align:'right', get: r => r[2], render: r => num(r[2]) },
];
```

Open it with the section's `open(title, rows, cols)` helper.

**Put the arithmetic in the drilldown title.** Since the card-11020 change a cohort cell is *not* the
average of its list, so titles state it explicitly:
`avg 1,484 = ARR / 15 cohort sellers (10 had ARR at this age)`. Same for rates:
`Spend/Live 58% = 11 of 19 live sellers`. Name the denominator — a percentage column whose base
differs from its neighbours' (Acceptance divides by *assigned*, Spend/Live by *live*) needs a
per-column label or the drilldown lies.

## Adding a whole new nav destination

Four coordinated edits (all must be present or the tab silently does nothing):

1. **Component** — insert the function before a known anchor, e.g. `  function PlatformWkView({ B }) {`.
2. **Nav pill** — add a `FloatingTabBar` (or custom styled div) in the nav row.
3. **Route** — extend the ternary chain:
   `{section === 'projects' ? <ProjectsView /> : …}`
4. **Breadcrumb** — add an `else if (section === 'projects') { crumbs.push({ label: '…' }); }`.

## Verify in the browser (always do this)

The dev server serves `public/`, so **run `node build.mjs` first**.

```
preview_start { name: "metrics-public" }      # .claude/launch.json
```

**The browser pane is often hidden, and then the page has ZERO layout width** —
`getBoundingClientRect()` returns 0, screenshots come back blank white, and `computer` scroll/click
actions time out. This is not a bug in your change. In that state:

- Verify through the **DOM via `javascript_tool`**, not screenshots. Read the rendered table back and
  assert it against the JSON.
- Mouse-driven behaviour (hover tooltips, drag) cannot be exercised. Verify the *logic* instead by
  evaluating the same expressions against the real data in the page context, and cross-check against
  Python where possible (e.g. weekday labels vs `datetime`).
- Say so when reporting — don't claim a visual check you couldn't perform.

Navigation recipe (each step usually needs its **own** call — React re-renders between calls):

1. Click **"Skip — browse with sample data"** (a leaf element whose text starts with `Skip`).
2. Click the role: text reads `I want to view as Leadership…`.
3. Click `Bird's Eye view` (exact text, with the apostrophe).
4. Click the sub-tab (`Google`, `1k-5k Overview`, …) — there may be several elements with that text;
   click them all.
5. Locate a section by its title prefix, then walk up to the element with
   `borderLeftWidth === '4px'` (that's the `BevSection` wrapper) and scope all reads to it.

Then read values out and **assert them against the JSON**:
```js
[...sec.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent.trim()))
```

Gotchas:
- Reading a value in the *same* call as the click returns the pre-render state. Split into two calls.
- Suggestion lists may use **`onMouseDown`**, not `onClick`.
- Setting a React-controlled `<input>` needs the native setter, then an `input` **and** `change`
  event:
  ```js
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el, v);
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
  ```
- Never dump `document.body.innerText` or an unfiltered `querySelectorAll('*')` map — it blows the
  token limit. Scope to a section element first.
- Console shows pre-existing noise: Babel "deoptimised" notes, GSI popup failures, React
  duplicate-key warnings. Those are not your bug.
- Some views (Central Reports cohort/TvA) sit behind **"Connect Google Sheets"** and cannot render
  locally without `GOOGLE_SA_KEY` — verify those on production after deploy.

---

# 3. metrics-tracker-pipeline

# Writing a pipeline (`pipelines/*_refresh.py`)

One pipeline = one output `*.json` in the repo root. It fetches Metabase card(s), reshapes into
exactly what the view needs, and dumps compact JSON. All computation belongs here, **not** in the
browser — the frontend is static.

## Skeleton (copy this)

```python
#!/usr/bin/env python3
"""Build <name>_data.json — <what the view shows>.

Source: card <id> (<what it returns>). Grain: <one row per …>.
<Every non-obvious definition, threshold and caveat goes in this docstring — it is the
 only place a future maintainer will look.>

Run: cd ~/shopdeck-metrics-site && python3 pipelines/<name>_refresh.py
"""
import json, os, re, sys, datetime, urllib.request, urllib.error, subprocess

CARD = 12345
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "<name>_data.json")
CRED_CACHE  = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")


def creds():
    if os.environ.get("METABASE_URL"):          # CI
        return (os.environ["METABASE_URL"].rstrip("/"), os.environ.get("METABASE_USER_EMAIL"),
                os.environ.get("METABASE_PASSWORD"))
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else \
        json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e.get("METABASE_USER_EMAIL"), e.get("METABASE_PASSWORD")


def req(url, method="GET", body=None, H=None, timeout=900):
    """Retry 5xx and timeouts; NEVER retry a 4xx — a BigQuery quota rejection is a 400 and is
    permanent until reset. Retrying it once turned a hard failure into a ~100 minute no-op run."""
    import time as _t
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as ex:
            if 400 <= ex.code < 500:
                raise
            last = ex; _t.sleep(3 * (attempt + 1))
        except Exception as ex:
            last = ex; _t.sleep(3 * (attempt + 1))
    raise last


def fnum(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0


def load(name, default=None):
    try: return json.load(open(os.path.join(REPO, name)))
    except Exception as ex:
        print(f"[<name>] {name} unreadable ({ex}) — using default")
        return default if default is not None else {}


def main():
    url, email, pw = creds()
    key = os.environ.get("METABASE_API_KEY")
    if not key:
        try: key = json.load(open(CRED_CACHE)).get("METABASE_API_KEY")
        except Exception: key = None
    AUTH = {"x-api-key": key} if key else {"X-Metabase-Session": req(
        url + "/api/session", "POST", {"username": email, "password": pw},
        {"Content-Type": "application/json"})["id"]}
    H = {"Content-Type": "application/json", **AUTH}

    # MANDATORY: cached-result fallback. The api-key path forces a fresh BigQuery scan and is the
    # first thing to fail on a spent daily quota (db 6 AND db 23 both run out routinely). A session
    # token returns Metabase's cached result for the same card.
    _sess = {}
    def _sess_hdr():
        if "h" not in _sess:
            tok = req(url + "/api/session", "POST", {"username": email, "password": pw},
                      {"Content-Type": "application/json"})["id"]
            _sess["h"] = {"X-Metabase-Session": tok, "Content-Type": "application/json"}
            print("[<name>] opened a session token for cached-result fallback")
        return _sess["h"]

    def fetch(path, body=None, timeout=900):
        try:
            return req(f"{url}{path}", "POST", body if body is not None else {}, H, timeout)
        except urllib.error.HTTPError as ex:
            if not (400 <= ex.code < 500):
                raise
            return req(f"{url}{path}", "POST", body if body is not None else {}, _sess_hdr(), timeout)

    rows = fetch(f"/api/card/{CARD}/query/json")
    # … reshape …

    out = {"generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
           "card": CARD, "rows": …, "dq": {…}}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))     # compact: no spaces
    print(f"[<name>] … -> {OUT}")


if __name__ == "__main__":
    main()
```

## Non-negotiables

1. **`separators=(",", ":")`** — these files ship to the browser; whitespace is bandwidth.
2. **`generatedAt`** in every output, plus a `dq` (data-quality) block: input row counts, rows
   dropped and why, threshold values used, cross-check mismatch counts. The views surface these,
   and they are how you debug a month later.
3. **A `print()` summary line** ending in `-> {OUT}` — this is what you read in CI logs.
4. **Pooled ratios only**: `round(100*num/den, 2) if den else None`. Never average per-row ratios.
   Emit `None` for undefined, never `0` — the view renders `—`/`no GMV`.
5. **The `fetch()` cached fallback above, on every remote call.** Non-optional.
6. **Graceful degradation + no-clobber guards.** See below.
7. **Register it in CI** — add a step to `.github/workflows/refresh.yml`:
   ```yaml
   - name: <Name> (card <id>)
     continue-on-error: true
     run: python pipelines/<name>_refresh.py
   ```
   A pipeline not in a workflow silently goes stale forever.
8. **Compile-check**: `python3 -c "import ast;ast.parse(open('pipelines/x.py').read())"`, then run
   it locally and inspect the JSON before committing.

## No-clobber guards — cover every source, not just the expensive one

A quota-failed fetch must degrade to *yesterday's value*, never to zero. Load the previous output
once, early, and fall back per field:

```python
prev = load("<name>_data.json", {})
prev_row = {r["s"]: r for r in (prev.get("rows") or [])}
...
if last7 is None:                 # only card 7401 carries last-7-day spend
    last7 = (prev_row.get(sid) or {}).get("last7")
...
# and for a whole expensive block, refuse to publish a collapse:
carried = 0
if prev_pnl and len(gpnl) < 0.9 * len(prev_pnl):
    for sid in ids:
        if sid not in gpnl and sid in prev_pnl:
            gpnl[sid] = {...prev...}; carried += 1
    print(f"[<name>] WARN coverage collapsed — carried {carried} forward")
out["dq"]["pnlCarriedForward"] = carried
```

Real incident: without this, one run shipped `3K = 0` and `CL = 0/240` because db 6 was over quota.
Publish the carry count in `dq` so the view can say so.

Also measure `dq` coverage **off the emitted rows**, not the fetch step — a quota-failed role query
still yields coverage via carry-forward, and dq must reflect what shipped.

## Verification discipline (this is where bugs are actually caught)

After running, load the JSON and assert invariants. Real checks that caught real bugs:

```python
# subset:      google cohort ⊂ all-1k-5k cohort
assert gsids <= asids
# nesting:     the PNL buckets nest, so exclusive tiers must sum back to the widest
assert h_excl + p_excl + o == raw_health
# consistency: arm columns sum to the total (±1 for independent rounding)
assert abs(sum(cells) - total) <= 1
# anchoring:   every seller appears exactly once at W0, all with google spend
assert w0_rows == n_sellers and w0_without_google == 0
# set algebra: both == hit1 ∪ hit2, revenue disjoint from both
assert bo == (h1 | h2) and not (rv & bo)
# roll-up:     per-group series must sum to the total on every day and metric
#              (allow a few units for independent per-group rounding)
```

Compare like-for-like vintages: two JSONs generated a day apart *will* differ on recent weeks
because spend accrues, and the roster itself moves within a day. Re-run both before concluding
there's a bug.

**Beware your own check script.** A reused variable (`c = cells(total)` then `c = cells(one_gm)`)
once produced a false "exclusivity FAILED". Verify the verifier before believing a failure.

## Traps that have actually cost time

- **Variable shadowing** in `bev_refresh.py`: a local named `cohort` overwrote the module-level
  ARR-cohort dict and blanked two whole views for two days. Prefix loop locals (`_ccoh`, `_r`).
- **Sentinel strings that are truthy.** `clean()` maps a blank name to the literal
  `'Unassigned'`, so `gc2gm_all.get(gl) or team.get('gm')` never falls through — 48 of 214 sellers
  landed in an Unassigned bucket while having a perfectly good GM. Use a helper:
  ```python
  def _named(v):
      v = (v or '').strip()
      return v if v and v != 'Unassigned' else ''
  ```
- **Whitespace in names.** `"Bhavana  Ahirwar"` and `"Bhavana Ahirwar"` group as two people.
  Always `re.sub(r"\s+", " ", name).strip()` (this is `ts_refresh._norm`).
- **Per-seller parameterised cards are slow and throttle.** Card 5207 is ~1.7 s/seller and db 23
  throttles under concurrency. Use a small `ThreadPoolExecutor` (4 workers), retry the misses at
  lower concurrency, and only loop over the sellers that can possibly have data.
- **Don't widen a per-seller detail map casually** — `gc_detail_data.json` is already ~43 MB.

## Reading other pipelines' output

Later pipelines may read earlier ones' JSON (that's why `bev_refresh` runs last in `refresh.yml`).
Use `load(name, default)`; never assume a file exists. `ts_data.json → hitsMap` and
`scaling_data.json → sellers` are the two most reused.

## Heavy-pipeline notes

- `bev_refresh.py` is ~15–24 min, fetches card 10469 (~737k rows, 2025-01 → today) **once** and
  reuses it. Don't add a second fetch of it.
- It needs `GOOGLE_SA_KEY` (or a local SA key) for the Sheets-backed TvA/cohort inputs; without it
  those views come out empty and a no-clobber guard preserves the previous values.
- It writes atomically (serialize fully in memory, then write) so a serialization error cannot
  truncate `bev_data.json`. **Keep it that way.**
- **`bev_refresh.py` still has no session-token fallback** — it is the most quota-exposed pipeline
  in the repo. Adding the `fetch()` wrapper there is the highest-value outstanding hardening.

## Where the output goes in `bev_data.json`

- new numbered Bird's-Eye section → `bev2.<key>`
- TvA / ARR cohort → `cards.cohort` (already occupied; extend, don't replace)

Standalone views should get their **own** `*.json` + pipeline instead of bloating `bev_data.json`
(`google_sellers_refresh.py` is the model to copy — small, single-purpose, fully guarded).

---

# 4. metrics-tracker-metabase

# Metabase & BigQuery

Base URL and credentials live in `~/metabase-arr-refresh/.mbcreds` (JSON, gitignored, **outside**
the repo):

```json
{ "METABASE_URL": "...", "METABASE_USER_EMAIL": "...", "METABASE_PASSWORD": "...", "METABASE_API_KEY": "mb_..." }
```

Never print, commit or echo these values. In CI they come from repo secrets.

## The single most important thing: api-key vs session token

| Auth | Behaviour |
|---|---|
| `x-api-key` | Forces a **fresh BigQuery scan**. First thing to fail when a daily quota is spent. |
| `X-Metabase-Session` (login) | Returns Metabase's **cached result** for the same card. Costs no quota. |

Both databases run out routinely. Real error:
`Daily quota exceeded: Used 501 GB of 500 GB. Resets on 2026-08-05 00:00 IST` (db 23), and the same
shape on db 6. **Every remote fetch must try the key, then fall back to a token:**

```python
_sess = {}
def _sess_hdr():
    if "h" not in _sess:
        tok = req(url + "/api/session", "POST", {"username": email, "password": pw},
                  {"Content-Type": "application/json"})["id"]
        _sess["h"] = {"X-Metabase-Session": tok, "Content-Type": "application/json"}
    return _sess["h"]

def fetch(path, body=None, timeout=900):
    try:
        return req(f"{url}{path}", "POST", body if body is not None else {}, H, timeout)
    except urllib.error.HTTPError as ex:
        if not (400 <= ex.code < 500):
            raise
        return req(f"{url}{path}", "POST", body if body is not None else {}, _sess_hdr(), timeout)
```

Not theoretical: with both quotas spent, `google_sellers_refresh.py` went from
`7753 0/278, 7401 0/278, 5207 0/119` to **fully populated** purely by adding this. Already used by
`revival_`, `lt_`, `gc_view_`, `kae_`, `cohort_google_` and `google_sellers_refresh`.

**Ad-hoc SQL also works on the token** while the key is quota-blocked — verified. So you can still
investigate during an outage; you just can't force fresh scans.

**Never retry a 4xx.** A quota rejection is an HTTP 400 and is permanent until reset. Retrying it
turned a hard failure into a ~100-minute no-op run (119 per-seller calls × 3 escalating passes).

## API recipes

```python
import json, os, urllib.request
e = json.load(open(os.path.expanduser("~/metabase-arr-refresh/.mbcreds")))
url = e["METABASE_URL"].rstrip("/")
H = {"x-api-key": e["METABASE_API_KEY"], "Content-Type": "application/json"}

def req(u, m="GET", b=None):
    r = urllib.request.Request(u, data=(json.dumps(b).encode() if b is not None else None),
                               headers=H, method=m)
    return json.loads(urllib.request.urlopen(r, timeout=600).read())

card = req(f"{url}/api/card/12207")                         # metadata + SQL
rows = req(f"{url}/api/card/12207/query/json", "POST", {})   # run it
dash = req(f"{url}/api/dashboard/609")                       # dashcards -> card ids
```

- **Read a card's SQL:** `card["dataset_query"]` is either `{"native":{"query":…}}` **or**
  `{"stages":[{"native":…}]}` (newer MBQL). Handle both:
  ```python
  q = card["dataset_query"]
  sql = q["stages"][0]["native"] if "stages" in q else q["native"]["query"]
  ```
- **Edit a card:** mutate that structure and `PUT {"dataset_query": q}` (optionally `name`).
- **Parameterised card:** the param type must be **`string/=`**, not `id` — `id` returns
  `500 Invalid parameter value type :id`.
  ```python
  body = {"parameters": [{"type": "string/=",
                          "target": ["variable", ["template-tag", "seller_id"]], "value": sid}]}
  ```
- **Column names are snake_case** (`arr_overall`, not `ARR_All__c`).

### Ad-hoc SQL — two endpoints, and the row cap that will bite you

| Endpoint | Body | Rows |
|---|---|---|
| `POST /api/dataset` | JSON `{"database":6,"type":"native","native":{"query":sql}}` | **capped at 2000** (preview) |
| `POST /api/dataset/json` | **form-encoded** `query=<json>` | all rows (export endpoint) |

```python
payload = urllib.parse.urlencode({"query": json.dumps(
    {"database": 6, "type": "native", "native": {"query": sql}})}).encode()
r = urllib.request.Request(f"{url}/api/dataset/json", data=payload, method="POST",
      headers={**AUTH, "Content-Type": "application/x-www-form-urlencoded"})
rows = json.loads(urllib.request.urlopen(r, timeout=1800).read())   # list of dicts
```

A result of exactly 2000 rows is the tell. `/api/card/<id>/query/json` is also capped in previews —
never conclude "only 2000 sellers" from one.

**Free queries** (metadata only, no scan, work at zero quota):
`nushop.INFORMATION_SCHEMA.COLUMNS`, `…INFORMATION_SCHEMA.TABLES`, and `nushop.__TABLES__`
(`row_count`, `size_bytes`) — always size a table this way before scanning it.

**BigQuery SQL gotcha:** correlated scalar subqueries in a `SELECT` list over another table are
rejected. Rewrite as `LEFT JOIN`s over CTEs.

## BigQuery databases

| id | Dataset | Notes |
|---|---|---|
| **6** | `nushop`, `csv_upload`, `fb_marketings` | The main one. Small daily quota, exhausts easily. Resets 00:00 IST. |
| **2** | team/meta mapping | card 2787 lives here |
| **23** | heavier workloads | **500 GB/day — and it does run out.** Cards 5207 / 12142 live here |

### Partition-filter requirements (query fails without them)

- `nushop.google_marketing_insights_master` → filter `spend_date`
- `nushop.changeslogs` → filter `createdat`

### Reducing scan cost

1. **Prefer an existing card over new SQL.** Cards used by the nightly run are cached and
   effectively free on the token path. Card 10469 alone covers seller × day × (meta/google/overall)
   spend **and** ARR from 2025-01 to today for 13k+ sellers.
2. **Bound every scan** on the *partition* column. A date filter on a non-partition column (e.g.
   `gc_view_3.start_date`) does **not** prune.
3. **Don't retry blindly.** A failed run still burns quota. `COUNT(*)`-only or `LIMIT`-ed first.

## Card catalogue (the ones that matter)

**Spend / ARR**
- `10469` day-wise seller-wise spend + ARR — `seller_id, date, spend_meta, spend_google,
  spend_overall, arr_meta, arr_google, arr_overall`. 2025-01 → today, ~737k rows. **The workhorse.**
  `arr_overall` **is** `ROUND(total_profit*365/80)` per day (now materialised in
  `analytics.seller_wise_day_wise_arr_frm_jan25`), which is why
  `Σ(daily arr)/days ≡ Σ(total_profit)*365/(80*days)`.
- `7336` seller × month ARR · `11020` ARR cohort matrix (M0..M6 + TARGET row) · `12072` same for
  Revenue sellers · `12186` seller-level detail behind 12072
- `2787` (db 2) meta yesterday/lifetime spend + `facebook_ad_account_id`
- `7401` google: `google_ad_account_id`, yesterday / last-3 / last-7 / lifetime spend
- `7275` `google ad account id of seller` — unnests `nushop.userprofiles.google_ad_accounts`.
  The authority for "google assets created"
- `11850` google golive month (first google-spend month)

**Google PNL — three ids, one source**
- `5207` Google Seller PNL (db 23, per-seller param) — what the pipelines use
- `6911` Google Seller PNL - Modified (db 6) — literally
  `SELECT * FROM analytics.google_seller_pnl_temp_cache`; the card on **dashboard 609**
- `7644` google-seller-pnl-duplicate — the google leg inside card 11011

5207 and 6911 return **identical** values (verified on 12 sellers, both PNL % and grossed-up spend).
The cache is rebuilt each morning, so a *closed* week's PNL still moves as delivery/RTO data lands —
an apparent mismatch is usually a stale local copy, not a fetch bug.

**Population / mapping**
- `10453` `hit_master_data` — `team, good_seller, hit_year_week, hit2, hit2_year_week,
  hit_month/year`. The HIT bucket source of truth
- `7753` seller → GC / GL / GM / KAM / KAE / AM / Golive POC. **No CL column**, and
  `growth_lead_name` is `'-'` for the whole 1k-5k book
- `nushop.seller_managers` + `nushop.users` → CL (`manager_type LIKE '%category_lead%'`), the same
  derivation card 10181 uses for `cl_name`
- `10992` assignment changelog · `11244` seller → team mapping

**Views / metrics**
- `11115` / `11727` / `11740` weekly 1k-5k HIT1 / HIT2 / HIT1+HIT2 · `11815` weekly 1k-5k Google
- `11838` / `11840` 1k-5k cohort analysis · `12264` google-live variant
- `11771` churn flag · `4118` churn final logic · `12142` / `12159` churn cohort
- `11011` `Best P&L Visibility - Hits` — **cannot back a 1k-5k view.** Its `hit_sellers` CTE is
  `NOT EXISTS (… hit_master_data …)`, so it overlaps the 1k-5k base by **0 sellers**, and
  `best_source='google'` is **0 rows card-wide** (best_source tags whichever source had the greatest
  W-1 PNL and the CASE resolves gc_view_3 → new_pnl → facebook before google)
- `11736` Clothing A2H cohort **+ the locked `campaign_type` seed** (see below)
- `12207` Clothing A2H creative test (campaign/adset/ad × day)
- `11746` platform metrics · `10181` TS SOP · `9688` seller calls

### Card 11736 — the A/B assignment authority (reworked 2026-08-05)

Its SQL now carries ~94 hand-recorded `STRUCT('<seller_id>', '<campaign_type>')` literals as a
frozen seed, `COALESCE`d over the `MOD(ABS(FARM_FINGERPRINT(seller_id)),2)` fallback, and it no
longer drops sellers once they go live (**201 qualified, 100 live**).

Card 12207 still derives `campaign_type` from the hash. **They agree 100%** (79/79 seeded sellers
that reached go-live), so the seed came from the same rule and the hash *is* the real assignment.
`creative_test_refresh.py` re-derives this every run into an `assignment` block — if it ever drops
below 100 the ITT arm is invalid and every arm comparison in that section is suspect.

```python
seed = dict(re.findall(r"STRUCT\('([0-9a-f]{24})'(?:\s+AS\s+seller_id)?,\s*'([^']+)'", sql))
```

## Useful tables (small, cheap, often overlooked)

| Table | Rows | Use |
|---|---|---|
| `nushop.userprofiles` | 47k | `google_ad_accounts` JSON array — the **only** google column there |
| `nushop.sellers` | 51k | `google_merchant_id`, `google_tag_id` |
| `nushop.google_customer` | 796 | `seller_id` + `google_ad_account_id` + `customer_status`; does **not** cover the 1k-5k book (0 overlap) |
| `nushop.seller_managers` / `nushop.users` | small | the role mapping, incl. CL |
| `csv_upload.hit_master_data` | ~2.8k | full historical dump |

## Editing card SQL — safety

Pipelines sometimes **string-replace** a known clause inside a card's SQL to build variants
(`bev_refresh` does this for the card-11815 HIT1/HIT2/both/revenue split) and `raise` if the
expected clause is missing. If you edit such a card, that guard fires and the view goes empty —
grep `pipelines/` for the card id before changing it.

## Known data-model traps

- `hit_master_data` is a **full historical dump**. The current HIT1 base is
  `team='HITS' AND good_seller IS NULL`, **not** every row with a `hit_year_week`.
- `gc_view_3.marketing_spend` is **total** (meta+google); `marketing_spend_tax_` is the same figure
  with tax (×1.18). It undercounts google by ~15% on google-heavy weeks vs card 10469.
- `gc_view_3.start_date` is **not always Monday** (~27% aren't), so joining it to ISO weeks is lossy.
- Meta `purchases` in `fb_marketing_insights` is purchase **value**; `actions_purchase` is the
  **count**. Always `breakdown_key IS NULL`.
- Adset-level Meta data (`fb_adset_breakdown_insights`) lands a **day later** than campaign level —
  never compare across levels on recent days.

---

# 5. metrics-tracker-data-model

# Metric definitions

Use these verbatim. Inventing a variant makes a new view disagree with every existing one.

## Populations

| Bucket | Definition (`csv_upload.hit_master_data`, card 10453) |
|---|---|
| **1k-5k team ("assigned")** | `ts_data.json → hitsMap` sellers with `good = 0`. **~240 and moving** |
| **HIT1** | `team = 'HITS' AND good_seller IS NULL` |
| **HIT2** | `hit2 = 1` — **42 sellers** |
| **HIT1 + HIT2** | either — **they overlap by design** in this convention (by 4 sellers today) |
| **Revenue** | `good_seller IS NULL AND team ≠ 'HITS' AND hit2 ≠ 1` |

These are the card-11815 variant predicates (`bev_refresh.py`, `_G15UNI`). **Match them for any new
bucket split.**

> Caveat: the churn cohort (cards 12142/12159) deliberately makes the three **mutually exclusive**
> because a churn cohort must not double-count. Two conventions coexist on purpose — check which
> one a view uses.

### `hitsMap` silently excludes most HIT2 sellers — know this

`ts_refresh` keeps each seller's **latest** `hit_master_data` row and only where `team = 'HITS'`.
Converting to HIT2 clears `team`, so **38 of the 42 HIT2 sellers have a NULL latest team and fall
out of `hitsMap` entirely.** Only 4 HIT2 sellers are inside the 240-seller book.

So a view built on `hitsMap` is a **HIT1 view**, not a 1k-5k view, regardless of what it's called.
To cover both, take the union with `hit2 = 1` straight from card 10453 (that's what
`google_sellers_refresh.py` does: 240 ∪ 42 = **278**, overlap 4). It is *not* that HIT2 sellers lack
data — 33 of the 42 have a google ad account.

`hit_master_data` is a full historical dump; never treat "has a `hit_year_week`" as "is HIT1".

## Roles — GL, GM, CL (this trips everyone)

| Label in the UI | What it actually is | Source |
|---|---|---|
| **GL** | the **growth-consultant-level owner** | `hitsMap.gc` = card 7753 `growth_consultant_name`, falling back to `growth_lead_name` |
| **GM** | growth manager | `hitsMap.gm` → 7753 `growth_manager_name` |
| **CL** | **Category Lead** | `nushop.seller_managers` `manager_type LIKE '%category_lead%'` → `nushop.users`; same derivation as card 10181's `cl_name` |

- **Do not wire GL to the literal `google_growth_lead` manager_type.** Only ~6 of the book has one;
  doing that made a table read 112/118 "Unassigned". Card 7753's `growth_lead_name` is `'-'` for the
  entire 1k-5k book too.
- Every `bev_refresh` detail row emits `'gl': team[sid]['gc']`. Match that or your table disagrees
  with every other drilldown.
- Deriving GM from the **GL** (via `gc2gm_all`) rather than per-seller keeps a GL's whole book inside
  one GM instead of splitting it.
- Normalise whitespace and treat the literal `'Unassigned'` as absent — see
  `metrics-tracker-pipeline`.

## ARR

**Per seller per month:**

```
ARR = Σ(daily total_profit) × 365 / (80 × days_in_period)
    ≡ Σ(daily arr_overall) / days_in_period          # card 10469's arr_overall IS total_profit*365/80
```

```
days_in_period = calendar days in that month                       (a completed month)
               = (current ISO-week start − month start) in days     (the current month)
rows dated inside the current ISO week are EXCLUDED
```

Dividing by *days-with-data* instead inflates every cell 10–20% (a seller live 20 of 31 days looked
~55% better) and breaks comparability with the targets, which are set on this basis.

### The card-11020 denominator changed on 2026-08-03 — the big one

```sql
-- BEFORE: AVG skips NULLs, so each age divided by sellers-with-ARR-at-that-age
ROUND(AVG(CASE WHEN cohort_month_num = a THEN arr END), 0)
-- NOW: every age divides by the cohort's own seller_count
SAFE_DIVIDE(ROUND(SUM(CASE WHEN cohort_month_num = a THEN arr END), 0), COUNT(DISTINCT seller_id))
```

A cohort member with **no ARR at that age counts as 0** rather than being dropped, so later ages
fall as sellers churn instead of holding up on a shrinking base. Feb-26 M3 went 2,226 → **1,484**;
M6 3,856 → **1,551**.

Consequences to preserve in any new cohort view:
- **A cell is no longer the plain average of its drilldown list.** The list holds the *k* sellers
  with ARR; the divisor is *n*, the cohort size. Every cohort drilldown title spells this out:
  `avg 1,484 = ARR / 15 cohort sellers (10 had ARR at this age)`, and each cell carries a `k of n`
  sub-line. Keep that, or the numbers look wrong.
- The **ARR formula did not change** — only the denominator.
- The **TARGET vector did not change**.
- **Card 12072 (Revenue cohort) was NOT updated** and still returns `AVG`. `bev_refresh` therefore
  recomputes its cells from the card-12186 seller detail over the same cohort `seller_count`;
  without that the HIT and Revenue tables sit on two different denominators. That block becomes a
  no-op if 12072 is ever fixed upstream.

Per-age target vector (the `TARGET` row of card 11020), age capped at M5:

| M0 | M1 | M2 | M3 | M4 | M5+ |
|---|---|---|---|---|---|
| 1,859 | 3,668 | 4,133 | 4,480 | 4,748 | 4,647 |

## ARR tranches (sections 3d / 3e)

Both sections rank a cohort's sellers by ARR and split into Top 20% / Mid 20% / Bottom 60%, against
**the cohort's own seller count** — the same whole-cohort convention as above.

| Rule | Split |
|---|---|
| **20% of sellers** | `t = round(cohort_n × 0.2)`; top = ranked[0:t], mid = [t:2t], bottom = the rest. Top/mid divide by `t`, bottom by `max(n − 2t, 1)` |
| **20% of ARR** | walk the ARR-ranked list cutting at 20% / 40% of the cohort's total ARR; each group divides by its own size |

- **3d re-ranks at every age** — a column answers "who is top 20% *right now*".
- **3e freezes the rank at M1** — the cohort is ranked once on M1 ARR and that same seller set is
  reported at every age, so membership never changes across a row. Implemented as a `freezeAt` prop
  on the same component, not a copy. At M1 the two tables are **identical by construction** — that's
  the invariant to check after any change.
- An age the cohort **hasn't reached** is blank; a **zero** means the age has data and none of the
  frozen tranche earned any of it (Feb-26's M1 winners really did decay to ~0 by M6 — that's signal).

## Google assets, live, spending

| Metric | Definition |
|---|---|
| **Total assigned** | every 1k-5k seller on the book |
| **Total google assets created** | a `google_ad_account_id` exists — non-empty `nushop.userprofiles.google_ad_accounts` |
| **Acceptance** | assets created ÷ total assigned |
| **Live sellers** | lifetime google spend > **10** (card 7401 `total_marketing_spend_with_tax`) |
| **Yesterday spending** | google spend yesterday > **1** |
| **Spend/Live** | sellers spending yesterday ÷ live sellers (**a rate, not rupees**) |
| **3K** | google spend last 7 days > **3540** |
| **3K Spend/Live** | 3K sellers ÷ live sellers |

All pooled — the Total row divides the summed numerator by the summed denominator, never averages
the per-GM rates.

> **"Assets created" is 119 on the current book, not 146.** Verified four ways: `scaling_data.ga`,
> card 7401, card 7275, and the raw `userprofiles.google_ad_accounts` array all return the same set,
> every one with a populated id (no created-but-unlinked gap). The ceiling for *any* google asset
> (ads account ∪ merchant id ∪ tag id) is **123**. A circulated 146-seller list turned out to include
> 31 sellers with no google asset at all, 2 with only merchant-id/tag, and 1 not on the book — while
> omitting 7 that do qualify. On the wider HIT1+HIT2 roster the count is **150**.

## Spend / Live (the older, separate definitions)

Two variants; both correct for their purpose.

**A · Snapshot (KPI cards, "yesterday")** — from `scaling_data.json` (`my`/`gy`/`gt`/`ga`):

| Channel | Numerator ("spending") | Denominator ("live") |
|---|---|---|
| Meta | `my > 1` | **all assigned** 1k-5k sellers |
| Google | `gy > 50` | sellers **Google-live** |
| Blended | `my > 1 OR gy > 50` | all assigned |

The asymmetry is deliberate: a seller not spending on Meta is a failure, but a seller with no Google
account can't be faulted; Google also uses a ₹50 floor because of trickle spends.

**B · Day-wise weighted (Target vs Achievement, incentives)**

```
Spend/Live % = Σ(seller-days with channel spend > 0) ÷ (settled days in month × live sellers)
```
Built from card 10469 via `perfByDate`. "Settled day" = a past day where booked spend > 0.

## Google-live

```
has google_ad_account_id  AND  lifetime google spend > 10
```
Authority: **card 7401** — the same source as Spend/Live and the go-live multiplier, so numbers tie.

## Go-live multiplier (Google)

```
Google Golive % = (sellers Google-live) ÷ (sellers assigned)     per GC, rolled up pooled to GM
```
| Golive % | Effect |
|---|---|
| **< 50%** | **0 — hard gate, the entire incentive becomes 0** |
| 50–65% | 1.0× |
| > 65% | 1.25× |

`gGoliveDelta = ceil(0.5 × gAcc) − gLive` is the actionable "how many more must go live". Reads a
**current snapshot**, not a month-end freeze.

## Google PNL buckets — mutually exclusive tiers

Weekly google PNL from card 5207, spend gate **3540** (the canonical `bucket_refresh` thresholds):

```
health     = w1_spend > 3540 and w1_pnl > -20
potential  = w1_spend > 3540 and w1_pnl > 5
objective  = w1_spend > 3540 and w2_spend > 3540 and w1_pnl > 5 and w2_pnl > 5
subjective = same as objective but w2_pnl only > 3      (computed, NOT displayed)
```

These **nest**: `objective ⊆ potential ⊆ health`. Reported as **exclusive tiers** so a seller is
counted once, in its best tier:

```
Objective      = objective
Potential      = potential AND NOT objective
Bucket Health  = health    AND NOT potential AND NOT objective
```
The three exclusive counts must sum back to the raw `health` count — that's the check.
HIT1 today: raw 27/18/5 → **9 / 13 / 5**.

## Target vs Achievement (GM-wise, 1k-5k)

**ARR target = Σ per-age targets of running sellers.** For each seller in the GM's cohort, age =
months since their HIT1 month, look up the per-age target, sum.

- **ARR achieved** = Σ those sellers' actual monthly ARR (card 7336)
- **HIT2 target** — per GC per month from the **Collated sheet**; **HIT2 achieved** = `hit2=1` count
  for the report month, credited to the owner **at conversion** (card 10992)
- **`qualified`** = HIT2 achieved ≥ HIT2 target **AND** ARR achieved ≥ **85%** of ARR target
- **`delta`** = `max(0, 0.85 × arrT − arrA)` — gap to the 85% gate, not to 100%
- **Churn column** uses the stricter legacy rule: revenue spend ≥ **₹11,800** AND no spend > **21 days**

TvA **sums** targets and ARR over running sellers, so the card-11020 denominator change does **not**
touch it. Verified: `arrT`/`arrA` still equal the sum of their own drilldowns.

### The HIT2 freeze (critical)

When a seller converts HIT1 → HIT2 they graduate out of 1k-5k, so at conversion we freeze:
1. **Conversion Friday** = Friday of `hit2_year_week` (earliest one)
2. **Frozen ARR** = latest daily `arr_overall` (card 10469) on/before that Friday
3. **Frozen age** — target stops accruing from the conversion month
4. **Frozen owner** — credit to the GC/GM who owned them at conversion (card 10992)

Freeze applies only when `HIT1 month ≤ conversion month ≤ report month`; a HIT2 week before the HIT1
month is bad data → seller stays active. Detail rows carry `frozen` / `freezeMonth`.

## Incentive %

```
base × arrMult × churnMult × metaMult × googMult × goliveMult      (0 if ANY gate fails)
```
| Factor | Rule |
|---|---|
| base (HIT2 attainment) | ≥100% → 25 · 50–99% → 15 · <50% → **0** |
| ARR | gate ≥85% of target, else 0; ≥2× → 2.0 · ≥1.5× → 1.25 · else 1.0 |
| Churn | 0 churns → 1.0 · 1 → 0.5 · ≥2 → **0** |
| Meta S/L | <60% → **0** · 60–80% → 1.0 · >80% → 1.25 |
| Google S/L | <65% → **0** · 65–75% → 1.0 · >75% → 1.2 |
| Google golive | <50% → **0** · 50–65% → 1.0 · >65% → 1.25 |

Task-compliance/TS and NPS gates are not available per-GM and are **not** applied.

## Churn

**Base rule (cards 4118 / 11771):** churned = no week with `marketing_spend_tax_ ≥ 1000` in the last
**21 days**. Weekly grain. The **churn week** = that last spend week.

**Cohort churn (card 12159, the current one):**
- eligibility: ≥21 days since handover, where **handover = Friday of the hit week**
- **churn age** = `ROUND((churn_week − hit_week) / 4.5)` — week-based months, *not* calendar diff
- **cohort month** = month of the **Monday of the hit week**
- Revenue sellers are anchored on their **first REVENUE-team week**
- buckets are mutually exclusive here; `M12+` collects everything beyond 12 months

Cumulative churn curves must be **monotonic** and are clipped at each segment's last month with
actual churn (no flat extrapolated tail). Chart drilldowns are **incremental** (churned *at* that
month) so a seller appears in exactly one point.

## Spend & ARR trend (the day-on-day chart)

Spend and ARR aggregate **differently, deliberately**:

```
spend  is a flow            -> a bucket is the SUM of its days
ARR    is an annualised rate -> a bucket is the MEAN of its daily values
```
Summing daily ARR across a week would produce a number 7× the real rate. The window "avg daily ARR"
stat is averaged over **days**, not buckets, so it doesn't drift when the day/week toggle flips (the
edge ISO weeks are partial). Partial ISO weeks are flagged, not hidden.

## S/GMV and the funnel

```
S/GMV % = spend ÷ GMV × 100      (LOWER is better; 100% = spent ₹1 to make ₹1 of top-line)
```
Always **pooled**: `Σspend / ΣGMV`. Quote *pooled* to finance and *median* for "a typical adset" —
means are wrecked by a few catastrophic outliers.

Decomposition: **CPM** (cost of reach) → **CTR** (does the asset earn a click) → **C2PR**
(click→purchase) → **AOV**. Isolating the broken step tells you what to fix.

GMV sources: **Meta-attributed** below seller level; **true platform GMV** (`nushop.orderitems`)
only at seller level — use it as the sanity check.

## Cohort analyses

| Table | Cohort row | W0 / M0 | Cells |
|---|---|---|---|
| ARR Cohort (card 11020) | HIT month | M0 = HIT month | avg ARR ÷ **cohort seller count**; green = at/above target |
| ARR tranches 3d / 3e | HIT month | M0 = HIT month | tranche avg ARR; 3e freezes the rank at M1 |
| Cohort Analysis 1k-5k (section 6, card 11840) | HIT month | W0 = HIT week | `t` present, `s` spending (>0), `g` ≥₹3,000 |
| Cohort Analysis 1k-5k **Google** (card 12264) | month of **first google spend** | W0 = week of first google spend | same; cohorts from **Mar-26**; HIT1/HIT2/both/Revenue toggle |
| Churn cohort (card 12159) | hit-week month | M0 = same month | churn counts by age |

**3K Retention = ≥3K sellers at Wn ÷ spending sellers at W0.** Spend threshold ₹3,000/week; the
separate troubleshoot/scaling threshold is ₹3,540 (`SPEND3K`).

Google is switched on **weeks after** HIT (often months) — that's why the Google cohort re-anchors on
first google spend rather than the HIT week.

## Golive Creative Testing — the A/B

| Grouping | What it is |
|---|---|
| **ITT (intent-to-treat)** | card 12207's `campaign_type`, from `MOD(ABS(FARM_FINGERPRINT(seller_id)),2)`. **Confirmed to be the real assignment** — card 11736 carries 94 hand-recorded locked values and the hash reproduces 79/79 of those that reached go-live. Unbiased comparison. |
| **Per-protocol** | what was actually executed, from the **adset name**. Biased by self-selection, but the only way to split arm-vs-arm inside a B seller. |

The gap between them (**77/85 = 91%** agree) is **seller non-compliance**, not assignment error —
e.g. sellers assigned to catalogue who never built a catalogue adset.

Arm keywords: `catalog` / `cat.` / `all product` → Catalogue · `banner` / `video` / `creative` /
`ugc` → Creative · both → Both · neither → Unclassified (a naming-hygiene gap, ~9 adsets).

Cohort funnel from card 11736: **201 A2H-qualified, 100 live** — the tracker covers the live ones.

## Other constants

- `COHORT_EXCLUDE` — 26 hardcoded seller ids excluded from the 1k-5k cohort (mirrors card 10881)
- ARR cohort membership: HIT month ≥ `202510`, non-good, HITS-or-HIT2
- Canonical 1k-5k GL list comes from the **Collated sheet**; a seller under an unlisted GC is dropped
  from both target and achievement

---

# 6. metrics-tracker-deploy

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


---

# Appendix — quick reference

## Commands

```bash
node build.mjs                                              # build (ends "Babel dropped: true")
npx --yes vercel@latest --prod --yes --token "$(cat ~/.vc_token)"   # deploy (needs >=7 min)
gh workflow run refresh.yml -R pawankumar-pkaytsk/shopdeck-metrics-tracker
python3 pipelines/<name>_refresh.py                         # run one pipeline
python3 -c "import ast;ast.parse(open('pipelines/x.py').read())"    # compile-check
```

## Post-deploy verification (all three)

```bash
curl -s https://hits-tracker.xyz/bev_data.json | python3 -c "import json,sys;print(json.load(sys.stdin)['generatedAt'])"
curl -s https://hits-tracker.xyz/main.js | grep -c '<YourComponent>'
curl -s https://hits-tracker.xyz/main.js > /tmp/live.js && shasum -a 256 public/main.js /tmp/live.js | awk '{print $1}' | uniq | wc -l   # 1 = identical
```

## Known outstanding items

- **`bev_refresh.py` has no session-token fallback.** It is the heaviest and most quota-exposed
  pipeline in the repo; adding the `fetch()` wrapper from §3 is the highest-value hardening left.
- **Card 12072 (Revenue ARR cohort) still returns `AVG`.** `bev_refresh` rebases its cells onto the
  cohort seller count to match card 11020; fixing 12072 upstream makes that block a no-op.
- **Card 12207 still derives the A/B arm from the hash.** That is currently correct (it matches
  card 11736's locked seed 100%), but the check must keep passing — see the `assignment` block.
- ~9 adsets in the creative test have blank names, so their arm can't be read; a naming fix at
  source recovers them.

Generated 2026-08-06 · repo @ b35ba3c3
