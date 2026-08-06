#!/usr/bin/env python3
"""Build google_sellers_data.json — Leadership -> Bird's Eye View -> Google -> Google Seller Book.

Population: the 1k-5k book from the ops 'Daily Plan' sheet (column G Status == '5K_HIT'), which
replaced ts_data.json hitsMap on 2026-08-06 — hit_master_data's `team` is stale in both directions,
so hitsMap both kept sellers who had left the book and dropped sellers still being serviced. See
the DAILY_PLAN_SHEET block below. Falls back to hitsMap(good=0) if the sheet is unreachable.
HIT1 and HIT2 are **mutually exclusive here** (HIT1 = book minus HIT2), unlike the Weekly Metrics /
cohort views where they overlap; HIT1+HIT2 is still the union so the combined figure agrees.
Sellers in GOOGLE_HANDOVER_DONE are dropped entirely. One row per seller.
Having a google ad account is a flag on the row, not an entry condition, because "total assigned"
is one of the roll-up columns and so has to be the denominator.

Columns / definitions
  gaMade    : google ad account exists     (scaling_data.json .ga, i.e. a non-empty
              nushop.userprofiles.google_ad_accounts entry) -> "total google assets created"
  live      : lifetime google spend > 10   (card 7401 total_marketing_spend_with_tax)
  spending  : google spend yesterday > 1   (card 7401 yesterday_spend) -> "Yesterday spending"
  last7     : google spend last 7 days     (card 7401 last_7_days_spend)
  k3 (3K)   : last7 > 3540                 (ts_data.json spendThreshold)
  Spend/Live: SUM(yesterday google spend) / COUNT(live sellers) — a rupee figure, pooled, never
              an average of per-seller ratios.

Roles — resolved in the SAME order and with the same meaning as the rest of the dashboard,
so this table agrees with every other Bird's-Eye drilldown:
  GL : ts_data.json hitsMap[sid].gc  ->  card 7753 growth_consultant_name  ->  card 7753
       growth_lead_name  ->  nushop.seller_managers growth_consultant.
       NOTE this dashboard's "GL" column is the growth_consultant-level owner: ts_refresh._gl
       sets hitsMap.gc = growth_consultant_name with a growth_lead_name fallback, and every
       bev_refresh detail row emits 'gl': team[sid]['gc']. Do NOT wire GL to the literal
       google_growth_lead manager_type — only ~6 of these sellers have one, which is what made
       an earlier version of this table read 112/118 "Unassigned".
  GM : hitsMap[sid].gm  ->  card 7753 growth_manager_name  ->  seller_managers growth_manager.
  CL : nushop.seller_managers manager_type='category_lead' -> nushop.users (same derivation as
       card 10181's cl_name). Not present in card 7753 or seller_console_metrics_summary.
  ggl: google_growth_lead where one exists (CSV only, ~6 sellers) — kept because it is real
       google-specific ownership, but it is NOT the GL column.
All role names are whitespace-collapsed (ts_refresh._norm), else "Bhavana  Ahirwar" and
"Bhavana Ahirwar" group as two different people.

Weekly GOOGLE PNL — card 5207 (Google Seller PNL, per-seller param, db 23):
  w1 = latest COMPLETED iso week (week_end_date < today), w2 = the week before.
  pnl   = net_profit_percentage
  spend = abs(total_marketing_spend_without_tax) * 1.18  (grossed up to with-tax, same as
          the existing Google bucket block in bev_refresh.py)

Bucket flags — canonical rules (bucket_refresh.py thresholds + the client-side bk() in
index.html), applied to the GOOGLE channel weekly PNL with spend gate TH = 3540:
  health     = w1s > TH and w1p > -20
  potential  = w1s > TH and w1p > 5
  objective  = w1s > TH and w2s > TH and w1p > 5 and w2p > 5
  subjective = w1s > TH and w2s > TH and w1p > 5 and w2p > 3

WHY NOT CARD 11011 (which was the requested PNL source): two independent blockers, both
recorded in dq.card11011 and surfaced in the view's footnote.
  1. Its hit_sellers CTE is `NOT EXISTS (... csv_upload.hit_master_data ...)`, i.e. it covers
     sellers that are NOT in HIT master data. Overlap with the 1k-5k HITS base is 0 sellers.
  2. best_source='google' is 0 rows card-wide: best_source tags whichever source had the
     GREATEST w1 pnl, and the CASE resolves gc_view_3 -> new_pnl -> facebook before google.
  Card 11011's own google leg is {{#7644-google-seller-pnl-duplicate}}; card 5207 is the same
  Google Seller PNL on db 23 (1 TB/day) and is what bev_refresh already uses, so we use that.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/google_sellers_refresh.py
"""
import glob
import json, os, re, sys, datetime, urllib.parse, urllib.request, urllib.error, subprocess
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "google_sellers_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")

SPEND_GATE = 3540      # weekly spend gate for the bucket flags AND the 3K toggle
PNL_HIT = 5            # potential / objective threshold
PNL_SUBJ = 3           # subjective threshold on w-2
HEALTH_FLOOR = -20     # bucket-health floor
LIVE_MIN = 10          # lifetime google spend > 10 => "live"
YEST_MIN = 1           # yesterday google spend > 1 => counted in "Yesterday spending"

# ---- the 1k-5k book: ops' own 'Daily Plan' sheet, column G "Status" == '5K_HIT' ----
# hit_master_data's `team` column is stale in BOTH directions, so hitsMap is the wrong roster:
#   * it KEEPS sellers who have left the book (Churned / Revenue / Unassigned / handover not done)
#   * it DROPS sellers whose `team` was cleared but who are still being serviced
#     (e.g. Blashyslashy, Life Fashion — team=NULL, hit2=NULL, handover complete)
# The Daily Plan sheet is what the growth team actually works off, so it is the source of truth
# for "is this seller in the 1k-5k book today". 229 sellers vs hitsMap's 240 on 2026-08-06.
DAILY_PLAN_SHEET = "1QCdVIkKa_4yMb1NZHSkIt50x4qoKaFlw2WXpQZnL6KM"
DAILY_PLAN_RANGE = "'Daily Plan'!A:G"    # A..G is all we need: E=Seller Id(4), G=Status(6)
DP_SELLER_COL, DP_STATUS_COL = 4, 6
BOOK_STATUS = "5K_HIT"
MIN_BOOK = 50          # sanity floor: below this the sheet read is untrustworthy -> keep hitsMap

# ---- sellers whose GOOGLE management has been handed to the central Google team ----
# They stay on the 1k-5k book for Meta but must not appear in the Google Seller Book at all.
# FLAGGED: there is no field for this anywhere we can reach — not card 10453, not the handover
# sheet (A:J), not the Daily Plan, not seller_managers.google_growth_lead (only 6 of these 10
# have one, and 29 sellers who are NOT handed over do). Supplied by the growth team on
# 2026-08-06 and hardcoded here, same pattern as bev_refresh's COHORT_EXCLUDE.
# TODO: replace with a real column once ops expose one — this list WILL go stale.
GOOGLE_HANDOVER_DONE = {
    "69c4eeaa317e68f10e2ab99e",  # Devikalooms
    "69733854317e68f10eb20117",  # Laenzy
    "699c3657524a3a0bdfe109eb",  # Anu kumar Mandal / magpulse
    "69c0f143f9cf517be2238375",  # M R Engineering
    "693945e3dc96074448b6a78f",  # gorya
    "68aff593859590b2aa413521",  # Nature Nook Kids
    "699f141fe3b403e118972401",  # The Worship Wear
    "69c679a70a1e1a55aeed925d",  # Milano Nest
    "69c53e58317e68f10e44543f",  # Heerkamal enterprise
    "69d4d03f16ccce91990833c5",  # Lakhani Dhruv
}


def read_sheet_sa(sid, rng):
    """Read a Google Sheet range via the service account (GOOGLE_SA_KEY env or local key file)."""
    from google.oauth2 import service_account
    import google.auth.transport.requests as gtr
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    if os.environ.get("GOOGLE_SA_KEY"):
        cred = service_account.Credentials.from_service_account_info(
            json.loads(os.environ["GOOGLE_SA_KEY"]), scopes=SCOPES)
    else:
        exact = os.path.expanduser("~/Downloads/metrics-tracker-automation-53ad2cdd4b65.json")
        path = exact if os.path.exists(exact) else (
            glob.glob(os.path.expanduser("~/Downloads/metrics-tracker-automation-*.json")) or [None])[0]
        cred = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    cred.refresh(gtr.Request())
    u = (f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{urllib.parse.quote(rng)}"
         "?valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=FORMATTED_STRING")
    return json.loads(urllib.request.urlopen(u, timeout=180).read()).get("values", [])


def creds():
    if os.environ.get("METABASE_URL"):
        return (os.environ["METABASE_URL"].rstrip("/"), os.environ.get("METABASE_USER_EMAIL"),
                os.environ.get("METABASE_PASSWORD"))
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else \
        json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e.get("METABASE_USER_EMAIL"), e.get("METABASE_PASSWORD")


def req(url, method="GET", body=None, H=None, timeout=900):
    """Retry 5xx and timeouts; never retry a 4xx.

    A BigQuery quota rejection comes back as HTTP 400 and is not transient — retrying it just
    burns the backoff. With 119 per-seller card-5207 calls and 3 escalating passes that turned a
    hard 'Daily quota exceeded' into a ~100 minute no-op run.
    """
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
            last = ex
            _t.sleep(3 * (attempt + 1))
        except Exception as ex:
            last = ex
            _t.sleep(3 * (attempt + 1))
    raise last


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fnum_or_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load(name, default=None):
    p = os.path.join(REPO, name)
    try:
        return json.load(open(p))
    except Exception as ex:
        print(f"[gsellers] {name} unreadable ({ex}) — using default")
        return default if default is not None else {}


def main():
    url, email, pw = creds()
    key = os.environ.get("METABASE_API_KEY")
    if not key:
        try:
            key = json.load(open(CRED_CACHE)).get("METABASE_API_KEY")
        except Exception:
            key = None
    if key:
        AUTH = {"x-api-key": key}
    else:
        AUTH = {"X-Metabase-Session": req(url + "/api/session", "POST",
                {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]}
    H = {"Content-Type": "application/json", **AUTH}

    # The api-key path forces a FRESH BigQuery scan, so it is the first thing to fail when a
    # daily quota is spent (db 6 and db 23 both hit this routinely). A session token returns
    # Metabase's CACHED result for the same card, which is good enough for every source here.
    # Try the key first, fall back to the token on any 4xx. Same trick as revival/lt/gc_view/kae.
    _sess = {}

    def _sess_hdr():
        if "h" not in _sess:
            tok = req(url + "/api/session", "POST", {"username": email, "password": pw},
                      {"Content-Type": "application/json"})["id"]
            _sess["h"] = {"X-Metabase-Session": tok, "Content-Type": "application/json"}
            print("[gsellers] opened a session token for cached-result fallback")
        return _sess["h"]

    def fetch(path, body=None, timeout=900):
        try:
            return req(f"{url}{path}", "POST", body if body is not None else {}, H, timeout)
        except urllib.error.HTTPError as ex:
            if not (400 <= ex.code < 500):
                raise
            return req(f"{url}{path}", "POST", body if body is not None else {},
                       _sess_hdr(), timeout)

    # ---- population: 1k-5k (good=0) with a google ad account ----
    ts = load("ts_data.json")
    scaling = load("scaling_data.json")
    hits = ts.get("hitsMap", {})
    sc = scaling.get("sellers", {})
    base = {sid: m for sid, m in hits.items() if not m.get("good")}

    # The 1k-5k book comes from the Daily Plan sheet (Status == '5K_HIT'), not hitsMap — see the
    # DAILY_PLAN_SHEET comment. Degrade to hitsMap if the sheet is unreachable (no GOOGLE_SA_KEY
    # locally, Sheets API blip) or returns an implausibly small book, so a failed read can never
    # empty the table.
    book, book_src = set(base), "hitsMap(good=0) [Daily Plan unavailable]"
    try:
        _dp = read_sheet_sa(DAILY_PLAN_SHEET, DAILY_PLAN_RANGE)
        _k5 = set()
        for _r in _dp[2:]:                      # rows 0-1 are the two header rows
            if len(_r) > DP_STATUS_COL and str(_r[DP_STATUS_COL]).strip() == BOOK_STATUS:
                _s = str(_r[DP_SELLER_COL]).strip() if len(_r) > DP_SELLER_COL else ""
                if _s:
                    _k5.add(_s)
        if len(_k5) >= MIN_BOOK:
            book, book_src = _k5, f"Daily Plan Status=={BOOK_STATUS}"
            print(f"[gsellers] book from Daily Plan: {len(_k5)} sellers "
                  f"(hitsMap had {len(base)}; +{len(_k5 - set(base))} / -{len(set(base) - _k5)})")
        else:
            print(f"[gsellers] Daily Plan returned only {len(_k5)} '{BOOK_STATUS}' sellers "
                  f"(< {MIN_BOOK}) — keeping hitsMap book of {len(base)}")
    except Exception as _ex:
        print(f"[gsellers] Daily Plan sheet unavailable ({str(_ex)[:80]}) — keeping hitsMap "
              f"book of {len(base)}")

    # HIT1 = the 1k-5k book (above) MINUS the HIT2 graduates. HIT2 = hit2=1 excluding good sellers,
    # taken straight from card 10453.
    # NOTE this table deliberately makes HIT1 and HIT2 **mutually exclusive**, unlike the Weekly
    # Metrics / cohort views where they overlap by design (card-11815 `_G15UNI` predicates). The
    # growth team reads the HIT1 toggle as "still in the 1k-5k book", so a seller who has converted
    # to HIT2 must not be counted there. HIT1+HIT2 is still the full union, so the combined figure
    # matches the other views. Two conventions coexist on purpose — see metrics-tracker-data-model.
    # Converting to HIT2 flips team off HITS, so most HIT2 sellers are absent from hitsMap; taking
    # HIT2 straight from 10453 is what keeps them visible.
    _TRUE = ("1", "1.0", "True", "true")
    h2ids, name10453 = set(), {}
    try:
        for r in fetch("/api/card/10453/query/json"):
            _s = str(r.get("seller_id") or "").strip()
            if not _s:
                continue
            if str(r.get("seller_name") or "").strip():
                name10453[_s] = str(r.get("seller_name")).strip()
            if str(r.get("hit2")).strip() in _TRUE and str(r.get("good_seller")).strip() not in _TRUE:
                h2ids.add(_s)
        print(f"[gsellers] card 10453: HIT2 population {len(h2ids)} "
              f"({len(h2ids & book)} of them also inside the book)")
    except Exception as ex:
        print(f"[gsellers] card 10453 failed ({ex}) — HIT2 toggle will be empty")

    # HIT1 = book minus HIT2 graduates. Google-handed-over sellers leave the Google book entirely.
    h1ids = (book - h2ids) - GOOGLE_HANDOVER_DONE
    h2ids = h2ids - GOOGLE_HANDOVER_DONE
    gsids = sorted(h1ids | h2ids)
    gaids = sorted(sid for sid in gsids if str(sc.get(sid, {}).get("ga") or "").strip())
    print(f"[gsellers] book={len(book)} ({book_src}) · HIT1 {len(h1ids)} + HIT2 {len(h2ids)} "
          f"= {len(gsids)} rows · dropped {len(GOOGLE_HANDOVER_DONE & (book | h2ids))} "
          f"google-handed-over")
    if not gsids:
        print("[gsellers] no sellers — aborting without writing")
        return

    # Previous output, used as a no-clobber fallback for every remote source below. BigQuery
    # quota (db 6 and db 23 both) is a routine failure here, and a quota-failed fetch must degrade
    # to yesterday's value rather than silently zero a column.
    prev = load("google_sellers_data.json", {})
    prev_row = {r["s"]: r for r in (prev.get("rows") or [])}

    inlist = ",".join("'%s'" % s.replace("'", "") for s in gsids)

    # Role names are whitespace-normalised the same way ts_refresh._norm does it, otherwise
    # "Bhavana  Ahirwar" and "Bhavana Ahirwar" group as two different people.
    _norm = lambda v: re.sub(r"\s+", " ", str(v or "")).strip()
    clean = lambda v: ("" if _norm(v) in ("", "-") else _norm(v))

    roles = {sid: {} for sid in gsids}

    # ---- (1) house source of truth: ts_data.json hitsMap ----
    # hitsMap.gc is card 7753 growth_consultant_name with a growth_lead_name fallback, and it is
    # what every other Bird's-Eye drilldown renders under the column labelled "GL". Match that so
    # this table agrees with the rest of the dashboard.
    for sid in gsids:
        h = base.get(sid) or {}
        if clean(h.get("gc")):
            roles[sid]["gl"] = clean(h.get("gc"))
        if clean(h.get("gm")):
            roles[sid]["gm"] = clean(h.get("gm"))
    print(f"[gsellers] hitsMap: GL {sum(1 for s in gsids if roles[s].get('gl'))}/{len(gsids)} · "
          f"GM {sum(1 for s in gsids if roles[s].get('gm'))}/{len(gsids)}")

    # ---- (2) card 7753 fills whatever hitsMap left blank (same precedence as ts_refresh) ----
    m7753 = {}
    try:
        for r in fetch("/api/card/7753/query/json"):
            sid = str(r.get("seller_id") or "").strip()
            if sid in roles:
                m7753[sid] = r
        filled = 0
        for sid in gsids:
            r = m7753.get(sid) or {}
            if not roles[sid].get("gl"):
                v = clean(r.get("growth_consultant_name")) or clean(r.get("growth_lead_name"))
                if v:
                    roles[sid]["gl"] = v; filled += 1
            if not roles[sid].get("gm") and clean(r.get("growth_manager_name")):
                roles[sid]["gm"] = clean(r.get("growth_manager_name")); filled += 1
        print(f"[gsellers] card 7753 covered {len(m7753)}/{len(gsids)} · filled {filled} blank role names")
    except Exception as ex:
        print(f"[gsellers] card 7753 failed: {ex}")

    # ---- (3) nushop.seller_managers: CL (category_lead) plus last-resort GL/GM ----
    ROLE_OF = {"category_lead": "cl", "growth_manager": "_gm", "growth_consultant": "_gc",
               "google_growth_lead": "ggl", "google_growth_manager": "ggm"}
    role_cov = {}
    try:
        sql = f"""
        SELECT sm.seller_id, sm.manager_type,
               TRIM(CONCAT(COALESCE(u.first_name,''),' ',COALESCE(u.last_name,''))) AS nm
        FROM nushop.seller_managers sm
        JOIN nushop.users u ON sm.manager_id = u._id
        WHERE sm.seller_id IN ({inlist})
          AND sm.manager_type IN ({",".join("'%s'" % k for k in ROLE_OF)})
        """
        rr = fetch("/api/dataset", {"database": 6, "type": "native", "native": {"query": sql}})
        for sid, mt, nm in rr["data"]["rows"]:
            sid = str(sid)
            k = ROLE_OF.get(str(mt))
            if sid in roles and k and not roles[sid].get(k):
                roles[sid][k] = clean(nm)
        for sid in gsids:                       # last resort only
            if not roles[sid].get("gl") and roles[sid].get("_gc"):
                roles[sid]["gl"] = roles[sid]["_gc"]
            if not roles[sid].get("gm") and roles[sid].get("_gm"):
                roles[sid]["gm"] = roles[sid]["_gm"]
        for k in ("cl", "ggl", "ggm"):
            role_cov[k] = sum(1 for sid in gsids if roles[sid].get(k))
        print(f"[gsellers] seller_managers: " +
              " · ".join(f"{k.upper()} {role_cov[k]}/{len(gsids)}" for k in ("cl", "ggl", "ggm")))
    except Exception as ex:
        print(f"[gsellers] seller_managers role query failed: {ex}")
    _role_carried = 0
    for sid in gsids:                       # carry any role the live sources could not supply
        for k in ("cl", "gl", "gm", "ggl"):
            if not roles[sid].get(k) and (prev_row.get(sid) or {}).get(k):
                roles[sid][k] = prev_row[sid][k]
                _role_carried += 1
    if _role_carried:
        print(f"[gsellers] carried {_role_carried} role names forward from the previous file")
    for k in ("gl", "gm"):
        role_cov[k] = sum(1 for sid in gsids if roles[sid].get(k))
    print(f"[gsellers] final roles: " +
          " · ".join(f"{k.upper()} {role_cov.get(k, 0)}/{len(gsids)}" for k in ("gl", "gm", "cl")))

    # ---- google spend: card 7401 (lifetime / yesterday / last-7) ----
    g7401 = {}
    try:
        for r in fetch("/api/card/7401/query/json"):
            sid = str(r.get("seller_id") or "").strip()
            if sid in roles:
                g7401[sid] = r
        print(f"[gsellers] card 7401 covered {len(g7401)}/{len(gsids)} sellers")
    except Exception as ex:
        print(f"[gsellers] card 7401 failed: {ex}")

    # ---- weekly google PNL: card 5207 per seller (parallel, db 23) ----
    todayISO = datetime.date.today().isoformat()

    def q5207(sid):
        body = {"parameters": [{"type": "string/=",
                                "target": ["variable", ["template-tag", "seller_id"]], "value": sid}]}
        try:
            rows = fetch("/api/card/5207/query/json", body, timeout=300)
        except Exception:
            return sid, None
        rows = [r for r in rows if str(r.get("week_end_date") or "")[:10]
                and str(r.get("week_end_date"))[:10] < todayISO]
        rows.sort(key=lambda r: str(r.get("week_start_date") or ""), reverse=True)
        if not rows:
            return sid, None

        def wk(r):
            return {"p": fnum_or_none(r.get("net_profit_percentage")),
                    "s": abs(fnum(r.get("total_marketing_spend_without_tax"))) * 1.18,
                    "w": (f"{int(r['week_year'])}-W{int(r['week_number']):02d}"
                          if r.get("week_year") is not None and r.get("week_number") is not None else "")}
        w1 = wk(rows[0])
        w2 = wk(rows[1]) if len(rows) > 1 else {"p": None, "s": 0.0, "w": ""}
        return sid, {"w1p": w1["p"], "w1s": w1["s"], "w1w": w1["w"],
                     "w2p": w2["p"], "w2s": w2["s"], "w2w": w2["w"]}

    gpnl = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for sid, v in ex.map(q5207, gaids):     # only a google-account holder can have google PNL
            if v:
                gpnl[sid] = v
    print(f"[gsellers] card 5207 pass 1: {len(gpnl)}/{len(gaids)}")

    # db 23 throttles under concurrency and q5207 swallows the failure, so a first pass can come
    # back with a fraction of the sellers. Retry the misses with less concurrency before accepting.
    for rnd, workers in ((2, 2), (3, 1)):
        miss = [s for s in gaids if s not in gpnl]
        if not miss:
            break
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for sid, v in ex.map(q5207, miss):
                if v:
                    gpnl[sid] = v
        print(f"[gsellers] card 5207 pass {rnd} (retried {len(miss)}): {len(gpnl)}/{len(gaids)}")

    # No-clobber guard: never publish a run whose PNL coverage collapsed vs the file already on
    # disk — a throttled fetch would silently zero out the bucket counts. Carry the previous
    # seller's PNL forward instead and record it in dq.
    prev_pnl = {s: r for s, r in prev_row.items()
                if r.get("w1p") is not None or r.get("w1s") is not None}
    carried = 0
    if prev_pnl and len(gpnl) < 0.9 * len(prev_pnl):
        for sid in gaids:
            if sid not in gpnl and sid in prev_pnl:
                r = prev_pnl[sid]
                gpnl[sid] = {"w1p": r.get("w1p"), "w1s": r.get("w1s"), "w1w": "",
                             "w2p": r.get("w2p"), "w2s": r.get("w2s"), "w2w": ""}
                carried += 1
        print(f"[gsellers] WARN coverage collapsed ({len(gpnl) - carried} fetched vs "
              f"{len(prev_pnl)} previously) — carried {carried} sellers' PNL from the previous file")

    weeks = sorted({v["w1w"] for v in gpnl.values() if v["w1w"]}, reverse=True)
    print(f"[gsellers] card 5207 google PNL: {len(gpnl)}/{len(gaids)} google-account sellers "
          f"({carried} carried forward) · w1 weeks seen {weeks[:3]}")

    # ---- assemble ----
    def gate(v):
        return v is not None and v > SPEND_GATE

    rows_out = []
    for sid in gsids:
        meta = base.get(sid, {})
        sr = sc.get(sid, {})
        c = g7401.get(sid, {})
        # lifetime google spend: card 7401 (with tax) is authoritative; fall back to scaling.gt
        life = fnum_or_none(c.get("total_marketing_spend_with_tax"))
        if life is None:
            life = fnum_or_none(sr.get("gt"))
        ysp = fnum_or_none(c.get("yesterday_spend"))
        if ysp is None:
            ysp = fnum_or_none(sr.get("gy"))
        last7 = fnum_or_none(c.get("last_7_days_spend"))
        _pv = prev_row.get(sid) or {}
        if last7 is None:               # card 7401 unavailable: only it carries last-7-day spend
            last7 = _pv.get("last7")
        if life is None:
            life = _pv.get("life")
        if ysp is None:
            ysp = _pv.get("ysp")
        p = gpnl.get(sid) or {}
        w1p, w2p = p.get("w1p"), p.get("w2p")
        w1s, w2s = p.get("w1s"), p.get("w2s")
        g1, g2 = gate(w1s), gate(w2s)
        health = bool(g1 and w1p is not None and w1p > HEALTH_FLOOR)
        potential = bool(g1 and w1p is not None and w1p > PNL_HIT)
        objective = bool(g1 and g2 and w1p is not None and w2p is not None
                         and w1p > PNL_HIT and w2p > PNL_HIT)
        subjective = bool(g1 and g2 and w1p is not None and w2p is not None
                          and w1p > PNL_HIT and w2p > PNL_SUBJ)
        rows_out.append({
            "s": sid,
            "n": str(meta.get("n") or "").strip() or name10453.get(sid, ""),
            "h1": sid in h1ids,         # in the 1k-5k book and NOT a HIT2 graduate
            "h2": sid in h2ids,         # hit2 = 1 (overlaps h1 by design)
            "ga": str(sr.get("ga") or ""),
            "gl": roles[sid].get("gl", ""), "gm": roles[sid].get("gm", ""),
            "cl": roles[sid].get("cl", ""), "ggl": roles[sid].get("ggl", ""),
            "life": None if life is None else round(life, 2),
            "ysp": None if ysp is None else round(ysp, 2),
            "last7": None if last7 is None else round(last7, 2),
            # scaling_data only carries the sellers it tracks, so fall back to card 7401's
            # google_ad_account_id — the two agree exactly on the assigned book.
            "gaMade": bool(str(sr.get("ga") or "").strip()
                           or str(c.get("google_ad_account_id") or "").strip()),
            "live": bool(life is not None and life > LIVE_MIN),
            "spending": bool(ysp is not None and ysp > YEST_MIN),
            "k3": bool(last7 is not None and last7 > SPEND_GATE),
            "w1p": None if w1p is None else round(w1p, 1),
            "w2p": None if w2p is None else round(w2p, 1),
            "w1s": None if w1s is None else round(w1s),
            "w2s": None if w2s is None else round(w2s),
            "health": health, "potential": potential,
            "objective": objective, "subjective": subjective,
        })
    rows_out.sort(key=lambda r: -(r["last7"] or 0))

    cnt = lambda k: sum(1 for r in rows_out if r[k])
    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cards": {"pnl": 5207, "googleSpend": 7401, "roles": 7753,
                  "roleTable": "nushop.seller_managers + nushop.users"},
        "thresholds": {"spendGate": SPEND_GATE, "pnlHit": PNL_HIT, "pnlSubjective": PNL_SUBJ,
                       "healthFloor": HEALTH_FLOOR, "liveMin": LIVE_MIN, "yestMin": YEST_MIN},
        "weeks": {"w1": (weeks[0] if weeks else ""), "w2": ""},
        "rows": rows_out,
        "dq": {
            "base1k5k": len(book), "bookSource": book_src, "totalAssigned": len(h1ids),
            "googleHandedOver": sorted(GOOGLE_HANDOVER_DONE), "googleAssetsCreated": len(gaids),
            # per-bucket headline counts. HIT2 rows carry no GL/CL mapping and no weekly google
            # PNL, so the view shows only these four for HIT2 / HIT1+HIT2.
            "buckets": {
                bk: {
                    "total": sum(1 for r in rows_out if pick(r)),
                    "gaMade": sum(1 for r in rows_out if pick(r) and r["gaMade"]),
                    "live": sum(1 for r in rows_out if pick(r) and r["live"]),
                    "spending": sum(1 for r in rows_out if pick(r) and r["spending"]),
                }
                for bk, pick in (("hit1", lambda r: r["h1"]),
                                 ("hit2", lambda r: r["h2"]),
                                 ("hit12", lambda r: r["h1"] or r["h2"]))
            },
            "pnlCovered": len(gpnl), "pnlCarriedForward": carried, "spendCovered": len(g7401),
            # measured off the emitted rows, not the fetch step: a quota-failed role query
            # still yields coverage via the carry-forward above, and dq must say so.
            "roleCoverage": {k: sum(1 for r in rows_out if r.get(k)) for k in ("gl", "gm", "cl", "ggl")},
            "counts": {k: cnt(k) for k in ("gaMade", "live", "spending", "k3", "health",
                                           "potential", "objective", "subjective")},
            "yestSpendTotal": round(sum(r["ysp"] or 0 for r in rows_out)),
            "spendPerLive": (round(sum(r["ysp"] or 0 for r in rows_out) / cnt("live"))
                             if cnt("live") else None),
            "card11011": ("unusable as the PNL source: its hit_sellers CTE excludes every "
                          "csv_upload.hit_master_data seller, so overlap with the 1k-5k base is 0; "
                          "and best_source='google' is 0 rows card-wide because best_source tags "
                          "whichever source had the greatest w1 pnl and resolves gc_view_3 -> "
                          "new_pnl -> facebook before google. Using card 5207 (Google Seller PNL, "
                          "the same google leg card 11011 itself reads) instead."),
            "glNote": ("GL = the growth_consultant-level owner, resolved hitsMap.gc -> card 7753 "
                       "growth_consultant_name -> growth_lead_name -> seller_managers "
                       "growth_consultant. This is the same field every other Bird's-Eye drilldown "
                       "shows as 'GL' (ts_refresh._gl / bev_refresh 'gl': team[sid]['gc']). The "
                       "literal google_growth_lead manager_type is carried separately as ggl "
                       "(only a handful of sellers have one) and is NOT used for the GL column."),
        },
    }
    w2s_seen = sorted({v["w2w"] for v in gpnl.values() if v["w2w"]}, reverse=True)
    out["weeks"]["w2"] = w2s_seen[0] if w2s_seen else ""

    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    _yt = sum(r["ysp"] or 0 for r in rows_out)
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(rows_out)} assigned 1k-5k sellers · "
          + " · ".join(f"{k}={cnt(k)}" for k in ("gaMade", "live", "spending", "k3", "health",
                                                 "potential", "objective", "subjective"))
          + f" · yesterday spend Rs{_yt:,.0f} · spend/live Rs{(_yt / cnt('live')) if cnt('live') else 0:,.0f}")

    if "--push" in sys.argv:
        subprocess.run(["git", "-C", REPO, "add", "google_sellers_data.json"], check=True)
        r = subprocess.run(["git", "-C", REPO, "commit", "-m", "Refresh Google seller book data"],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())


if __name__ == "__main__":
    main()
