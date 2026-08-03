#!/usr/bin/env python3
"""Build google_sellers_data.json — Leadership -> Bird's Eye View -> Google -> Google Seller Book.

Population: 1k-5k sellers (ts_data.json hitsMap, good=0) whose GOOGLE ad account is made
(scaling_data.json sellers[sid].ga non-empty). One row per seller.

Columns / definitions
  live      : lifetime google spend > 10   (card 7401 total_marketing_spend_with_tax)
  spending  : google spend yesterday       (card 7401 yesterday_spend)
  last7     : google spend last 7 days     (card 7401 last_7_days_spend)
  k3 (3K)   : last7 > 3540                 (ts_data.json spendThreshold)
  GM / CL / GC / GL: nushop.seller_managers.manager_type -> nushop.users (first+last name).
              CL = category_lead. GL = google_growth_lead.

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
import json, os, sys, datetime, urllib.request, subprocess
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


def creds():
    if os.environ.get("METABASE_URL"):
        return (os.environ["METABASE_URL"].rstrip("/"), os.environ.get("METABASE_USER_EMAIL"),
                os.environ.get("METABASE_PASSWORD"))
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else \
        json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e.get("METABASE_USER_EMAIL"), e.get("METABASE_PASSWORD")


def req(url, method="GET", body=None, H=None, timeout=900):
    import time as _t
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
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

    # ---- population: 1k-5k (good=0) with a google ad account ----
    ts = load("ts_data.json")
    scaling = load("scaling_data.json")
    hits = ts.get("hitsMap", {})
    sc = scaling.get("sellers", {})
    base = {sid: m for sid, m in hits.items() if not m.get("good")}
    gsids = sorted(sid for sid in base if str(sc.get(sid, {}).get("ga") or "").strip())
    print(f"[gsellers] 1k-5k base {len(base)} · google account made {len(gsids)}")
    if not gsids:
        print("[gsellers] no google sellers — aborting without writing")
        return

    inlist = ",".join("'%s'" % s.replace("'", "") for s in gsids)

    # ---- roles: nushop.seller_managers -> nushop.users (scoped to our sellers = tiny scan) ----
    ROLE_OF = {"category_lead": "cl", "growth_manager": "gm", "growth_consultant": "gc",
               "google_growth_lead": "gl", "google_growth_manager": "ggm"}
    roles = {sid: {} for sid in gsids}
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
        rr = req(f"{url}/api/dataset", "POST",
                 {"database": 6, "type": "native", "native": {"query": sql}}, H)
        for sid, mt, nm in rr["data"]["rows"]:
            sid = str(sid)
            k = ROLE_OF.get(str(mt))
            if sid in roles and k and not roles[sid].get(k):
                roles[sid][k] = str(nm or "").strip()
        for k in set(ROLE_OF.values()):
            role_cov[k] = sum(1 for sid in gsids if roles[sid].get(k))
        print(f"[gsellers] roles from seller_managers: " +
              " · ".join(f"{k.upper()} {role_cov[k]}/{len(gsids)}" for k in sorted(role_cov)))
    except Exception as ex:
        print(f"[gsellers] seller_managers role query failed: {ex}")

    # fall back to card 7753 for GM/GC/GL where seller_managers had nothing
    m7753 = {}
    try:
        for r in req(f"{url}/api/card/7753/query/json", "POST", {}, H):
            sid = str(r.get("seller_id") or "").strip()
            if sid in roles:
                m7753[sid] = r
        clean = lambda v: ("" if str(v or "").strip() in ("", "-") else str(v).strip())
        filled = 0
        for sid in gsids:
            r = m7753.get(sid) or {}
            for k, col in (("gm", "growth_manager_name"), ("gc", "growth_consultant_name"),
                           ("gl", "growth_lead_name")):
                if not roles[sid].get(k) and clean(r.get(col)):
                    roles[sid][k] = clean(r.get(col)); filled += 1
        print(f"[gsellers] card 7753 covered {len(m7753)}/{len(gsids)} · filled {filled} blank role names")
    except Exception as ex:
        print(f"[gsellers] card 7753 failed: {ex}")

    # ---- google spend: card 7401 (lifetime / yesterday / last-7) ----
    g7401 = {}
    try:
        for r in req(f"{url}/api/card/7401/query/json", "POST", {}, H):
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
            rows = req(f"{url}/api/card/5207/query/json", "POST", body, H, timeout=300)
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
    with ThreadPoolExecutor(max_workers=6) as ex:
        for sid, v in ex.map(q5207, gsids):
            if v:
                gpnl[sid] = v
    weeks = sorted({v["w1w"] for v in gpnl.values() if v["w1w"]}, reverse=True)
    print(f"[gsellers] card 5207 google PNL: {len(gpnl)}/{len(gsids)} sellers · w1 weeks seen {weeks[:3]}")

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
            "n": str(meta.get("n") or "").strip(),
            "ga": str(sr.get("ga") or ""),
            "gl": roles[sid].get("gl", ""), "gm": roles[sid].get("gm", ""),
            "cl": roles[sid].get("cl", ""), "gc": roles[sid].get("gc", ""),
            "life": None if life is None else round(life, 2),
            "ysp": None if ysp is None else round(ysp, 2),
            "last7": None if last7 is None else round(last7, 2),
            "live": bool(life is not None and life > LIVE_MIN),
            "spending": bool(ysp is not None and ysp > 0),
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
                       "healthFloor": HEALTH_FLOOR, "liveMin": LIVE_MIN},
        "weeks": {"w1": (weeks[0] if weeks else ""), "w2": ""},
        "rows": rows_out,
        "dq": {
            "base1k5k": len(base), "googleAcctMade": len(gsids),
            "pnlCovered": len(gpnl), "spendCovered": len(g7401),
            "roleCoverage": role_cov,
            "counts": {k: cnt(k) for k in ("live", "spending", "k3", "health",
                                           "potential", "objective", "subjective")},
            "card11011": ("unusable as the PNL source: its hit_sellers CTE excludes every "
                          "csv_upload.hit_master_data seller, so overlap with the 1k-5k base is 0; "
                          "and best_source='google' is 0 rows card-wide because best_source tags "
                          "whichever source had the greatest w1 pnl and resolves gc_view_3 -> "
                          "new_pnl -> facebook before google. Using card 5207 (Google Seller PNL, "
                          "the same google leg card 11011 itself reads) instead."),
            "glNote": ("growth_lead is not assigned for this population: card 7753 "
                       "growth_lead_name is '-' for all of them and only a handful carry a "
                       "google_growth_lead in nushop.seller_managers. GC (growth_consultant) is "
                       "the populated third level."),
        },
    }
    w2s_seen = sorted({v["w2w"] for v in gpnl.values() if v["w2w"]}, reverse=True)
    out["weeks"]["w2"] = w2s_seen[0] if w2s_seen else ""

    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(rows_out)} google 1k-5k sellers · "
          + " · ".join(f"{k}={cnt(k)}" for k in ("live", "spending", "k3", "health",
                                                 "potential", "objective", "subjective")))

    if "--push" in sys.argv:
        subprocess.run(["git", "-C", REPO, "add", "google_sellers_data.json"], check=True)
        r = subprocess.run(["git", "-C", REPO, "commit", "-m", "Refresh Google seller book data"],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())


if __name__ == "__main__":
    main()
