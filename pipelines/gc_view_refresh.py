#!/usr/bin/env python3
"""Build gc_data.json for the 'View as Growth Consultant' role view.

Per GC (card 7753 growth_consultant_name), assemble assigned sellers, per-GC task
buckets, headline metrics, and a per-seller DETAIL record (for the click-through).

Sources:
  - 7753   seller -> all assigned people (GC/GM/GL/KAM/KAE/AM/POCs)
  - 10352  seller company / website / contact
  - golive_data.json   a2h/golive (live / not-live-yet)
  - ts_data.json       s7 spend, last T/S date + actions
  - scaling_data.json  meta/google yest spend (spending)
  - 2787   today / yesterday / lifetime spend
  - 3539   FB balance (funds-addition: prepaid & balance<2000)
  - 11286  disabled ad accounts (ad-account-blocked)
  - 10206  calls (total calls done per seller)
  - task_data.json     tasks + scheduled calls (pending & completed)
  - escalation sheet   SOS escalations (SOS Pending + Strikes)
  - 11322  GC HIT targets (current month) ; hit1_data.json HIT1 achieved

GAPS still pending a source: account-paused flag + pause date (=> calls-after-paused),
PQ (lifetime / last-15-days), Experimental flag.

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/gc_view_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, urllib.parse, datetime, re
from collections import defaultdict

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "gc_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
LOCAL_SA_KEY = os.path.expanduser("~/Downloads/metrics-tracker-automation-53ad2cdd4b65.json")
ESC_SHEET = "1eIbQU-odVp6lwBnawIIdSbpVHIrywy98Ib4RsZhEPgk"
ESC_RANGE = "'Raw_Suggested'!A2:P"
_norm = lambda v: re.sub(r"\s+", " ", str(v or "").strip())
FUNDS_THRESHOLD = 2000
SPEND3K = 3540


def creds():
    if os.environ.get("METABASE_URL"):
        return os.environ["METABASE_URL"].rstrip("/"), os.environ["METABASE_USER_EMAIL"], os.environ["METABASE_PASSWORD"]
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e["METABASE_USER_EMAIL"], e["METABASE_PASSWORD"]


def req(url, method="GET", body=None, H=None):
    import time as _t
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=600) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last = e
            _t.sleep(3 * (attempt + 1))
    raise last


def load_json(name, dflt):
    p = os.path.join(REPO, name)
    return json.load(open(p)) if os.path.exists(p) else dflt


def num(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0


def fetch_escalations():
    """SOS escalations from the sheet -> per GC list. Source col A == 'SOS'."""
    sa = json.loads(os.environ["GOOGLE_SA_KEY"]) if os.environ.get("GOOGLE_SA_KEY") else (json.load(open(LOCAL_SA_KEY)) if os.path.exists(LOCAL_SA_KEY) else None)
    if not sa:
        print("[gc] no SA key -> skipping escalation (SOS/strikes)")
        return {}
    from google.oauth2 import service_account
    import google.auth.transport.requests as gtr
    c = service_account.Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    c.refresh(gtr.Request())
    u = "https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s" % (ESC_SHEET, urllib.parse.quote(ESC_RANGE))
    vals = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"Authorization": "Bearer " + c.token}), timeout=180).read()).get("values", [])
    esc = defaultdict(list)   # gc_lower -> [rows]
    for r in vals:
        r = r + [""] * (16 - len(r))
        if _norm(r[0]).upper() != "SOS":
            continue
        esc[_norm(r[12]).lower()].append({
            "s": _norm(r[1]), "date": _norm(r[2])[:10], "voc": _norm(r[4]),
            "status": _norm(r[5]).lower(), "gm": _norm(r[13]), "cl": _norm(r[14]),
        })
    print(f"[gc] escalation SOS rows across {len(esc)} GCs")
    return esc


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]
    H = {"Content-Type": "application/json", "X-Metabase-Session": tok}

    # ---- 7753: seller -> GC + all assigned people ----
    people_of, gc_of = {}, {}
    for r in req(f"{url}/api/card/7753/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if not sid:
            continue
        gc = _norm(r.get("growth_consultant_name"))
        if gc in ("", "-") or "dummy" in gc.lower():
            continue
        gc_of[sid] = gc
        people_of[sid] = {
            "GC": gc, "GM": _norm(r.get("growth_manager_name")), "GL": _norm(r.get("growth_lead_name")),
            "KAM": _norm(r.get("key_account_manager_name")), "KAE": _norm(r.get("key_account_executive_name")),
            "AM": _norm(r.get("assistant_manager_name")), "Golive POC": _norm(r.get("golive_poc_name")),
            "Onboarding POC": _norm(r.get("onboarding_poc_name")), "Profitability AM": _norm(r.get("profitability_associate_manager_name")),
        }
    print(f"[gc] 7753: {len(gc_of)} sellers under a GC")

    # ---- previous outputs (fallback when a BigQuery card is quota-blocked) ----
    prev = load_json("gc_data.json", {}) or {}
    prev_detail = (load_json("gc_detail_data.json", {}) or {}).get("detail", {})
    prev_sellers = {}
    for _g, _v in (prev.get("byGC") or {}).items():
        for _s in _v.get("sellers", []):
            prev_sellers[_s["id"]] = _s

    # ---- 10352: company / website / contact (fallback: previous file) ----
    info = {}
    try:
        for r in req(f"{url}/api/card/10352/query/json", "POST", {}, H):
            sid = str(r.get("seller_id") or "").strip()
            if sid in gc_of:
                info[sid] = {"name": _norm(r.get("company")), "website": _norm(r.get("website")), "contact": _norm(r.get("seller_contact"))}
        print(f"[gc] 10352: info for {len(info)} sellers")
    except Exception as _e:
        for sid, s in prev_sellers.items():
            info[sid] = {"name": s.get("name", ""), "website": s.get("website", ""), "contact": s.get("contact", "")}
        print(f"[gc] 10352 failed (quota?) -> reused {len(info)} from previous file: {_e}")

    # ---- 10065: last spend date (paused flag + pause date; fallback: previous detail) ----
    last_spend = {}
    try:
        for r in req(f"{url}/api/card/10065/query/json", "POST", {}, H):
            sid = str(r.get("seller id") or r.get("seller_id") or "").strip()
            d = str(r.get("last spend date") or "")[:10]
            if sid in gc_of and d:
                last_spend[sid] = d
        print(f"[gc] 10065: last-spend-date for {len(last_spend)} sellers")
    except Exception:
        for sid, dd in prev_detail.items():
            if dd.get("pauseDate"):
                last_spend[sid] = dd["pauseDate"]
        print(f"[gc] 10065 failed -> reused {len(last_spend)} pause dates from previous detail")

    # ---- local data ----
    golive = (load_json("golive_data.json", {}) or {}).get("sellers", {})
    ts_sellers = (load_json("ts_data.json", {}) or {}).get("sellers", {})
    scaling = (load_json("scaling_data.json", {}) or {}).get("sellers", {})
    hit1 = (load_json("hit1_data.json", {}) or {}).get("rows", [])

    # ---- 2787: spend today/yest/lifetime ----
    spend_of = {}
    for r in req(f"{url}/api/card/2787/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if sid not in gc_of:
            continue
        cur = spend_of.setdefault(sid, {"today": 0.0, "yest": 0.0, "life": 0.0})
        cur["today"] += num(r.get("today_spend")); cur["yest"] += num(r.get("yesterday_spend")); cur["life"] += num(r.get("lifetime_spend"))

    # ---- 8684 experimental sellers (any seller present is experimental) ----
    experimental = set()
    try:
        for r in req(f"{url}/api/card/8684/query/json", "POST", {}, H):
            sid = str(r.get("seller_id") or (list(r.values())[0] if r else "") or "").strip()
            if sid in gc_of:
                experimental.add(sid)
        print(f"[gc] 8684: {len(experimental)} experimental sellers")
    except Exception as _e:
        experimental = {sid for sid, s in prev_sellers.items() if s.get("experimental")}
        print(f"[gc] 8684 failed (quota?) -> reused {len(experimental)} from previous file")

    # ---- 3539 funds-low, 11286 ad-blocked (fallback: previous file) ----
    funds_low, ad_blocked = set(), set()
    try:
        for r in req(f"{url}/api/card/3539/query/json", "POST", {}, H):
            sid = str(r.get("seller_id") or "").strip()
            if sid in gc_of and bool(r.get("is_prepay_account")) and num(r.get("balance")) < FUNDS_THRESHOLD:
                funds_low.add(sid)
    except Exception:
        funds_low = {sid for sid, s in prev_sellers.items() if s.get("fundsLow")}
        print(f"[gc] 3539 failed -> reused {len(funds_low)} funds-low from previous file")
    try:
        for r in req(f"{url}/api/card/11286/query/json", "POST", {}, H):
            sid = str(r.get("seller_id") or "").strip()
            if sid in gc_of:
                ad_blocked.add(sid)
    except Exception:
        ad_blocked = {sid for sid, s in prev_sellers.items() if s.get("adBlocked")}
        print(f"[gc] 11286 failed -> reused {len(ad_blocked)} ad-blocked from previous file")
    print(f"[gc] funds-low={len(funds_low)} ad-blocked={len(ad_blocked)}")

    # ---- 10206 total calls + calls after pause date (fallback: previous detail) ----
    calls_total = defaultdict(int)
    calls_after = defaultdict(int)
    try:
        for r in req(f"{url}/api/card/10206/query/json", "POST", {}, H):
            sid = str(r.get("seller_id") or "").strip()
            if sid not in gc_of:
                continue
            calls_total[sid] += 1
            cd = str(r.get("call_date") or "")[:10]
            ls = last_spend.get(sid)
            if cd and ls and cd > ls:
                calls_after[sid] += 1
    except Exception:
        for sid, dd in prev_detail.items():
            calls_total[sid] = dd.get("totalCalls", 0)
            if dd.get("callsAfterPaused") is not None:
                calls_after[sid] = dd["callsAfterPaused"]
        print("[gc] 10206 failed -> reused calls from previous detail")

    # ---- task_data: per-seller pending/done tasks & scheduled calls ----
    tasks = (load_json("task_data.json", {}) or {}).get("tasks", [])
    t_pending, t_done = defaultdict(list), defaultdict(list)   # non-callback tasks
    c_pending, c_done = defaultdict(list), defaultdict(list)   # callbacks
    for t in tasks:
        sid = str(t.get("s") or "").strip()
        if sid not in gc_of:
            continue
        rec = {"id": t.get("id"), "ty": t.get("ty"), "st": t.get("st"), "du": t.get("du"), "cp": t.get("cp"), "status": t.get("status")}
        is_cb = str(t.get("ty") or "").lower() == "callback"
        done = str(t.get("status") or "").lower() in ("completed", "closed")
        if is_cb:
            (c_done if done else c_pending)[sid].append(rec)
        else:
            (t_done if done else t_pending)[sid].append(rec)

    # ---- escalations (SOS / strikes) ----
    esc = fetch_escalations()

    # ---- HIT targets (GC, current month) + achieved ----
    today = datetime.date.today()
    yday_iso = (today - datetime.timedelta(days=1)).isoformat()
    cur_m, cur_y = today.month, today.year
    hits_target = {}
    for r in req(f"{url}/api/card/11322/query/json", "POST", {}, H):
        if str(r.get("Role") or "").strip().upper() != "GC":
            continue
        if int(r.get("Target_Month") or 0) == cur_m and int(r.get("Target_Year") or 0) == cur_y:
            nm = _norm(r.get("Name"))
            if nm and r.get("HITS_Target") is not None:
                hits_target[nm.lower()] = num(r.get("HITS_Target"))
    hits_ach = defaultdict(int)
    for x in hit1:
        if int(x.get("hm") or 0) == cur_m and int(x.get("hy") or 0) == cur_y:
            gc = gc_of.get(str(x.get("id") or "").strip())
            if gc:
                hits_ach[gc.lower()] += 1

    # ---- assemble ----
    sellers_by_gc = defaultdict(list)
    for sid, gc in gc_of.items():
        sellers_by_gc[gc].append(sid)
    gcs = sorted(set(gc_of.values()))
    by_gc, detail = {}, {}

    for gc in gcs:
        sids = sellers_by_gc[gc]
        sellers = []
        live = spending = spend3k = 0
        pending_ts = []
        for sid in sids:
            gv = golive.get(sid) or {}
            a2h, gol = gv.get("a") or "", gv.get("g") or ""
            is_live = bool(gol); nl = bool(a2h and not gol)
            sc = scaling.get(sid) or {}
            is_spending = num(sc.get("my")) > 1 or num(sc.get("gy")) > 10
            t = ts_sellers.get(sid) or {}
            s7 = num(t.get("s7")); over3k = s7 > SPEND3K
            d = t.get("d") or ""
            ds = None
            if d:
                try: ds = (today - datetime.date.fromisoformat(d[:10])).days
                except ValueError: ds = None
            elig = over3k and (ds is None or ds > 7)
            if is_live: live += 1
            if is_spending: spending += 1
            if over3k: spend3k += 1
            if elig: pending_ts.append(sid)
            sp = spend_of.get(sid, {"today": 0.0, "yest": 0.0, "life": 0.0})
            pause_date = last_spend.get(sid, "")
            paused = bool(pause_date and pause_date < yday_iso)
            is_exp = sid in experimental
            hypercare = False   # eligibility criteria pending from user
            nfo = info.get(sid, {})
            sellers.append({
                "id": sid, "name": nfo.get("name") or _norm(t.get("n")), "contact": nfo.get("contact", ""), "website": nfo.get("website", ""),
                "live": is_live, "notLiveYet": nl, "spending": is_spending,
                "adBlocked": sid in ad_blocked, "fundsLow": sid in funds_low,
                "paused": paused, "experimental": is_exp, "hypercare": hypercare,
            })
            cases = []
            if nl: cases.append("Golive pending")
            if sid in ad_blocked: cases.append("Ad account blocked")
            if sid in funds_low: cases.append("Funds addition (low balance)")
            if elig: cases.append("Troubleshoot due")
            if paused: cases.append("Account paused (no spend today/yesterday)")
            detail[sid] = {
                "spend": {"today": round(sp["today"]), "yest": round(sp["yest"]), "life": round(sp["life"])},
                "people": {k: v for k, v in (people_of.get(sid) or {}).items() if v and v != "-"},
                "lastTsDate": d, "lastTsActions": _norm(t.get("a")),
                "totalCalls": calls_total.get(sid, 0), "callsAfterPaused": calls_after.get(sid, 0), "pauseDate": pause_date,
                "tasksPending": t_pending.get(sid, []), "tasksDone": t_done.get(sid, []),
                "callsPending": c_pending.get(sid, []), "callsDone": c_done.get(sid, []),
                "adBlocked": sid in ad_blocked, "fundsLow": sid in funds_low,
                "live": is_live, "notLiveYet": nl, "spending": is_spending,
                "paused": paused, "experimental": is_exp, "hypercare": hypercare,
                "pqLifetime": None, "pq15": None,
                "cases": cases,
            }
        assigned = len(sids)
        eg = esc.get(gc.lower(), [])
        by_gc[gc] = {
            "sellers": sellers,
            "metrics": {
                "assigned": assigned, "live": live, "spending": spending, "spend3k": spend3k,
                "spendLivePct": round(spending / live * 100, 1) if live else None,
                "liveAssignedPct": round(live / assigned * 100, 1) if assigned else None,
                "spend3kLivePct": round(spend3k / live * 100, 1) if live else None,
                "hitsTarget": hits_target.get(gc.lower()), "hitsAchieved": hits_ach.get(gc.lower(), 0),
            },
            "golive": [s["id"] for s in sellers if s["notLiveYet"]],
            "funds": [s["id"] for s in sellers if s["fundsLow"]],
            "adBlocked": [s["id"] for s in sellers if s["adBlocked"]],
            "hypercare": [s["id"] for s in sellers if s.get("hypercare")],
            "pendingTS": pending_ts,
            "pendingTasks": [x for sid in sids for x in t_pending.get(sid, [])],
            "callbacks": [x for sid in sids for x in c_pending.get(sid, [])],
            "sos": [e for e in eg if e["status"] == "pending"],
            "strikes": eg,
        }

    ts_gen = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"generatedAt": ts_gen, "asOfMonth": today.strftime("%Y-%m"), "gcs": gcs, "byGC": by_gc}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    DETAIL_OUT = os.path.join(REPO, "gc_detail_data.json")
    json.dump({"generatedAt": ts_gen, "detail": detail}, open(DETAIL_OUT, "w"), separators=(",", ":"))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(gcs)} GCs")
    print(f"[out] {DETAIL_OUT} ({os.path.getsize(DETAIL_OUT)} bytes) · {len(detail)} seller details")

    if "--push" in sys.argv:
        subprocess.run(["git", "-C", REPO, "add", "gc_data.json", "gc_detail_data.json"], check=True)
        r = subprocess.run(["git", "-C", REPO, "commit", "-m", "Refresh GC view data"], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(["git", "-C", REPO, "push", "origin", "main"], check=True)
            print("[push] deployed")


if __name__ == "__main__":
    main()
