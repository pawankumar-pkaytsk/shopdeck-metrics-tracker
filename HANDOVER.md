# Shopdeck Metrics Tracker — Complete Handover

**Live dashboard:** https://hits-tracker.xyz
**Repo:** `pawankumar-pkaytsk/shopdeck-metrics-tracker` (branch `main`)
**Packaged:** 2026-07-30 · repo commit `3f92142e`

This one file is everything needed to take over the project. It is self-contained so you can read
it *before* you have repo access.

- **Part 1 — Onboarding.** What the system is, access to request, architecture, how to make a
  change, local setup, CI, operational gotchas, handoff checklist.
- **Part 2 — Claude Code skills.** Six deep-dive references. These are also committed at
  `.claude/skills/<name>/SKILL.md`, so once you clone the repo, Claude Code loads them
  automatically and you can stop reading this file.

**No credentials are in this document** — only paths and secret *names*. Credentials transfer
separately (Part 1 §2 and §9).

---

# Part 1 — Onboarding

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

## 9. Handoff checklist
- [ ] New owner added as GitHub collaborator (write).
- [ ] Metabase API key issued on the 500 GB-quota account; `METABASE_API_KEY` secret rotated to it.
- [ ] Google SA has Sheets access; `GOOGLE_SA_KEY` valid.
- [ ] Vercel org membership (if manual deploys needed).
- [ ] New owner cloned repo, created `~/metabase-arr-refresh/.mbcreds`, ran `python3 pipelines/bev_refresh.py` once successfully.
- [ ] Confirmed a `gh workflow run refresh.yml` completes and redeploys.

---

# Part 2 — Claude Code skills

Six skills live at `.claude/skills/<name>/SKILL.md` in the repo (tracked in git — `.gitignore`
un-ignores `.claude/skills/` and `.claude/launch.json`). With the repo cloned, Claude Code picks
them up by their `description` field; you do not need to invoke them by name.

| Skill | Use it for |
|---|---|
| `metrics-tracker-orientation` | Start here. Repo map, architecture, which file/pipeline/card backs each screen. |
| `metrics-tracker-edit-view` | Editing index.html (the JSON-encoded bundler template), components, drilldown modals, browser verification. |
| `metrics-tracker-pipeline` | Writing/changing a pipelines/*_refresh.py — skeleton, conventions, verification invariants. |
| `metrics-tracker-metabase` | Metabase API, card catalogue, BigQuery databases/partitions, quota workarounds. |
| `metrics-tracker-data-model` | Every business definition: buckets, Spend/Live, Google-live, TvA, incentives, churn, S/GMV, cohorts. |
| `metrics-tracker-deploy` | Build, manual + CI deploy, the 5 workflows, and the git-clobber trap. |

The full text of each follows, verbatim.

---

## Skill: `metrics-tracker-orientation`

*File:* `.claude/skills/metrics-tracker-orientation/SKILL.md`
*When it applies:* Start here for the Shopdeck Metrics Tracker dashboard (hits-tracker.xyz). Repo map, architecture, where each screen's data comes from, and which other skill to use next. Use when asked to change, debug, or extend this dashboard, or when you need to find which file/pipeline/card backs a given view.

## Shopdeck Metrics Tracker — orientation

Static React-in-HTML analytics dashboard for the HITS / 1k-5k team.

- **Live:** https://hits-tracker.xyz
- **Repo:** `pawankumar-pkaytsk/shopdeck-metrics-tracker`, branch `main`
- **Hosting:** Vercel project `shopdeck-dashboard`
- **Full handover doc:** `ONBOARDING.md` in the repo root — read it once; it covers access, secrets, CI and the handoff checklist. This skill set is the working companion to it.

### Architecture in one picture

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

### Repo map

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

### Which file backs which screen

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

### Two data roots inside `bev_data.json` — get this right

- `data.cards.*` — the older root. **Target vs Achievement and the ARR cohort live at `cards.cohort`** (with `cards.cohort.tva`).
- `data.bev2.*` — the numbered Bird's-Eye sections (1..18) read from here.

**Put a new numbered section's data in `bev2`.** Getting this wrong renders an empty section.

> Real incident: a loop variable named `cohort` inside `bev_refresh.py` shadowed the module-level
> `cohort` dict, so `cards.cohort` was written as the string `'M0'` and both the ARR Cohort and the
> whole TvA view went blank for two days. Never reuse `cohort`, `churn`, `arr`, `target` as local
> names in that file.

### Working rules for this repo

1. **Verify data, don't trust the render.** After any change, re-read the JSON and assert the
   specific numbers. Pooled ratios (`sum(num)/sum(den)`), never an average of per-row ratios.
2. **Check your own comparison logic before declaring a data bug.** A string mismatch
   (`'Only Creative'` vs `'Creative only'`) once produced a bogus "the column is random" claim.
3. **`continue-on-error: true`** on every CI pipeline step means a step can crash and still show
   green. Grep the log for `Traceback`.
4. **Never overlap two data-writing workflow runs** — see `metrics-tracker-deploy`.
5. Ratios and thresholds are business definitions, not arbitrary. Look them up in
   `metrics-tracker-data-model` before inventing one.

### Next skill

| Task | Skill |
|---|---|
| Change/add a view, section, table, chart | `metrics-tracker-edit-view` |
| Add or change a data pipeline | `metrics-tracker-pipeline` |
| Query Metabase, create/edit a card, hit BigQuery quota | `metrics-tracker-metabase` |
| Look up a metric definition (1k-5k, HIT1/HIT2, Spend/Live, churn, S/GMV, cohorts, incentives) | `metrics-tracker-data-model` |
| Build, deploy, CI, git-clobber troubleshooting | `metrics-tracker-deploy` |

---

## Skill: `metrics-tracker-edit-view`

*File:* `.claude/skills/metrics-tracker-edit-view/SKILL.md`
*When it applies:* Edit the Shopdeck Metrics Tracker UI in index.html — the app lives inside one JSON-encoded bundler template, so normal text edits corrupt it. Use when adding or changing a section, table, chart, toggle, nav pill or drilldown modal in this dashboard.

## Editing the dashboard UI (`index.html`)

The entire app is one JSON-encoded string inside
`<script type="__bundler/template"> … </script>`. You **cannot** use Edit/Write on it directly,
and you cannot grep it usefully as plain text. Always decode → patch → re-encode with Python.

### The canonical edit pattern

```python
import json, re
p = 'index.html'; content = open(p).read()
m = re.search(r'(<script type="__bundler/template">)(.*?)(</script>)', content, re.DOTALL)
html = json.loads(m.group(2).replace(r'<\/', '</'))      # decode

old = "…exact snippet…"
new = "…replacement…"
assert html.count(old) == 1, html.count(old)              # ALWAYS assert uniqueness
html = html.replace(old, new, 1)

open(p, 'w').write(content[:m.start(2)] + json.dumps(html).replace('</', r'<\/') + content[m.end(2):])
```

Then **always**:

```bash
node build.mjs   # success ends with: "Babel dropped: true"
```

A JSX syntax error prints a Babel stack trace and writes nothing — fix before continuing.

#### Hard-won rules

- **`assert html.count(old) == 1`** on every replacement. Batch edits as a list of
  `(old, new)` pairs and assert each; the whole script then fails atomically before writing.
- **Never slice between two markers you also introduced.** Doing index-based
  `html[:start] + new + html[end:]` where `end` matched a comment inside *your own newly
  inserted component* corrupted the file. Either (a) do text replacements only, or (b) apply
  the text replacement **first** and insert the new component **second**, or (c) make your
  new component's comments unique.
- To replace a whole function, locate it by boundaries and confirm ordering:
  ```python
  start = html.index("  function Foo({ ... }) {")
  end   = html.index("  function NextKnownFn(", start)   # note: search AFTER start
  html = html[:start] + new_fn + html[end:]
  ```
- **Escaped characters:** Babel escapes `·` → `·` and `—` → `—` in `public/main.js`,
  so grepping the compiled file for those literals fails. Grep the decoded `html` string, or
  grep for an identifier instead. In the decoded string the characters are literal (`·`, `—`).
- Inside the JSON-encoded source, a JS single-quote escape appears as `\'`; when writing Python
  replacement strings, match what's actually there (print `repr(...)` of the region first).

### Reusable components already in the app

| Component | Signature / use |
|---|---|
| `BevSection` | `{ tone, title, sub, awaiting, children }`. Tones: `blue green amber purple rose teal slate` (`BEV_TONES`). Standard wrapper for a numbered section. |
| `FloatingTabBar` | `{ items:[{value,label,icon}], value, onChange, size:'lg'\|'md' }`. No colour prop — for a coloured pill, hand-roll a styled `div` (see the "Track Ongoing Projects" nav pill). |
| `CRModal` | `{ title, rows, cols, onClose }` — the drilldown modal used across Bird Eye View. |
| `DataTable` | `{ columns, rows }` |
| `SectionTitle` | `{ title, sub, right }` |
| `Card` | `variant="regular"`, `padding={0}` |
| `Button` | `size`, `variant`, `icon`, `onClick` |
| `StatCard` | `{ label, value, sub, onClick }` |
| `TimeSwitch` | render-prop toggle: `<TimeSwitch modes={[{k,label}]} initial="x">{(mode)=>…}</TimeSwitch>` |
| `exportCSV`, `exportPNG` | CSV / PNG export helpers; mark chrome `className="no-export"` |
| `CohortHeatmap1k5k` | `{ open, src, dataOverride }` — cohort heatmap; `src` picks the JSON, `dataOverride` supplies pre-fetched `{rows, cohorts, maxWeek}` so a wrapper can own extra toggles. |

### Drilldown modals — the recurring bug

`CRModal` renders each cell as **`col.render ? col.render(row) : row[col.key]`**.

A column that only has `get` (used for CSV export and sorting) renders **blank** when the row is
an array or the key doesn't exist. This has caused blank Seller/ARR/name columns three separate
times.

**Rule: every drilldown column needs a `render`.**

```jsx
const cols = [
  { key: 's',  label: 'Seller ID', get: r => r[0], render: r => <span style={{ font:'400 12px/1 monospace' }}>{r[0]}</span> },
  { key: 'n',  label: 'Seller',    get: r => r[1], render: r => r[1] || '(unnamed)' },
  { key: 'arr',label: 'ARR', align:'right', get: r => r[2], render: r => num(r[2]) },
];
```

Open it with the section's `open(title, rows, cols)` helper
(`const open = (title, rows, cols) => setModal({ title, rows: rows||[], cols })`, rendered as
`<CRModal title={modal&&modal.title} rows={modal&&modal.rows} cols={modal&&modal.cols} onClose={()=>setModal(null)} />`).

### Adding a whole new nav destination

Four coordinated edits (all must be present or the tab silently does nothing):

1. **Component** — insert the function before a known anchor, e.g. `  function BirdEyeView() {`.
2. **Nav pill** — add a `FloatingTabBar` (or custom styled div) in the nav row, before
   `<div style={{ marginLeft: 'auto' }}><SnapshotPicker /></div>`.
3. **Route** — extend the ternary chain:
   `{section === 'projects' ? <ProjectsView /> : section === 'allreports' ? … }`
4. **Breadcrumb** — add an `else if (section === 'projects') { crumbs.push({ label: '…' }); }`.

### Verify in the browser (always do this)

The dev server serves `public/`, so **run `node build.mjs` first**.

```
preview_start { name: "metrics-public" }      # .claude/launch.json, port 8099
```

Then, because screenshots after scrolling often render blank, prefer `javascript_tool`:

1. Click **"Skip — browse with sample data"** (it's a `<p>`, not a button — click the leaf element
   whose text starts with `Skip`).
2. Pick the role: the button text reads `I want to view as Leadership…`.
3. Click the section pill by exact text (`Bird Eye View`, `Google`, …). Clicking may need a second
   call — React re-renders between calls.
4. Read values back out of the DOM and **assert them against the JSON**, e.g.
   `[...table.querySelectorAll('tr')].map(tr => [...tr.cells].map(c => c.textContent.trim()))`.

Gotchas:
- Suggestion lists may use **`onMouseDown`**, not `onClick` — dispatch
  `new MouseEvent('mousedown', {bubbles:true})`.
- Reading a value in the *same* call as the click returns the pre-render state. Split into two calls.
- Never dump `document.body.innerText` of the whole app or an unfiltered `querySelectorAll('*')`
  map — the output blows the token limit. Scope to a section element first.
- Console shows pre-existing noise: Babel 500 KB "deoptimised" notes, GSI popup failures, React
  duplicate-key warnings. Those are not your bug.

---

## Skill: `metrics-tracker-pipeline`

*File:* `.claude/skills/metrics-tracker-pipeline/SKILL.md`
*When it applies:* Write or modify a Shopdeck Metrics Tracker data pipeline (pipelines/*_refresh.py that turns Metabase cards into a committed *.json). Use when adding a new dashboard data source, changing how a metric is computed, or debugging a pipeline that produced wrong or empty data.

## Writing a pipeline (`pipelines/*_refresh.py`)

One pipeline = one output `*.json` in the repo root. It fetches Metabase card(s), reshapes into
exactly what the view needs, and dumps compact JSON. All computation belongs here, **not** in the
browser — the frontend is static.

### Skeleton (copy this)

```python
#!/usr/bin/env python3
"""Build <name>_data.json — <what the view shows>.

Source: card <id> (<what it returns>). Grain: <one row per …>.
<Every non-obvious definition, threshold and caveat goes in this docstring — it is the
 only place a future maintainer will look.>

Run: cd ~/shopdeck-metrics-site && python3 pipelines/<name>_refresh.py
"""
import json, os, urllib.request, datetime
from collections import defaultdict

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


def req(url, method="GET", body=None, H=None):
    import time as _t
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    last = None
    for attempt in range(4):                     # retry — Metabase 5xx/timeouts are common
        try:
            with urllib.request.urlopen(r, timeout=900) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last = e; _t.sleep(3 * (attempt + 1))
    raise last


def fnum(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0


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

    rows = req(f"{url}/api/card/{CARD}/query/json", "POST", {}, H)
    # … reshape …

    out = {"generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
           "card": CARD, "rows": …, "dq": {…}}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))     # compact: no spaces
    print(f"[<name>] … -> {OUT}")


if __name__ == "__main__":
    main()
```

### Non-negotiables

1. **`separators=(",", ":")`** — these files ship to the browser; whitespace is bandwidth.
2. **`generatedAt`** in every output, plus a `dq` (data-quality) block: input row counts, rows
   dropped and why, threshold values used, cross-check mismatch counts. The views surface these,
   and they are how you debug a month later.
3. **A `print()` summary line** ending in `-> {OUT}` — this is what you read in CI logs.
4. **Pooled ratios only**: `round(100*num/den, 2) if den else None`. Never average per-row ratios.
   Emit `None` for undefined, never `0` — the view renders `—`/`no GMV`.
5. **Graceful degradation.** Wrap optional sources in `try/except`, print the failure, keep going.
   Where a view would break on empty data, fall back to the previous JSON (see the
   `prev_detail`/no-clobber guards in `gc_view_refresh.py` and `bev_refresh.py`).
6. **Register it in CI** — add a step to `.github/workflows/refresh.yml`:
   ```yaml
   - name: <Name> (card <id>)
     continue-on-error: true
     run: python pipelines/<name>_refresh.py
   ```
   A pipeline not in a workflow silently goes stale forever.
7. **Compile-check**: `python3 -c "import ast;ast.parse(open('pipelines/x.py').read())"`, then run
   it locally and inspect the JSON before committing.

### Verification discipline (this is where bugs are actually caught)

After running, load the JSON and assert invariants. Real checks that caught real bugs:

```python
## subset:      google cohort ⊂ all-1k-5k cohort
assert gsids <= asids
## consistency: arm columns sum to the total (±1 for independent rounding)
assert abs(sum(cells) - total) <= 1
## anchoring:   every seller appears exactly once at W0, all with google spend
assert w0_rows == n_sellers and w0_without_google == 0
## set algebra: both == hit1 ∪ hit2, revenue disjoint from both
assert bo == (h1 | h2) and not (rv & bo)
## cross-source: compare the anchor against an independent card, count mismatches
```

Compare like-for-like vintages: two JSONs generated a day apart *will* differ on recent weeks
because spend accrues. Re-run both before concluding there's a bug.

### Reading other pipelines' output

Later pipelines may read earlier ones' JSON (that's why `bev_refresh` runs last in
`refresh.yml`). Use `load_json(name, default)`; never assume a file exists.

### Heavy-pipeline notes

- `bev_refresh.py` is ~15–24 min, fetches card 10469 (~727k rows, 2025-01 → today) **once** and
  reuses it. Don't add a second fetch of it.
- It needs `GOOGLE_SA_KEY` (or a local SA key) for the Sheets-backed TvA/cohort inputs; without it
  those views come out empty and a no-clobber guard preserves the previous values.
- It writes atomically (serialize fully in memory, then write) so a serialization error cannot
  truncate `bev_data.json`. **Keep it that way.**
- **Variable shadowing is a live hazard** in this file: a local named `cohort` once overwrote the
  module-level ARR-cohort dict and blanked two whole views. Prefix loop locals (`_ccoh`, `_r`).

### Where the output goes in `bev_data.json`

- new numbered Bird's-Eye section → `bev2.<key>`
- TvA / ARR cohort → `cards.cohort` (already occupied; extend, don't replace)

Standalone views should get their **own** `*.json` + pipeline instead of bloating `bev_data.json`.

---

## Skill: `metrics-tracker-metabase`

*File:* `.claude/skills/metrics-tracker-metabase/SKILL.md`
*When it applies:* Query Metabase and BigQuery for the Shopdeck Metrics Tracker — read/create/edit cards via the API, the card catalogue, BigQuery databases and partition rules, and how to work around the daily quota. Use when you need data that isn't in a *.json yet, need to inspect or change a card's SQL, or hit a BigQuery quota / partition-filter error.

## Metabase & BigQuery

Base URL and credentials live in `~/metabase-arr-refresh/.mbcreds` (JSON, gitignored, **outside**
the repo):

```json
{ "METABASE_URL": "...", "METABASE_USER_EMAIL": "...", "METABASE_PASSWORD": "...", "METABASE_API_KEY": "mb_..." }
```

Never print, commit or echo these values. In CI they come from repo secrets.

### API recipes

```python
import json, os, urllib.request
e = json.load(open(os.path.expanduser("~/metabase-arr-refresh/.mbcreds")))
url = e["METABASE_URL"].rstrip("/")
H = {"x-api-key": e["METABASE_API_KEY"], "Content-Type": "application/json"}

def req(u, m="GET", b=None):
    r = urllib.request.Request(u, data=(json.dumps(b).encode() if b is not None else None),
                               headers=H, method=m)
    return json.loads(urllib.request.urlopen(r, timeout=600).read())

card  = req(f"{url}/api/card/12207")                      # metadata + SQL
rows  = req(f"{url}/api/card/12207/query/json", "POST", {})  # run it
```

- **Read a card's SQL:** `card["dataset_query"]` is either `{"native":{"query":…}}` **or**
  `{"stages":[{"native":…}]}` (newer MBQL). Handle both:
  ```python
  q = card["dataset_query"]
  sql = q["stages"][0]["native"] if "stages" in q else q["native"]["query"]
  ```
- **Edit a card:** mutate that same structure and `PUT {"dataset_query": q}` (optionally `name`).
- **Create a card:**
  ```python
  req(f"{url}/api/card", "POST", {"name": "...", "display": "table",
      "visualization_settings": {},
      "dataset_query": {"type": "native", "native": {"query": sql}, "database": 6},
      "collection_id": None})
  ```
- **Ad-hoc SQL** (no card): `POST /api/dataset` with
  `{"database": 6, "type": "native", "native": {"query": sql}}` → `data.cols` / `data.rows`.
- **Column names are snake_case** (`arr_overall`, not `ARR_All__c`).
- `/api/card/<id>/query/json` returns at most **2000 rows in previews**; a pipeline run gets all
  rows. Don't conclude "only 2000 sellers" from a preview.

### BigQuery databases

| id | Dataset | Notes |
|---|---|---|
| **6** | `nushop`, `csv_upload`, `fb_marketings` | The main one. **Small daily "default plan" quota (single-digit GB) — exhausts easily.** Resets 00:00 IST. |
| **2** | team/meta mapping | card 2787 lives here |
| **23** | 1 TB/day | card 7401, card 12142 — use for anything heavy |

#### Partition-filter requirements (query fails without them)

- `nushop.google_marketing_insights_master` → filter `spend_date`
- `nushop.changeslogs` → filter `createdat`

#### Quota errors and the workarounds

`HTTP 400: "This query would scan ~16 GB, exceeding your remaining daily quota of 13 GB"` or
`"Daily quota exceeded"`.

1. **Prefer an existing card over new SQL.** Cards used by the nightly run are usually cached and
   effectively free. Card 10469 alone covers seller × day × (meta/google/overall) spend **and** ARR
   from 2025-01 to today for 13k+ sellers — most "I need spend history" questions need no new query.
2. **Session-token fallback.** The api-key path forces a *fresh* BigQuery scan; a **session token**
   returns Metabase's **cached** result. Build every fetch this way:
   ```python
   def fetch(cid):
       try:      # api-key: fresh (costs quota)
           return req(f"{url}/api/card/{cid}/query/json", "POST", {})
       except Exception:
           tok = req(url + "/api/session", "POST",
                     {"username": e["METABASE_USER_EMAIL"], "password": e["METABASE_PASSWORD"]})["id"]
           r = urllib.request.Request(f"{url}/api/card/{cid}/query/json", data=b"{}",
                 headers={"X-Metabase-Session": tok, "Content-Type": "application/json"}, method="POST")
           return json.loads(urllib.request.urlopen(r, timeout=900).read())
   ```
   Already used by `revival_refresh`, `lt_refresh`, `gc_view_refresh`, `kae_refresh`,
   `cohort_google_refresh`.
3. **Bound every scan.** Add date predicates on the *partition* column. Note a date filter on a
   non-partition column (e.g. `gc_view_3.start_date`) does **not** prune — it won't save you.
4. **Move heavy work to db 23** (1 TB/day) when the same tables exist there.
5. **Don't retry blindly.** A failed run still burns quota. Estimate first, or test on a
   `LIMIT`-ed / `COUNT(*)`-only version.

### Card catalogue (the ones that matter)

**Spend / ARR**
- `10469` day-wise seller-wise spend + ARR — `seller_id, date, spend_meta, spend_google, spend_overall, arr_meta, arr_google, arr_overall`. 2025-01 → today, ~727k rows. **The workhorse.**
- `7336` seller × month ARR · `11020` ARR cohort matrix (M0..M6 + TARGET row) · `12072` same for Revenue sellers · `12186` seller-level detail behind 12072
- `2787` (db 2) meta yesterday/lifetime spend + `facebook_ad_account_id`
- `7401` (db 23) google: `google_ad_account_id`, yesterday / last-3 / last-7 / lifetime spend
- `11850` google golive month (first google-spend month) — authority for "when did google start"

**Population / mapping**
- `10453` `hit_master_data` — `team, good_seller, hit_year_week, hit2, hit2_year_week, hit_month/year`. The HIT bucket source of truth.
- `7753` seller → GC / GM / GL / KAM / KAE / AM / Golive POC (roles)
- `10992` assignment changelog (who owned a seller when; has leading-holder periods)
- `11244` seller → team mapping (HIT / REVENUE)

**Views / metrics**
- `11115` / `11727` / `11740` weekly 1k-5k HIT1 / HIT2 / HIT1+HIT2 · `11815` weekly 1k-5k Google
- `11838` / `11840` 1k-5k cohort analysis (11840 = seller-level detail) · `12264` google-live variant
- `11771` churn flag (revenue + hit1) · `4118` churn final logic · `12142` / `12159` churn cohort (12159 = week-based age)
- `12207` Clothing A2H creative test (campaign/adset/ad × day)
- `11746` platform metrics · `11576` google benchmarking · `10181` TS SOP · `10959`+`11244` KAE tasks
- `9688` seller call records (sharded into `calls/`) · `11911` revival log · `9353` hypercare movement

### Editing card SQL — safety

Pipelines sometimes **string-replace** a known clause inside a card's SQL to build variants
(`bev_refresh` does this for the card-11815 HIT1/HIT2/both/revenue split) and `raise` if the
expected clause is missing. If you edit such a card, that guard fires and the view goes empty —
grep `pipelines/` for the card id before changing it.

### Known data-model traps

- `hit_master_data` is a **full historical dump** (~2,800 rows). The current HIT1 base is
  `team='HITS' AND good_seller IS NULL` (~205), **not** every row with a `hit_year_week`.
- `gc_view_3.marketing_spend` is **total** (meta+google); `marketing_spend_tax_` is the same figure
  with tax (×1.18). It undercounts google by ~15% on google-heavy weeks vs card 10469.
- `gc_view_3.start_date` is **not always Monday** (~27% aren't), so joining it to ISO weeks is lossy.
- Meta `purchases` in `fb_marketing_insights` is purchase **value**; `actions_purchase` is the
  **count**. Always `breakdown_key IS NULL`.
- Adset-level Meta data (`fb_adset_breakdown_insights`) lands a **day later** than campaign level —
  never compare across levels on recent days.

---

## Skill: `metrics-tracker-data-model`

*File:* `.claude/skills/metrics-tracker-data-model/SKILL.md`
*When it applies:* Business metric definitions for the Shopdeck Metrics Tracker — 1k-5k, HIT1/HIT2/Revenue buckets, Spend/Live, Google-live, go-live multiplier, Target vs Achievement, ARR cohorts, churn, S/GMV and incentives. Use before computing, changing or explaining any of these numbers so the definition matches the rest of the dashboard.

## Metric definitions

Use these verbatim. Inventing a variant makes a new view disagree with every existing one.

### Populations

| Bucket | Definition (`csv_upload.hit_master_data`, card 10453) |
|---|---|
| **1k-5k team** | `ts_data.json → hitsMap` sellers with `good = 0` (i.e. `good_seller` unset). ~216. |
| **HIT1** | `team = 'HITS' AND good_seller IS NULL` → ~205 sellers |
| **HIT2** | `hit2 = 1` |
| **HIT1 + HIT2** | either of the above — **HIT1 and HIT2 overlap by design** in this convention |
| **Revenue** | `good_seller IS NULL AND team is not 'HITS' AND hit2 is not 1` |

These are the card-11815 variant predicates (see `bev_refresh.py`, `_G15UNI`), used by the Google
weekly table and the Google cohort table. **Match them for any new bucket split.**

> Caveat: the churn cohort (cards 12142/12159) deliberately makes the three **mutually exclusive**
> (HIT2 excluded from HIT1, HITS sellers excluded from Revenue) because a churn cohort must not
> double-count. Two conventions coexist on purpose — check which one a view uses.

`hit_master_data` is a full historical dump; never treat "has a `hit_year_week`" as "is HIT1".

### Spend / Live

Two variants; both correct for their purpose.

**A · Snapshot (KPI cards, "yesterday")** — from `scaling_data.json` (`my`/`gy`/`gt`/`ga`, built
from card 2787 meta + card 7401 google):

| Channel | Numerator ("spending") | Denominator ("live") |
|---|---|---|
| Meta | `my > 1` (meta yesterday spend > ₹1) | **all assigned** 1k-5k sellers |
| Google | `gy > 50` (google yesterday spend > ₹50) | sellers **Google-live** |
| Blended | `my > 1 OR gy > 50` | all assigned |

The asymmetry is deliberate: a seller not spending on Meta is a failure, but a seller with no
Google account can't be faulted; Google also uses a ₹50 floor because of trickle spends.

**B · Day-wise weighted (Target vs Achievement, incentives)**

```
Spend/Live % = Σ(seller-days with channel spend > 0) ÷ (settled days in month × live sellers)
```
Built from card 10469 via `perfByDate`. "Settled day" = a past day where booked spend > 0.
Exposed as `metaSLPct` / `gSLPctDW`, with `slDays` and per-seller detail.

### Google-live

```
has google_ad_account_id  AND  lifetime google spend > 10
```
Authority: **card 7401** (`google_ad_account_id`, `total_marketing_spend_with_tax`) — the same
source as Spend/Live and the go-live multiplier, so numbers tie across views.
(`bev_refresh` uses `gt > 1`; empirically `>1` and `>10` select the identical 1,337 sellers.)

### Go-live multiplier (Google)

```
Google Golive % = (sellers Google-live) ÷ (sellers assigned)     per GC, rolled up pooled to GM
```
| Golive % | Effect |
|---|---|
| **< 50%** | **0 — hard gate, the entire incentive becomes 0** |
| 50–65% | 1.0× |
| > 65% | 1.25× |

`>= 50` passes the gate; the bonus needs `> 65`. `gGoliveDelta = ceil(0.5 × gAcc) − gLive` is the
actionable "how many more must go live". GM rollup sums numerator and denominator, then divides.
It reads a **current snapshot**, not a month-end freeze.

### Target vs Achievement (GM-wise, 1k-5k)

**ARR target = Σ per-age targets of running sellers.** For each seller in the GM's cohort, take
age = months since their HIT1 month, look up the per-age target, and sum.

Per-age target vector (the `TARGET` row of card 11020), age capped at M5:

| M0 | M1 | M2 | M3 | M4 | M5+ |
|---|---|---|---|---|---|
| 1,859 | 3,668 | 4,133 | 4,480 | 4,748 | 4,647 |

- **ARR achieved** = Σ those sellers' actual monthly ARR (card 7336)
- **HIT2 target** — per GC per month from the **Collated sheet**; **HIT2 achieved** = `hit2=1`
  count for the report month, credited to the owner **at conversion** (card 10992)
- **`qualified`** = HIT2 achieved ≥ HIT2 target **AND** ARR achieved ≥ **85%** of ARR target
- **`delta`** = `max(0, 0.85 × arrT − arrA)` — gap to the 85% gate, not to 100%
- **Churn column** uses the stricter legacy rule: revenue spend ≥ **₹11,800** AND no spend > **21 days**

#### The HIT2 freeze (critical)

When a seller converts HIT1 → HIT2 they graduate out of 1k-5k, so at conversion we freeze:
1. **Conversion Friday** = Friday of `hit2_year_week` (earliest one)
2. **Frozen ARR** = latest daily `arr_overall` (card 10469) on/before that Friday
3. **Frozen age** — target stops accruing from the conversion month
4. **Frozen owner** — credit to the GC/GM who owned them at conversion (card 10992)

Freeze applies only when `HIT1 month ≤ conversion month ≤ report month`; a HIT2 week before the
HIT1 month is bad data → seller stays active. Detail rows carry `frozen` / `freezeMonth`.

### Incentive %

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

### Churn

**Base rule (cards 4118 / 11771):** churned = no week with `marketing_spend_tax_ ≥ 1000` in the
last **21 days**. Weekly grain. The **churn week** = that last spend week.

**Cohort churn (card 12159, the current one):**
- eligibility: ≥21 days since handover, where **handover = Friday of the hit week**
- **churn age** = `ROUND((churn_week − hit_week) / 4.5)` — week-based months, *not* calendar diff
- **cohort month** = month of the **Monday of the hit week**
- Revenue sellers are anchored on their **first REVENUE-team week**
- buckets are mutually exclusive here; `M12+` collects everything beyond 12 months

Cumulative churn curves must be **monotonic** and are clipped at each segment's last month with
actual churn (no flat extrapolated tail). Chart drilldowns are **incremental** (churned *at* that
month) so a seller appears in exactly one point.

### S/GMV and the funnel

```
S/GMV % = spend ÷ GMV × 100      (LOWER is better; 100% = spent ₹1 to make ₹1 of top-line)
```
Always **pooled**: `Σspend / ΣGMV`. Quote *pooled* to finance and *median* for "a typical adset" —
means are wrecked by a few catastrophic outliers.

Decomposition: **CPM** (cost of reach) → **CTR** (does the asset earn a click) → **C2PR**
(click→purchase) → **AOV**. Isolating the broken step tells you what to fix.

GMV sources: **Meta-attributed** below seller level; **true platform GMV** (`nushop.orderitems`)
only at seller level — use it as the sanity check.

### Cohort analyses

| Table | Cohort row | W0 / M0 | Cells |
|---|---|---|---|
| ARR Cohort (card 11020) | HIT month | M0 = HIT month | avg ARR; green = at/above target |
| Cohort Analysis 1k-5k (section 6, card 11840) | HIT month | W0 = HIT week | `t` present, `s` spending (>0), `g` ≥₹3,000 |
| Cohort Analysis 1k-5k **Google** (card 12264 / `cohort_google_refresh.py`) | month of **first google spend** | W0 = **week of first google spend** | same; cohorts from **Mar-26**; HIT1/HIT2/both/Revenue toggle |
| Churn cohort (card 12159) | hit-week month | M0 = same month | churn counts by age |

**3K Retention = ≥3K sellers at Wn ÷ spending sellers at W0.**
Spend threshold ₹3,000/week; the separate troubleshoot/scaling threshold is ₹3,540 (`SPEND3K`).

Google is switched on **weeks after** HIT (often months) — that's why the Google cohort re-anchors
on first google spend rather than the HIT week.

### Other constants

- `COHORT_EXCLUDE` — 26 hardcoded seller ids excluded from the 1k-5k cohort (mirrors card 10881)
- ARR cohort membership: HIT month ≥ `202510`, non-good, HITS-or-HIT2
- Canonical 1k-5k GL list comes from the **Collated sheet**; a seller under an unlisted GC is
  dropped from both target and achievement

---

## Skill: `metrics-tracker-deploy`

*File:* `.claude/skills/metrics-tracker-deploy/SKILL.md`
*When it applies:* Build, ship and operate the Shopdeck Metrics Tracker — node build.mjs, manual Vercel deploy, the five GitHub Actions workflows, secrets, and the git-clobber trap that silently reverts data files. Use when deploying a change, triggering or debugging a refresh run, or when data on the live site looks stale or reverted.

## Build, deploy & operations

### Build

```bash
node build.mjs
```
Extracts the `index.html` bundler template, Babel-compiles the inline JSX, writes `public/main.js`
+ resource chunks, and copies root `*.json` and `calls/` into `public/`.

- Success line ends with **`Babel dropped: true`**.
- `GOOGLE_SA_KEY not set — sheet-backed views will be empty` is **normal locally**.
- A JSX error prints a Babel stack trace and writes nothing. Fix before deploying.

### Deploy

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

### Verify after every deploy

The app sits behind Google login, so headless screenshots of prod aren't reliable. Curl the data:

```bash
curl -s "https://hits-tracker.xyz/bev_data.json" | python3 -c "
import json,sys; d=json.load(sys.stdin)
print(type(d['cards']['cohort']).__name__)          # must be dict, not str
print(d['generatedAt'])"
```
Assert the specific fields you changed. For UI changes, verify locally first via
`preview_start { name: 'metrics-public' }` (see `metrics-tracker-edit-view`).

### GitHub Actions

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

### The git-clobber trap (read this before touching a data file)

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

### Restoring a data file

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

### Local dev server

`.claude/launch.json` defines **`metrics-public`** → `python3 -m http.server 8099 --directory public`.
It serves `public/`, so **run `node build.mjs` first** or you'll test a stale bundle.

### Sizes to keep an eye on

`gc_detail_data.json` ~43 MB, `bev_data.json` ~4 MB, `gc_data.json` ~4.4 MB. Widening a
per-seller detail map can add tens of MB to what the browser downloads — scope such maps to the
sellers a view actually needs (e.g. KAE/KAM-managed rather than "anyone with any role").


---

# Appendix — what is in the repo

| Path | Count / note |
|---|---|
| `index.html` | the entire app (JSON-encoded bundler template) |
| `build.mjs` | JSX compiler / `public/` builder |
| `pipelines/*.py` | 28 data builders |
| `*.json` (root) | 33 generated data files, committed |
| `.github/workflows/*.yml` | 5 workflows (`refresh.yml` is the nightly one) |
| `.claude/skills/` | the 6 skills reproduced in Part 2 |
| `.claude/launch.json` | local dev server config (`metrics-public`, port 8099) |
| `snapshots/` | dated gzipped archives, 180-day retention |
| `calls/` | sharded seller call records |
| `ONBOARDING.md` | Part 1 of this document |

## First hour for the new owner

1. Get the access in Part 1 §2 (GitHub write + **Metabase API key on the 500 GB-quota account** —
   that key is the single most important credential).
2. `git clone` the repo. Confirm `.claude/skills/` is present.
3. Create `~/metabase-arr-refresh/.mbcreds` (schema in the `metrics-tracker-metabase` skill).
4. `node build.mjs` — expect the line ending `Babel dropped: true`.
5. `gh workflow run refresh.yml` and watch it complete + redeploy. **Grep the logs for
   `Traceback`** — every pipeline step is `continue-on-error: true`, so a step can crash green.
6. Read the `metrics-tracker-orientation` skill, then `metrics-tracker-data-model`.

## Still owned by the outgoing owner — move these

- Metabase cards were authored on a personal account; reparent them to a service/team account.
- The GitHub repo sits under a personal account (`pawankumar-pkaytsk`) — move to an org.
- Re-issue `METABASE_API_KEY`, `GOOGLE_SA_KEY` and `VERCEL_TOKEN` from service accounts, then
  update the repo secrets **and** the new owner's local `.mbcreds`.
