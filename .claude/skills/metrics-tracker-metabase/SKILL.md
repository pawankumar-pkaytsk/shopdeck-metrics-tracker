---
name: metrics-tracker-metabase
description: Query Metabase and BigQuery for the Shopdeck Metrics Tracker — read/create/edit cards via the API, the card catalogue, BigQuery databases and quota, and the session-token fallback that survives a spent daily quota. Use when you need data that isn't in a *.json yet, need to inspect or change a card's SQL, or hit a BigQuery quota / partition-filter error.
---

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
