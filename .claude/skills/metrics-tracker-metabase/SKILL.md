---
name: metrics-tracker-metabase
description: Query Metabase and BigQuery for the Shopdeck Metrics Tracker — read/create/edit cards via the API, the card catalogue, BigQuery databases and partition rules, and how to work around the daily quota. Use when you need data that isn't in a *.json yet, need to inspect or change a card's SQL, or hit a BigQuery quota / partition-filter error.
---

# Metabase & BigQuery

Base URL and credentials live in `~/metabase-arr-refresh/.mbcreds` (JSON, gitignored, **outside**
the repo):

```json
{ "METABASE_URL": "...", "METABASE_USER_EMAIL": "...", "METABASE_PASSWORD": "...", "METABASE_API_KEY": "mb_..." }
```

Never print, commit or echo these values. In CI they come from repo secrets.

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

## BigQuery databases

| id | Dataset | Notes |
|---|---|---|
| **6** | `nushop`, `csv_upload`, `fb_marketings` | The main one. **Small daily "default plan" quota (single-digit GB) — exhausts easily.** Resets 00:00 IST. |
| **2** | team/meta mapping | card 2787 lives here |
| **23** | 1 TB/day | card 7401, card 12142 — use for anything heavy |

### Partition-filter requirements (query fails without them)

- `nushop.google_marketing_insights_master` → filter `spend_date`
- `nushop.changeslogs` → filter `createdat`

### Quota errors and the workarounds

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

## Card catalogue (the ones that matter)

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

## Editing card SQL — safety

Pipelines sometimes **string-replace** a known clause inside a card's SQL to build variants
(`bev_refresh` does this for the card-11815 HIT1/HIT2/both/revenue split) and `raise` if the
expected clause is missing. If you edit such a card, that guard fires and the view goes empty —
grep `pipelines/` for the card id before changing it.

## Known data-model traps

- `hit_master_data` is a **full historical dump** (~2,800 rows). The current HIT1 base is
  `team='HITS' AND good_seller IS NULL` (~205), **not** every row with a `hit_year_week`.
- `gc_view_3.marketing_spend` is **total** (meta+google); `marketing_spend_tax_` is the same figure
  with tax (×1.18). It undercounts google by ~15% on google-heavy weeks vs card 10469.
- `gc_view_3.start_date` is **not always Monday** (~27% aren't), so joining it to ISO weeks is lossy.
- Meta `purchases` in `fb_marketing_insights` is purchase **value**; `actions_purchase` is the
  **count**. Always `breakdown_key IS NULL`.
- Adset-level Meta data (`fb_adset_breakdown_insights`) lands a **day later** than campaign level —
  never compare across levels on recent days.
