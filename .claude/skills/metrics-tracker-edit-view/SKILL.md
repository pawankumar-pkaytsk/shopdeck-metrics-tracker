---
name: metrics-tracker-edit-view
description: Edit the Shopdeck Metrics Tracker UI in index.html — the app lives inside one JSON-encoded bundler template, so normal text edits corrupt it. Use when adding or changing a section, table, chart, toggle, nav pill or drilldown modal in this dashboard.
---

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
