---
name: metrics-tracker-edit-view
description: Edit the Shopdeck Metrics Tracker UI in index.html — the app lives inside one JSON-encoded bundler template, so normal text edits corrupt it. Use when adding or changing a section, table, chart, toggle, nav pill or drilldown modal in this dashboard.
---

# Editing the dashboard UI (`index.html`)

The entire app is one JSON-encoded string inside
`<script type="__bundler/template"> … </script>`. You **cannot** use Edit/Write on it directly,
and you cannot grep it usefully as plain text. Always decode → patch → re-encode with Python.

## The canonical edit pattern

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

### Hard-won rules

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

## Reusable components already in the app

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

## Drilldown modals — the recurring bug

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

## Adding a whole new nav destination

Four coordinated edits (all must be present or the tab silently does nothing):

1. **Component** — insert the function before a known anchor, e.g. `  function BirdEyeView() {`.
2. **Nav pill** — add a `FloatingTabBar` (or custom styled div) in the nav row, before
   `<div style={{ marginLeft: 'auto' }}><SnapshotPicker /></div>`.
3. **Route** — extend the ternary chain:
   `{section === 'projects' ? <ProjectsView /> : section === 'allreports' ? … }`
4. **Breadcrumb** — add an `else if (section === 'projects') { crumbs.push({ label: '…' }); }`.

## Verify in the browser (always do this)

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
