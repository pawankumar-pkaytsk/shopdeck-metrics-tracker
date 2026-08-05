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
