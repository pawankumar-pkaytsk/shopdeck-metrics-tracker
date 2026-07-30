---
name: metrics-tracker-pipeline
description: Write or modify a Shopdeck Metrics Tracker data pipeline (pipelines/*_refresh.py that turns Metabase cards into a committed *.json). Use when adding a new dashboard data source, changing how a metric is computed, or debugging a pipeline that produced wrong or empty data.
---

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

## Non-negotiables

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

## Verification discipline (this is where bugs are actually caught)

After running, load the JSON and assert invariants. Real checks that caught real bugs:

```python
# subset:      google cohort ⊂ all-1k-5k cohort
assert gsids <= asids
# consistency: arm columns sum to the total (±1 for independent rounding)
assert abs(sum(cells) - total) <= 1
# anchoring:   every seller appears exactly once at W0, all with google spend
assert w0_rows == n_sellers and w0_without_google == 0
# set algebra: both == hit1 ∪ hit2, revenue disjoint from both
assert bo == (h1 | h2) and not (rv & bo)
# cross-source: compare the anchor against an independent card, count mismatches
```

Compare like-for-like vintages: two JSONs generated a day apart *will* differ on recent weeks
because spend accrues. Re-run both before concluding there's a bug.

## Reading other pipelines' output

Later pipelines may read earlier ones' JSON (that's why `bev_refresh` runs last in
`refresh.yml`). Use `load_json(name, default)`; never assume a file exists.

## Heavy-pipeline notes

- `bev_refresh.py` is ~15–24 min, fetches card 10469 (~727k rows, 2025-01 → today) **once** and
  reuses it. Don't add a second fetch of it.
- It needs `GOOGLE_SA_KEY` (or a local SA key) for the Sheets-backed TvA/cohort inputs; without it
  those views come out empty and a no-clobber guard preserves the previous values.
- It writes atomically (serialize fully in memory, then write) so a serialization error cannot
  truncate `bev_data.json`. **Keep it that way.**
- **Variable shadowing is a live hazard** in this file: a local named `cohort` once overwrote the
  module-level ARR-cohort dict and blanked two whole views. Prefix loop locals (`_ccoh`, `_r`).

## Where the output goes in `bev_data.json`

- new numbered Bird's-Eye section → `bev2.<key>`
- TvA / ARR cohort → `cards.cohort` (already occupied; extend, don't replace)

Standalone views should get their **own** `*.json` + pipeline instead of bloating `bev_data.json`.
